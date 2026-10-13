"""The single network boundary for permitted, unauthenticated public retrieval.

Default sources are limited to SEC's public hosts, whose access/reuse policy is
published at https://www.sec.gov/files/about/webmaster-faq.htm and whose shared
10 requests/second ceiling is at https://www.sec.gov/about/privacy-information.
Collectors may supply other explicitly vetted public hosts and their rates.
Allowlisting a hostname is not itself a review of the source's terms.
"""

import ipaddress
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass
from types import TracebackType

import httpx

from nopoach.store.provenance import ProvenanceLog, ProvenanceRecord

DEFAULT_HOSTS: Mapping[str, float] = {
    "www.sec.gov": 10,
    "data.sec.gov": 10,
    "sec.gov": 10,
}
_SEC_HOSTS = {"sec.gov", "www.sec.gov", "data.sec.gov", "archives.sec.gov"}
_RETRY_STATUSES = {429, 500, 502, 503, 504}
_REDIRECT_STATUSES = {301, 302, 303, 307, 308}


class FetchPolicyError(ValueError):
    """The request violates the public-source retrieval policy."""


class OfflineError(FetchPolicyError):
    """Offline mode forbids every request."""


@dataclass(frozen=True)
class FetchedResponse:
    body: bytes
    status_code: int
    headers: Mapping[str, str]
    provenance: ProvenanceRecord


class _RateBudget:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.last_completed: float | None = None

    def wait(self, rate: float) -> None:
        if self.last_completed is not None:
            remaining = 1 / rate - (time.monotonic() - self.last_completed)
            if remaining > 0:
                time.sleep(remaining)


# All client instances share budgets, including across SEC subdomains. A host
# lock spans sending so competing threads cannot bunch delayed request starts.
_BUDGETS: dict[str, _RateBudget] = {}
_BUDGETS_LOCK = threading.Lock()


def _budget(host: str) -> _RateBudget:
    key = "sec.gov" if host in _SEC_HOSTS else host
    with _BUDGETS_LOCK:
        return _BUDGETS.setdefault(key, _RateBudget())


class FetchClient:
    def __init__(
        self,
        provenance: ProvenanceLog,
        *,
        contact: str,
        allowed_hosts: Mapping[str, float] | None = None,
        offline: bool = False,
        transport: httpx.BaseTransport | None = None,
        retries: int = 2,
    ) -> None:
        if not contact.strip() or "\n" in contact or "\r" in contact:
            raise FetchPolicyError("A descriptive public-source contact is required")
        if retries < 0:
            raise ValueError("retries must be nonnegative")
        hosts = DEFAULT_HOSTS if allowed_hosts is None else allowed_hosts
        if any(rate <= 0 for rate in hosts.values()):
            raise ValueError("Host rates must be positive")
        self.allowed_hosts = {host.lower(): rate for host, rate in hosts.items()}
        self.offline = offline
        self.provenance = provenance
        self.retries = retries
        self._http = httpx.Client(
            headers={
                "User-Agent": (
                    f"nopoach-screen/0.1 (public-data screening; contact: {contact})"
                )
            },
            transport=transport,
            follow_redirects=False,
            trust_env=False,
            timeout=30,
        )

    def _validate(self, url: httpx.URL) -> str:
        if self.offline:
            raise OfflineError("Fetch refused: offline mode is enabled")
        host = url.host.lower()
        if url.username or url.password:
            raise FetchPolicyError("Account credentials are forbidden")
        if host == "linkedin.com" or host.endswith(".linkedin.com"):
            raise FetchPolicyError("LinkedIn collection is forbidden")
        if host not in self.allowed_hosts:
            raise FetchPolicyError(
                f"Host is not on the public-source allowlist: {host}"
            )
        is_loopback = host == "localhost"
        try:
            is_loopback = ipaddress.ip_address(host).is_loopback
        except ValueError:
            pass
        if url.scheme != "https" and not (url.scheme == "http" and is_loopback):
            raise FetchPolicyError("Public source requests require HTTPS")
        return host

    def fetch(self, url: str, *, method: str = "GET") -> FetchedResponse:
        """Audit raw responses before status handling, redirects, or parsing."""
        if self.offline:
            raise OfflineError("Fetch refused: offline mode is enabled")
        method = method.upper()
        if method not in {"GET", "HEAD"}:
            raise FetchPolicyError("Only public GET and HEAD retrieval is permitted")
        current = httpx.URL(url)
        redirects = 0
        while True:
            host = self._validate(current)
            rate = self.allowed_hosts[host]
            if host in _SEC_HOSTS:
                rate = min(rate, 10)
            budget = _budget(host)
            for attempt in range(self.retries + 1):
                try:
                    with budget.lock:
                        self._validate(current)
                        budget.wait(rate)
                        # Do not carry session cookies from any earlier response.
                        request = self._http.build_request(method, current)
                        request.headers.pop("cookie", None)
                        try:
                            response = self._http.send(request)
                        finally:
                            budget.last_completed = time.monotonic()
                except httpx.TransportError:
                    if attempt == self.retries:
                        raise
                    time.sleep(0.5 * 2**attempt)
                    continue
                record = self.provenance.append(
                    source=host,
                    url=str(current),
                    method=method,
                    body=response.content,
                    status_code=response.status_code,
                )
                if response.status_code in _RETRY_STATUSES and attempt < self.retries:
                    time.sleep(0.5 * 2**attempt)
                    continue
                break
            if response.status_code in _REDIRECT_STATUSES:
                redirects += 1
                if redirects > 10:
                    raise FetchPolicyError("Too many public-source redirects")
                current = current.join(response.headers["location"])
                continue
            response.raise_for_status()
            return FetchedResponse(
                response.content, response.status_code, dict(response.headers), record
            )

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "FetchClient":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()
