"""Scan Git content without disclosing confidential denylist terms or paths."""

import argparse
import gzip
import hashlib
import io
import os
import re
import subprocess
import sys
import tarfile
import zipfile
import zlib
from pathlib import Path, PurePosixPath
from typing import BinaryIO

ROOT = Path(__file__).resolve().parents[1]
MAX_DEPTH = 5
MAX_BYTES = 32 * 1024 * 1024
MAX_MEMBERS = 1000
LFS_PREFIX = b"version https://git-lfs.github.com/spec/v1"
# Web assets are opaque to text matching. Data/document formats are not exempt.
BINARY_ASSETS = {
    ".woff",
    ".woff2",
    ".ttf",
    ".otf",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".ico",
}


def git(*args: str) -> bytes:
    return subprocess.run(
        ["git", *args], cwd=ROOT, check=True, capture_output=True
    ).stdout


def prohibited(path: str) -> bool:
    parts = PurePosixPath(path.lower()).parts
    return (
        any(part in {"workspace", "workspaces", "exports"} for part in parts)
        or path.lower().endswith((".duckdb", ".duckdb.wal", ".parquet"))
        or PurePosixPath(path.lower()).name == "provenance.jsonl"
        or (
            PurePosixPath(path.lower()).name.startswith("dossier-")
            and path.lower().endswith(".docx")
        )
    )


def is_tar(body: bytes) -> bool:
    if len(body) < 512:
        return False
    try:
        tarfile.TarInfo.frombuf(body[:512], "utf-8", "surrogateescape")
        return True
    except tarfile.HeaderError:
        return body[257:262] == b"ustar" or body[:1024] == b"\0" * 1024


class Inspection:
    """One shared expansion budget per Git blob, including nested containers."""

    def __init__(self, terms: list[str]) -> None:
        self.terms = terms
        self.remaining = MAX_BYTES
        self.members = 0

    def read(self, stream: BinaryIO) -> bytes:
        body = stream.read(self.remaining + 1)
        if len(body) > self.remaining:
            raise ValueError
        return body

    def member(self) -> None:
        self.members += 1
        if self.members > MAX_MEMBERS:
            raise ValueError

    def matches(self, path: str, body: bytes, depth: int = 0) -> bool:
        self.remaining -= len(body)
        if depth > MAX_DEPTH or self.remaining < 0:
            raise ValueError
        content = body.decode("utf-8", errors="replace").casefold()
        if prohibited(path) or any(
            term in path.casefold() or term in content for term in self.terms
        ):
            return True

        if body.startswith(LFS_PREFIX):
            pointer = re.fullmatch(
                re.escape(LFS_PREFIX)
                + rb"\noid sha256:([0-9a-f]{64})\nsize ([0-9]+)\n?",
                body,
            )
            if pointer is None:
                raise ValueError
            oid = pointer[1].decode("ascii")
            size = int(pointer[2])
            if size > self.remaining:
                raise ValueError
            # Read only the local object store; never fetch or invoke an LFS filter.
            store = Path(
                os.fsdecode(git("rev-parse", "--git-path", "lfs/objects")).strip()
            )
            if not store.is_absolute():
                store = ROOT / store
            with (store / oid[:2] / oid[2:4] / oid).open("rb") as stream:
                resolved = self.read(stream)
            if len(resolved) != size or hashlib.sha256(resolved).hexdigest() != oid:
                raise ValueError
            return self.matches(path, resolved, depth + 1)

        suffix = PurePosixPath(path.lower()).suffix
        if body.startswith(b"\x1f\x8b") or suffix in {".gz", ".tgz"}:
            with gzip.GzipFile(fileobj=io.BytesIO(body)) as stream:
                expanded = self.read(stream)
            name = path
            if suffix == ".tgz":
                name = path[:-4] + ".tar"
            elif suffix == ".gz":
                name = path[:-3]
            return self.matches(name, expanded, depth + 1)

        if (
            body.startswith((b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"))
            or suffix in {".zip", ".docx", ".xlsx", ".pptx", ".ods", ".odt"}
            or zipfile.is_zipfile(io.BytesIO(body))
        ):
            with zipfile.ZipFile(io.BytesIO(body)) as archive:
                for member in archive.infolist():
                    self.member()
                    if member.file_size > self.remaining:
                        raise ValueError
                    with archive.open(member) as stream:
                        expanded = self.read(stream)
                    if self.matches(member.filename, expanded, depth + 1):
                        return True
            return False

        if suffix == ".tar" or is_tar(body):
            with tarfile.open(fileobj=io.BytesIO(body), mode="r:") as archive:
                for entry in archive:
                    self.member()
                    if entry.isdir():
                        if self.matches(entry.name, b"", depth + 1):
                            return True
                        continue
                    if not entry.isfile() or entry.size > self.remaining:
                        raise ValueError
                    stream = archive.extractfile(entry)
                    if stream is None:
                        raise ValueError
                    with stream:
                        expanded = self.read(stream)
                    if len(expanded) != entry.size:
                        raise ValueError
                    if self.matches(entry.name, expanded, depth + 1):
                        return True
            return False

        if suffix not in BINARY_ASSETS:
            # Replacement decoding alone lets opaque data pass. Require readable
            # UTF-8 and reject embedded binary controls in data-capable files.
            text = body.decode("utf-8")
            if any(ord(char) < 32 and char not in "\t\n\r" for char in text):
                raise ValueError
        return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--history", action="store_true", help="scan all reachable commits"
    )
    args = parser.parse_args(argv)
    try:
        supplied = os.environ.get("NOPOACH_DENYLIST_PATH", "")
        if not supplied:
            raise ValueError
        denylist = Path(supplied).expanduser().resolve(strict=True)
        if denylist.is_relative_to(ROOT):
            raise ValueError
        terms = [
            term.strip().casefold()
            for term in denylist.read_text(encoding="utf-8").splitlines()
            if term.strip()
        ]
        if not terms:
            raise ValueError
    except (OSError, ValueError, UnicodeError):
        print(
            "Repository scan refused: external nonempty denylist required.",
            file=sys.stderr,
        )
        return 1

    violations = 0
    seen: set[tuple[str, bytes]] = set()
    try:
        revisions = (
            git("rev-list", "--all").decode().splitlines() if args.history else [""]
        )
        for revision in revisions:
            paths = (
                git("ls-tree", "-rz", "--name-only", revision)
                if revision
                else git("ls-files", "-z")
            )
            for raw_path in paths.split(b"\0"):
                if not raw_path:
                    continue
                path = os.fsdecode(raw_path)
                # Read the index, not the working copy: partial staging is honored.
                body = git("show", f"{revision}:{path}")
                key = (path, body)
                if key in seen:
                    continue
                seen.add(key)
                if Inspection(terms).matches(path, body):
                    violations += 1
    except (
        OSError,
        ValueError,
        EOFError,
        RuntimeError,
        NotImplementedError,
        tarfile.TarError,
        zipfile.BadZipFile,
        zlib.error,
        subprocess.CalledProcessError,
    ):
        print(
            "Repository scan refused: content unreadable or inspection limit exceeded.",
            file=sys.stderr,
        )
        return 1
    if violations:
        print(
            f"Repository scan rejected {violations} file version(s).", file=sys.stderr
        )
        return 1
    print("Repository scan passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
