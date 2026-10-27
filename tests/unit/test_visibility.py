import pytest

from app.engines.base import FRIENDLY_ERRORS, EngineError
from app.engines.fake import FakeEngine
from app.limits import DailyBudget
from app.models import Citation, EngineResult
from app.visibility import AskService, aggregate, run_questions, summarize_question


def result(q, domains, error=None):
    cites = [Citation(url=f"https://{d}/p{i}", domain=d, position=i + 1) for i, d in enumerate(domains)]
    return EngineResult(engine="t", question=q, citations=cites, error=error)


class CostlyFake(FakeEngine):
    costly = True


class TestAskService:
    async def test_cache_hit_skips_engine(self, storage):
        engine = FakeEngine(default=["https://a.com"], simulated=False)
        svc = AskService(engine, storage, day=lambda: "2026-10-27")
        first = await svc.ask("q", 0)
        second = await svc.ask("Q ", 0)  # same question after normalising case/space
        assert first == second and engine.calls["q"] == 1 and engine.calls.get("Q ", 0) == 0

    async def test_cache_separates_runs_and_days(self, storage):
        engine = FakeEngine(default=["https://a.com"], simulated=False)
        day = {"d": "2026-10-27"}
        svc = AskService(engine, storage, day=lambda: day["d"])
        await svc.ask("q", 0)
        await svc.ask("q", 1)
        day["d"] = "2026-10-28"
        await svc.ask("q", 0)
        assert engine.calls["q"] == 3

    async def test_simulated_results_not_cached(self, storage):
        engine = FakeEngine()
        svc = AskService(engine, storage)
        await svc.ask("q")
        await svc.ask("q")
        assert engine.calls["q"] == 2

    async def test_budget_consumed_for_costly_engine(self, storage):
        budget = DailyBudget(storage, limit=1, day=lambda: "d")
        svc = AskService(CostlyFake(default=["https://a.com"], simulated=False), None, budget)
        assert (await svc.ask("q1")).ok
        out = await svc.ask("q2")
        assert out.error == FRIENDLY_ERRORS["budget"] and budget.remaining() == 0

    async def test_free_engine_ignores_budget(self, storage):
        budget = DailyBudget(storage, limit=0, day=lambda: "d")
        assert (await AskService(FakeEngine(), None, budget).ask("q")).ok

    async def test_engine_error_becomes_result(self):
        svc = AskService(FakeEngine(script={"q": [EngineError("quota")]}))
        out = await svc.ask("q")
        assert out.error == FRIENDLY_ERRORS["quota"] and not out.ok

    async def test_unexpected_exception_becomes_result(self):
        svc = AskService(FakeEngine(script={"q": [RuntimeError("bug")]}))
        assert (await svc.ask("q")).error == "Unexpected error: RuntimeError"

    async def test_failed_results_not_cached(self, storage):
        engine = FakeEngine(script={"q": [EngineError("server"), ["https://a.com"]]}, simulated=False)
        svc = AskService(engine, storage)
        assert not (await svc.ask("q")).ok
        assert (await svc.ask("q")).ok


class TestRunQuestions:
    async def test_runs_every_question_n_times_with_progress(self):
        engine = FakeEngine(default=["https://a.com"])
        seen = []
        out = await run_questions(AskService(engine), ["a", "b"], runs=3, concurrency=2,
                                  on_progress=lambda d, t: seen.append((d, t)))
        assert {q: len(r) for q, r in out.items()} == {"a": 3, "b": 3}
        assert seen[-1] == (6, 6) and len(seen) == 6

    async def test_async_progress_callback(self):
        calls = []

        async def progress(d, t):
            calls.append(d)
        await run_questions(AskService(FakeEngine()), ["a"], runs=2, on_progress=progress)
        assert sorted(calls) == [1, 2]

    async def test_zero_questions(self):
        assert await run_questions(AskService(FakeEngine()), [], runs=3) == {}


class TestAggregate:
    def test_score_and_rates(self):
        results = {
            "q1": [result("q1", ["me.com", "a.com"]), result("q1", ["a.com"])],  # cited 1/2
            "q2": [result("q2", ["b.com"]), result("q2", ["b.com", "a.com"])],  # cited 0/2
        }
        score, cited, stats, qrs = aggregate(results, "me.com")
        assert score == 25.0 and cited == 1
        by = {s.domain: s for s in stats}
        assert by["a.com"].runs_cited == 3 and by["a.com"].citation_rate == 0.75
        assert by["a.com"].share_of_voice == pytest.approx(3 / 6)
        assert by["me.com"].is_own and by["me.com"].avg_position == 1.0
        assert by["a.com"].avg_position == pytest.approx((2 + 1 + 2) / 3, abs=0.01)
        assert stats[0].domain == "a.com"
        assert qrs[0].own_rate == 0.5 and qrs[1].own_rate == 0.0

    def test_own_domain_added_when_never_cited(self):
        _, cited, stats, _ = aggregate({"q": [result("q", ["a.com"])]}, "me.com")
        own = [s for s in stats if s.is_own]
        assert cited == 0 and own[0].runs_cited == 0 and own[0].avg_position is None

    def test_failed_runs_excluded(self):
        results = {"q": [result("q", ["me.com"]), result("q", [], error="boom")]}
        score, _, _, qrs = aggregate(results, "me.com")
        assert score == 100.0 and qrs[0].ok_runs == 1

    def test_all_runs_failed(self):
        score, cited, stats, qrs = aggregate({"q": [result("q", [], error="x")]}, "me.com")
        assert score is None and cited == 0 and qrs[0].own_rate is None
        assert [s.domain for s in stats] == ["me.com"]

    def test_ties_sorted_by_name(self):
        _, _, stats, _ = aggregate({"q": [result("q", ["z.com", "b.com"])]}, "")
        assert [s.domain for s in stats] == ["b.com", "z.com"]

    def test_repeated_domain_in_one_run_counts_once_for_rate(self):
        _, _, stats, _ = aggregate({"q": [result("q", ["a.com", "a.com"])]}, "me.com")
        a = next(s for s in stats if s.domain == "a.com")
        assert a.runs_cited == 1 and a.share_of_voice == 1.0 and a.avg_position == 1

    def test_third_party_flag(self):
        _, _, stats, _ = aggregate({"q": [result("q", ["g2.com"])]}, "me.com")
        assert next(s for s in stats if s.domain == "g2.com").is_third_party

    def test_empty(self):
        assert aggregate({}, "me.com")[0] is None


def test_summarize_question_top_lists():
    qr = summarize_question("q", [result("q", ["a.com", "b.com"]), result("q", ["b.com"])], "me.com")
    assert qr.top_domains == [("b.com", 2), ("a.com", 1)]
    # each URL appears once, so ties are broken alphabetically
    assert qr.top_urls == ["https://a.com/p0", "https://b.com/p0", "https://b.com/p1"]
