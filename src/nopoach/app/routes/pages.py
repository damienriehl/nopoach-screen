"""One-browser workflow, serialized workspace writes and background screens."""

import csv
import importlib
import io
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from uuid import uuid4

import pyarrow.parquet as parquet  # type: ignore[import-untyped]
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from nopoach.imports.loader import ImportResult, column_mapping, import_file
from nopoach.imports.schema import COLUMNS, VENDOR_ALIASES, ProvenanceClass
from nopoach.market.coverage import workspace_estimates
from nopoach.market.define import Employer, Geography, Market, propose_competitors
from nopoach.pipeline import ScreenResult, run_screen
from nopoach.rank import wording
from nopoach.rank.aggregate import RankedPair
from nopoach.resolve.parents import ParentResolver
from nopoach.store.workspace import Workspace

router = APIRouter()
templates = Jinja2Templates(directory=Path(__file__).parents[1] / "templates")
templates.env.globals["wording"] = wording


@dataclass
class Session:
    folder: Path | None = None
    market: Market | None = None
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
    upload: bytes | None = None
    suffix: str = ".csv"
    columns: list[str] = field(default_factory=list)
    suggested: dict[str, str] = field(default_factory=dict)
    import_result: ImportResult | None = None
    import_source: str = ""

    def start(self, retry: str | None = None, *, accept_partial: bool = False) -> None:
        if self.status == "Collecting":
            raise HTTPException(409, "A screen is already running")
        if self.folder is None or self.market is None:
            raise HTTPException(409, "Define a market before running a screen")
        self.status = "Collecting"
        self.progress = "Preparing stored records."
        self.executor.submit(self._run, self.folder, self.market, retry, accept_partial)

    def _run(
        self, folder: Path, market: Market, retry: str | None, accept_partial: bool
    ) -> None:
        if retry is None and not accept_partial:
            self.failures = {}
        try:
            with Workspace.open(folder) as workspace:
                for name, collect in self.collectors.items():
                    if accept_partial:
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
                self.result = run_screen(
                    workspace, market, accept_partial=accept_partial
                )
            self.status = "Scored"
            self.status = (
                "Failed"
                if self.failures and not accept_partial
                else (
                    "Insufficient"
                    if self.result.partial_screen and not accept_partial
                    else "Ready"
                )
            )
            self.progress = "Screen finished. Review coverage and sufficiency."
        except Exception as exc:
            self.failures["Screen scoring"] = str(exc)
            self.progress = "Scoring failed. Stored records and prior results are kept."
            self.status = "Failed"


def state(request: Request) -> Session:
    session: Session = request.app.state.session
    return session


def page(
    request: Request, name: str, *, status_code: int = 200, **context: Any
) -> HTMLResponse:
    session = state(request)
    return templates.TemplateResponse(
        request=request,
        name=name,
        context={
            "session": session,
            "result": session.result,
            "market": session.market,
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
        session.upload, session.import_result = None, None
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
    folder = idle(session)
    form = await request.form()
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
        if session.market != market:
            session.result = None
            session.failures = {}
        session.market = market
        if form.get("action") == "propose":
            with Workspace.open(folder) as workspace:
                proposal = propose_competitors(
                    market,
                    [Employer(*e) for e in session.employers],
                    workspace_estimates(workspace, market),
                )
            return page(
                request, "market.html", title="Confirm competitors", proposal=proposal
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
            status_code=400,
        )
    return page(request, "progress.html", title="Screen progress")


@router.post("/screen/run")
def start_screen(request: Request) -> HTMLResponse:
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
    reliable = [
        p for p in candidates if (p.employer_a_id, p.employer_b_id) not in unreliable
    ]
    filtered = [
        p
        for p in reliable
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
        leads=filtered[(page_number - 1) * 20 : page_number * 20],
        tab=tab,
        signal=signal,
        employer=employer,
        sort=sort,
        page_number=page_number,
        pages=pages,
        count=count,
        flagged=any(p.fired_signals for p in reliable),
        mutual_count=sum(
            (p.employer_a_id, p.employer_b_id) not in unreliable for p in result.mutual
        ),
        one_way_count=sum(
            (p.employer_a_id, p.employer_b_id) not in unreliable for p in result.one_way
        ),
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
    body = await upload.read()
    suffix = Path(upload.filename or "").suffix.lower()
    try:
        if suffix == ".csv":
            columns = next(csv.reader(io.StringIO(body.decode("utf-8-sig"))))
        elif suffix == ".parquet":
            columns = parquet.read_schema(io.BytesIO(body)).names
        else:
            raise ValueError("Choose a CSV or Parquet file")
        if len(columns) != len(set(columns)):
            raise ValueError("Duplicate input column names")
        session.upload, session.suffix, session.columns = body, suffix, columns
        # Use U3's mapper, while allowing missing/ambiguous mappings to be edited.
        try:
            session.suggested = column_mapping(columns)
        except ValueError:
            session.suggested = {
                c: c
                if c in columns
                else next((a for a in VENDOR_ALIASES.get(c, ()) if a in columns), "")
                for c in (*COLUMNS, "cbsa", "latitude", "longitude")
            }
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
    folder = idle(session)
    form = await request.form()
    try:
        if session.upload is None:
            raise ValueError("Choose and map a file first")
        source = str(form.get("source", ""))
        classification = str(form.get("provenance_class", ""))
        mapping = {
            str(k)[4:]: str(v)
            for k, v in form.items()
            if str(k).startswith("map_") and v
        }
        with TemporaryDirectory(prefix="nopoach-import-") as temporary:
            path = Path(temporary) / ("upload" + session.suffix)
            path.write_bytes(session.upload)
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
        session.upload = None
        if session.market is not None:
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
    )


@router.post("/imports/parent")
async def parent_override(request: Request) -> HTMLResponse:
    session = state(request)
    folder = idle(session)
    form = await request.form()
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
        if session.market:
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
        sources = [
            r
            for r in workspace.provenance.records()
            if r.id in reference.provenance_ids
        ]
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
        tables = {r[0] for r in workspace.connection.execute("SHOW TABLES").fetchall()}
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
