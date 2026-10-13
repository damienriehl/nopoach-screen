---
session: "f695c47b-562c-4f2a-a54c-63b1d9c07611"
written_at: "2026-10-13T14:37:48.137Z"
head: "6a9ed7c1dc66f9c804bf329a15d3dfbe45b82db3"
worktree: "."
branch: "docs/requirements-plan"
---
# Handoff: no-poach screen, brainstorm done, ce-plan in progress

## State

- **Requirements plan:** `docs/plans/2026-10-13-0935-feat-no-poach-screen-plan.md` (ce-brainstorm Product Contract, committed on branch `docs/requirements-plan`).
- **Current step:** ce-plan is enriching that same file in place. External research (data sources, flow-gap methodology, local app stack) was dispatched; the implementation units are not yet written.
- **Repo:** new and local only, with no remote yet. It is MIT-intended and must stay public-safe. Never name the collaborator, the agency or live targets.

## Settled decisions (do not re-ask)

- **Primary user:** state AG antitrust lawyers.
- **Order:** screen a market first, then drill into a pair.
- **Data:** LinkedIn plus free public data; paid sources are only recommended.
- **Distribution:** open source, run by the agency.
- **Signals:** all three in version 1.
- **Validation:** backtest, then a live pilot.

## Pending decision

- **Cockpit ask `nopoach-screen-2026-10-13-1439-flow-data-source`:** research found no free, low-risk source of dated LinkedIn job histories. The options are bring-your-own data plus free sources (recommended), logged-out collection, or a paid pilot extract. Run `cockpit-answer inspect` before re-asking.
- **Research done:** data sources, methodology and app stack. Key picks: PPML gravity baseline with Poisson changepoint and FDR; FastAPI on localhost packaged with PyInstaller onedir; DuckDB with Splink; DOCX export.

## Next

1. Finish ce-plan: consolidate research, Phase 5.1.5 synthesis, write the units, run doc review, show the handoff menu.
2. Then ce-work: Codex workers implement and the orchestrator verifies.

## Open, not blocking

- **Pilot market:** the collaborator chooses it (R22).
- **Repo and product name:** `nopoach-screen` is a working name.
