"""Host-owned Chromium/socket/axe checks: pytest -m browser tests/app/test_e2e.py.

Screenshots go to pytest's temporary artifacts directory, never the repository.
The host must inspect ranked-list.png and dossier.png for reading legibility.
"""

import io
import re
import runpy
import socket
import time
from pathlib import Path
from threading import Thread
from urllib.parse import urlsplit

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
    response = page.goto("/")
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


def test_planted_pair_dossier_import_and_partial_export(browser):
    page, app, artifacts = browser
    root = artifacts / "synthetic-ae1"
    load_scenario(root, "AE1")
    accessibility(page)
    define_and_run(page, root)
    first = page.locator("table").first.locator("tbody tr").first
    expect(first).to_contain_text("Synthetic Amber Hospital / Synthetic Birch Hospital")
    page.screenshot(path=str(artifacts / "ranked-list.png"), full_page=False)
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
    accessibility(page)
    page.screenshot(path=str(artifacts / "dossier.png"), full_page=False)
    page.get_by_text("Show the data", exact=True).first.click()
    page.locator("a[hx-get*='/moves?']").first.click()
    expect(
        page.get_by_role("heading", name="Person-level moves in 2015")
    ).to_be_visible()
    expect(page.get_by_text("Person key", exact=True).first).to_be_visible()
    accessibility(page)
    if page.get_by_role("link", name="Export dossier (Word)").count():
        with page.expect_download() as download:
            page.get_by_role("link", name="Export dossier (Word)").click()
        from docx import Document

        document = Document(io.BytesIO(Path(download.value.path()).read_bytes()))
        assert "Partial screen" in "\n".join(p.text for p in document.paragraphs)
    else:
        expect(page.get_by_text("Export unavailable.", exact=False)).to_be_visible()
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
