---
session: "34ced6f5-52a4-4d3d-9d81-d2b04a5c7ac9"
written_at: "2026-10-14T19:06:18.937Z"
head: "c6b02cb4964fba2995f9a3e012aff9bfa6338c38"
branch: "docs/requirements-plan"
---
# Handoff: no-poach screen v1 built, PR #1 awaiting the denylist secret

## State

- **Built:** all 15 plan units from `docs/plans/2026-10-13-0935-feat-no-poach-screen-plan.md`, plus a simplification pass and nine code-review fixes, on branch `docs/requirements-plan`.
- **PR:** https://github.com/damienriehl/nopoach-screen/pull/1 (private repository; the code is written to be public-safe).
- **Verified locally:** 371 offline tests, 5 browser end-to-end tests, ruff, format, strict mypy, and a Linux PyInstaller build with a live smoke test and hash check.
- **Code review:** complete, verdict "Ready with fixes". All 9 confirmed findings are fixed. The PR body lists the unapplied findings and residual risks.

## Blocked on Damien

- **`NOPOACH_DENYLIST` Actions secret.** The repository-hygiene job fails closed without it, and the Linux and Windows test jobs (including the Windows build smoke) wait behind it. The terms are confidential: the collaborator and the agency. Only Damien can supply them, and he chose to run the paste-safe prompt command himself (ask `nopoach-screen-2026-10-14-1626-v1-release-questions`, qid `denylist-supply`). Once the secret exists, rerun CI. Merge after hygiene and both OS jobs pass.

## Decided 2026-10-14

- **Repository goes public after PR #1 merges** (qid `repo-visibility`). There is no live investigation; the tool is built for future ones. After the merge, flip the repository to public under the MIT license, after one more full-history scan with the denylist.
- **Aggregator data: validation only, disclosed** (ask `nopoach-screen-2026-10-13-1454-backtest-aggregator-data`). Aggregator data may be bought only for historical backtests, never for live leads, and the method note names its source. Any purchase is spend, so it needs Damien's separate approval once quotes come in.

## Open, non-blocking

- The collaborator picks the pilot market (R22).

## Follow-ups (not built in v1)

- A collector for Census CBSA centroids to fill employer coordinates for real data.
- Company headcount extraction from 10-K and Form 990 filings (coverage takes counts as inputs today).
- Review residual risks: duplicate positions when a collector runs again, a torn final provenance line blocking workspace open, and per-screen in-memory copies with no size guard.
- Record-availability dating for backtests.
- A guard that keeps aggregator-class (validation-only) data out of live screens, so the method-note commitment is enforced in code rather than by policy alone.

## Lessons for the next ce-work run

- Never call a controller transition such as `integrate` as a probe. It acts, even with a no-op verification command.
- Running `uv lock` online adds new PyPI metadata to the shared uv cache. After that, offline installs pick newer versions that the wheelhouse doesn't have. Point offline installs at a dedicated `UV_CACHE_DIR`.
- When a worker copies files in from a backup, the workspace directory's mode can change. The controller requires mode 0700.
