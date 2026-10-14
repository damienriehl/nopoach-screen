"""Build a local one-folder app, SBOM, review bundle and SHA-256 manifest."""

import argparse
import hashlib
import importlib.metadata
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def bundle(output: Path) -> None:
    app = output / "nopoach-screen"
    shutil.copy2(ROOT / "packaging" / "IT-REVIEW.md", output / "IT-REVIEW.md")
    for source, name in (
        (ROOT / "packaging" / "IT-REVIEW.md", "IT-REVIEW.md"),
        (ROOT / "README.md", "README.md"),
        (ROOT / "THIRD-PARTY.md", "THIRD-PARTY.md"),
        (ROOT / "docs" / "method.md", "method.md"),
        (ROOT / "LICENSE", "LICENSE"),
    ):
        shutil.copy2(source, app / name)
    shutil.make_archive(str(output / "nopoach-screen"), "zip", output, app.name)
    paths = sorted(path for path in app.rglob("*") if path.is_file())
    paths += [
        output / "nopoach-screen.zip",
        output / "bom.cdx.json",
        output / "IT-REVIEW.md",
    ]
    manifest = []
    for path in paths:
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        manifest.append(f"{digest}  {path.relative_to(output).as_posix()}\n")
    (output / "SHA256SUMS.txt").write_text("".join(manifest), encoding="utf-8")


def licenses(app: Path) -> None:
    """Keep installed license notices with the release, including transitive ones.

    The build environment inventory can include tools not embedded by PyInstaller;
    the SBOM identifies these as environment components rather than data sources.
    """
    rows = []
    for dist in sorted(
        importlib.metadata.distributions(), key=lambda d: d.name.lower()
    ):
        declared = dist.metadata.get("License-Expression") or dist.metadata.get(
            "License"
        )
        rows.append(f"{dist.name}=={dist.version}\t{declared or 'See notices/SBOM'}")
        for file in dist.files or ():
            if not any(
                marker in file.name.lower()
                for marker in ("license", "copying", "copyright", "notice")
            ):
                continue
            source = Path(dist.locate_file(file))
            if source.is_file():
                target = app / "licenses" / dist.name / str(file).replace("..", "_")
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
    (app / "DEPENDENCIES.txt").write_text("\n".join(rows) + "\n", encoding="utf-8")
    python_license = Path(sys.base_prefix) / "lib" / "python3.12" / "LICENSE.txt"
    if not python_license.exists():
        python_license = Path(sys.base_prefix) / "LICENSE.txt"
    if python_license.exists():
        target = app / "licenses" / "Python-LICENSE.txt"
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(python_license, target)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="nopoach-build-") as temporary:
        scratch = Path(temporary)
        subprocess.run(
            [
                sys.executable,
                "-m",
                "PyInstaller",
                "--noconfirm",
                "--clean",
                "--distpath",
                str(output),
                "--workpath",
                str(scratch / "build"),
                str(ROOT / "packaging" / "nopoach.spec"),
            ],
            cwd=ROOT,
            check=True,
            env=os.environ
            | {
                "PYTHONPATH": str(ROOT / "src"),
                "PYINSTALLER_CONFIG_DIR": str(scratch / "pyinstaller-cache"),
                "MPLCONFIGDIR": str(scratch / "matplotlib-cache"),
            },
        )
    subprocess.run(
        [
            sys.executable,
            "-m",
            "cyclonedx_py",
            "environment",
            "--of",
            "JSON",
            "--spec-version",
            "1.6",
            "--output-file",
            str(output / "bom.cdx.json"),
            "--pyproject",
            str(ROOT / "pyproject.toml"),
        ],
        cwd=ROOT,
        check=True,
    )
    licenses(output / "nopoach-screen")
    bundle(output)


if __name__ == "__main__":
    main()
