"""Scan Git content without disclosing confidential denylist terms or paths."""

import argparse
import os
import subprocess
import sys
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]


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
                content = body.decode("utf-8", errors="replace").casefold()
                if prohibited(path) or any(
                    term in path.casefold() or term in content for term in terms
                ):
                    violations += 1
    except (OSError, ValueError, subprocess.CalledProcessError):
        print(
            "Repository scan refused: Git content could not be read.", file=sys.stderr
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
