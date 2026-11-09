"""Host-owned Chromium/socket/axe checks: pytest -m browser tests/app/test_e2e.py.

Original workflow screenshots remain temporary artifacts. The analytical workflow
also captures four explicitly required synthetic repository evidence images.
The host must inspect all captures for reading legibility.
"""

import io
import re
import runpy
import socket
import time
from pathlib import Path
from threading import Thread
from urllib.parse import urljoin, urlsplit

import pytest
import uvicorn
from axe_playwright_python.sync_playwright import Axe
from playwright.sync_api import expect, sync_playwright

from nopoach.app.main import create_app
from nopoach.store.workspace import Workspace
from nopoach.synth.scenarios import named_scenario

pytestmark = pytest.mark.browser


@pytest.fixture
def browser(tmp_path):
    # All socket and browser capabilities belong to the host running this marker.
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    app = create_app(port=port, token="synthetic-browser-session")
    server = uvicorn.Server(
        uvicorn.Config(
            app, host="127.0.0.1", port=port, access_log=False, log_level="warning"
        )
    )
    thread = Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(500):
        if server.started:
            break
        time.sleep(0.01)
    assert server.started, "Host server did not start on IPv4 loopback"
    requests = []
    with sync_playwright() as playwright:
        chromium = playwright.chromium.launch()
        context = chromium.new_context(viewport={"width": 1440, "height": 1000})
        context.on("request", lambda request: requests.append(request.url))
        page = context.new_page()
        try:
            page.goto(f"http://127.0.0.1:{port}/?token=synthetic-browser-session")
            yield page, app, tmp_path
            assert requests
            assert {urlsplit(url).hostname for url in requests} == {"127.0.0.1"}
        finally:
            context.close()
            chromium.close()
            server.should_exit = True
            thread.join(timeout=10)
            assert not thread.is_alive(), "Host server did not exit cleanly"


def accessibility(page):
    # Axe is injected from the installed wheel's local bundle, never a CDN.
    results = Axe().run(
        page,
        options={
            "runOnly": {
                "type": "tag",
                "values": ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"],
            }
        },
    )
    assert results.violations_count == 0, results.generate_report()


def test_refusal_page_accessibility(browser):
    page, _, _ = browser
    page.context.clear_cookies()
    response = page.goto(urljoin(page.url, "/"))
    assert response.status == 403
    expect(page).to_have_title("Request refused · Labor market screen")
    expect(page.get_by_role("heading", name="Request refused")).to_be_visible()
    expect(page.get_by_text("Session token required.", exact=True)).to_be_visible()
    accessibility(page)


def load_scenario(root, name):
    helper = runpy.run_path(str(Path(__file__).parents[1] / "test_pipeline.py"))[
        "load_resolved_scenario"
    ]
    with Workspace.open(root) as workspace:
        helper(workspace, named_scenario(name))


def define_and_run(page, root, occupation="29-1141"):
    page.get_by_label("Workspace folder").fill(str(root))
    page.get_by_role("button", name="Open workspace").click()
    accessibility(page)
    page.get_by_label("Occupation codes (SOC)").fill(occupation)
    page.get_by_label("State or metro code").fill("SYNTHETIC")
    page.get_by_label("First year").fill("2015")
    page.get_by_label("Last year").fill("2024")
    page.get_by_role("button", name="Propose competitors").click()
    page.get_by_role("button", name="Run screen", exact=True).click()
    expect(page.locator("#screen-progress")).to_be_visible()
    # Polling uses only the local htmx asset and a live region.
    expect(page.get_by_role("link", name="Read screen results")).to_be_visible(
        timeout=120000
    )
    accessibility(page)
    page.get_by_role("link", name="Read screen results").click()
    accessibility(page)


def coverage_layout(page):
    region = page.get_by_role(
        "region", name="Workforce coverage by employer and year", exact=True
    )
    expect(region).to_have_attribute("tabindex", "0")
    assert region.locator("caption").evaluate(
        "element => parseFloat(getComputedStyle(element).paddingLeft) > 0"
    )
    region.focus()
    expect(region).to_be_focused()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert region.evaluate(
        """element => {
            const panel = element.closest('aside').getBoundingClientRect();
            const bounds = element.getBoundingClientRect();
            return bounds.left >= panel.left && bounds.right <= panel.right + 1;
        }"""
    )
    if region.evaluate("element => element.scrollWidth > element.clientWidth"):
        page.keyboard.press("ArrowRight")
        page.wait_for_function(
            """() => document.querySelector(
                '[aria-label="Workforce coverage by employer and year"]'
            ).scrollLeft > 0"""
        )
        region.evaluate("element => { element.scrollLeft = element.scrollWidth; }")
    assert region.evaluate(
        """element => {
            const header = element.querySelector('thead th:last-child')
                .getBoundingClientRect();
            const bounds = element.getBoundingClientRect();
            return header.left >= bounds.left && header.right <= bounds.right + 1;
        }"""
    )
    region.evaluate("element => { element.scrollLeft = 0; }")


def test_planted_pair_dossier_import_and_partial_export(browser):
    page, app, artifacts = browser
    root = artifacts / "synthetic-ae1"
    load_scenario(root, "AE1")
    accessibility(page)
    define_and_run(page, root)
    first = page.locator("table").first.locator("tbody tr").first
    expect(first).to_contain_text("Synthetic Amber Hospital / Synthetic Birch Hospital")
    coverage_layout(page)
    page.evaluate("window.scrollTo(0, 0)")
    page.screenshot(path=str(artifacts / "ranked-list.png"), full_page=False)
    page.set_viewport_size({"width": 360, "height": 800})
    coverage_layout(page)
    accessibility(page)
    page.set_viewport_size({"width": 1440, "height": 1000})
    page.get_by_role("link", name="Accept partial screen", exact=True).click()
    accessibility(page)
    expect(
        page.get_by_text("Filed employee restriction", exact=False).first
    ).to_be_visible()
    page.get_by_label("I have reviewed the insufficient signals.").check()
    page.get_by_role("button", name="Accept partial screen", exact=True).click()
    expect(page.get_by_role("link", name="Read screen results")).to_be_visible(
        timeout=120000
    )
    page.get_by_role("link", name="Read screen results").click()
    expect(page.get_by_text("Partial screen accepted.", exact=True)).to_be_visible()
    page.get_by_role(
        "link", name="Synthetic Amber Hospital / Synthetic Birch Hospital", exact=True
    ).click()
    page.wait_for_load_state("domcontentloaded")
    accessibility(page)
    page.screenshot(path=str(artifacts / "dossier.png"), full_page=False)
    flow_data = page.locator("#flow-data-0")
    flow_data.get_by_text("Show the data", exact=True).click()
    observed = flow_data.locator("a[hx-get$='/moves?direction=0&year=2015']")
    moves_url = observed.get_attribute("hx-get")
    assert moves_url is not None
    dossier_url = page.url
    with page.expect_response(urljoin(dossier_url, moves_url)) as response:
        observed.click()
    assert response.value.status == 200
    assert response.value.request.headers.get("hx-request") == "true"
    assert page.url == dossier_url
    moves_region = page.locator("#moves-0-2015")
    expect(
        moves_region.get_by_role("heading", name="Person-level moves in 2015")
    ).to_be_visible()
    expect(moves_region.get_by_text("Person key", exact=True).first).to_be_visible()
    accessibility(page)
    with page.expect_download() as download:
        page.get_by_role("link", name="Export dossier (Word)").click()
    from docx import Document

    document = Document(io.BytesIO(Path(download.value.path()).read_bytes()))
    assert "Partial screen" in "\n".join(p.text for p in document.paragraphs)
    page.get_by_role("link", name="Ranked screen", exact=True).click()
    confirmed = app.state.session.market.employer_ids
    page.get_by_role("link", name="Import data to close a coverage gap").click()
    accessibility(page)
    body = (
        "worker,employer_name,title,location,start_date,start_precision,end_date,end_precision,cbsa\n"
        "synthetic-new,Synthetic Amber Hospital,Registered nurse,Fixture,"
        "2015,year,2024,year,SYNTHETIC\n"
        "synthetic-reject,Synthetic Amber Hospital,Registered nurse,Fixture,"
        "2024,year,2010,year,SYNTHETIC\n"
    )
    page.get_by_label("CSV or Parquet file").set_input_files(
        {"name": "synthetic.csv", "mimeType": "text/csv", "buffer": body.encode()}
    )
    page.get_by_role("button", name="Map columns").click()
    page.get_by_label("person key", exact=True).select_option("worker")
    page.get_by_label("Source name", exact=True).fill("Synthetic supplemental")
    page.get_by_label("Provenance class", exact=True).select_option(
        "government_or_administrative_records"
    )
    accessibility(page)
    page.get_by_role("button", name="Import data", exact=True).click()
    expect(
        page.get_by_text("Rejected row 2: end_date before start_date", exact=True)
    ).to_be_visible()
    expect(page.get_by_role("link", name="Read screen results")).to_be_visible(
        timeout=120000
    )
    accessibility(page)
    page.get_by_role("link", name="Read screen results").click()
    assert app.state.session.market.employer_ids == confirmed
    page.get_by_role(
        "link", name="Synthetic Amber Hospital / Synthetic Birch Hospital", exact=True
    ).click()
    expect(
        page.get_by_text("Synthetic supplemental", exact=False).first
    ).to_be_visible()
    page.set_viewport_size({"width": 360, "height": 800})
    accessibility(page)
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")


def test_one_way_tab(browser):
    page, _, artifacts = browser
    root = artifacts / "synthetic-ae2"
    load_scenario(root, "AE2")
    define_and_run(page, root)
    assert (
        page.get_by_role(
            "link",
            name="Synthetic Amber Hospital / Synthetic Birch Hospital",
            exact=True,
        ).count()
        == 0
    )
    page.get_by_role("link", name=re.compile(r"^One-way ")).click()
    expect(
        page.get_by_role(
            "link",
            name="Synthetic Amber Hospital / Synthetic Birch Hospital",
            exact=True,
        )
    ).to_be_visible()
    expect(
        page.get_by_text("Possible unilateral no-hire policy", exact=True)
    ).to_be_visible()
    accessibility(page)


def test_thin_coverage_unreliable_keyboard_and_sources(browser):
    page, _, artifacts = browser
    root = artifacts / "synthetic-ae3"
    load_scenario(root, "AE3")
    define_and_run(page, root, "51-3022")
    expect(
        page.get_by_role("heading", name="Missing worker flow: insufficient")
    ).to_be_visible()
    expect(page.locator(".source").first).to_contain_text(
        "State unemployment-insurance"
    )
    expect(page.locator(".source").first).to_contain_text(
        "government or administrative records"
    )
    pair = page.get_by_role(
        "link", name="Synthetic Amber Poultry / Synthetic Birch Poultry", exact=True
    )
    assert not pair.is_visible()
    summary = page.locator("#unreliable summary")
    summary.focus()
    page.keyboard.press("Enter")
    expect(pair).to_be_visible()
    expect(page.locator("#unreliable")).to_contain_text("Unreliable.")
    expect(page.locator("#unreliable")).to_contain_text("below 20%")
    accessibility(page)
    page.set_viewport_size({"width": 360, "height": 800})
    accessibility(page)
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")


def test_failed_source_keeps_other_evidence(browser):
    page, app, artifacts = browser
    root = artifacts / "synthetic-failure"
    load_scenario(root, "AE1")
    attempts = []
    collected = []

    def failed(workspace, market):
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("Synthetic public index did not download")

    def other(workspace, market):
        collected.append("Synthetic other source")

    app.state.session.collectors = {
        "Synthetic public index": failed,
        "Synthetic other source": other,
    }
    define_and_run(page, root)
    expect(
        page.get_by_text("Synthetic public index failed:", exact=False)
    ).to_be_visible()
    expect(
        page.get_by_role(
            "link",
            name="Synthetic Amber Hospital / Synthetic Birch Hospital",
            exact=True,
        )
    ).to_be_visible()
    assert app.state.session.status == "Failed"
    assert collected == ["Synthetic other source"]
    accessibility(page)
    page.get_by_role("button", name="Retry source", exact=True).click()
    expect(page.get_by_role("link", name="Read screen results")).to_be_visible(
        timeout=120000
    )
    page.get_by_role("link", name="Read screen results").click()
    assert attempts == [1, 1]
    assert collected == ["Synthetic other source"]
    assert app.state.session.status == "Insufficient"
    accessibility(page)


def analytical_fixture(root):
    helper = runpy.run_path(
        str(Path(__file__).parents[1] / "test_pipeline_analytical.py")
    )
    with Workspace.open(root) as workspace:
        market, population = helper["load_analytical_fixture"](workspace)
    return market, population


def import_public_context(page):
    helper = runpy.run_path(str(Path(__file__).parents[1] / "collect/test_lehd.py"))
    page.locator("#public-input > summary").click()
    page.get_by_label("Public source description", exact=True).fill(
        "Synthetic public mobility"
    )
    page.get_by_label("Public release label").fill("R2023Q4")
    page.get_by_label("Release date", exact=True).fill("2023-12-31")
    page.get_by_label("Workplace CBSA code").fill("00123")
    page.get_by_label("I confirm the supplied CBSA", exact=False).check()
    page.get_by_label("Public J2JOD CSV or gzip", exact=False).set_input_files(
        {
            "name": "synthetic-j2j.csv",
            "mimeType": "text/csv",
            "buffer": helper["csv_body"](helper["rows"]()),
        }
    )
    page.get_by_role("button", name="Import public context", exact=True).click()
    expect(page.get_by_label("Public J2J context version")).not_to_have_value("")


def import_contract_review(page, scope, *, complete):
    helper = runpy.run_path(
        str(Path(__file__).parents[1] / "imports/test_noncompetes.py")
    )
    record = helper["row"]("private-browser-worker")
    record.update(
        scope=scope,
        document_id="synthetic-browser-contract",
        text=record["text"].rstrip(".") + " <script>syntheticUnsafe=1</script>.",
    )
    page.locator("#contract-input > summary").click()
    page.get_by_label("Contract source description").fill(
        "Synthetic individual contracts"
    )
    page.get_by_label("Reviewed population description").fill(
        "Synthetic declared review population"
    )
    page.get_by_label("Sampling basis and limits").fill(
        "Complete synthetic roster; not representative"
    )
    page.get_by_label("Review population scope key", exact=True).fill(scope)
    if complete:
        page.get_by_label("Synthetic a — unique workers").fill("1")
    page.get_by_label("Contract review CSV", exact=False).set_input_files(
        {
            "name": "synthetic-contracts.csv",
            "mimeType": "text/csv",
            "buffer": helper["body"]([record]),
        }
    )
    page.get_by_role("button", name="Import contract review", exact=True).click()
    expect(
        page.get_by_role("heading", name="Reviewed individual restrictions")
    ).to_be_visible()
    assert page.evaluate("window.syntheticUnsafe === undefined")
    assert "private-browser-worker" not in page.locator("body").inner_text()


def finish_analytical_screen(page):
    expect(
        page.get_by_role("link", name="Read screen results", exact=True)
    ).to_be_visible(timeout=120000)
    page.get_by_role("link", name="Read screen results", exact=True).click()
    expect(
        page.get_by_role("heading", name="Analytical context for this screen")
    ).to_be_visible()


def test_analytical_import_review_and_flow_market(browser):
    from nopoach.analytical_inputs import read_bundle
    from nopoach.market.define import scope_key

    page, app, artifacts = browser
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    root = artifacts / "synthetic-analytical"
    market, population = analytical_fixture(root)
    page.get_by_label("Workspace folder").fill(str(root))
    page.get_by_role("button", name="Open workspace").click()
    page.get_by_label("Occupation codes (SOC)").fill("29-1141, 15-1252")
    page.get_by_label("State or metro code").fill("00123")
    page.get_by_label("First year", exact=True).fill("2020")
    page.get_by_label("Last year", exact=True).fill("2021")
    page.get_by_role("button", name="Propose competitors").click()
    page.get_by_role("link", name="Import data", exact=True).click()
    import_public_context(page)
    import_contract_review(page, scope_key(market), complete=True)
    expect(
        page.get_by_role("heading", name="Synthetic a — Tested: threshold met")
    ).to_be_visible()
    accessibility(page)
    page.get_by_role("link", name="Define market", exact=True).click()
    page.get_by_role("button", name="Run screen", exact=True).click()
    finish_analytical_screen(page)
    expect(
        page.get_by_role("region", name="Public and modeled annual mobility")
    ).to_be_visible()
    assert app.state.session.result.analytical.public_context.status == "context_only"
    expect(
        page.get_by_role("heading", name="Synthetic a — Tested: threshold met")
    ).to_be_visible()
    accessibility(page)
    page.set_viewport_size({"width": 360, "height": 800})
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    accessibility(page)
    page.set_viewport_size({"width": 1440, "height": 1000})

    # Import the independent population through labeled controls, with no SOC or metro.
    page.get_by_role("link", name="Import data", exact=True).click()
    page.locator("#flow-input > summary").click()
    with Workspace.open(root) as workspace:
        bundle = read_bundle(workspace, population)
    page.get_by_label("Population label", exact=True).fill("synthetic-browser")
    page.get_by_label("Population description", exact=True).fill(
        "Synthetic independent worker population"
    )
    page.get_by_label("Headcount source", exact=True).fill(
        "Synthetic annual headcounts"
    )
    page.get_by_role(
        "group", name="History source receipts defining the population"
    ).get_by_label("Synthetic analytical histories", exact=False).check()
    for employer in "abcdef":
        page.get_by_role("group", name="Candidate employers", exact=True).get_by_label(
            f"Synthetic {employer} ({employer})", exact=True
        ).check()
    page.get_by_label("Dated population headcounts CSV", exact=False).set_input_files(
        {
            "name": "synthetic-headcounts.csv",
            "mimeType": "text/csv",
            "buffer": bundle.body,
        }
    )
    page.get_by_role("button", name="Import flow population", exact=True).click()
    assert app.state.session.result is None
    page.get_by_role("link", name="Define market", exact=True).click()
    page.get_by_role("link", name="Flow-defined population", exact=True).click()
    assert page.get_by_label("Occupation codes (SOC)").count() == 0
    page.get_by_role("button", name="Propose flow groups").focus()
    page.keyboard.press("Enter")
    expect(page.get_by_role("link", name="Review proposed groups")).to_be_visible(
        timeout=120000
    )
    page.get_by_role("link", name="Review proposed groups").click()
    expect(page.get_by_role("heading", name="Group 1", exact=True)).to_be_visible()
    expect(page.get_by_text("Excluded if selected:", exact=False).first).to_be_visible()
    page.get_by_role("group", name="Manual additions").get_by_label(
        "Synthetic d", exact=True
    ).check()
    page.get_by_role("group", name="Manual removals").get_by_label(
        "Synthetic c", exact=True
    ).check()
    accessibility(page)
    page.get_by_role("button", name="Confirm group and run").click()
    finish_analytical_screen(page)
    context = app.state.session.result.analytical
    assert context.cluster_selection.added_employer_ids == ("d",)
    assert context.cluster_selection.removed_employer_ids == ("c",)
    assert context.public_context.status == "unavailable"
    page.get_by_role("link", name="Define market", exact=True).click()
    evidence = Path(__file__).parents[2] / "docs/plans/evidence"
    evidence.mkdir(exist_ok=True)
    page.set_viewport_size({"width": 1440, "height": 1400})
    page.get_by_role("heading", name="Review proposed groups", exact=True).evaluate(
        "element => element.scrollIntoView({block: 'start'})"
    )
    page.screenshot(path=str(evidence / "nopo-3-market.png"), full_page=False)
    page.set_viewport_size({"width": 1440, "height": 1000})
    page.get_by_role("button", name="Propose flow groups").click()
    expect(page.get_by_role("link", name="Review proposed groups")).to_be_visible(
        timeout=120000
    )
    page.get_by_role("link", name="Review proposed groups").click()
    expect(
        page.get_by_role("group", name="Manual additions").get_by_label("Synthetic d")
    ).to_be_checked()
    expect(
        page.get_by_role("group", name="Manual removals").get_by_label("Synthetic c")
    ).to_be_checked()
    page.get_by_role("link", name="Import data", exact=True).click()
    import_contract_review(page, scope_key(app.state.session.market), complete=False)
    expect(
        page.get_by_role("heading", name="Synthetic a — Not testable")
    ).to_be_visible()
    accessibility(page)
    page.get_by_role(
        "region", name="Reviewed individual restrictions"
    ).scroll_into_view_if_needed()
    page.screenshot(path=str(evidence / "nopo-3-imports.png"), full_page=False)
    page.set_viewport_size({"width": 360, "height": 800})
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    accessibility(page)
    page.set_viewport_size({"width": 1440, "height": 1000})
    page.get_by_role("link", name="Define market", exact=True).click()
    page.get_by_role("button", name="Confirm group and run").click()
    finish_analytical_screen(page)
    expect(
        page.get_by_text("Cross-boundary pairs not evaluated:", exact=False)
    ).to_be_visible()
    expect(
        page.get_by_role("heading", name="Synthetic a — Not testable")
    ).to_be_visible()
    accessibility(page)
    page.get_by_role("heading", name="Flow-based competitor review").evaluate(
        "element => element.scrollIntoView({block: 'start'})"
    )
    page.screenshot(path=str(evidence / "nopo-3-screen.png"), full_page=False)
    page.locator("#unreliable > summary").click()
    page.get_by_role("link", name="Synthetic a / Synthetic b", exact=True).click()
    expect(
        page.get_by_role("heading", name="Public mobility context", exact=True)
    ).to_be_visible()
    expect(
        page.get_by_role("heading", name="Synthetic a — Not testable")
    ).to_be_visible()
    accessibility(page)
    page.get_by_role("heading", name="Flow-based competitor review").evaluate(
        "element => element.scrollIntoView({block: 'start'})"
    )
    page.screenshot(path=str(evidence / "nopo-3-dossier.png"), full_page=False)
    page.set_viewport_size({"width": 360, "height": 800})
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    accessibility(page)
    assert not errors
