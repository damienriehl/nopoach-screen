"""Offline, workspace-backed whole screens shared by the app and backtest.

Cutoffs use event dates, never collection timestamps. Future starts/filings are
excluded; future position ends are censored to unknown, preserving an already
started spell without using its future departure. Future DOL decisions/starts
are excluded; planned ends are censored to the cutoff. Spanning ties retain
unknown ends. Clause extraction and tie scoring run on an ephemeral filtered copy,
leaving facts intact.

Stored bulk feeds have no completeness calendars: clause/tie sufficiency remains
unknown rather than treating an observed document as a complete collection.
Accepting a partial screen records that choice; it does not change reliability.
Centroids use a common local equirectangular projection in kilometres for flow scoring
and explanations.
This is a metro-scale distance approximation, not a geodesic measurement.
"""

import json
from collections import defaultdict
from dataclasses import asdict, dataclass, replace
from datetime import UTC, date, datetime
from itertools import combinations
from math import cos, radians
from typing import Any
from uuid import uuid4

from nopoach.catalog.recommend import Recommendation, recommend_sources
from nopoach.collect.dol import DolRecord, load_dol_records
from nopoach.market.coverage import (
    CoverageReport,
    MarketWorker,
    coverage_report,
    dol_in_period,
)
from nopoach.market.define import Employer, Geography, Market, soc_code
from nopoach.market.sufficiency import SignalSufficiency, Verdict, signal_sufficiency
from nopoach.rank.aggregate import RankedPair, rank_pairs
from nopoach.rank.explanations import EmployerExplanationData
from nopoach.signals.clauses import (
    Clause,
    ClauseDocument,
    ClauseSignal,
    extract_clauses,
)
from nopoach.signals.clauses import (
    score_pairs as score_clauses,
)
from nopoach.signals.flow import FlowEmployer, ResolvedPosition, screen_flow
from nopoach.signals.ties import PairTies, board, executive
from nopoach.signals.ties import positions as tie_positions
from nopoach.signals.ties import score_pair as score_ties
from nopoach.store import connect
from nopoach.store.workspace import Workspace


@dataclass(frozen=True)
class PairReferences:
    employer_a_id: str
    employer_b_id: str
    position_ids: tuple[str, ...]
    clause_ids: tuple[str, ...]
    filing_ids: tuple[str, ...]
    tie_ids: tuple[str, ...]
    dol_record_ids: tuple[str, ...]
    workforce_input_ids: tuple[str, ...]
    provenance_ids: tuple[str, ...]


@dataclass(frozen=True)
class UnreliablePair:
    pair: RankedPair
    reason: str


@dataclass(frozen=True)
class ScreenResult:
    screen_id: str
    market: Market
    as_of: date | None
    mutual: tuple[RankedPair, ...]
    one_way: tuple[RankedPair, ...]
    unreliable: tuple[UnreliablePair, ...]
    sufficiency: SignalSufficiency
    recommendations: tuple[Recommendation, ...]
    partial_screen: bool
    accept_partial: bool
    coverage: CoverageReport
    references: tuple[PairReferences, ...]
    method: str
    explanation_method: str


def _filtered_workspace(workspace: Workspace, cutoff: date | None) -> Workspace:
    """Copy facts into memory before filtering for clause and tie scoring."""
    connection = connect()
    try:
        tables = workspace.table_names()
        for table in sorted(tables):
            # Quote workspace-controlled SQL identifiers, including resolver tables.
            name = '"' + table.replace('"', '""') + '"'
            arrow = workspace.connection.execute(
                f"SELECT * FROM {name}"
            ).to_arrow_table()
            connection.register("screen_copy", arrow)
            connection.execute(f"CREATE TABLE {name} AS SELECT * FROM screen_copy")
            connection.unregister("screen_copy")
        # Clause extraction lazily creates clause_details with a reference to this key.
        connection.execute("ALTER TABLE clauses ADD PRIMARY KEY (id)")
        if cutoff is not None:
            future_ends = {
                r[0]
                for r in connection.execute(
                    "SELECT id FROM positions WHERE end_date > ?", [cutoff]
                ).fetchall()
            }
            connection.execute("DELETE FROM positions WHERE start_date > ?", [cutoff])
            connection.execute(
                "UPDATE positions SET end_date = NULL, end_precision = 'unknown' "
                "WHERE end_date > ?",
                [cutoff],
            )
            connection.execute("DELETE FROM filings WHERE filed_on > ?", [cutoff])
            connection.execute(
                "DELETE FROM clauses WHERE filing_id NOT IN (SELECT id FROM filings)"
            )
            connection.execute("DELETE FROM ties WHERE start_date > ?", [cutoff])
            connection.execute(
                "UPDATE ties SET end_date = NULL WHERE end_date > ?", [cutoff]
            )
            connection.execute("DELETE FROM workforce_inputs WHERE as_of > ?", [cutoff])
            connection.execute(
                "DELETE FROM dol_records WHERE decision_date > ? "
                "OR employment_begin_date > ?",
                [cutoff, cutoff],
            )
            # Scoring loads dates from details_json, not the indexed SQL columns.
            for identifier, encoded in connection.execute(
                "SELECT id, details_json FROM dol_records "
                "WHERE employment_end_date > ?",
                [cutoff],
            ).fetchall():
                payload = json.loads(encoded)
                payload["record"]["employment_end_date"] = cutoff.isoformat()
                connection.execute(
                    "UPDATE dol_records SET employment_end_date = ?, "
                    "details_json = ? WHERE id = ?",
                    [cutoff, json.dumps(payload), identifier],
                )
            if "clause_details" in tables:
                connection.execute(
                    "DELETE FROM clause_details WHERE clause_id NOT IN "
                    "(SELECT id FROM clauses)"
                )
            if "imported_rows" in tables:
                deleted = []
                updated = []
                for import_id, number, raw, position_id in connection.execute(
                    "SELECT import_id, row_number, raw_json, position_id "
                    "FROM imported_rows"
                ).fetchall():
                    data = json.loads(raw)
                    if (
                        data.get("filed_on")
                        and date.fromisoformat(data["filed_on"]) > cutoff
                    ):
                        deleted.append((import_id, number))
                    elif position_id in future_ends:
                        # Use normalized import date bounds for every precision;
                        # raw months/years can include days after the cutoff.
                        data["end_date"] = ""
                        data["end_precision"] = "unknown"
                        updated.append((json.dumps(data), import_id, number))
                if deleted:
                    connection.executemany(
                        "DELETE FROM positions WHERE id IN (SELECT position_id "
                        "FROM imported_rows WHERE import_id = ? AND row_number = ?)",
                        deleted,
                    )
                    connection.executemany(
                        "DELETE FROM imported_rows WHERE import_id = ? "
                        "AND row_number = ?",
                        deleted,
                    )
                if updated:
                    connection.executemany(
                        "UPDATE imported_rows SET raw_json = ? "
                        "WHERE import_id = ? AND row_number = ?",
                        updated,
                    )
                connection.execute(
                    "DELETE FROM imported_rows WHERE position_id IS NOT NULL "
                    "AND position_id NOT IN (SELECT id FROM positions)"
                )
        connection.execute(
            "DELETE FROM position_worksites WHERE position_id NOT IN "
            "(SELECT id FROM positions)"
        )
        return replace(workspace, connection=connection)
    except BaseException:
        connection.close()
        raise


def _resolved_positions(workspace: Workspace) -> tuple[ResolvedPosition, ...]:
    # Only CBSA is persisted. A state crosswalk is not inferred from city/name.
    geography = {
        identifier: Geography("metro", cbsa)
        for identifier, cbsa in workspace.connection.execute(
            "SELECT position_id, cbsa FROM position_worksites"
        ).fetchall()
    }
    return ResolvedPosition.from_workspace(workspace, geography_by_position=geography)


def _workers(
    positions: tuple[ResolvedPosition, ...], market: Market, end: date
) -> tuple[MarketWorker, ...]:
    return tuple(
        MarketWorker(p.person_id, p.employer_id, p.occupation_code, p.geography, year)
        for p in positions
        if p.geography is not None and p.start_date is not None
        for year in range(market.start_year, market.end_year + 1)
        if p.start_date <= min(date(year, 12, 31), end)
        and (p.end_date is None or p.end_date >= date(year, 1, 1))
    )


def _explanation_inputs(
    workspace: Workspace,
    positions: tuple[ResolvedPosition, ...],
    employers: tuple[Employer, ...],
    market: Market,
    end: date,
) -> tuple[dict[str, EmployerExplanationData], dict[str, tuple[float, float]]]:
    start = date(market.start_year, 1, 1)
    receipts: dict[str, set[str]] = defaultdict(set)
    sites: dict[str, set[tuple[float, float]]] = defaultdict(set)
    market_sites: dict[str, set[tuple[float, float]]] = defaultdict(set)
    metro_sites: dict[str, set[tuple[float, float]]] = defaultdict(set)
    centroids: dict[str, tuple[float, float]] = {}
    rows = workspace.connection.execute(
        "SELECT p.employer_id, w.cbsa, w.latitude, w.longitude, w.provenance_id "
        "FROM position_worksites w JOIN positions p ON p.id = w.position_id "
        "WHERE (p.start_date IS NULL OR p.start_date <= ?) "
        "AND (p.end_date IS NULL OR p.end_date >= ?)",
        [end, start],
    ).fetchall()
    latitude = sum(r[2] for r in rows if r[2] is not None) / max(
        1, sum(r[2] is not None for r in rows)
    )
    longitude = sum(r[3] for r in rows if r[3] is not None) / max(
        1, sum(r[3] is not None for r in rows)
    )
    for employer, cbsa, lat, lon, provenance in rows:
        if lat is not None and lon is not None:
            point = (
                6371 * radians(lon - longitude) * cos(radians(latitude)),
                6371 * radians(lat - latitude),
            )
            centroids[cbsa] = point
            metro_sites[cbsa].add(point)
            sites[employer].add(point)
            # Position worksites have CBSA only; do not infer a state crosswalk.
            if market.geography == Geography("metro", cbsa):
                market_sites[employer].add(point)
            receipts[employer].add(provenance)
    gravity_centroids = {
        cbsa: (
            sum(p[0] for p in points) / len(points),
            sum(p[1] for p in points) / len(points),
        )
        for cbsa, points in metro_sites.items()
    }
    wages: dict[str, list[DolRecord]] = defaultdict(list)
    for row in load_dol_records(workspace):
        if not dol_in_period(row, start, end):
            continue
        if row.program == "LCA":
            geo = (
                row.worksite_state
                if market.geography.kind == "state"
                else row.worksite_metro
            )
            if (
                soc_code(row.soc_code) in market.occupation_codes
                and geo == market.geography.code
            ):
                wages[row.employer_id].append(row)
            if row.worksite_metro in centroids:
                sites[row.employer_id].add(centroids[row.worksite_metro])
            if geo == market.geography.code and row.worksite_metro in gravity_centroids:
                market_sites[row.employer_id].add(gravity_centroids[row.worksite_metro])
            receipts[row.employer_id].add(row.provenance.id)
    occupation_people: dict[str, dict[str, set[str]]] = defaultdict(
        lambda: defaultdict(set)
    )
    spells: dict[tuple[str, str], list[tuple[date, date]]] = defaultdict(list)
    histories: dict[str, list[ResolvedPosition]] = defaultdict(list)
    for p in positions:
        histories[p.person_id].append(p)
        if (
            p.start_date is None
            or p.start_date > end
            or (p.end_date and p.end_date < start)
        ):
            continue
        receipts[p.employer_id].add(p.provenance_id)
        if p.occupation_code:
            occupation_people[p.employer_id][soc_code(p.occupation_code)].add(
                p.person_id
            )
        spells[p.employer_id, p.person_id].append(
            (max(start, p.start_date), min(end, p.end_date or end))
        )
    exposure: dict[str, float] = defaultdict(float)
    for (employer, _), intervals in spells.items():
        merged: list[tuple[date, date]] = []
        for first, last in sorted(intervals):
            if merged and first <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(last, merged[-1][1]))
            else:
                merged.append((first, last))
        exposure[employer] += (
            sum((last - first).days + 1 for first, last in merged) / 365.25
        )
    hires: dict[str, set[tuple[str, date]]] = defaultdict(set)
    for person, history in histories.items():
        history.sort(key=lambda p: (p.start_date or date.min, p.id))
        for i, p in enumerate(history):
            if (
                p.start_date is not None
                and start <= p.start_date <= end
                and (i == 0 or history[i - 1].employer_id != p.employer_id)
            ):
                hires[p.employer_id].add((person, p.start_date))
    result = {}
    for employer in employers:
        counts = occupation_people[employer.id]
        total = sum(len(people) for people in counts.values())
        result[employer.id] = EmployerExplanationData(
            tuple(sorted(sites[employer.id])),
            tuple(wages[employer.id]),
            {code: len(people) / total for code, people in counts.items()}
            if total
            else None,
            len(hires[employer.id]) / exposure[employer.id]
            if exposure[employer.id]
            else None,
            tuple(sorted(receipts[employer.id])),
        )
    locations = {
        employer: (
            sum(p[0] for p in points) / len(points),
            sum(p[1] for p in points) / len(points),
        )
        for employer, points in market_sites.items()
    }
    return result, locations


def _clause_signals(
    workspace: Workspace, employers: tuple[Employer, ...], start: date, end: date
) -> tuple[ClauseSignal, ...]:
    receipts = {r.id: r for r in workspace.provenance.records()}
    structured = (
        {
            r[0]
            for r in workspace.connection.execute(
                "SELECT clause_id FROM clause_details"
            ).fetchall()
        }
        if "clause_details" in workspace.table_names()
        else set()
    )
    # Extract unstructured imported clauses from their persisted text, using
    # the same clause rules and preserving their record IDs.
    employers_by_id = {employer.id: employer for employer in employers}
    extracted: list[Clause] = []
    for (
        identifier,
        text,
        location,
        filing,
        filed,
        form,
        issuer,
        provenance,
    ) in workspace.connection.execute(
        "SELECT c.id, c.text, c.location, f.id, f.filed_on, f.form_type, "
        "f.employer_id, c.provenance_id FROM clauses c "
        "JOIN filings f ON f.id = c.filing_id ORDER BY c.id"
    ).fetchall():
        if identifier in structured or not start <= filed <= end:
            continue
        doc = ClauseDocument(
            text,
            receipts[provenance],
            filed,
            form,
            employers_by_id.get(issuer, Employer(issuer, "")),
            location or filing,
        )
        extracted.extend(
            replace(c, id=identifier) for c in extract_clauses(doc, employers)
        )
    result = []
    for signal in score_clauses(
        workspace, tuple((a.id, b.id) for a, b in combinations(employers, 2))
    ):
        clauses = tuple(c for c in signal.clauses if start <= c.filed_on <= end)
        clauses += tuple(
            c
            for c in extracted
            if (signal.employer_a_id, signal.employer_b_id) in c.pairs
        )
        result.append(replace(signal, clauses=clauses))
    return tuple(result)


def _tie_signals(
    workspace: Workspace, employers: tuple[Employer, ...], start: date, end: date
) -> tuple[PairTies, ...]:
    # Tie scoring only connects leadership people, but uses their entire employment
    # history for former-coworker evidence. Use its role predicates exactly;
    # retain every spell for eligible people, including ordinary former jobs.
    positions = tie_positions(workspace)
    people = sorted({p.person_id for p in positions if board(p) or executive(p)})
    eligible_people = set(people)
    eligible_positions = [p for p in positions if p.person_id in eligible_people]
    connection = workspace.connection
    connection.execute("ALTER TABLE positions RENAME TO screen_all_positions")
    try:
        connection.execute(
            "CREATE TABLE positions AS SELECT * FROM screen_all_positions "
            "WHERE person_id IN (SELECT unnest(?))",
            [people],
        )
        return tuple(
            score_ties(
                workspace,
                a.id,
                b.id,
                start_date=start,
                end_date=end,
                precomputed_positions=eligible_positions,
            )
            for a, b in combinations(employers, 2)
        )
    finally:
        connection.execute("DROP TABLE IF EXISTS positions")
        connection.execute("ALTER TABLE screen_all_positions RENAME TO positions")


def _references(workspace: Workspace, pair: RankedPair) -> PairReferences:
    employers = [pair.employer_a_id, pair.employer_b_id]
    positions = {
        r[0]: r[1]
        for r in workspace.connection.execute(
            "SELECT id, provenance_id FROM positions WHERE employer_id IN (?, ?)",
            employers,
        ).fetchall()
    }
    if pair.ties:
        positions.update(
            (p.id, p.provenance_id) for tie in pair.ties.ties for p in tie.positions
        )
    clause_ids = (
        tuple(sorted({c.id for c in pair.clauses.clauses})) if pair.clauses else ()
    )
    filing_ids = tuple(
        sorted(
            {
                r[0]
                for r in workspace.connection.execute(
                    "SELECT filing_id FROM clauses WHERE id IN (SELECT unnest(?))",
                    [list(clause_ids)],
                ).fetchall()
            }
        )
    )
    ties = workspace.connection.execute(
        "SELECT id, provenance_id FROM ties WHERE "
        "(employer_a_id = ? AND employer_b_id = ?) OR "
        "(employer_a_id = ? AND employer_b_id = ?)",
        employers + employers[::-1],
    ).fetchall()
    dol = workspace.connection.execute(
        "SELECT id, provenance_id FROM dol_records WHERE employer_id IN (?, ?)",
        employers,
    ).fetchall()
    workforce = workspace.connection.execute(
        "SELECT id, provenance_id FROM workforce_inputs WHERE employer_id IN (?, ?)",
        employers,
    ).fetchall()
    sources = set(positions.values()) | {r[1] for r in ties + dol + workforce}
    if pair.clauses:
        sources.update(c.provenance.id for c in pair.clauses.clauses)
    for check in pair.explanations:
        sources.update(check.provenance_ids)
    return PairReferences(
        pair.employer_a_id,
        pair.employer_b_id,
        tuple(sorted(positions)),
        clause_ids,
        filing_ids,
        tuple(sorted(r[0] for r in ties)),
        tuple(sorted(r[0] for r in dol)),
        tuple(sorted(r[0] for r in workforce)),
        tuple(sorted(sources)),
    )


def run_screen(
    workspace: Workspace,
    market: Market,
    *,
    as_of: date | None = None,
    accept_partial: bool = False,
) -> ScreenResult:
    """Run all available signals without collection and persist inputs/summary."""
    end = min(date(market.end_year, 12, 31), as_of or date.max)
    start = date(market.start_year, 1, 1)
    if end < start:
        raise ValueError("as_of precedes the market window")
    effective_market = replace(market, end_year=end.year)
    view = _filtered_workspace(workspace, as_of)
    try:
        employers = tuple(
            Employer(*r)
            for r in view.connection.execute(
                "SELECT id, name FROM employers ORDER BY id"
            ).fetchall()
            if r[0] not in market.removed_employer_ids
            and (
                market.employer_ids is None
                or r[0] in market.employer_ids
                or r[0] in market.added_employer_ids
            )
        )
        effective_market = replace(
            effective_market, employer_ids=frozenset(e.id for e in employers)
        )
        positions = _resolved_positions(view)
        report = coverage_report(
            effective_market,
            employers,
            workers=_workers(positions, effective_market, end),
            workspace=view,
        )
        explanation_data, flow_locations = _explanation_inputs(
            view, positions, employers, effective_market, end
        )
        flow_employers = tuple(
            FlowEmployer(
                e.id,
                flow_locations[e.id],
                explanation_data[e.id].occupation_shares or {},
            )
            for e in employers
            if e.id in flow_locations
        )
        flow = screen_flow(effective_market, positions, flow_employers, report)
        effective = {
            (d.source_id, d.destination_id): sum(y.expected for y in d.years)
            for p in flow.pairs
            for d in p.directions
        }
        sufficiency = signal_sufficiency(
            report, effective, clause_feeds=(), tie_feeds=()
        )
        clauses = _clause_signals(view, employers, start, end)
        ties = _tie_signals(view, employers, start, end)
        ranked = rank_pairs(
            employers,
            flow=flow,
            clauses=clauses,
            ties=ties,
            explanation_data=explanation_data,
        )
        reliability = {(p.employer_a_id, p.employer_b_id): p for p in sufficiency.pairs}
        unreliable = tuple(
            UnreliablePair(p, reliability[p.employer_a_id, p.employer_b_id].reason)
            for p in ranked.mutual + ranked.one_way
            if not reliability[p.employer_a_id, p.employer_b_id].reliable
        )
        unreliable_keys = {
            (p.pair.employer_a_id, p.pair.employer_b_id) for p in unreliable
        }

        # Independent clause/tie leads remain visible despite insufficient flow.
        def visible(pair: RankedPair) -> bool:
            return (
                pair.employer_a_id,
                pair.employer_b_id,
            ) not in unreliable_keys or any(
                s.fired for s in pair.signals if s.key != "flow"
            )

        verdicts = (sufficiency.flow, sufficiency.clauses, sufficiency.ties)
        partial = any(v.status == "insufficient" for v in verdicts)
        recommendations: tuple[Recommendation, ...] = ()
        if partial:
            recommendation_verdict = replace(
                sufficiency,
                flow=Verdict(
                    "insufficient",
                    "; ".join(v.reason for v in verdicts if v.status == "insufficient"),
                ),
            )
            # The catalog has worker-history sources, not clause/tie feed remedies.
            recommendations = tuple(
                replace(
                    r,
                    market_addition=(
                        "This catalog lists worker-history sources. "
                        "Clause/tie feed gaps require collection evidence "
                        "and are not remedied by these sources. " + r.market_addition
                    ),
                )
                for r in recommend_sources(report, recommendation_verdict, as_of=as_of)
            )
        references = tuple(_references(view, p) for p in ranked.mutual + ranked.one_way)
        result = ScreenResult(
            str(uuid4()),
            market,
            as_of,
            tuple(p for p in ranked.mutual if visible(p)),
            tuple(p for p in ranked.one_way if visible(p)),
            unreliable,
            sufficiency,
            recommendations,
            partial,
            accept_partial,
            report,
            references,
            ranked.method,
            ranked.explanation_method,
        )
    finally:
        view.close()
    _persist(workspace, result)
    return result


def _lead_summary(pair: RankedPair) -> dict[str, Any]:
    return {
        "employer_a_id": pair.employer_a_id,
        "employer_b_id": pair.employer_b_id,
        "score": pair.score,
        "summary": pair.summary,
    }


def _persist(workspace: Workspace, result: ScreenResult) -> None:
    parameters: dict[str, Any] = {
        "version": 1,
        "market": json.loads(result.market.to_json()),
        "as_of": result.as_of.isoformat() if result.as_of else None,
        "accept_partial": result.accept_partial,
        "references": [asdict(r) for r in result.references],
    }
    summary = {
        "version": 1,
        "mutual_count": len(result.mutual),
        "one_way_count": len(result.one_way),
        "unreliable_count": len(result.unreliable),
        "partial_screen": result.partial_screen,
        "sufficiency": asdict(result.sufficiency),
        "mutual": [_lead_summary(p) for p in result.mutual],
        "one_way": [_lead_summary(p) for p in result.one_way],
        "recommendation_ids": [r.source.id for r in result.recommendations],
    }
    body = json.dumps(
        {"inputs": parameters, "summary": summary}, sort_keys=True
    ).encode()
    receipt = workspace.provenance.append(
        source="screen_pipeline",
        url="file:screen",
        method="SCREEN",
        body=body,
        collection_method="offline_screen",
    )
    market_id = str(uuid4())
    workspace.connection.execute("BEGIN TRANSACTION")
    try:
        workspace.connection.execute(
            "INSERT INTO markets VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                market_id,
                "Workspace screen",
                ",".join(sorted(result.market.occupation_codes)),
                json.dumps(asdict(result.market.geography)),
                date(result.market.start_year, 1, 1),
                date(result.market.end_year, 12, 31),
                receipt.id,
            ],
        )
        workspace.connection.execute(
            "INSERT INTO screens VALUES (?, ?, ?, ?, ?, ?)",
            [
                result.screen_id,
                market_id,
                datetime.now(UTC).replace(tzinfo=None),
                json.dumps(parameters),
                json.dumps(summary),
                receipt.id,
            ],
        )
        workspace.connection.execute("COMMIT")
    except BaseException:
        workspace.connection.execute("ROLLBACK")
        raise
