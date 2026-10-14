# Handoff: no-poach screen v1 built, PR #1 awaiting the denylist secret

## State

- **Built:** all 15 plan units from `docs/plans/2026-10-13-0935-feat-no-poach-screen-plan.md`, plus a simplification pass and nine code-review fixes, on branch `docs/requirements-plan`.
- **PR:** https://github.com/damienriehl/nopoach-screen/pull/1 (private repository; the code is written to be public-safe).
- **Verified locally:** 371 offline tests, 5 browser end-to-end tests, ruff, format, strict mypy, and a Linux PyInstaller build with a live smoke test and hash check.
- **Code review:** complete, verdict "Ready with fixes". All 9 confirmed findings are fixed. The PR body lists the unapplied findings and residual risks.

## Blocked on Damien

- **`NOPOACH_DENYLIST` Actions secret.** The repository-hygiene job fails closed without it, and the Linux and Windows test jobs (including the Windows build smoke) wait behind it. The terms are confidential: the collaborator, the agency and live targets. Only Damien can supply them. Once the secret exists, rerun CI. Merge after hygiene and both OS jobs pass.

## Open, non-blocking

- Cockpit ask `nopoach-screen-2026-10-13-1454-backtest-aggregator-data` decides whether aggregator data may validate the method. It gates only the real-data backtest. Run `cockpit-answer inspect` before re-asking.
- The collaborator picks the pilot market (R22).

## Follow-ups (not built in v1)

- A collector for Census CBSA centroids to fill employer coordinates for real data.
- Company headcount extraction from 10-K and Form 990 filings (coverage takes counts as inputs today).
- Review residual risks: duplicate positions when a collector runs again, a torn final provenance line blocking workspace open, and per-screen in-memory copies with no size guard.
- Record-availability dating for backtests.

## Lessons for the next ce-work run

- Never call a controller transition such as `integrate` as a probe. It acts, even with a no-op verification command.
- Running `uv lock` online adds new PyPI metadata to the shared uv cache. After that, offline installs pick newer versions that the wheelhouse doesn't have. Point offline installs at a dedicated `UV_CACHE_DIR`.
- When a worker copies files in from a backup, the workspace directory's mode can change. The controller requires mode 0700.
