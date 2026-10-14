"""Synthetic history proofs; Git fast-import never writes an index."""

import gzip
import hashlib
import importlib.util
import io
import subprocess
import tarfile
import zipfile
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
TERM = "Synthetic Restricted Term"


def archive_bytes(kind, name, body):
    if kind == "gzip":
        return gzip.compress(body)
    output = io.BytesIO()
    if kind == "zip":
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(name, body)
    else:
        with tarfile.open(
            fileobj=output, mode="w:gz" if kind == "tgz" else "w"
        ) as archive:
            info = tarfile.TarInfo(name)
            info.size = len(body)
            archive.addfile(info, io.BytesIO(body))
    return output.getvalue()


@pytest.fixture
def history_scan(tmp_path, monkeypatch, capsys):
    spec = importlib.util.spec_from_file_location(
        "scan_repository", ROOT / "packaging/scan_repository.py"
    )
    scan = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(scan)
    repository = tmp_path / "synthetic.git"
    subprocess.run(
        ["git", "init", "--bare", str(repository)], check=True, capture_output=True
    )
    denylist = tmp_path / "external.txt"
    denylist.write_text(TERM + "\n", encoding="utf-8")
    monkeypatch.setenv("NOPOACH_DENYLIST_PATH", str(denylist))
    monkeypatch.setattr(scan, "ROOT", repository)

    def run(body, path="record.bin", expected=1):
        # The file is deleted at HEAD: only a real history scan can find it.
        history = (
            b"commit refs/heads/synthetic\n"
            b"committer Synthetic <synthetic@example.invalid> 1 +0000\n"
            b"data 4\nadd\n\nM 100644 inline "
            + path.encode()
            + b"\ndata "
            + str(len(body)).encode()
            + b"\n"
            + body
            + b"\n\n"
            b"commit refs/heads/synthetic\n"
            b"committer Synthetic <synthetic@example.invalid> 2 +0000\n"
            b"data 7\nremove\n\nD " + path.encode() + b"\n\ndone\n"
        )
        subprocess.run(
            ["git", "-C", str(repository), "fast-import", "--quiet"],
            input=history,
            check=True,
            capture_output=True,
        )
        result = scan.main(["--history"])
        logs = capsys.readouterr()
        assert TERM.casefold() not in (logs.out + logs.err).casefold()
        assert path not in logs.out + logs.err
        assert not (repository / "index").exists()
        assert result == expected

    return scan, repository, run


@pytest.mark.parametrize("kind", ["zip", "tar", "tgz"])
@pytest.mark.parametrize("name", ["workspace/rows.csv", "data.parquet"])
def test_history_rejects_archived_data_without_denylist(
    history_scan, monkeypatch, kind, name
):
    _, _, run = history_scan
    monkeypatch.delenv("NOPOACH_DENYLIST_PATH", raising=False)
    run(archive_bytes(kind, name, b"clean"))


@pytest.mark.parametrize("prohibited_data", [False, True])
def test_history_inspects_local_lfs_content_without_denylist(
    history_scan, monkeypatch, prohibited_data
):
    _, repository, run = history_scan
    monkeypatch.delenv("NOPOACH_DENYLIST_PATH", raising=False)
    body = (
        archive_bytes("zip", "data.parquet", b"clean") if prohibited_data else b"clean"
    )
    oid = hashlib.sha256(body).hexdigest()
    obj = repository / "lfs/objects" / oid[:2] / oid[2:4] / oid
    obj.parent.mkdir(parents=True)
    obj.write_bytes(body)
    pointer = (
        "version https://git-lfs.github.com/spec/v1\n"
        f"oid sha256:{oid}\nsize {len(body)}\n"
    ).encode()
    run(pointer, "rows.csv", expected=1 if prohibited_data else 0)


@pytest.mark.parametrize("kind", ["gzip", "zip", "tar", "tgz"])
def test_history_rejects_compressed_records_in_generic_paths(history_scan, kind):
    _, _, run = history_scan
    run(archive_bytes(kind, "rows.csv", TERM.upper().encode()))


def test_history_rejects_nested_archives(history_scan):
    _, _, run = history_scan
    body = archive_bytes("tar", "rows.csv", TERM.upper().encode())
    body = archive_bytes("gzip", "unused", body)
    run(archive_bytes("zip", "nested.bin", body))


@pytest.mark.parametrize("kind", ["gzip", "zip", "tar", "tgz"])
def test_clean_archives_pass(history_scan, kind):
    _, _, run = history_scan
    run(archive_bytes(kind, "rows.csv", b"synthetic,value\n"), expected=0)


@pytest.mark.parametrize("name", [f"{TERM}.csv", "workspace/rows.csv", "data.parquet"])
@pytest.mark.parametrize("kind", ["zip", "tar"])
def test_archive_member_names_are_scanned(history_scan, kind, name):
    _, _, run = history_scan
    run(archive_bytes(kind, name, b"clean"))


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("record.gz", b"broken"),
        ("record.zip", b"broken"),
        ("record.tar", b"broken"),
        ("record.tar.gz", b"broken"),
        ("record.bin", gzip.compress(b"clean")[:-3]),
        ("record.bin", b"PK\x03\x04broken"),
        ("rows.csv", b"\xff\x00opaque record"),
        ("record.bin", b"\xff\x00opaque record"),
    ],
)
def test_unreadable_archives_and_opaque_data_fail_closed(history_scan, path, body):
    _, _, run = history_scan
    run(body, path)


@pytest.mark.parametrize("limit", ["depth", "size", "members"])
def test_archive_inspection_limits_fail_closed(history_scan, monkeypatch, limit):
    scan, _, run = history_scan
    if limit == "depth":
        monkeypatch.setattr(scan, "MAX_DEPTH", 2, raising=False)
        body = b"clean"
        for _ in range(4):
            body = archive_bytes("zip", "nested.bin", body)
    elif limit == "size":
        monkeypatch.setattr(scan, "MAX_BYTES", 1024, raising=False)
        body = archive_bytes("gzip", "rows.csv", b"a" * 2048)
    else:
        monkeypatch.setattr(scan, "MAX_MEMBERS", 1, raising=False)
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w") as archive:
            archive.writestr("one.csv", b"clean")
            archive.writestr("two.csv", b"clean")
        body = output.getvalue()
    run(body)


def test_tar_link_cannot_stand_in_for_readable_member(history_scan):
    _, _, run = history_scan
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w") as archive:
        link = tarfile.TarInfo("rows.csv")
        link.type = tarfile.SYMTYPE
        link.linkname = "outside.csv"
        archive.addfile(link)
    run(output.getvalue())


@pytest.mark.parametrize(
    "state", ["missing", "denylisted", "mismatch", "clean", "nested"]
)
def test_lfs_requires_verified_readable_local_content(history_scan, state):
    _, repository, run = history_scan
    body = TERM.encode() if state == "denylisted" else b"clean"
    if state == "nested":
        body = archive_bytes("zip", "rows.csv", TERM.encode())
    oid = hashlib.sha256(body).hexdigest()
    pointer = (
        "version https://git-lfs.github.com/spec/v1\n"
        f"oid sha256:{oid}\nsize {len(body)}\n"
    ).encode()
    if state != "missing":
        obj = repository / "lfs/objects" / oid[:2] / oid[2:4] / oid
        obj.parent.mkdir(parents=True)
        obj.write_bytes(b"other" if state == "mismatch" else body)
    run(pointer, "rows.csv", expected=0 if state == "clean" else 1)


def test_malformed_lfs_pointer_fails_closed(history_scan):
    _, _, run = history_scan
    run(b"version https://git-lfs.github.com/spec/v1\ninvalid\n", "rows.csv")


@pytest.mark.parametrize("filename", ["ci.yml", "release.yml"])
def test_dependency_installs_require_committed_lockfile(filename):
    workflow = yaml.safe_load((ROOT / ".github/workflows" / filename).read_text())
    installs = [
        step["run"]
        for job in workflow["jobs"].values()
        for step in job["steps"]
        if step.get("name", "").startswith("Install")
    ]
    assert installs
    assert (ROOT / "uv.lock").is_file()
    for install in installs:
        assert install.strip() == "uv sync --locked --group dev --group build"


def test_release_checkout_does_not_persist_write_credentials():
    workflow = yaml.safe_load((ROOT / ".github/workflows/release.yml").read_text())
    checkouts = [
        step
        for job in workflow["jobs"].values()
        for step in job["steps"]
        if step.get("uses", "").startswith("actions/checkout@")
    ]
    assert checkouts
    for step in checkouts:
        assert step["with"].get("persist-credentials") is False


@pytest.mark.parametrize("path", ["record.GZ", "record.TGZ", "record.tar.GZ"])
def test_clean_archives_with_uppercase_extensions_pass(history_scan, path):
    _, _, run = history_scan
    body = b"clean"
    if path != "record.GZ":
        body = archive_bytes("tar", "rows.csv", body)
    run(gzip.compress(body), path, expected=0)


@pytest.mark.parametrize("kind", ["zip", "tar"])
def test_total_expanded_size_is_bounded_across_members(history_scan, monkeypatch, kind):
    scan, _, run = history_scan
    output = io.BytesIO()
    if kind == "zip":
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name in ("one.csv", "two.csv"):
                archive.writestr(name, b"a" * 1024)
    else:
        with tarfile.open(fileobj=output, mode="w") as archive:
            for name in ("one.csv", "two.csv"):
                info = tarfile.TarInfo(name)
                info.size = 1024
                archive.addfile(info, io.BytesIO(b"a" * 1024))
    body = output.getvalue()
    monkeypatch.setattr(scan, "MAX_BYTES", len(body) + 1500)
    run(body)


def test_staged_archive_is_inspected(history_scan, monkeypatch):
    scan, _, _ = history_scan
    results = iter([b"record.bin\0", archive_bytes("zip", "rows.csv", TERM.encode())])
    monkeypatch.setattr(scan, "git", lambda *args: next(results))
    assert scan.main([]) == 1
