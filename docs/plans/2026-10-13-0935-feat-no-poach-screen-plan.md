---
title: No-Poach Agreement Screen - Plan
type: feat
date: 2026-10-13
topic: no-poach-screen
artifact_contract: ce-unified-plan/v1
product_contract_source: ce-brainstorm
execution: code
---

# No-Poach Agreement Screen - Plan

## Goal Capsule

- **Objective:** A state antitrust lawyer can pick a labor market and get a ranked, explainable list of employer pairs that may have a hidden agreement not to hire each other's workers. Each lead is strong enough to justify the agency's own investigative demands.
- **Product authority:** Damien Riehl, building with an antitrust attorney at a state enforcement agency who is the first user. The collaborator and the agency stay unnamed in this repository.
- **Open blockers:** None for planning. The live pilot (R22) needs the collaborator to choose a pilot market.

---

## Product Contract

### Summary

An open-source screening tool that a state agency runs on its own machines. A lawyer defines a labor market. The tool ranks every employer pair in it using three signals: missing worker flow, filed no-hire clauses, and executive or board ties. Each flagged pair opens into a dossier written for lawyers. The tool says plainly when public data is too thin to trust, and names the paid sources that would fix that.

### Problem Frame

No-poach agreements suppress wages and mobility for workers who never learn the agreements exist. The evidence lives in the companies' own email and HR files. Enforcers can compel those files, but only after they know where to look. Today that takes a tip from an insider, which is rare.

The agreements that have come to light share a visible footprint. In High-Tech Employee (Apple, Google, Intel, Adobe, Intuit, Pixar, Lucasfilm, about 2005-2009), firms that competed hard for engineers stopped recruiting each other's staff. The flow of people between them dropped while flow to every other competitor continued. Animation Workers, Knorr-Bremse/Wabtec (DOJ, 2018), Seaman v. Duke (Duke and UNC medical faculty), and United States v. Patel (Pratt & Whitney engineering suppliers) follow similar patterns. Some agreements are also written down: the Washington Attorney General's 2018-2020 franchise campaign won commitments from more than 200 chains by reading no-poach clauses in public franchise disclosure documents.

Low flow between two firms has innocent explanations too: non-competes, different pay, geography, different occupations, a firm that rarely hires laterally, or plain data gaps. A screen that ignores them buries a busy enforcer in false positives. A state agency also lacks the budget for six-figure workforce datasets, and it cannot type confidential target names into someone else's hosted service.

### Actors

- A1. Agency antitrust lawyer: defines markets, reviews rankings, and exports dossiers. Not an economist and not a command-line user.
- A2. Agency reviewer: a supervisor or staff economist who reads an exported dossier before an investigation opens.
- A3. Maintainer: Damien and contributors, who keep the open-source code and method current.
- A4. Public data sources: LinkedIn public profile data, SEC EDGAR, state franchise disclosure filings, licensing boards, H-1B/LCA records and press releases.

### Key Decisions

- **Enforcement agencies are the primary user.** The output is a screen that tells an agency where to aim its investigative demands. It is not proof of an agreement. Governs R11, R13. (session-settled: user-directed — chosen over the plaintiffs' antitrust bar, corporate compliance teams, and journalists or researchers: the first user is a state-agency antitrust attorney, so this tool is built for lawyers by lawyers)
- **Screen a market first, then drill into a pair.** The market screen finds leads nobody has yet. The pair dossier is what a lawyer forwards to a supervisor. Governs R1, R2, R8. (session-settled: user-directed — chosen over a named-pair tester alone: screening is where the new value is, and every flag needs a drill-down)
- **Use LinkedIn data plus every free public source, and only recommend paid data.** Governs R14, R16, R17. (session-settled: user-directed — chosen over licensed vendor data such as Revelio, People Data Labs or Coresignal: free data first, with spending decided case by case when the coverage report shows a gap)
  - **Conflict call-out:** in hiQ v. LinkedIn (final judgment 2022), the court found that scraping public profiles breached LinkedIn's user agreement. A lead built on data collected against a platform's terms invites a provenance attack if it ever reaches litigation. Planning must pick the lowest-risk way to read LinkedIn data and record its reasoning (see Outstanding Questions).
- **Open source, run by the agency.** Investigation targets never leave the agency, and other states' AGs can adopt the tool. The method is public. The only way to avoid tripping it is to actually hire from competitors. Governs R18, R19, R20. (session-settled: user-approved — chosen over a hosted app run by Damien and over a report-writing service: keeps confidential queries inside the agency)
- **Version 1 carries all three signals: missing flow, filed clauses, and executive and board ties.** Governs R3, R6, R7. (session-settled: user-directed — chosen over a flow screen with document evidence only: each signal catches agreements the others miss, including handshake deals between executives)
- **Build the signals in order: flow, then clauses, then ties.** The backtest (R21) depends only on the flow signal, so the flow signal lands first. All three ship in version 1.
- **Prove it with a backtest, then a live pilot.** Governs R21, R22. (session-settled: user-approved — chosen over a backtest alone or a live pilot alone: the backtest earns trust and the pilot earns relevance)
- **The tool produces leads, never conclusions.** Its wording must survive being read aloud by opposing counsel. Governs R11.
- **The repository holds code and method only.** Every repo may become public, so collected data, pilot results, live targets and the agency's identity stay out of git. Governs R20.

### Requirements

**Market screen**

- R1. A lawyer defines a labor market by occupation and geography (state or metro), optionally narrowed to a list of employers. The tool proposes the employers that compete for that talent.
- R2. The tool ranks every employer pair in the market by combined evidence from the three signals (R3, R6, R7), and shows which signals fired for each pair.
- R3. The missing-flow signal compares observed job-to-job moves between a pair, in both directions, with the moves expected from the two employers' size, occupational overlap and geography.
- R4. When a pair's flow dropped and stayed low, the tool reports the approximate break date and the flow before and after it.
- R5. Flow suppressed in only one direction is labeled as a possible unilateral no-hire policy. It is ranked separately from mutual suppression.
- R6. The filed-clause signal finds employee non-solicit and no-hire clauses binding both parties in public filings: SEC exhibits, franchise disclosure documents and merger agreements. It shows each clause's text, date, scope and duration.
- R7. The executive and board signal finds shared directors, executive moves between the pair, and other executive ties visible in SEC filings and public profiles.

**Pair dossier**

- R8. Each ranked pair opens into a dossier with expected versus observed flow over time, any break date, filed clauses, executive and board ties, innocent-explanation checks, and data coverage.
- R9. The dossier lists each innocent explanation the tool tested and the result. Candidates include non-compete prevalence, pay gaps, geographic separation, low lateral hiring at either firm, and occupational mismatch. It also names the explanations the available data could not test.
- R10. Every number in a dossier traces back to the underlying records: the people, filings and dates behind it.
- R11. All output describes leads for further inquiry and never states that an agreement exists.
- R12. A dossier exports as a standalone document that a lawyer can attach to an internal case-opening memo, written so a non-economist can follow it.

**Data and coverage**

- R13. The tool collects employment histories and related records from LinkedIn public profile data and all free public sources listed under A4.
- R14. Every screen reports coverage: the estimated share of each employer's workforce in the market that the data actually sees, and the overall share.
- R15. When coverage falls below a reliability threshold, the tool marks the affected rankings as unreliable rather than presenting low flow as suspicious.
- R16. When coverage is inadequate, the tool recommends specific paid sources, with rough costs and what each would add for this market.
- R17. Every record carries its provenance: source, collection date and collection method.

**Operation and distribution**

- R18. The tool runs entirely on agency-controlled machines. Nothing leaves the agency except requests to public data sources.
- R19. A lawyer can run a screen, read results and export dossiers through a point-and-click local interface, without using a command line.
- R20. The tool is MIT-licensed open source. The published repository contains no collected data, live target names, pilot results or agency identity.

**Validation**

- R21. A backtest mode reruns the screen on the markets of known no-poach cases, using only data from before each case became public. It reports where the known pairs ranked.
- R22. A live pilot runs one screen on a market the collaborator chooses in their state, and the collaborator reviews the top leads.

### Key Flows

- F1. Screen a market
  - **Trigger:** A1 wants to find leads in a labor market.
  - **Actors:** A1, A4
  - **Steps:** A1 defines occupation and geography. The tool proposes competing employers and A1 adjusts the list. The tool collects records, builds the three signals, and shows a ranked list of pairs with the coverage report.
  - **Outcome:** A ranked list of leads, or a statement that coverage is too thin, with paid-data recommendations.
  - **Covered by:** R1, R2, R3, R6, R7, R13, R14, R15, R16
- F2. Review and export a pair
  - **Trigger:** A1 opens a flagged pair.
  - **Actors:** A1, A2
  - **Steps:** A1 reads the dossier and drills into its underlying records, then exports it. A2 reads the export.
  - **Outcome:** A2 can judge whether to open an investigation without running the tool.
  - **Covered by:** R4, R5, R8, R9, R10, R11, R12
- F3. Backtest
  - **Trigger:** A3 or A1 wants to check the method.
  - **Actors:** A1, A3
  - **Steps:** The tool reconstructs each known case's market as of a date before disclosure, runs the screen, and reports the known pairs' ranks.
  - **Outcome:** Evidence of whether the screen finds real agreements.
  - **Covered by:** R21

### Acceptance Examples

- AE1. Mutual break
  - **Covers R3, R4.**
  - **Given** two hospital systems that each hired about 40 of the other's nurses a year through 2019, and about 2 a year after.
  - **When** A1 screens that metro's nursing market.
  - **Then** the pair ranks high, with a break date near 2020 and the before-and-after flow shown.
- AE2. One-way suppression
  - **Covers R5.**
  - **Given** Firm A hires steadily from Firm B but almost never loses staff to B.
  - **Then** the pair is labeled a possible unilateral policy and appears in the separate one-way list, not among mutual leads.
- AE3. Thin coverage
  - **Covers R14, R15, R16.**
  - **Given** public data sees 8% of the workforce at a poultry processor.
  - **Then** pairs involving that employer are marked unreliable, and the tool names paid sources that would raise coverage, with rough costs.
- AE4. Clause without a flow drop
  - **Covers R2, R6.**
  - **Given** an outsourcing contract with a two-year mutual non-solicit clause, and normal flow between the parties.
  - **Then** the pair appears with the clause shown and the filed-clause signal marked, ranked below pairs where several signals agree.
- AE5. Lead wording
  - **Covers R11, R12.**
  - **Given** the top-ranked pair in any screen.
  - **Then** its dossier says the pattern warrants inquiry and lists the innocent explanations, and nowhere states that the firms agreed to anything.

### Success Criteria

- The backtest ranks most known pairs near the top of their markets. The exact bar (for example, a majority in the top decile) is set during planning with the collaborator.
- In the live pilot, the collaborator judges at least one top-ranked lead worth further inquiry.
- An agency reviewer (A2) can follow an exported dossier without asking how the tool works.

### Scope Boundaries

**Deferred for later**

- Importing state unemployment-insurance wage records or productions from investigative demands.
- Pulling paid data sources directly. Version 1 only recommends them (R16).
- Sharing screens or methods between state agencies.
- A standalone Clayton Act §8 board-interlock screen beyond what R7 needs.

**Outside this product's identity**

- Hosted or software-as-a-service operation.
- Statements that an agreement exists, or anything framed as proof.
- Tools for plaintiffs' firms, corporate compliance teams or journalists, even though the open-source code allows them.

### Dependencies / Assumptions

- Public data reaches usable coverage for at least some white-collar and licensed-profession markets. R14 and R15 test this assumption on every run.
- Current public profiles contain enough job history to reconstruct pre-disclosure markets for the backtest. Survivorship bias is likely: people who have left the workforce or dropped their profiles are missing.
- The collaborator's agency can run local software on its machines. Agency IT approval may gate the pilot.

### Outstanding Questions

**Resolve Before Planning**

- None.

**Deferred to Planning**

- How to read LinkedIn public profile data at the lowest legal and provenance risk, given the hiQ outcome. Options include search-engine-indexed snippets and logged-out page views. Record the reasoning.
- How to collect public data without revealing the agency's interest in a particular pair to search providers or data hosts. For example, collect by market rather than by target pair.
- The baseline model for expected flow, and how break dates are detected.
- The reliability threshold for R15.
- How the three signals combine into one ranking.
- The backtest corpus: which known cases, which markets and dates, and where that private dataset lives outside the repo.
- The final product and repository name. `nopoach-screen` is a working name.

### Sources / Research

- In re High-Tech Employee Antitrust Litigation, N.D. Cal. No. 11-cv-02509 (Apple, Google, Intel, Adobe, Intuit, Pixar, Lucasfilm).
- In re Animation Workers Antitrust Litigation, N.D. Cal. No. 14-cv-04062.
- United States v. Knorr-Bremse AG and Westinghouse Air Brake Technologies Corp., D.D.C. (2018 consent decree).
- Seaman v. Duke University, M.D.N.C. No. 15-cv-462.
- United States v. Patel, D. Conn. (Pratt & Whitney engineering suppliers; acquittal 2023).
- Washington Attorney General franchise no-poach initiative, 2018-2020.
- hiQ Labs, Inc. v. LinkedIn Corp., N.D. Cal. No. 17-cv-03301 (final judgment 2022 on breach of user agreement).
- DOJ/FTC Antitrust Guidance for Human Resource Professionals (2016).
