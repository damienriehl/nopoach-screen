"""One-browser workflow, serialized workspace writes and background screens."""

import csv
import importlib
import io
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from uuid import uuid4

import pyarrow.parquet as parquet  # type: ignore[import-untyped]
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from nopoach.analytical_inputs import (
    AnalyticalInputs,
    BundleRef,
    ClusterSettings,
    inspect_bundles,
    read_bundle,
)
from nopoach.collect.lehd import import_j2j
from nopoach.imports.flow_population import import_population, parse_population
from nopoach.imports.loader import ImportResult, column_mapping, import_file
from nopoach.imports.noncompetes import (
    import_contracts,
    load_contracts,
    parse_contracts,
)
from nopoach.imports.schema import COLUMNS, VENDOR_ALIASES, ProvenanceClass
from nopoach.market.clusters import ClusterProposal, confirm_group, selection_summary
from nopoach.market.coverage import workspace_estimates
from nopoach.market.define import (
    Employer,
    FlowDefinedMarket,
    Geography,
    Market,
    MarketDefinition,
    propose_competitors,
    scope_description,
    scope_key,
)
from nopoach.pipeline import ScreenResult, propose_flow_market, run_screen
from nopoach.rank import wording
from nopoach.rank.aggregate import RankedPair
from nopoach.resolve.parents import ParentResolver
from nopoach.signals.noncompetes import assess_contracts
from nopoach.store.workspace import Workspace

router = APIRouter()
templates = Jinja2Templates(directory=Path(__file__).parents[1] / "templates")
templates.env.globals.update(wording=wording, scope_description=scope_description)


@dataclass
class UploadPreview:
    body: bytes
    suffix: str
    columns: list[str]
    suggested: dict[str, str]


@dataclass
class Session:
    folder: Path | None = None
    market: MarketDefinition | None = None
    analytical_inputs: AnalyticalInputs = field(default_factory=AnalyticalInputs)
    cluster_proposal: ClusterProposal | None = None
    proposal_job: bool = False
    prior_result: bool = False

    def invalidate(self, *, graph: bool = False) -> None:
        self.result = None
        self.prior_result = False
        self.failures = {}
        self.status = "Defined" if self.market else "Empty"
        if graph:
            self.cluster_proposal = None

    def active_inputs(self) -> AnalyticalInputs | None:
        inputs = self.analytical_inputs
        if not isinstance(self.market, FlowDefinedMarket):
            inputs = replace(inputs, flow_population=None, cluster_settings=None)
        return (
            inputs
            if any((inputs.j2j, inputs.contracts, inputs.flow_population))
            else None
        )

    def propose(self, start: int, end: int, settings: ClusterSettings) -> None:
        folder = idle(self)
        inputs = replace(self.analytical_inputs, cluster_settings=settings)
        if inputs.flow_population is None:
            raise ValueError("Select a flow population on Import data first")
        if not 1 <= start <= end <= 9999 or settings.pre_end_year >= start:
            raise ValueError(
                "The training period must end before the assessment starts"
            )
        previous = self.market
        self.invalidate(graph=True)
        self.analytical_inputs = inputs
        self.status, self.proposal_job = "Collecting", True
        self.progress = "Fitting the earlier-period flow population."
        self.executor.submit(self._propose, folder, inputs, start, end, previous)

    def _propose(
        self,
        folder: Path,
        inputs: AnalyticalInputs,
        start: int,
        end: int,
        previous: MarketDefinition | None,
    ) -> None:
        try:
            with Workspace.open(folder) as workspace:
                market, proposal = propose_flow_market(
                    workspace, inputs, start_year=start, end_year=end
                )
            if isinstance(previous, FlowDefinedMarket) and scope_key(
                previous
            ) == scope_key(market):
                market = replace(
                    market,
                    added_employer_ids=previous.added_employer_ids,
                    removed_employer_ids=previous.removed_employer_ids,
                )
            self.market, self.cluster_proposal = market, proposal
            self.status = "Defined"
            self.progress = "Review the proposed groups and confirm before screening."
        except Exception as exc:
            self.status = "Failed"
            self.progress = (
                f"Grouping unavailable: {exc}. Ordinary selection remains available."
            )

    employers: list[tuple[str, str]] = field(default_factory=list)
    status: str = "Empty"
    progress: str = "Choose a workspace."
    result: ScreenResult | None = None
    failures: dict[str, str] = field(default_factory=dict)
    collectors: dict[str, Callable[[Workspace, Market], None]] = field(
        default_factory=dict
    )
    executor: ThreadPoolExecutor = field(
        default_factory=lambda: ThreadPoolExecutor(max_workers=1)
    )
    uploads: dict[str, UploadPreview] = field(default_factory=dict)
    upload_id: str | None = None
    import_result: ImportResult | None = None
    import_source: str = ""

    def start(self, retry: str | None = None, *, accept_partial: bool = False) -> None:
        if self.status == "Collecting":
            raise HTTPException(409, "A screen is already running")
        if self.folder is None or self.market is None:
            raise HTTPException(409, "Define a market before running a screen")
        if isinstance(self.market, FlowDefinedMarket) and (
            self.cluster_proposal is None or not self.market.cluster_digest
        ):
            raise HTTPException(409, "Review and confirm a fresh flow proposal first")
        inputs = self.active_inputs()
        proposal = (
            self.cluster_proposal
            if isinstance(self.market, FlowDefinedMarket)
            else None
        )
        self.status, self.proposal_job = "Collecting", False
        self.progress = "Preparing stored records."
        self.executor.submit(
            self._run, self.folder, self.market, retry, accept_partial, inputs, proposal
        )

    def _run(
        self,
        folder: Path,
        market: MarketDefinition,
        retry: str | None,
        accept_partial: bool,
        inputs: AnalyticalInputs | None,
        proposal: ClusterProposal | None,
    ) -> None:
        if retry is None and not accept_partial:
            self.failures = {}
        try:
            with Workspace.open(folder) as workspace:
                for name, collect in self.collectors.items():
                    if accept_partial or isinstance(market, FlowDefinedMarket):
                        break
                    if retry is not None and retry != name:
                        continue
                    self.progress = f"Collecting {name}."
                    try:
                        collect(workspace, market)
                        self.failures.pop(name, None)
                    except Exception as exc:
                        self.failures[name] = str(exc)
                self.progress = "Scoring stored evidence across three signals."
                options: dict[str, Any] = {"accept_partial": accept_partial}
                if inputs is not None:
                    options.update(analytical_inputs=inputs, cluster_proposal=proposal)
                self.result = run_screen(workspace, market, **options)
                self.prior_result = False
                self.failures.pop("Screen scoring", None)
            self.status = self._finished_status(self.result, accept_partial)
            self.progress = "Screen finished. Review coverage and sufficiency."
        except Exception as exc:
            self.prior_result = self.result is not None
            self.failures["Screen scoring"] = str(exc)
            self.progress = "Scoring failed. Stored records and prior results are kept."
            self.status = "Failed"

    def _finished_status(self, result: ScreenResult, accept_partial: bool) -> str:
        if self.failures and not accept_partial:
            return "Failed"
        if result.partial_screen and not accept_partial:
            return "Insufficient"
        return "Ready"


def state(request: Request) -> Session:
    session: Session = request.app.state.session
    return session


def page(
    request: Request, name: str, *, status_code: int = 200, **context: Any
) -> HTMLResponse:
    session = state(request)
    context.setdefault(
        "flow_mode",
        request.query_params.get(
            "mode",
            "flow" if isinstance(session.market, FlowDefinedMarket) else "ordinary",
        )
        == "flow",
    )
    context.setdefault("employer_names", dict(session.employers))
    if (
        isinstance(session.market, FlowDefinedMarket)
        and session.cluster_proposal
        and session.market.cluster_digest
    ):
        context.setdefault(
            "cluster_selection",
            selection_summary(session.cluster_proposal, session.market),
        )
    context.setdefault("scope", scope_key(session.market) if session.market else "")
    if name == "imports.html" and session.folder and session.status != "Collecting":
        with Workspace.open(session.folder) as workspace:
            bundles = []
            issues: list[str] = []
            try:
                inventory, inventory_issues = inspect_bundles(workspace)
                issues.extend(inventory_issues)
            except (ValueError, OSError) as exc:
                inventory = ()
                issues.append(f"Input inventory integrity check failed: {exc}")
            for ref in inventory:
                try:
                    bundle = read_bundle(workspace, ref)
                    bundles.append((ref, bundle.metadata))
                except (ValueError, OSError) as exc:
                    issues.append(f"Input version integrity check failed: {exc}")
            context["bundles"] = bundles
            context["receipts"] = workspace.provenance.records()
            if session.analytical_inputs.contracts and session.market:
                try:
                    corpus = load_contracts(
                        workspace, session.analytical_inputs.contracts
                    )
                    context["contract_review"] = assess_contracts(
                        corpus,
                        scope=scope_key(session.market),
                        start=date(session.market.start_year, 1, 1),
                        end=date(session.market.end_year, 12, 31),
                    )
                except (ValueError, OSError) as exc:
                    issues.append(
                        f"Selected contract review integrity check failed: {exc}"
                    )
            context["integrity_issues"] = issues
    return templates.TemplateResponse(
        request=request,
        name=name,
        context={
            "session": session,
            "result": session.result,
            "market": session.market,
            "upload_id": session.upload_id,
            "upload": session.uploads.get(session.upload_id or ""),
            **context,
        },
        status_code=status_code,
    )


def idle(session: Session) -> Path:
    if session.status == "Collecting":
        raise HTTPException(409, "Wait for the running screen to finish")
    if session.folder is None:
        raise HTTPException(409, "Choose a workspace first")
    return session.folder


def pairs(result: ScreenResult) -> tuple[RankedPair, ...]:
    unique = {
        (p.employer_a_id, p.employer_b_id): p for p in result.mutual + result.one_way
    }
    unique.update(
        {
            (r.pair.employer_a_id, r.pair.employer_b_id): r.pair
            for r in result.unreliable
        }
    )
    return tuple(unique.values())


def find_pair(session: Session, a: str, b: str) -> RankedPair:
    if session.result is None:
        raise HTTPException(404, "Run a screen first")
    for pair in pairs(session.result):
        if (pair.employer_a_id, pair.employer_b_id) == (a, b):
            return pair
    raise HTTPException(404, "Pair not in this screen")


def exporter() -> Callable[..., Any] | None:
    try:
        module = importlib.import_module("nopoach.export.dossier")
    except ModuleNotFoundError as exc:
        if exc.name in {"nopoach.export", "nopoach.export.dossier"}:
            return None
        raise
    function: Callable[..., Any] | None = getattr(module, "export_dossier", None)
    return function


@router.get("/")
def workspace_picker(request: Request) -> HTMLResponse:
    return page(request, "workspace.html", title="Choose a workspace")


@router.post("/workspace")
async def choose_workspace(request: Request) -> HTMLResponse:
    session = state(request)
    if session.status == "Collecting":
        raise HTTPException(409, "Wait for the running screen to finish")
    form = await request.form()
    if session.status == "Collecting":
        raise HTTPException(409, "Wait for the running screen to finish")
    try:
        folder = str(form.get("folder", "")).strip()
        if not folder:
            raise ValueError("Choose a workspace folder")
        with Workspace.open(folder) as workspace:
            employers = workspace.connection.execute(
                "SELECT id, name FROM employers ORDER BY name"
            ).fetchall()
            root = workspace.root
        session.folder, session.employers = root, employers
        session.market, session.result = None, None
        session.analytical_inputs = AnalyticalInputs()
        session.cluster_proposal = None
        session.proposal_job = False
        session.prior_result = False
        session.uploads.clear()
        session.upload_id, session.import_result = None, None
        session.failures = {}
        session.status = "Empty"
    except (ValueError, OSError) as exc:
        return page(
            request,
            "workspace.html",
            title="Choose a workspace",
            error=str(exc),
            status_code=400,
        )
    return page(request, "market.html", title="Define the market", proposal=None)


@router.get("/market", response_model=None)
def market_page(request: Request) -> HTMLResponse | RedirectResponse:
    if state(request).folder is None:
        return RedirectResponse("/", status_code=303)
    return page(request, "market.html", title="Define the market", proposal=None)


@router.post("/market")
async def define_market(request: Request) -> HTMLResponse:
    session = state(request)
    idle(session)
    form = await request.form()
    folder = idle(session)
    try:
        market = Market(
            frozenset(str(form.get("occupations", "")).replace(",", " ").split()),
            Geography(
                str(form.get("geography_kind", "metro")),  # type: ignore[arg-type]
                str(form.get("geography_code", "")),
            ),
            int(str(form.get("start_year", ""))),
            int(str(form.get("end_year", ""))),
            frozenset(str(v) for v in form.getlist("employers"))
            if form.get("action") != "propose"
            else None,
        )
        known = {identifier for identifier, _ in session.employers}
        if market.employer_ids is not None and not market.employer_ids <= known:
            raise ValueError("Select employers from this workspace")
        session.invalidate(graph=True)
        session.market = market
        if form.get("action") == "propose":
            with Workspace.open(folder) as workspace:
                proposal = propose_competitors(
                    market,
                    [Employer(*e) for e in session.employers],
                    workspace_estimates(workspace, market),
                )
            return page(
                request,
                "market.html",
                title="Confirm competitors",
                proposal=proposal,
                flow_mode=False,
            )
        session.status = "Defined"
        session.start()
    except ValueError as exc:
        return page(
            request,
            "market.html",
            title="Define the market",
            error=str(exc),
            proposal=None,
            flow_mode=False,
            status_code=400,
        )
    return page(request, "progress.html", title="Screen progress")


@router.post("/screen/run")
async def start_screen(request: Request) -> HTMLResponse:
    state(request).start()
    return page(request, "progress.html", title="Screen progress")


@router.get("/screen/progress")
def progress(request: Request) -> HTMLResponse:
    return page(request, "progress_fragment.html", title="Screen progress")


@router.get("/screen")
def screen(
    request: Request,
    tab: str = "mutual",
    signal: str = "",
    employer: str = "",
    sort: str = "score",
    page_number: int = 1,
) -> HTMLResponse:
    session = state(request)
    if session.status == "Collecting":
        return page(request, "progress.html", title="Screen progress")
    result = session.result
    if result is None:
        return page(request, "progress.html", title="Screen progress")
    unreliable = {
        (r.pair.employer_a_id, r.pair.employer_b_id) for r in result.unreliable
    }
    candidates = result.one_way if tab == "one-way" else result.mutual

    def visible(pair: RankedPair) -> bool:
        return (pair.employer_a_id, pair.employer_b_id) not in unreliable or any(
            s.fired for s in pair.signals if s.key != "flow"
        )

    visible_pairs = [p for p in candidates if visible(p)]
    filtered = [
        p
        for p in visible_pairs
        if (not signal or signal in p.fired_signals)
        and (
            not employer
            or employer.casefold()
            in (p.employer_a_name + " " + p.employer_b_name).casefold()
        )
    ]
    filtered.sort(
        key=(lambda p: (p.employer_a_name, p.employer_b_name))
        if sort == "employer"
        else (lambda p: (-p.score, p.employer_a_id, p.employer_b_id))
    )
    count = len(filtered)
    pages = max(1, (count + 19) // 20)
    page_number = max(1, min(page_number, pages))
    return page(
        request,
        "screen.html",
        title="Ranked screen",
        market=result.market,
        leads=filtered[(page_number - 1) * 20 : page_number * 20],
        tab=tab,
        signal=signal,
        employer=employer,
        sort=sort,
        page_number=page_number,
        pages=pages,
        count=count,
        unreliable_keys=unreliable,
        flagged=any(p.fired_signals for p in visible_pairs),
        mutual_count=sum(visible(p) for p in result.mutual),
        one_way_count=sum(visible(p) for p in result.one_way),
    )


@router.get("/screen/accept")
def confirm_partial(request: Request) -> HTMLResponse:
    session = state(request)
    if session.status not in {"Insufficient", "Failed"} or session.result is None:
        raise HTTPException(409, "No partial screen to accept")
    return page(request, "accept.html", title="Accept partial screen")


@router.post("/screen/accept")
async def accept_partial(request: Request) -> HTMLResponse:
    session = state(request)
    idle(session)
    form = await request.form()
    if (
        form.get("confirm") != "yes"
        or session.result is None
        or session.status not in {"Insufficient", "Failed"}
    ):
        raise HTTPException(409, "Confirm the insufficient signals before accepting")
    # The pipeline persists acceptance; scoring remains a background job.
    session.start(accept_partial=True)
    return page(request, "progress.html", title="Screen progress")


@router.post("/screen/retry")
async def retry_source(request: Request) -> HTMLResponse:
    session = state(request)
    form = await request.form()
    source = str(form.get("source", ""))
    if source not in session.failures:
        raise HTTPException(409, "Source has not failed")
    session.start(None if source == "Screen scoring" else source)
    return page(request, "progress.html", title="Screen progress")


@router.get("/imports")
def import_page(request: Request) -> HTMLResponse:
    idle(state(request))
    return page(
        request,
        "imports.html",
        title="Import data",
        classes=ProvenanceClass,
        fields=(*COLUMNS, "cbsa", "latitude", "longitude"),
    )


@router.post("/imports/preview")
async def preview_import(request: Request) -> HTMLResponse:
    session = state(request)
    idle(session)
    form = await request.form()
    upload = form.get("file")
    if upload is None or isinstance(upload, str):
        raise HTTPException(400, "Choose a CSV or Parquet file")
    suffix = Path(upload.filename or "").suffix.lower()
    try:
        body = await bounded_upload(upload, 64 * 1024 * 1024)
        idle(session)
        if suffix == ".csv":
            columns = next(csv.reader(io.StringIO(body.decode("utf-8-sig"))))
        elif suffix == ".parquet":
            columns = parquet.read_schema(io.BytesIO(body)).names
        else:
            raise ValueError("Choose a CSV or Parquet file")
        if len(columns) != len(set(columns)):
            raise ValueError("Duplicate input column names")
        # Use the import mapper; missing/ambiguous mappings remain editable.
        try:
            suggested = column_mapping(columns)
        except ValueError:
            suggested = {
                c: c
                if c in columns
                else next((a for a in VENDOR_ALIASES.get(c, ()) if a in columns), "")
                for c in (*COLUMNS, "cbsa", "latitude", "longitude")
            }
        session.upload_id = str(uuid4())
        session.uploads[session.upload_id] = UploadPreview(
            body, suffix, columns, suggested
        )
        session.import_result = None
    except (ValueError, StopIteration, UnicodeError) as exc:
        return page(
            request,
            "imports.html",
            title="Import data",
            classes=ProvenanceClass,
            fields=COLUMNS,
            error=str(exc),
            status_code=400,
        )
    return import_page(request)


@router.post("/imports")
async def load_import(request: Request) -> HTMLResponse:
    session = state(request)
    idle(session)
    form = await request.form()
    folder = idle(session)
    upload_id = str(form.get("upload_id", ""))
    upload = session.uploads.get(upload_id)
    try:
        if upload is None:
            raise ValueError(
                "Upload preview is missing or stale. Choose and map a file first"
            )
        source = str(form.get("source", ""))
        classification = str(form.get("provenance_class", ""))
        mapping = {
            str(k)[4:]: str(v)
            for k, v in form.items()
            if str(k).startswith("map_") and v
        }
        with TemporaryDirectory(prefix="nopoach-import-") as temporary:
            path = Path(temporary) / ("upload" + upload.suffix)
            path.write_bytes(upload.body)
            with Workspace.open(folder) as workspace:
                session.import_result = import_file(
                    workspace,
                    path,
                    source=source,
                    provenance_class=classification,
                    column_map=mapping,
                )
                session.employers = workspace.connection.execute(
                    "SELECT id, name FROM employers ORDER BY name"
                ).fetchall()
        session.import_source = source
        session.uploads.pop(upload_id)
        if session.upload_id == upload_id:
            session.upload_id = None
        session.invalidate(graph=True)
        if isinstance(session.market, Market):
            session.start()
    except ValueError as exc:
        return page(
            request,
            "imports.html",
            title="Import data",
            classes=ProvenanceClass,
            fields=COLUMNS,
            error=str(exc),
            status_code=400,
            upload_id=upload_id,
            upload=upload,
        )
    return page(
        request,
        "imports.html",
        title="Import data",
        classes=ProvenanceClass,
        fields=COLUMNS,
    )


@router.post("/imports/parent")
async def parent_override(request: Request) -> HTMLResponse:
    session = state(request)
    idle(session)
    form = await request.form()
    folder = idle(session)
    try:
        with Workspace.open(folder) as workspace:
            reason = str(form.get("reason", "")).strip()
            if not reason:
                raise ValueError("Manual overrides require a reason")
            receipt = workspace.provenance.append(
                source="Employer-parent override",
                url="file:manual-override",
                method="IMPORT",
                body=str(dict(form)).encode(),
                collection_method="manual_override",
            )
            ParentResolver(workspace).add_link(
                str(form.get("employer_id", "")),
                str(form.get("parent_id", "")),
                valid_from=date.fromisoformat(str(form.get("valid_from", ""))),
                valid_to=date.fromisoformat(str(form["valid_to"]))
                if form.get("valid_to")
                else None,
                provenance_id=receipt.id,
                origin="manual_override",
                reason=reason,
            )
        session.invalidate(graph=True)
        if isinstance(session.market, Market):
            session.start()
    except ValueError as exc:
        return page(
            request,
            "imports.html",
            title="Import data",
            classes=ProvenanceClass,
            fields=COLUMNS,
            error=str(exc),
            status_code=400,
        )
    return page(
        request,
        "imports.html",
        title="Import data",
        classes=ProvenanceClass,
        fields=COLUMNS,
        notice="Employer-parent override recorded with provenance.",
    )


@router.get("/pairs/{a}/{b}")
def dossier(request: Request, a: str, b: str) -> HTMLResponse:
    session = state(request)
    idle(session)
    pair = find_pair(session, a, b)
    assert session.folder and session.result
    with Workspace.open(session.folder) as workspace:
        reference = next(
            r
            for r in session.result.references
            if (r.employer_a_id, r.employer_b_id) == (a, b)
        )
        provenance_ids = set(reference.provenance_ids)
        sources = [r for r in workspace.provenance.records() if r.id in provenance_ids]
        records = workspace.connection.execute(
            "SELECT p.id, people.name, p.person_id, employers.name, p.title, "
            "p.start_date, p.end_date, p.start_precision, p.end_precision, "
            "pr.source, pr.collected_at, pr.collection_method FROM positions p "
            "JOIN people ON people.id = p.person_id "
            "JOIN employers ON employers.id = p.employer_id "
            "JOIN provenance pr ON pr.id = p.provenance_id "
            "WHERE p.id IN (SELECT unnest(?)) ORDER BY p.id",
            [list(reference.position_ids)],
        ).fetchall()
        tables = workspace.table_names()
        imports = (
            workspace.connection.execute(
                "SELECT DISTINCT source, provenance_class FROM imports ORDER BY source"
            ).fetchall()
            if "imports" in tables
            else []
        )
        evidence = {}
        for table, ids in (
            ("filings", reference.filing_ids),
            ("workforce_inputs", reference.workforce_input_ids),
            ("dol_records", reference.dol_record_ids),
            ("ties", reference.tie_ids),
        ):
            query = workspace.connection.execute(
                f"SELECT * FROM {table} WHERE id IN (SELECT unnest(?)) ORDER BY id",
                [list(ids)],
            )
            evidence[table] = {
                "columns": [c[0] for c in query.description],
                "rows": query.fetchall(),
            }
    maximum = (
        max(
            (
                max(y.observed, y.expected)
                for d in pair.flow.directions
                for y in d.years
            ),
            default=1,
        )
        if pair.flow
        else 1
    )
    unreliable = next(
        (r.reason for r in session.result.unreliable if r.pair == pair), None
    )
    return page(
        request,
        "dossier.html",
        title=f"{pair.employer_a_name} / {pair.employer_b_name}",
        pair=pair,
        market=session.result.market,
        sources=sources,
        records=records,
        imports=imports,
        evidence=evidence,
        maximum=max(1, maximum),
        unreliable=unreliable,
        export_available=exporter() is not None,
    )


@router.get("/pairs/{a}/{b}/moves")
def moves(request: Request, a: str, b: str, direction: int, year: int) -> HTMLResponse:
    pair = find_pair(state(request), a, b)
    if not pair.flow or direction not in {0, 1}:
        raise HTTPException(404, "Flow direction unavailable")
    flow = next(
        (y for y in pair.flow.directions[direction].years if y.year == year), None
    )
    if flow is None:
        raise HTTPException(404, "Flow year unavailable")
    return page(
        request,
        "moves_fragment.html" if request.headers.get("HX-Request") else "moves.html",
        title="Person-level moves",
        moves=flow.moves,
        year=year,
        a=a,
        b=b,
    )


@router.get("/pairs/{a}/{b}/export", response_model=None)
def export(request: Request, a: str, b: str) -> FileResponse | HTMLResponse:
    session = state(request)
    folder = idle(session)
    pair = find_pair(session, a, b)
    function = exporter()
    if function is None:
        return page(
            request, "unavailable.html", title="Export unavailable", status_code=503
        )
    output = folder / "exports" / f"dossier-{uuid4()}.docx"
    output.parent.mkdir(exist_ok=True, mode=0o700)
    with Workspace.open(folder) as workspace:
        function(
            workspace=workspace, screen=session.result, pair=pair, destination=output
        )
    return FileResponse(
        output,
        filename="dossier.docx",
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )


async def bounded_upload(upload: Any, limit: int) -> bytes:
    if upload is None or isinstance(upload, str):
        raise ValueError("Choose a local file")
    if upload.size is not None and upload.size > limit:
        raise ValueError(f"File exceeds the {limit // 1024 // 1024} MiB limit")
    body: bytes = await upload.read(limit + 1)
    if len(body) > limit:
        raise ValueError(f"File exceeds the {limit // 1024 // 1024} MiB limit")
    return body


def select_input(session: Session, kind: str, ref: BundleRef | None) -> None:
    if kind == "flow_population":
        session.analytical_inputs = replace(
            session.analytical_inputs, flow_population=ref
        )
    elif kind == "j2j":
        session.analytical_inputs = replace(session.analytical_inputs, j2j=ref)
    else:
        session.analytical_inputs = replace(session.analytical_inputs, contracts=ref)
    session.invalidate(graph=kind == "flow_population")


@router.post("/imports/analytical/select")
async def select_analytical(request: Request) -> HTMLResponse:
    session = state(request)
    idle(session)
    form = await request.form()
    folder = idle(session)
    try:
        kind, identifier = str(form.get("kind", "")), str(form.get("bundle_id", ""))
        if kind not in {"flow_population", "j2j", "contracts"}:
            raise ValueError("Choose an analytical input type")
        ref = None
        if identifier:
            with Workspace.open(folder) as workspace:
                ref = next(
                    (
                        r
                        for r in inspect_bundles(workspace)[0]
                        if r.kind == kind and r.bundle_id == identifier
                    ),
                    None,
                )
                if ref is None:
                    raise ValueError("Choose a retained version from this workspace")
                read_bundle(workspace, ref)
        select_input(session, kind, ref)
    except (ValueError, OSError) as exc:
        return page(
            request,
            "imports.html",
            title="Import data",
            classes=ProvenanceClass,
            fields=COLUMNS,
            error=str(exc),
            status_code=400,
        )
    return import_page(request)


@router.post("/imports/analytical/{kind}")
async def import_analytical(request: Request, kind: str) -> HTMLResponse:
    session = state(request)
    idle(session)
    limits = {"flow_population": 4, "contracts": 8, "j2j": 64}
    if kind not in limits:
        raise HTTPException(404, "Unknown analytical input")
    form = await request.form()
    idle(session)
    try:
        body = await bounded_upload(form.get("file"), limits[kind] * 1024 * 1024)
        folder = idle(session)
        metadata: dict[str, Any] = {"source": str(form.get("source", ""))}
        known = {identifier for identifier, _ in session.employers}
        with Workspace.open(folder) as workspace:
            if kind == "flow_population":
                metadata.update(
                    population_id=str(form.get("population_id", "")),
                    description=str(form.get("description", "")),
                    provenance_ids=[str(v) for v in form.getlist("provenance_ids")],
                    employer_ids=[str(v) for v in form.getlist("employer_ids")],
                )
                population = parse_population(body, metadata)
                if not population.employer_ids <= known:
                    raise ValueError(
                        "Candidate employer IDs must belong to this workspace"
                    )
                receipts = {r.id for r in workspace.provenance.records()}
                if not population.provenance_ids <= receipts:
                    raise ValueError("Select source receipts from this workspace")
                ref = import_population(workspace, body, metadata)
            elif kind == "j2j":
                for key in (
                    "schema_version",
                    "release",
                    "release_date",
                    "geography",
                    "ownercode",
                    "ownercode_orig",
                    "workplace_geography",
                ):
                    metadata[key] = str(form.get(key, ""))
                metadata["availability"] = {
                    "geo_level": "B",
                    "geography": metadata["geography"],
                    "geo_level_orig": "B",
                    "geography_orig": metadata["geography"],
                    **{
                        key: int(str(form.get(key, "")))
                        for key in (
                            "start_year",
                            "start_quarter",
                            "end_year",
                            "end_quarter",
                        )
                    },
                }
                ref = import_j2j(workspace, body, metadata)
            else:
                for key in ("population_description", "sampling_basis", "scope"):
                    metadata[key] = str(form.get(key, ""))
                metadata["declared_workers"] = {
                    identifier: int(str(form[f"workers_{identifier}"]))
                    for identifier in known
                    if form.get(f"workers_{identifier}")
                }
                if form.get("reviewed_on"):
                    metadata["reviewed_on"] = str(form["reviewed_on"])
                corpus = parse_contracts(body, metadata)
                if any(row.employer_id not in known for row in corpus.records):
                    raise ValueError(
                        "Contract employer IDs must belong to this workspace"
                    )
                ref = import_contracts(workspace, body, metadata)
        select_input(session, kind, ref)
    except (ValueError, OSError) as exc:
        return page(
            request,
            "imports.html",
            title="Import data",
            classes=ProvenanceClass,
            fields=COLUMNS,
            error=str(exc),
            status_code=400,
        )
    return import_page(request)


@router.post("/market/flow/propose")
async def propose_flow(request: Request) -> HTMLResponse:
    session = state(request)
    idle(session)
    form = await request.form()
    idle(session)
    try:
        settings = ClusterSettings(
            int(str(form.get("pre_start_year", ""))),
            int(str(form.get("pre_end_year", ""))),
            groups=int(str(form.get("groups", "2"))),
        )
        session.propose(
            int(str(form.get("start_year", ""))),
            int(str(form.get("end_year", ""))),
            settings,
        )
    except ValueError as exc:
        return page(
            request,
            "market.html",
            title="Define the market",
            flow_mode=True,
            proposal=None,
            error=str(exc),
            status_code=400,
        )
    return page(request, "progress.html", title="Group proposal progress")


@router.post("/market/flow/confirm")
async def confirm_flow(request: Request) -> HTMLResponse:
    session = state(request)
    idle(session)
    form = await request.form()
    idle(session)
    try:
        if (
            not isinstance(session.market, FlowDefinedMarket)
            or not session.cluster_proposal
        ):
            raise ValueError("Request and review a flow proposal first")
        if form.get("proposal_digest") != session.cluster_proposal.digest:
            raise ValueError(
                "This proposal changed. Review the current groups before confirming."
            )
        market = confirm_group(
            session.cluster_proposal,
            replace(
                session.market,
                added_employer_ids=frozenset(),
                removed_employer_ids=frozenset(),
            ),
            int(str(form.get("group", ""))),
            add=frozenset(str(v) for v in form.getlist("add")),
            remove=frozenset(str(v) for v in form.getlist("remove")),
        )
        session.invalidate()
        session.market = market
        session.start()
    except ValueError as exc:
        return page(
            request,
            "market.html",
            title="Define the market",
            flow_mode=True,
            proposal=None,
            error=str(exc),
            status_code=400,
        )
    return page(request, "progress.html", title="Screen progress")
