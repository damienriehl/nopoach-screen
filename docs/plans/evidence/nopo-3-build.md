# nopo-3 build evidence

Work source: `docs/plans/2026-11-09-0819-feat-nopo-3-analytical-checks-plan.md`.
Branch: `drawdown/nopo-3`. Item kind: planned.

## Final verification

All three analytical checks and their local review paths are implemented. All
seven independently validated review findings are fixed; none remain unapplied.

| Check | Final result |
|---|---|
| `uv run --no-sync pytest -q` | 530 passed, 1 existing skip, 14 browser/live tests deselected; 128.46 seconds. |
| `uv run --no-sync pytest -m browser tests/app/test_e2e.py -q` | 6 passed; 131.95 seconds, after the review fixes. |
| `uv run --no-sync pytest -m network_off -q` | 25 passed, 520 deselected; 6.61 seconds. |
| Ruff and formatting | Both passed across the repository. |
| Strict mypy | Passed for 50 source files. |
| Repository history data-file scan | Passed. No confidential denylist was supplied or accessed; its term scan remains for the controller. |
| Existing test preservation | All 62 original test/helper definitions in changed baseline test files are unchanged. |
| Visual evidence | Four actual Playwright captures inspected and committed. The final run refreshed the market capture; the other three matched the inspected files. |

The browser suite exercised the following pages and actions without skips:

| Route or action | Result |
|---|---|
| Workspace and refusal pages (`/`) | Pass, including accessibility. |
| Ordinary and flow market review (`/market`) | Pass, including keyboard review and manual edits. |
| Analytical imports and retained selections (`/imports`) | Pass for all three local input types. |
| Group proposal and confirmation | Pass with the posted proposal identity. |
| Ranked screen, progress and partial acceptance | Pass, including tested and unavailable analytical states. |
| Pair dossier, move evidence and Word download | Pass with the frozen analytical context. |

The analytical browser workflow asserts zero page errors. Axe, 360-pixel layout
checks, escaped source text and loopback-only request assertions passed. Browser
fixtures closed their local servers and Chromium sessions. Windows CI and the
campaign publication/merge gate remain controller responsibilities.

## Reverified baseline

The build began from the committed plan at `7874c7a`, with application base
`ddc0807`. The baseline suite passed: 395 passed, 1 skipped, 13 deselected.
Ruff, formatting and strict mypy passed. The five existing Playwright tests also
passed against the real local application before the interface changed.

`uv sync --frozen --offline` could not supply a missing cached dependency.
`uv sync --frozen` installed the existing locked environment without changing
dependency declarations or the lockfile. No package or external service was added.

## Unit evidence

| Change | Observed test-first evidence | Verification and resulting behavior |
|---|---|---|
| Immutable analytical inputs | New input tests failed before the bundle module existed. | Immutable byte/manifest roundtrips, containment, corruption, interrupted publication and reopen coverage passed. |
| Independent flow population | Independent-population tests failed before its importer and market variant existed. A later scope-identity regression failed before its correction. | Source-scoped coverage, no bridging, ordinary compatibility, dated denominators and geodesic distances passed. Review scope now binds the declared record population. |
| Earlier-period flow grouping | Cluster tests failed before the module existed; wrong-population confirmation failed before its guard was added. | All 13 cluster tests passed, including likelihood/margins, self-dyad exclusion, planted blocks, temporal invariance, budgets, unsupported data and manual confirmation. |
| Public mobility context | Parser and consumer tests failed before their modules existed; overflow/version regressions failed before fixes. | All 36 focused public-context tests passed. Local parsing retains status flags and complete-year intersections; public context never changes expected pair counts or scores. |
| Reviewed individual restrictions | Import/extractor tests failed before implementation. | Extraction, applicability, complete reviewed populations, conflict handling and rank isolation passed. Existing missing-input wording assertions remain intact. |
| Frozen pipeline and Word integration | New pipeline arguments and Word analytical headings initially failed. | All 39 integration, export and existing backtest tests passed. Frozen snapshots survive later bundle corruption, retain names/declarations and display diagnostics even with no pairs. |
| Local interface and documentation | Route tests failed on the missing retained-version UI and selection endpoint. | All 48 route tests passed after the final prior-result regression. The new real-browser analytical workflow passed, including actual file imports, keyboard confirmation, retained manual edits, tested and unavailable contexts, escaped evidence, axe accessibility, narrow layouts and loopback-only requests. |

All fixtures use synthetic records in temporary external workspaces. Existing
tests and assertions are retained. Tests verify implementation behavior, not
historical detection accuracy or real-world validity.

After the analytical modules were integrated, the core suite excluding application tests
passed: 469 passed, 1 skipped, 8 deselected in 86.50 seconds. The offline marker
passed: 25 passed, 495 deselected in 6.05 seconds. The only emitted warning was
the existing Starlette/httpx deprecation.

The full default suite subsequently passed: 516 passed, 1 skipped, 14 deselected
in 111.88 seconds. A final route-only regression distinguished a collector
failure with a fresh scoring result from a scoring exception retaining an older
result; all 48 route tests then passed in 26.26 seconds. Global Ruff, formatting
and strict mypy passed (50 source files).

The complete real-browser suite passed after the final interface change:
`uv run --no-sync pytest -m browser tests/app/test_e2e.py -q` — 6 passed in
117.41 seconds. The five original workflows and their assertions remain intact.

Four actual Playwright screenshots were visually inspected for readable layout,
synthetic-only content, exclusions and unavailable/untestable reasons:

- `docs/plans/evidence/nopo-3-market.png`
- `docs/plans/evidence/nopo-3-imports.png`
- `docs/plans/evidence/nopo-3-screen.png`
- `docs/plans/evidence/nopo-3-dossier.png`

## Bounded cluster measurements

The six-employer synthetic fixtures used two groups, seed zero, four restarts,
20 search sweeps, 200 fitting sweeps, a 4,096-refit budget and tolerance `1e-6`.

| Fixture | Moves | Candidate refits | Elapsed seconds | Final residual | Restart agreement |
|---|---:|---:|---:|---:|---:|
| Dense planted blocks | 864 | 11 | 0.0174 | 4.61e-7 | 1.0 |
| Source/sink blocks | 728 | 11 | 0.0156 | 6.49e-7 | 1.0 |

These are small-fixture measurements, not a benchmark of the 64-employer limit.
The method reports a local optimum and explicit incomplete/unavailable results.

## Simplification and review

Two full-team simplification passes preserved the verification tests. The final
pass reused invariant likelihood terms and named numerical operations; 30
complete fitter outputs matched before and after, including likelihood histories
and residuals. The subsequent full suite passed: 517 passed, 1 skipped and
14 deselected in 118.76 seconds.

The eight-lens local review covered correctness, testing, maintainability,
security, performance, HTTP contracts, reliability and asynchronous UI behavior.
An independent validator reproduced seven findings. The review fixes:

- Keep healthy retained versions selectable beside explicit integrity warnings,
  while preserving strict bundle reads and strict inventory validation.
- Bind confirmation to the displayed proposal digest and retain the confirmed
  group when reopening the form.
- Keep ordinary-market validation errors in the ordinary form.
- Serialize run submission with the existing event-loop mutation handlers.
- Preserve a matched post-employment restriction when permissive language occurs
  only in an exception; genuine operative negation stays nonpositive.
- Add supported-graph boundary and intervening-job regressions without removing
  the original test.

Before the fixes, all five new route regressions failed, as did the new inventory,
extractor and reviewed-population regressions. The added graph assertions failed
under deliberate in-memory date-bound and bridging defects. After the fixes,
all 112 focused tests passed, the 25 offline checks passed, and Ruff, formatting
and strict mypy passed. An AST comparison confirmed that all 62 original test
and helper definitions in changed baseline test files remain unchanged.

No external cross-model review ran in this clone; checkout configuration
disables it. The controller's campaign reviews remain part of publication.

## Delivery boundaries

There are no schema migrations, data rewrites, new services or package releases.
The lane commits locally; the controller owns scanning, publication, PR creation
and the campaign merge gate. Windows CI and the packaged Windows smoke test must
run after controller publication; Linux verification does not establish Windows
compatibility. The chief campaign source was unavailable in this isolated clone;
the supplied mandate governed this run, and the controller retains reconciliation
of the chief Goal Capsule and cited campaign requirements.
