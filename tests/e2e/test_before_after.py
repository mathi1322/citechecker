"""The whole story through the public API: report -> fix the site -> report again -> compare."""

import time
from dataclasses import replace

from fastapi.testclient import TestClient

from app.main import create_app
from tests.conftest import make_deps
from tests.mockweb import FILLER, page, rich_competitor_page
from tests.scenario import Q_LOST, Q_NO_PAGE, Q_WON, QUESTIONS, script


def build_report(client: TestClient, questions=QUESTIONS) -> dict:
    report_id = client.post("/api/reports", json={"site": "acme-labs.com", "questions": questions}).json()["id"]
    for _ in range(200):
        data = client.get(f"/api/reports/{report_id}").json()
        if data["status"] in ("done", "error"):
            return data
        time.sleep(0.05)
    raise AssertionError("report did not finish")


def test_fixing_the_site_shows_up_in_the_comparison(settings, storage, web):
    script(web)
    app = create_app(deps=make_deps(replace(settings, reports_per_hour=10), storage, web))
    with TestClient(app) as client:
        before = build_report(client)
        assert before["status"] == "done"
        before_rules = {f["rule_id"] for f in before["fixes"]}
        assert {"tech:robots-search-bots", "content:missing-page", "content:stats"} <= before_rules

        # The site owner applies the fixes...
        web.add("https://acme-labs.com/robots.txt", "User-agent: *\nAllow: /\n"
                "Sitemap: https://acme-labs.com/sitemap.xml\n", headers={"content-type": "text/plain"})
        web.add("https://acme-labs.com/labs/hands-on-lab-platform",
                rich_competitor_page(web.today).replace(
                    "<title>", '<link rel="canonical" href="https://acme-labs.com/labs/hands-on-lab-platform"><title>'))
        web.add("https://acme-labs.com/sitemap.xml",
                "<urlset><url><loc>https://acme-labs.com/labs/hands-on-lab-platform</loc></url>"
                "<url><loc>https://acme-labs.com/cheap-cloud-certification-courses</loc></url></urlset>")
        web.add("https://acme-labs.com/cheap-cloud-certification-courses",
                page("Cloud certification courses", f"<main><p>{FILLER * 10}</p></main>"))
        # ...and a month later the AI engine starts citing them (new day -> no cached answers).
        web.answer(Q_LOST, ["own", "rival"])
        web.answer(Q_NO_PAGE, ["g2", "https://acme-labs.com/cheap-cloud-certification-courses"])
        storage._conn.execute("DELETE FROM cache")

        after = build_report(client)
        assert after["status"] == "done"
        assert after["visibility_score"] == 100.0 and before["visibility_score"] < 50

        comparison = client.get("/api/compare", params={"a": before["id"], "b": after["id"]}).json()
        assert comparison["score_change"] > 50
        assert "Let AI search crawlers read your site" in comparison["fixed"]
        assert "Create pages that answer these questions" in comparison["fixed"]
        changes = {q["question"]: q["change"] for q in comparison["questions"]}
        assert changes[Q_LOST] == 1.0 and changes[Q_WON] == 0.0

        html = client.get("/compare", params={"a": before["id"], "b": after["id"]}).text
        assert "Fixed since before" in html and "Let AI search crawlers read your site" in html
        assert f"/compare?a={before['id']}&amp;b={after['id']}" in client.get(f"/reports/{after['id']}").text


def test_demo_mode_end_to_end(settings, storage, web):
    """No API key: answers are simulated but the site checks are real, and everything is labelled."""
    demo = replace(settings, gemini_api_key=None)
    with TestClient(create_app(deps=make_deps(demo, storage, web))) as client:
        assert "simulated" in client.get("/").text
        quick = client.post("/api/check", json={"site": "acme-labs.com", "question": Q_WON}).json()
        assert quick["simulated"] and quick["citations"]
        report = build_report(client, questions=[])  # questions come from the homepage heuristics
        assert report["status"] == "done" and report["simulated"]
        assert report["questions"][0].startswith("What is the best hands-on lab platform")
        assert any(c["id"] == "robots-search-bots" and c["status"] == "fail" for c in report["technical"])
        assert "simulated answers" in client.get(f"/reports/{report['id']}").text
