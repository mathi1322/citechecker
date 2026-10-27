from dataclasses import replace

import httpx
import pytest

from app.config import Settings
from app.engines.fake import FakeEngine
from app.engines.gemini import GeminiClient
from app.engines.open_engine import GroqClient, OpenEngine
from app.jobs import ReportError, ReportRunner, default_deps, features_for, quick_check
from app.fetch import FetchResult
from app.questions import mentions_brand
from tests.conftest import TODAY, make_deps
from tests.scenario import Q_LOST, Q_NO_PAGE, Q_WON, QUESTIONS, script, script_open

BASE, DOMAIN = "https://acme-labs.com", "acme-labs.com"


async def run(deps, questions=QUESTIONS, competitors=None):
    runner = ReportRunner(deps)
    report = runner.create(BASE, DOMAIN, list(questions), competitors or [])
    return await runner.run(report.id)


class TestPipeline:
    async def test_full_scenario(self, deps, web):
        script(web)
        r = await run(deps, competitors=["coursera.org"])
        assert r.status == "done" and r.progress == 1.0 and r.error is None
        assert r.visibility_score == pytest.approx(33.3) and r.questions_cited == 1
        assert "gemini" in r.engine and not r.simulated
        assert r.competitors[:2] == ["coursera.org", "rival.com"]

        by_q = {qr.question: qr for qr in r.question_results}
        assert by_q[Q_WON].own_cited_runs == 2 and by_q[Q_WON].gap is None
        lost = by_q[Q_LOST].gap
        assert lost.own_url == "https://acme-labs.com/labs/hands-on-lab-platform" and not lost.own_is_fallback
        assert len(lost.winner_features) == 3
        assert [w.ok for w in lost.winner_features].count(False) == 1  # reddit returns 403
        gap_keys = {g.key for g in lost.gaps if g.is_gap}
        assert {"stats", "question_headings", "fresh", "lists_tables"} <= gap_keys
        assert by_q[Q_NO_PAGE].gap.own_is_fallback

        tech = {(c.id, c.label): c.status for c in r.technical}
        assert tech[("robots-search-bots", "AI search crawlers")] == "fail"
        assert tech[("sitemap", "XML sitemap")] == "pass"
        # the matched labs page was checked too; it passes everything, so it's one summary line
        assert tech[("page-ok", "Page /labs/hands-on-lab-platform: all checks pass")] == "pass"
        assert not any(label.startswith("Page /labs") and cid != "page-ok" for cid, label in tech)

        rules = [f.rule_id for f in r.fixes]
        assert set(rules[:2]) == {"content:missing-page", "tech:robots-search-bots"}
        assert "offsite:third-party" in rules and "content:stats" in rules
        assert all(f.priority == "high" for f in r.fixes[:2])

    async def test_saved_with_progress_messages(self, deps, web, storage):
        script(web)
        r = await run(deps)
        assert storage.get_report(r.id).status == "done"

    async def test_suggests_questions_when_none_given(self, deps, web):
        r = await run(deps, questions=[])
        assert r.status == "done"
        assert r.questions == ["best hands-on lab platform for enterprises", "how to choose a tech skills platform"]
        # suggestions use a free text model, so only the engine calls count against the budget
        assert deps.budget.used() == len(r.questions) * deps.settings.runs

    async def test_budget_exhausted_suggestion_falls_back_then_fails(self, settings, storage, web):
        deps = make_deps(replace(settings, daily_call_budget=0), storage, web)
        r = await run(deps, questions=[])
        assert r.status == "error" and "daily limit" in r.error
        assert r.questions  # heuristic questions were still written

    async def test_no_questions_possible(self, deps, web):
        web.add("https://acme-labs.com/", "<html><body></body></html>")
        deps.make_gemini = lambda http: None
        r = await run(deps, questions=[])
        assert r.status == "error" and "Add your own questions" in r.error

    async def test_unreachable_site(self, deps, web):
        def down(request):
            raise httpx.ConnectError("down")
        web.routes["GET https://acme-labs.com/"] = down
        r = await run(deps)
        assert r.status == "error" and "Couldn't reach" in r.error

    async def test_site_returning_500_still_reports(self, deps, web):
        web.add("https://acme-labs.com/", "oops", status=500)
        script(web)
        r = await run(deps)
        assert r.status == "done"
        assert any(c.id == "status" and c.status == "fail" for c in r.technical)

    async def test_every_engine_call_fails(self, deps, web):
        for q in QUESTIONS:
            web.answer(q, 429)
        r = await run(deps)
        assert r.status == "error" and "quota is used up" in r.error

    async def test_partial_failures_still_report(self, deps, web):
        script(web)
        web.answer(Q_WON, 500)
        r = await run(deps)
        assert r.status == "done" and r.questions_cited == 0
        assert next(qr for qr in r.question_results if qr.question == Q_WON).own_rate is None

    async def test_unexpected_bug_is_reported_safely(self, deps, web, monkeypatch):
        import app.jobs as jobs

        def boom(*a, **k):
            raise RuntimeError("bug")
        monkeypatch.setattr(jobs, "aggregate", boom)
        script(web)
        r = await run(deps)
        assert r.status == "error" and "Something went wrong" in r.error

    async def test_missing_report(self, deps):
        with pytest.raises(ReportError):
            await ReportRunner(deps).run("nope")

    async def test_simulated_engine_skips_winner_pages(self, settings, storage, web):
        deps = make_deps(settings, storage, web, engine_factory=lambda http, site: FakeEngine(own_site=site))
        r = await run(deps)
        assert r.status == "done" and r.simulated
        assert all(qr.gap is None or qr.gap.winner_features == [] for qr in r.question_results)
        assert not any(u.startswith("https://competitor") for u in (str(x.url) for x in web.requests))

    async def test_start_runs_in_background(self, deps, web):
        script(web)
        runner = ReportRunner(deps)
        report = runner.create(BASE, DOMAIN, QUESTIONS, [])
        task = runner.start(report)
        assert (await task).status == "done"


class TestQuickCheck:
    async def test_cited(self, deps, web):
        script(web)
        result, positions = await quick_check(deps, BASE, DOMAIN, Q_WON)
        assert positions == [1] and result.ok

    async def test_not_cited(self, deps, web):
        script(web)
        _, positions = await quick_check(deps, BASE, DOMAIN, Q_LOST)
        assert positions == []

    async def test_error_returned_not_raised(self, deps, web):
        web.answer("q", 401)
        result, positions = await quick_check(deps, BASE, DOMAIN, "q")
        assert "API key" in result.error and positions == []


class TestDefaultDeps:
    async def _parts(self, settings, storage):
        deps = default_deps(settings, storage)
        async with deps.make_http() as http:
            return deps.make_engine(http, BASE), deps.make_writer(http), deps.make_memory(http)

    async def test_demo_mode(self, storage):
        engine, writer, memory = await self._parts(Settings(), storage)
        assert isinstance(engine, FakeEngine) and writer is None and memory is None

    async def test_gemini(self, storage):
        engine, writer, memory = await self._parts(Settings(gemini_api_key="k"), storage)
        assert engine.name == "gemini" and isinstance(writer, GeminiClient)
        assert memory[1] == "gemini-3.5-flash-lite" and memory[0].model == "gemini-3.5-flash-lite"

    async def test_open_engine_prefers_groq_writer_and_gemini_memory(self, storage):
        engine, writer, memory = await self._parts(
            Settings(tavily_api_key="t", groq_api_key="q", gemini_api_key="g"), storage)
        assert isinstance(engine, OpenEngine) and isinstance(writer, GroqClient)
        assert isinstance(memory[0], GeminiClient)

    async def test_open_engine_memory_falls_back_to_groq(self, storage):
        _, _, memory = await self._parts(Settings(tavily_api_key="t", groq_api_key="q"), storage)
        assert isinstance(memory[0], GroqClient) and memory[1] == "openai/gpt-oss-120b"

    async def test_memory_can_be_turned_off(self, storage):
        _, _, memory = await self._parts(Settings(gemini_api_key="g", memory_questions=0), storage)
        assert memory is None

    async def test_force_demo_disables_writer_and_memory(self, storage):
        _, writer, memory = await self._parts(Settings(gemini_api_key="g", force_demo=True), storage)
        assert writer is None and memory is None


def test_features_for_failed_pages():
    f = features_for(FetchResult(url="u", final_url="u", status=403, error="http_error"), "q", TODAY)
    assert not f.ok and f.error == "HTTP 403"
    f = features_for(FetchResult(url="u", final_url="u", status=None, error="timeout"), "q", TODAY)
    assert f.error == "timeout"


@pytest.mark.parametrize("text, brand, expected", [
    ("Is Acme Labs any good?", "acme-labs", True),
    ("acmelabs review", "acme-labs", True),
    ("best lab platforms", "acme-labs", False),
    ("anything", "", False),
])
def test_mentions_brand(text, brand, expected):
    assert mentions_brand(text, brand) is expected


class TestOpenEnginePipeline:
    @pytest.fixture
    def open_deps(self, storage, web):
        settings = Settings(tavily_api_key="t", groq_api_key="g", concurrency=4, default_questions=5)
        return make_deps(settings, storage, web)

    async def test_full_report(self, open_deps, web):
        script_open(web)
        r = await run(open_deps)
        assert r.status == "done" and r.runs_per_question == 1
        assert r.engine.startswith("open engine (Tavily search + ")
        assert r.visibility_score == pytest.approx(33.3) and r.questions_retrieved == 2
        by_q = {qr.question: qr for qr in r.question_results}
        assert by_q[Q_LOST].own_retrieved_rank == 3 and by_q[Q_NO_PAGE].own_retrieved_rank is None
        assert len(r.memory) == 3 and not any(m.mentioned for m in r.memory)
        assert r.memory[0].model == "openai/gpt-oss-120b"
        rules = {f.rule_id: f for f in r.fixes}
        assert "“cheap cloud certification courses”" in rules["retrieval:not-found"].evidence
        assert rules["retrieval:not-chosen"].evidence.startswith("#3 in results for")
        assert "memory:unknown" in rules
        # only engine calls (Tavily searches) count against the budget: 3 questions x 1 run
        assert open_deps.budget.used() == 3

    async def test_engine_concurrency_is_capped(self, open_deps, web, monkeypatch):
        import app.jobs as jobs
        seen = {}
        real = jobs.run_questions

        async def spy(service, questions, runs, concurrency, progress):
            seen["concurrency"] = concurrency
            return await real(service, questions, runs, concurrency, progress)
        monkeypatch.setattr(jobs, "run_questions", spy)
        script_open(web)
        await run(open_deps)
        assert seen["concurrency"] == 1

    async def test_search_quota_used_up(self, open_deps, web):
        for q in QUESTIONS:
            web.search(q, 432)
        r = await run(open_deps)
        assert r.status == "error" and "quota is used up" in r.error

    async def test_daily_budget_protects_tavily_credits(self, storage, web):
        deps = make_deps(Settings(tavily_api_key="t", groq_api_key="g"), storage, web)
        assert deps.budget.limit == 30

    async def test_memory_failure_does_not_fail_report(self, open_deps, web):
        script_open(web)
        real = web._groq

        def flaky(request):
            if "without searching the web" in request.content.decode():
                return __import__("httpx").Response(429, json={"error": {"message": "TPD"}})
            return real(request)
        web._groq = flaky
        r = await run(open_deps)
        assert r.status == "done" and all(m.mentioned is None and m.error for m in r.memory)
        assert "memory:unknown" not in {f.rule_id for f in r.fixes}

    async def test_memory_mentions_brand(self, open_deps, web):
        script_open(web)
        web.memory_answer = "Acme Labs is a strong choice."
        r = await run(open_deps)
        assert all(m.mentioned for m in r.memory)


async def test_problem_pages_list_only_their_problems(deps, web):
    script(web)
    web.add("https://acme-labs.com/labs/hands-on-lab-platform",
            web.routes["GET https://acme-labs.com/labs/hands-on-lab-platform"][2].replace(
                "<title>", '<meta name="robots" content="noindex"><title>'))
    r = await run(deps)
    page_checks = [c for c in r.technical if c.label.startswith("Page /labs")]
    assert [(c.id, c.status) for c in page_checks] == [("noindex", "fail")]
    assert any(f.rule_id == "tech:noindex" and f.priority == "high" for f in r.fixes)


async def test_cited_pages_follow_robots_txt(deps, web):
    script(web)
    web.add("https://rival.com/robots.txt", "User-agent: *\nDisallow: /guide/\n")
    r = await run(deps)
    lost = next(qr for qr in r.question_results if qr.question == Q_LOST)
    rival = next(w for w in lost.gap.winner_features if "rival.com" in w.url)
    assert not rival.ok and "robots.txt" in rival.error
    assert not any(str(x.url) == "https://rival.com/guide/best-hands-on-lab-platforms" for x in web.requests)
