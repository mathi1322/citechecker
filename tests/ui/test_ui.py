"""Browser tests: the real UI in Chromium against a live local server (mocked web + mocked Gemini).

Needs `uv run playwright install chromium` once. Skipped automatically if Chromium is missing.
"""

from __future__ import annotations

import socket
import threading
import time

import pytest
import uvicorn

from app.config import Settings
from app.main import create_app
from app.storage import Storage
from tests.conftest import make_deps
from tests.mockweb import MockWeb
from tests.scenario import Q_LOST, Q_WON, script, script_open

pytestmark = pytest.mark.ui
playwright_api = pytest.importorskip("playwright.sync_api")
expect = playwright_api.expect


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _serve(settings: Settings, web: MockWeb):
    storage = Storage(":memory:")
    app = create_app(deps=make_deps(settings, storage, web))
    port = _free_port()
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.05)
    return srv, thread, f"http://127.0.0.1:{port}"


@pytest.fixture(scope="module")
def server():
    web = MockWeb()
    script(web)
    settings = Settings(gemini_api_key="test-key", db_path=":memory:", runs_per_question=2, concurrency=2,
                        default_questions=3, reports_per_hour=100, checks_per_hour=100)
    srv, thread, url = _serve(settings, web)
    yield url
    srv.should_exit = True
    thread.join(timeout=5)


@pytest.fixture(scope="module")
def open_server():
    web = MockWeb()
    script_open(web)
    settings = Settings(tavily_api_key="t", groq_api_key="g", db_path=":memory:", checks_per_hour=100)
    srv, thread, url = _serve(settings, web)
    yield url
    srv.should_exit = True
    thread.join(timeout=5)


@pytest.fixture(autouse=True)
def _need_browser(request):
    try:
        request.getfixturevalue("browser")
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"Chromium not available: {exc}")


def test_quick_check_cited(page, server):
    page.goto(server)
    page.fill("#quick-form [name=site]", "acme-labs.com")
    page.fill("#quick-form [name=question]", Q_WON)
    page.click("#quick-form button[type=submit]")
    expect(page.locator(".verdict.yes")).to_contain_text("acme-labs.com is cited (source #1)")
    expect(page.locator("#quick-result .sources li.own")).to_have_count(1)


def test_quick_check_not_cited_links_to_full_report(page, server):
    page.goto(server)
    page.fill("#quick-form [name=site]", "acme-labs.com")
    page.fill("#quick-form [name=question]", Q_LOST)
    page.click("#quick-form button[type=submit]")
    expect(page.locator(".verdict.no")).to_contain_text("not among the 3 cited sources")
    page.click("[data-goto=report]")
    expect(page.locator("[data-panel=report]")).to_be_visible()
    expect(page.locator("#report-form [name=site]")).to_have_value("acme-labs.com")


def test_quick_check_shows_validation_error(page, server):
    page.goto(server)
    page.fill("#quick-form [name=site]", "not a site")
    page.fill("#quick-form [name=question]", "valid question here")
    page.click("#quick-form button[type=submit]")
    expect(page.locator("#quick-result .error-msg")).to_contain_text("spaces")


def test_paste_tab(page, server):
    page.goto(server)
    page.click("#tab-paste")
    page.fill("#paste-form [name=site]", "acme-labs.com")
    page.fill("#paste-form [name=engine_label]", "Perplexity")
    page.fill("#paste-form [name=text]", "Options: https://rival.com/a and [Acme](https://acme-labs.com/labs)")
    page.click("#paste-form button[type=submit]")
    expect(page.locator("#paste-result .verdict.yes")).to_contain_text("source #2")


def test_full_report_flow(page, server):
    page.goto(server)
    page.click("#tab-report")
    page.fill("#report-form [name=site]", "acme-labs.com")
    page.click("#suggest-btn")
    expect(page.locator("#report-form [name=questions]")).to_have_value(
        "best hands-on lab platform for enterprises\nhow to choose a tech skills platform")
    page.click("#report-form button[type=submit]")
    page.wait_for_url("**/reports/**")
    expect(page.locator("#score")).to_be_visible(timeout=15000)
    expect(page.locator("h2", has_text="What to fix, in order")).to_be_visible()
    expect(page.locator(".fix").first).to_contain_text("high")
    page.locator("details.question summary").first.click()
    expect(page.locator("table.gap").first).to_be_visible()


@pytest.mark.parametrize("path", ["/", "/reports/missing"])
def test_mobile_width_has_no_horizontal_scroll(browser, server, path):
    ctx = browser.new_context(viewport={"width": 375, "height": 740})
    page = ctx.new_page()
    page.goto(server + path)
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    ctx.close()


def test_dark_mode(browser, server):
    ctx = browser.new_context(color_scheme="dark")
    page = ctx.new_page()
    page.goto(server)
    bg = page.evaluate("getComputedStyle(document.body).backgroundColor")
    assert bg == "rgb(19, 20, 22)"
    ctx.close()


def test_quick_check_shows_search_rank(page, open_server):
    page.goto(open_server)
    page.fill("#quick-form [name=site]", "acme-labs.com")
    page.fill("#quick-form [name=question]", Q_LOST)
    page.click("#quick-form button[type=submit]")
    expect(page.locator(".verdict.no")).to_be_visible()
    expect(page.locator("#quick-result")).to_contain_text("Your site was #3 in the 4 search results the AI read, but it cited others.")
