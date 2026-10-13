import json
import socket
import threading
import time
from hashlib import sha256
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

from nopoach.collect.fetch import FetchClient, FetchPolicyError, OfflineError
from nopoach.store.provenance import ProvenanceLog

CONTACT = "public-data-contact@example.invalid"


def client(log, handler, **kwargs):
    return FetchClient(
        log, contact=CONTACT, transport=httpx.MockTransport(handler), **kwargs
    )


@pytest.mark.network_off
@pytest.mark.parametrize(
    "url", ["https://unapproved.invalid/data", "https://www.sec.gov.evil.invalid/data"]
)
def test_unknown_host_never_reaches_transport(tmp_path, url):
    def handler(request):
        pytest.fail("Disallowed host reached transport")

    with client(ProvenanceLog(tmp_path / "provenance.jsonl"), handler) as fetch:
        with pytest.raises(FetchPolicyError, match="allowlist"):
            fetch.fetch(url)


@pytest.mark.network_off
def test_offline_never_opens_socket_or_calls_transport(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Offline client attempted network")

    monkeypatch.setattr(socket, "socket", forbidden)
    log = ProvenanceLog(tmp_path / "provenance.jsonl")
    with client(log, forbidden, offline=True) as fetch:
        with pytest.raises(OfflineError, match="offline"):
            fetch.fetch("https://www.sec.gov/synthetic")
    assert log.records() == ()


def test_fetch_logs_before_caller_parses(tmp_path):
    log = ProvenanceLog(tmp_path / "provenance.jsonl")
    body = b'{"synthetic":true}'

    def handler(request):
        assert request.headers["User-Agent"] == (
            f"nopoach-screen/0.1 (public-data screening; contact: {CONTACT})"
        )
        return httpx.Response(200, content=body)

    with client(log, handler) as fetch:
        result = fetch.fetch("https://www.sec.gov/synthetic.json")
        assert result.body == body
        assert result.status_code == 200
        assert log.records() == (result.provenance,)
        assert result.provenance.sha256 == sha256(body).hexdigest()
        assert result.provenance.method == "GET"
        assert json.loads(result.body) == {"synthetic": True}


def test_sec_requests_share_rate_budget_across_clients_and_hosts(tmp_path):
    starts = []

    def handler(request):
        starts.append(time.monotonic())
        return httpx.Response(200, content=b"synthetic")

    log = ProvenanceLog(tmp_path / "provenance.jsonl")
    with client(log, handler) as first, client(log, handler) as second:
        for index in range(11):
            fetch = first if index % 2 else second
            host = "www.sec.gov" if index % 2 else "data.sec.gov"
            fetch.fetch(f"https://{host}/synthetic")
    assert all(b - a >= 0.1 for a, b in zip(starts, starts[1:], strict=False))
    assert starts[10] - starts[0] >= 1.0


def test_retry_backoff_and_audit_every_response(tmp_path, monkeypatch):
    delays = []
    monkeypatch.setattr("nopoach.collect.fetch.time.sleep", delays.append)
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(503 if len(calls) < 3 else 200, content=b"synthetic")

    log = ProvenanceLog(tmp_path / "provenance.jsonl")
    with client(log, handler, allowed_hosts={"public.invalid": 100}) as fetch:
        assert fetch.fetch("https://public.invalid/file").status_code == 200
    assert len(calls) == 3
    assert [record.status_code for record in log.records()] == [503, 503, 200]
    assert 0.5 in delays and 1.0 in delays


def test_redirect_is_logged_and_checked_before_request(tmp_path):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(302, headers={"location": "https://unapproved.invalid/"})

    log = ProvenanceLog(tmp_path / "provenance.jsonl")
    with client(log, handler) as fetch:
        with pytest.raises(FetchPolicyError, match="allowlist"):
            fetch.fetch("https://www.sec.gov/synthetic")
    assert len(calls) == 1
    assert log.records()[0].status_code == 302


@pytest.mark.parametrize(
    "url",
    [
        "https://user:password@www.sec.gov/file",
        "https://www.linkedin.com/file",
    ],
)
def test_no_account_or_linkedin_requests(tmp_path, url):
    def handler(request):
        pytest.fail("Account or LinkedIn collection reached transport")

    log = ProvenanceLog(tmp_path / "provenance.jsonl")
    with client(
        log, handler, allowed_hosts={"www.sec.gov": 10, "www.linkedin.com": 1}
    ) as fetch:
        with pytest.raises(FetchPolicyError):
            fetch.fetch(url)


def test_cookies_are_never_sent(tmp_path):
    def handler(request):
        assert "cookie" not in request.headers
        assert "authorization" not in request.headers
        return httpx.Response(200, headers={"set-cookie": "session=synthetic"})

    with client(ProvenanceLog(tmp_path / "provenance.jsonl"), handler) as fetch:
        fetch.fetch("https://www.sec.gov/first")
        fetch.fetch("https://www.sec.gov/second")


@pytest.mark.live
def test_local_server_provenance(tmp_path):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"synthetic local-server fixture")

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        log = ProvenanceLog(tmp_path / "provenance.jsonl")
        with FetchClient(
            log, contact=CONTACT, allowed_hosts={"127.0.0.1": 10}
        ) as fetch:
            result = fetch.fetch(f"http://127.0.0.1:{server.server_port}/synthetic")
        assert result.body == b"synthetic local-server fixture"
        assert log.records() == (result.provenance,)
        assert result.provenance.sha256 == sha256(result.body).hexdigest()
        print(log.path.read_text())
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
