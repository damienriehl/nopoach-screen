"""Packaging proofs. Socket smoke is host-owned: pytest -m live tests/packaging.

Set NOPOACH_EXECUTABLE to the built one-folder executable before the live test.
"""

import hashlib
import importlib.util
import json
import os
import signal
import subprocess
import sys
import time
import urllib.request
import zipfile
from http.cookiejar import CookieJar
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[2]


def load_script(name):
    spec = importlib.util.spec_from_file_location(
        name, ROOT / "packaging" / f"{name}.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("supplied", [None, ""])
def test_clean_scan_passes_without_external_denylist(monkeypatch, capsys, supplied):
    scan = load_script("scan_repository")
    if supplied is None:
        monkeypatch.delenv("NOPOACH_DENYLIST_PATH", raising=False)
    else:
        monkeypatch.setenv("NOPOACH_DENYLIST_PATH", supplied)
    with patch.object(
        scan, "git", side_effect=[b"src/example.py\0", b"# Synthetic Restricted Term"]
    ):
        assert scan.main([]) == 0
    assert capsys.readouterr().err == (
        "No denylist supplied; term scan skipped (data-file checks still run).\n"
    )


@pytest.mark.parametrize("with_denylist", [False, True])
@pytest.mark.parametrize(
    "path",
    [
        "private.duckdb",
        "private.duckdb.wal",
        "provenance.jsonl",
        "workspaces/notes.txt",
        "x.PARQUET",
        "workspace/notes.txt",
        "exports/result.docx",
        "dossier-synthetic.docx",
    ],
)
def test_staged_data_is_rejected_without_printing_paths(
    tmp_path, monkeypatch, capsys, path, with_denylist
):
    scan = load_script("scan_repository")
    if with_denylist:
        denylist = tmp_path / "external.txt"
        denylist.write_text("Synthetic Restricted Term\n")
        monkeypatch.setenv("NOPOACH_DENYLIST_PATH", str(denylist))
    else:
        monkeypatch.delenv("NOPOACH_DENYLIST_PATH", raising=False)
    # Supply Git's index read results; never write this checkout's Git index.
    with patch.object(scan, "git", side_effect=[path.encode() + b"\0", b"safe"]):
        assert scan.main([]) == 1
    logs = capsys.readouterr()
    assert "Repository scan rejected" in logs.err
    assert path not in logs.out + logs.err


@pytest.mark.parametrize("where", ["path", "content"])
def test_denylisted_terms_never_appear_in_logs(tmp_path, monkeypatch, capsys, where):
    scan = load_script("scan_repository")
    term = "Synthetic Restricted Term"
    denylist = tmp_path / "external.txt"
    denylist.write_text(term + "\n")
    monkeypatch.setenv("NOPOACH_DENYLIST_PATH", str(denylist))
    path = f"{term}.txt" if where == "path" else "safe.txt"
    body = term.upper().encode() if where == "content" else b"safe"
    with patch.object(scan, "git", side_effect=[path.encode() + b"\0", body]):
        assert scan.main([]) == 1
    logs = capsys.readouterr()
    assert term.lower() not in (logs.out + logs.err).lower()


def test_clean_staged_code_passes(tmp_path, monkeypatch):
    scan = load_script("scan_repository")
    denylist = tmp_path / "external.txt"
    denylist.write_text("Synthetic Restricted Term\n")
    monkeypatch.setenv("NOPOACH_DENYLIST_PATH", str(denylist))
    with patch.object(
        scan, "git", side_effect=[b"src/example.py\0", b"print('hello')"]
    ):
        assert scan.main([]) == 0


def test_set_but_missing_denylist_fails_closed(tmp_path, monkeypatch, capsys):
    scan = load_script("scan_repository")
    denylist = tmp_path / "missing.txt"
    monkeypatch.setenv("NOPOACH_DENYLIST_PATH", str(denylist))
    assert scan.main([]) == 1
    logs = capsys.readouterr()
    assert "Repository scan refused" in logs.err
    assert str(denylist) not in logs.out + logs.err


def test_unreadable_denylist_fails_closed(tmp_path, monkeypatch, capsys):
    scan = load_script("scan_repository")
    denylist = tmp_path / "external.txt"
    denylist.write_text("Synthetic Restricted Term\n")
    monkeypatch.setenv("NOPOACH_DENYLIST_PATH", str(denylist))
    with patch.object(Path, "read_text", side_effect=PermissionError):
        assert scan.main([]) == 1
    logs = capsys.readouterr()
    assert "Repository scan refused" in logs.err
    assert str(denylist) not in logs.out + logs.err


def test_empty_or_in_repo_denylist_fails_closed(tmp_path, monkeypatch):
    scan = load_script("scan_repository")
    denylist = tmp_path / "empty.txt"
    denylist.write_text("\n")
    monkeypatch.setenv("NOPOACH_DENYLIST_PATH", str(denylist))
    assert scan.main([]) == 1
    monkeypatch.setenv("NOPOACH_DENYLIST_PATH", str(ROOT / "THIRD-PARTY.md"))
    assert scan.main([]) == 1


def test_history_scan_rejects_deleted_data_without_index_writes(tmp_path, monkeypatch):
    scan = load_script("scan_repository")
    repository = tmp_path / "synthetic.git"
    subprocess.run(
        ["git", "init", "--bare", str(repository)], check=True, capture_output=True
    )
    # fast-import creates synthetic history directly; it never writes an index.
    history = (
        b"commit refs/heads/synthetic\n"
        b"committer Synthetic <synthetic@example.invalid> 1 +0000\n"
        b"data 4\nadd\n\nM 100644 inline private.duckdb\ndata 4\nsafe\n\n"
        b"commit refs/heads/synthetic\n"
        b"committer Synthetic <synthetic@example.invalid> 2 +0000\n"
        b"data 7\nremove\n\nD private.duckdb\n\ndone\n"
    )
    subprocess.run(
        ["git", "-C", str(repository), "fast-import", "--quiet"],
        input=history,
        check=True,
        capture_output=True,
    )
    monkeypatch.delenv("NOPOACH_DENYLIST_PATH", raising=False)
    monkeypatch.setattr(scan, "ROOT", repository)
    assert scan.main(["--history"]) == 1
    assert not (repository / "index").exists()


def test_build_keeps_temporary_work_outside_release_output(tmp_path, monkeypatch):
    build = load_script("build")
    output = tmp_path / "release"
    scratch_paths = []

    def run_tool(command, **kwargs):
        if command[2] == "PyInstaller":
            work = Path(command[command.index("--workpath") + 1])
            config = Path(kwargs["env"]["PYINSTALLER_CONFIG_DIR"])
            matplotlib = Path(kwargs["env"]["MPLCONFIGDIR"])
            for directory in (work, config, matplotlib):
                directory.mkdir(parents=True)
                (directory / "temporary.txt").write_text("build-only content")
                scratch_paths.append(directory)
            app = output / "nopoach-screen"
            app.mkdir()
            (app / "nopoach-screen.exe").write_bytes(b"synthetic executable")
        else:
            Path(command[command.index("--output-file") + 1]).write_text(
                json.dumps({"bomFormat": "CycloneDX", "specVersion": "1.6"})
            )

    monkeypatch.setattr(build.subprocess, "run", run_tool)
    monkeypatch.setattr(sys, "argv", ["build.py", "--output", str(output)])
    build.main()
    assert {path.name for path in output.iterdir()} == {
        "nopoach-screen",
        "nopoach-screen.zip",
        "SHA256SUMS.txt",
        "bom.cdx.json",
        "IT-REVIEW.md",
    }
    assert all(not path.is_relative_to(output) for path in scratch_paths)
    assert all(not path.exists() for path in scratch_paths)


def test_release_bundle_hashes_and_bom(tmp_path):
    build = load_script("build")
    app = tmp_path / "nopoach-screen"
    app.mkdir()
    (app / "nopoach-screen.exe").write_bytes(b"synthetic executable")
    (app / "_internal").mkdir()
    (app / "_internal" / "library.dll").write_bytes(b"synthetic library")
    (tmp_path / "bom.cdx.json").write_text(
        json.dumps({"bomFormat": "CycloneDX", "specVersion": "1.6", "components": []})
    )
    build.bundle(tmp_path)
    with zipfile.ZipFile(tmp_path / "nopoach-screen.zip") as archive:
        assert "nopoach-screen/nopoach-screen.exe" in archive.namelist()
        assert "nopoach-screen/IT-REVIEW.md" in archive.namelist()
    for line in (tmp_path / "SHA256SUMS.txt").read_text().splitlines():
        expected, filename = line.split("  ", 1)
        assert (
            hashlib.sha256((tmp_path / filename).read_bytes()).hexdigest() == expected
        )
    assert "bom.cdx.json" in (tmp_path / "SHA256SUMS.txt").read_text()
    assert (
        "nopoach-screen/_internal/library.dll"
        in (tmp_path / "SHA256SUMS.txt").read_text()
    )


@pytest.mark.live
def test_built_app_serves_home_and_exits_cleanly(tmp_path):
    executable = Path(os.environ["NOPOACH_EXECUTABLE"]).resolve()
    assert executable.is_file(), "Build the one-folder app first"
    # The real launcher calls webbrowser.open. Capture its session URL without
    # opening an interactive browser or adding a test-only app entry point.
    capture = tmp_path / "capture_browser.py"
    address = tmp_path / "address.txt"
    capture.write_text(
        f"import pathlib, sys\npathlib.Path({str(address)!r}).write_text(sys.argv[1])\n"
    )
    env = dict(os.environ, BROWSER=f'"{sys.executable}" "{capture}" %s')
    log_path = tmp_path / "app.log"
    kwargs = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    with log_path.open("w+") as log:
        process = subprocess.Popen(
            [str(executable)], cwd=tmp_path, env=env, stdout=log, stderr=log, **kwargs
        )
        try:
            deadline = time.monotonic() + 90
            while not address.exists() and process.poll() is None:
                assert time.monotonic() < deadline, "App did not open its local session"
                time.sleep(0.1)
            assert address.exists(), log_path.read_text()
            url = address.read_text()
            assert url.startswith("http://127.0.0.1:8765/?token=")
            opener = urllib.request.build_opener(
                urllib.request.ProxyHandler({}),
                urllib.request.HTTPCookieProcessor(CookieJar()),
            )
            with opener.open(url, timeout=10) as response:
                assert response.status == 200
                assert b"Workspace folder" in response.read()
            for asset in (
                "app.css",
                "htmx.min.js",
                "fonts/SourceSerif4-Variable.woff2",
            ):
                with opener.open(f"http://127.0.0.1:8765/static/{asset}") as response:
                    assert response.status == 200
                    assert response.read()
            process.send_signal(
                signal.CTRL_BREAK_EVENT if sys.platform == "win32" else signal.SIGINT
            )
            assert process.wait(timeout=30) == 0, log_path.read_text()
            assert "Application shutdown complete" in log_path.read_text()
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=10)
