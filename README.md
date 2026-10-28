# Labor market screen

A local screening tool for state antitrust lawyers. Choose an occupation,
geographic area and period, confirm competing employers, and review an
explainable ranking of employer pairs. The screen combines missing worker
flow, filed employee restrictions, and executive or board ties. A lead is a
reason to investigate; it does not establish an agreement or a legal violation.

Lawyers can inspect coverage, compare sources, examine a pair's evidence and
alternative explanations, and export a Word dossier through the local browser
interface. Analysts prepare lawful imports and resolve employer identities.
Agency IT reviews and runs the application on agency-controlled machines.

## Install for a lawyer

1. Ask IT to review the release's `IT-REVIEW.md`, `SHA256SUMS.txt`, CycloneDX
   bill of materials (`bom.cdx.json`) and GitHub build attestation.
2. Extract **the whole** `nopoach-screen.zip` into a user-owned folder. Keep
   `nopoach-screen.exe` beside `_internal`; moving only the executable breaks it.
   Python and administrator rights are not required for this Windows build.
3. Double-click `nopoach-screen.exe`. Keep its console open. The application
   opens a session in your default browser at `http://127.0.0.1:8765`.
4. Choose a workspace folder **outside** the extracted app or source tree, on
   encrypted, access-controlled agency storage. Open an existing workspace or
   import CSV/Parquet data, map columns, and declare its source and provenance.
5. Set the market and years, confirm competitors, and run the screen. Review
   sufficiency first; explicitly accept a partial screen if you choose to proceed
   with insufficient signals. Review ranked pairs, supporting records and
   explanations before exporting a Word dossier.
6. When work has finished, focus the application console and press Ctrl+C.
   Closing the browser tab alone leaves the local server running.

A blank workspace contains no collected data. The default launcher uses the
workspace's stored data; additional public collectors require analyst
configuration. Do not run two copies on port 8765. A browser address typed by
hand lacks the initial session token: restart the app to open a fresh session.

## Install and review for IT

See [the IT review note](packaging/IT-REVIEW.md) for hosts, storage, data classes,
hash allowlisting and shutdown. Releases contain a console application, local
HTML and assets, dependency notices, and the public method. No Windows service,
registry installation or privileged installer is required. The build is not
code-signed; allowlist only the reviewed release using its hashes and agency
policy. Review release provenance with `gh attestation verify nopoach-screen.zip
--repo OWNER/REPOSITORY` using the actual publishing repository.

Python 3.12 users can run from source with [uv](https://docs.astral.sh/uv/):

```sh
uv sync --locked --group dev
uv run --no-sync python -m nopoach.app.main
```

The repository uses a `src/` layout: set `PYTHONPATH` to the checkout's `src`
folder before that last command (`export PYTHONPATH="$PWD/src"` on Linux/macOS,
or `$env:PYTHONPATH = "$PWD/src"` in PowerShell). Keep the environment and
workspaces outside any published source archive.

## Lawful data and interpretation

Use free public records whose terms permit the use, and records the agency
lawfully obtains or imports. No account-based collection, profile scraping or
terms circumvention is included. Source catalog entries are recommendations,
not collection permission; prices and terms require review before access.
Restricted research microdata cannot supply enforcement leads, and restricted
employment-verification products are excluded from lead recommendations.

The source repository contains code, method and synthetic fixtures only. Never
commit collected records, workspaces, live target names, pilot results, agency
identity, credentials or a denylist. This tool has no telemetry. Local analysis
and exports stay on the agency machine; configured public-source requests are
the only intended outbound application traffic.

Read [the method](docs/method.md) before relying on scores, intervals or
rankings. Analysts should use the separate [historical validation and pilot
procedure](docs/validation.md) to predeclare review criteria and preserve private
evidence. Real historical-case validation and live pilot work require lawful
person-level data and remain outstanding. Synthetic tests do not establish
real-world accuracy. The code is [MIT licensed](LICENSE); see
[third-party notices and source terms](THIRD-PARTY.md).

## Build and contribute

```sh
uv pip install --python .venv -r pyproject.toml --group dev --group build
uv run --no-sync ruff check .
uv run --no-sync ruff format --check .
uv run --no-sync mypy src
uv run --no-sync pytest
uv run --no-sync python packaging/build.py --output /path/outside/checkout/release
```

Build Windows releases on Windows. PyInstaller produces a one-folder app;
`build.py` then creates a zip, CycloneDX 1.6 environment SBOM, license notices,
IT note and SHA-256 manifest covering every extracted file plus the zip and
SBOM. The SBOM includes build/development tools present in the build environment,
so it is broader than the frozen runtime. No collected data belongs in a build.

Enable the built-app socket smoke explicitly after building:

```sh
# On Windows set NOPOACH_EXECUTABLE to .../nopoach-screen/nopoach-screen.exe.
export NOPOACH_EXECUTABLE=/path/to/release/nopoach-screen/nopoach-screen
uv run --no-sync pytest -m live tests/packaging/test_smoke.py
```

The host owns socket and browser checks. Browser accessibility tests use
`uv run --no-sync pytest -m browser tests/app/test_e2e.py` and installed
Playwright Chromium. They are excluded from ordinary tests.

### Public-safe commits

The data-file guard always runs; a confidential-term denylist is optional.
Adopters who want to keep their own confidential terms out of a fork can create a private, nonempty UTF-8 file **outside** the repository with one term per line, then set `NOPOACH_DENYLIST_PATH` to that file.
Matching is case-insensitive in both Git paths and contents.
Do not put the terms in shell command lines, issue reports or logs.
Enable the local hook:

```sh
git config core.hooksPath packaging/hooks
python packaging/scan_repository.py
```

The hook scans all staged file versions (including partially staged content) and rejects workspace/export folders, generated dossiers, DuckDB, Parquet and provenance logs.
With no denylist, only term matching is skipped; a supplied path must name a readable, nonempty file outside the repository or the scan refuses.
It prints counts, never matching terms or paths.
Keep Python on the hook's PATH.
CI scans every reachable commit with `python packaging/scan_repository.py --history`.
The encrypted Actions secret `NOPOACH_DENYLIST` is optional; configure it with the same newline-separated terms if needed.
CI materializes a nonempty secret only in runner temporary storage and removes it after the scan.
Missing secrets, including unavailable secrets on fork PRs, skip term matching while the data-file guard still runs.
Never use `pull_request_target` to execute untrusted contribution code with this secret.
