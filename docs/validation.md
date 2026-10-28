# Historical validation and pilot procedure

This procedure prepares two different activities: a retrospective known-case
validation and a collaborator-led live pilot. Neither has yet occurred. Keep all
person-level records, completed forms, workspaces, reports, and review notes in
access-controlled agency storage outside this repository.

## Before a historical run

Obtain lawful job histories for every selected party **and comparison employer**,
occupation and location mappings, coordinates or an approved geographic
crosswalk, annual workforce denominators, and receipts for every input. For each
source, record its vendor, dataset and version in the import source label. If an
older label omits any of those details, retain the label and add them to the
private intake record; do not infer them from filenames.

Employment and filing dates describe events. Information availability describes
when the record could have been known. Acquisition time describes when this
workspace received it. They are not interchangeable. Independently document
that each input was available before public disclosure. The software filters by
event date only, so even a supported historical snapshot retains that software
limitation. Data from an aggregator with unclear collection may be used for
validation only, never for a live lead.

Complete these fields before inspecting results:

- Case catalog ID(s): ____
- Input vendor / dataset / version / provenance class: ____
- Lawful-use basis and custodian: ____
- Pre-disclosure availability evidence and date: ____
- Occupation codes, geography/crosswalk, years and comparison employers: ____
- Workforce-denominator source and years: ____
- Known-pair resolved employer IDs, if aliases are ambiguous: ____
- Success threshold (for example, required rank or percentile): ____
- Ranking list and denominator (mutual, one-way, or both separately): ____
- Treatment of partial screens and insufficient cases: ____
- Cases labeled from allegations only, rather than adjudicated facts: ____

The collaborator sets the success rule and denominator before the first
empirical run. Do not change them after viewing results. An insufficient case is
not a success, and mutual and one-way rankings remain separate lists.

## Run the existing analyst API

The catalog supplies public case facts, not markets. Define each market
explicitly and run in an external agency workspace. This invented example shows
the API shape; replace every placeholder privately:

```python
from nopoach.backtest.run import load_cases, run_backtest
from nopoach.market.define import Geography, Market
from nopoach.store.workspace import Workspace

cases = load_cases()
market = Market(
    frozenset({"00-0000"}),
    Geography("metro", "00000"),
    2000,
    2010,
)
with Workspace.open("/agency-controlled/path/validation-workspace") as workspace:
    report = run_backtest(
        workspace,
        {"high-tech": market},
        cases=cases,
        party_ids={"Ambiguous public party": "reviewed-employer-id"},
    )
    print(report.json_path, report.markdown_path)
```

Omit `party_ids` when conservative name resolution is unambiguous. Review both
reports. Their input-source section covers the whole stored screen reference set,
including comparison employers, and may overinclude employer-associated records;
it is not exact causal lineage. Confirm receipts, missing or unclassified sources,
synthetic warnings, sufficiency, partial status, list-specific rank and the
event-date limitation. Public case citations are separate from input sources.
The repository's synthetic SEC example is a software rehearsal, not a real
High-Tech validation.

Record privately: run/build ID ____; report paths and hashes ____; selected
market ____; receipt set ____; partial-screen decision ____; rank and denominator
____; insufficient cases ____; predeclared rule met (yes/no) ____; reviewer and
date ____; limitations and follow-up ____.

## Run a separate live pilot

1. Ask the collaborator to select an occupation and a market in their state,
   the period and comparison employers. Record that choice before screening.
2. Create an independent pilot workspace. Confirm every input is eligible for
   live enforcement use; do not copy validation-only aggregator data. The
   application does not enforce this separation or decide legal eligibility.
3. Import histories and denominators, map locations and occupations, resolve
   identities, and confirm competitors in the application.
4. Review signal and pair coverage first. Record whether the collaborator accepts
   an explicitly partial screen; acceptance does not make a signal sufficient.
5. Review the ranked mutual and one-way leads, their cited records and innocent
   explanations. Export and review a Word dossier only for a selected lead.
6. Record favorable and unfavorable outcomes. Pilot success requires the
   collaborator to judge at least one top-ranked lead worth further inquiry; a
   rank never establishes an agreement or violation.

Blank pilot record: collaborator-selected market ____; selection date ____;
eligibility reviewer/basis ____; workspace/build ID ____; receipt set ____;
coverage and partial decision ____; leads reviewed ____; dossier reviewed ____;
at least one worth inquiry (yes/no) ____; reasons/limitations ____; reviewer and
date ____.

No request is sent by this repository. A later authorized operator can ask:

> Please identify the pilot occupation, state or metro, years and comparison
> employers. Before the first historical run, please agree on its success
> threshold and how to count partial or insufficient results. Please identify
> the agency channel for lawful historical and pilot data, including
> vendor/dataset provenance and historical availability evidence. After the
> pilot, please review the top leads and record whether any merits further
> inquiry.
