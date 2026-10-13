---
session: "f695c47b-562c-4f2a-a54c-63b1d9c07611"
written_at: "2026-10-13T14:37:48.137Z"
head: "6a9ed7c1dc66f9c804bf329a15d3dfbe45b82db3"
worktree: "."
branch: "docs/requirements-plan"
---
# Handoff: no-poach screen, plan complete, ce-work next

## State

- **Plan:** `docs/plans/2026-10-13-0935-feat-no-poach-screen-plan.md`. It is implementation-ready: 15 units, document review applied, committed on branch `docs/requirements-plan`.
- **Repo:** new and local only, with no remote yet. It is MIT-intended and must stay public-safe. Never name the collaborator, the agency or live targets.

## Decisions

- **Settled:** see the plan's Key Decisions. Data is lawful-only: free sources first, then paid sources shown with cost and provenance class. Answered in Cockpit ask `nopoach-screen-2026-10-13-1439-flow-data-source`.
- **Open, non-blocking:** Cockpit ask `nopoach-screen-2026-10-13-1454-backtest-aggregator-data` (aggregator data for method validation only?). It gates only the real-data backtest run. Run `cockpit-answer inspect` before re-asking.

## Review items not applied (awaiting approval)

- **Workspace purge:** delete one import, or a whole workspace, with a deletion record.
- **Backtest availability dates:** track when each record became available, so the backtest can exclude data that wasn't available before the disclosure date.

## FYI items (no decision)

- **Tamper-evident log:** hash-chain the provenance log.
- **Dense-web fitting:** leave-pair-out fitting for dense cartel webs.
- **Occupation picker:** pick occupations by job title.

## Next

1. ce-work on the plan. Codex workers implement and the orchestrator verifies by rerunning tests.
2. Collaborator picks the pilot market (R22).
