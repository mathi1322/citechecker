import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.main import DEMO_ID, create_app, load_demo
from app.models import Report
from tests.conftest import make_deps
from tests.scenario import Q_LOST, Q_WON, QUESTIONS, script, script_open


@pytest.fixture
def make_client(settings, storage, web, tmp_path):
    def build(demo_report: Report | None = None, **overrides):
        s = replace(settings, **overrides)
        demo_path = tmp_path / "demo.json"
        if demo_report is not None:
            demo_path.write_text(demo_report.model_dump_json())
        return TestClient(create_app(deps=make_deps(s, storage, web), demo_path=demo_path))
    return build


@pytest.fixture
def client(make_client, web):
    script(web)
    with make_client() as c:
        yield c


def wait_done(client, report_id, timeout=10.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        data = client.get(f"/api/reports/{report_id}").json()
        if data["status"] in ("done", "error"):
            return data
        time.sleep(0.05)
    raise AssertionError("report did not finish")


class TestPages:
    def test_index(self, client):
        r = client.get("/")
        assert r.status_code == 200 and "Are AI answers citing your website?" in r.text
        assert "Demo mode" not in r.text and "Example report" not in r.text

    def test_index_demo_mode_banner(self, make_client):
        with make_client(gemini_api_key=None) as c:
            assert "simulated" in c.get("/").text

    def test_static_files(self, client):
        assert client.get("/static/app.js").status_code == 200
        assert "--bg" in client.get("/static/style.css").text

    def test_unknown_report_page(self, client):
        r = client.get("/reports/nope")
        assert r.status_code == 404 and "Not found" in r.text

    def test_demo_redirect_without_demo(self, client):
        r = client.get("/demo", follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/"

    def test_source_link_only_when_configured(self, client, make_client):
        assert "source code" not in client.get("/").text
        with make_client(source_url="https://github.com/me/citecheck") as c:
            assert 'href="https://github.com/me/citecheck"' in c.get("/").text

    def test_healthz(self, client):
        assert client.get("/healthz").json() == {"ok": True, "engine": "gemini", "demo_mode": False,
                                                "budget_remaining": 450}


class TestQuickCheck:
    def test_cited(self, client):
        data = client.post("/api/check", json={"site": "www.acme-labs.com", "question": Q_WON}).json()
        assert data["cited"] and data["positions"] == [1] and data["domain"] == "acme-labs.com"
        assert data["citations"][0]["url"] == "https://acme-labs.com/labs/hands-on-lab-platform"

    def test_not_cited(self, client):
        data = client.post("/api/check", json={"site": "acme-labs.com", "question": Q_LOST}).json()
        assert not data["cited"] and [c["domain"] for c in data["citations"]] == ["rival.com", "g2.com", "reddit.com"]

    def test_engine_error_is_in_body(self, client, web):
        web.answer("broken question", 429)
        data = client.post("/api/check", json={"site": "acme-labs.com", "question": "broken question"}).json()
        assert "quota is used up" in data["error"] and not data["cited"]

    @pytest.mark.parametrize("body, status, needle", [
        ({"site": "not a site", "question": "valid question"}, 422, "spaces"),
        ({"site": "acme-labs.com", "question": "x"}, 422, ""),
        ({"site": "acme-labs.com"}, 422, ""),
        ({"site": "acme-labs.com", "question": "q" * 301}, 422, ""),
    ])
    def test_validation(self, client, body, status, needle):
        r = client.post("/api/check", json=body)
        assert r.status_code == status and needle in str(r.json()["detail"])

    def test_rate_limited_per_ip(self, make_client, web):
        script(web)
        with make_client(checks_per_hour=2) as c:
            body = {"site": "acme-labs.com", "question": Q_WON}
            assert c.post("/api/check", json=body).status_code == 200
            assert c.post("/api/check", json=body).status_code == 200
            r = c.post("/api/check", json=body)
            assert r.status_code == 429 and "limit" in r.json()["detail"]
            other = c.post("/api/check", json=body, headers={"x-forwarded-for": "5.6.7.8"})
            assert other.status_code == 200

    def test_daily_budget_exhausted(self, make_client, web):
        script(web)
        with make_client(daily_call_budget=1) as c:
            c.post("/api/check", json={"site": "acme-labs.com", "question": Q_WON})
            data = c.post("/api/check", json={"site": "acme-labs.com", "question": Q_LOST}).json()
            assert "daily limit" in data["error"]
            assert c.get("/healthz").json()["budget_remaining"] == 0

    def test_cached_answer_does_not_use_budget(self, client):
        body = {"site": "acme-labs.com", "question": Q_WON}
        client.post("/api/check", json=body)
        client.post("/api/check", json=body)
        assert client.get("/healthz").json()["budget_remaining"] == 449


class TestQuestions:
    def test_ai_suggestions(self, client):
        data = client.post("/api/questions", json={"site": "acme-labs.com"}).json()
        assert data["source"] == "ai" and len(data["questions"]) == 2

    def test_heuristic_without_a_text_model(self, make_client):
        with make_client(force_demo=True) as c:
            data = c.post("/api/questions", json={"site": "acme-labs.com"}).json()
            assert data["source"] == "heuristic" and data["questions"]

    def test_unknown_site(self, client):
        assert client.post("/api/questions", json={"site": "unknown-site.com"}).json() == {
            "site": "https://unknown-site.com", "domain": "unknown-site.com", "questions": [], "source": "none"}


class TestReports:
    def test_lifecycle(self, client):
        r = client.post("/api/reports", json={"site": "acme-labs.com", "questions": QUESTIONS,
                                              "competitors": ["https://www.coursera.org"]})
        assert r.status_code == 202
        body = r.json()
        assert body["url"] == f"/reports/{body['id']}"
        data = wait_done(client, body["id"])
        assert data["status"] == "done" and data["visibility_score"] == pytest.approx(33.3)
        assert data["competitors"][0] == "coursera.org"

        page = client.get(body["url"])
        assert page.status_code == 200
        for text in ["Visibility score", "What to fix, in order", "Who AI engines cite", "Question by question",
                     "Can AI crawlers read your site?", "rival.com", "Create pages that answer these questions"]:
            assert text in page.text

    def test_running_page_shows_progress(self, client, storage):
        report = Report(id="run1", site="https://acme-labs.com", domain="acme-labs.com",
                        created_at=datetime.now(timezone.utc), status="running", progress=0.4, message="Asking")
        storage.save_report(report)
        page = client.get("/reports/run1").text
        assert 'data-report-id="run1"' in page and "width: 40%" in page

    def test_error_page(self, client, storage):
        storage.save_report(Report(id="bad", site="https://x.com", domain="x.com",
                                   created_at=datetime.now(timezone.utc), status="error", error="Boom happened"))
        assert "Boom happened" in client.get("/reports/bad").text

    def test_too_many_questions(self, client):
        r = client.post("/api/reports", json={"site": "acme-labs.com", "questions": [f"q{i}" for i in range(21)]})
        assert r.status_code == 422 and "at most 20" in r.json()["detail"]

    def test_duplicate_questions_collapsed(self, client):
        r = client.post("/api/reports", json={"site": "acme-labs.com", "questions": [Q_WON, Q_WON.upper()]})
        data = wait_done(client, r.json()["id"])
        assert data["questions"] == [Q_WON]

    def test_bad_site(self, client):
        assert client.post("/api/reports", json={"site": "ftp://x.com"}).status_code == 422

    def test_too_many_competitors(self, client):
        r = client.post("/api/reports", json={"site": "acme-labs.com", "competitors": ["a.com"] * 11})
        assert r.status_code == 422

    def test_report_rate_limit(self, make_client, web):
        script(web)
        with make_client(reports_per_hour=1) as c:
            first = c.post("/api/reports", json={"site": "acme-labs.com", "questions": [Q_WON]})
            assert first.status_code == 202
            wait_done(c, first.json()["id"])
            assert c.post("/api/reports", json={"site": "acme-labs.com", "questions": [Q_WON]}).status_code == 429

    def test_unknown_report_json(self, client):
        assert client.get("/api/reports/nope").status_code == 404


class TestPaste:
    def test_cited(self, client):
        text = "Try [Acme](https://www.acme-labs.com/labs) or https://rival.com/x."
        data = client.post("/api/paste", json={"site": "acme-labs.com", "text": text, "engine_label": "ChatGPT"}).json()
        assert data["cited"] and data["positions"] == [1] and data["engine"] == "ChatGPT"

    def test_not_cited_default_label(self, client):
        data = client.post("/api/paste", json={"site": "acme-labs.com", "text": "see https://rival.com"}).json()
        assert not data["cited"] and data["engine"] == "pasted answer"

    def test_empty_paste(self, client):
        r = client.post("/api/paste", json={"site": "acme-labs.com", "text": "  "})
        assert r.status_code == 422 and "Paste" in r.json()["detail"]


def finished(id, score, days_ago, domain="acme-labs.com", status="done"):
    return Report(id=id, site=f"https://{domain}", domain=domain, status=status, visibility_score=score,
                  created_at=datetime.now(timezone.utc) - timedelta(days=days_ago))


class TestCompare:
    def test_compare_page_and_json(self, client, storage):
        storage.save_report(finished("before", 10.0, 30))
        storage.save_report(finished("after", 40.0, 0))
        data = client.get("/api/compare", params={"a": "before", "b": "after"}).json()
        assert data["score_change"] == 30.0
        page = client.get("/compare", params={"a": "before", "b": "after"})
        assert page.status_code == 200 and "+30" in page.text

    def test_history_links_on_report_page(self, client, storage):
        storage.save_report(finished("before", 10.0, 30))
        storage.save_report(finished("after", 40.0, 0))
        assert "/compare?a=before&amp;b=after" in client.get("/reports/after").text

    @pytest.mark.parametrize("a, b, status", [("before", "missing", 404), ("before", "running", 409),
                                              ("before", "other", 422)])
    def test_compare_errors(self, client, storage, a, b, status):
        storage.save_report(finished("before", 10.0, 30))
        storage.save_report(finished("running", None, 0, status="running"))
        storage.save_report(finished("other", 5.0, 0, domain="other.com"))
        assert client.get("/api/compare", params={"a": a, "b": b}).status_code == status
        assert client.get("/compare", params={"a": a, "b": b}).status_code == status


class TestDemo:
    def test_demo_report_loaded_on_startup(self, make_client):
        with make_client(demo_report=finished("whatever", 25.0, 1)) as c:
            assert "Example report" in c.get("/").text
            r = c.get("/demo", follow_redirects=False)
            assert r.headers["location"] == f"/reports/{DEMO_ID}"
            assert c.get(f"/api/reports/{DEMO_ID}").json()["visibility_score"] == 25.0

    def test_invalid_demo_file_ignored(self, storage, tmp_path):
        bad = tmp_path / "bad.json"
        bad.write_text("{not json")
        assert load_demo(storage, bad) is False
        assert load_demo(storage, tmp_path / "missing.json") is False

    def test_stale_reports_marked_on_startup(self, make_client, storage):
        storage.save_report(finished("stuck", None, 0, status="running"))
        with make_client():
            assert storage.get_report("stuck").status == "error"


def test_create_app_default_settings(monkeypatch, tmp_path):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("DB_PATH", str(tmp_path / "x.db"))
    with TestClient(create_app()) as c:
        assert c.get("/healthz").json()["demo_mode"] is True


class TestOpenEngineApi:
    @pytest.fixture
    def open_client(self, make_client, web):
        script_open(web)
        with make_client(gemini_api_key=None, tavily_api_key="t", groq_api_key="g", runs_per_question=None) as c:
            yield c

    def test_healthz_reports_engine_and_budget(self, open_client):
        assert open_client.get("/healthz").json() == {"ok": True, "engine": "open", "demo_mode": False,
                                                      "budget_remaining": 30}

    def test_quick_check_seen_not_chosen(self, open_client):
        data = open_client.post("/api/check", json={"site": "acme-labs.com", "question": Q_LOST}).json()
        assert not data["cited"] and data["retrieved_rank"] == 3 and len(data["retrieved"]) == 4
        assert data["engine"].startswith("open engine")

    def test_quick_check_cited(self, open_client):
        data = open_client.post("/api/check", json={"site": "acme-labs.com", "question": Q_WON}).json()
        assert data["cited"] and data["retrieved_rank"] == 1

    def test_quick_check_not_retrieved(self, open_client):
        data = open_client.post("/api/check", json={"site": "acme-labs.com", "question": "anything else"}).json()
        assert data["retrieved_rank"] is None and data["retrieved"]

    def test_questions_from_groq(self, open_client):
        data = open_client.post("/api/questions", json={"site": "acme-labs.com"}).json()
        assert data["source"] == "ai" and len(data["questions"]) == 2

    def test_report_page_shows_search_and_memory(self, open_client):
        rid = open_client.post("/api/reports", json={"site": "acme-labs.com", "questions": QUESTIONS}).json()["id"]
        assert wait_done(open_client, rid)["status"] == "done"
        page = open_client.get(f"/reports/{rid}").text
        for text in ["Found by the AI's search", "Named from memory", "What the AI knows about you without searching",
                     "yes, #3, but it cited others", "not in the results", "Get into the search results first",
                     "Each question is asked once", "CiteCheck's open engine"]:
            assert text in page, text
        index = open_client.get("/").text
        assert "times each" not in index and "Perplexity and ChatGPT search" in index
