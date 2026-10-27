"""AI memory check, search-retrieval scoring, and the fix rules built on them."""

import json

import pytest

from app.engines.base import EngineError
from app.engines.fake import SIMULATED_POOL, FakeEngine
from app.memory import PROMPT, memory_check
from app.models import Citation, EngineResult, MemoryResult, QuestionResult
from app.questions import suggest_questions
from app.recommend import memory_fix, recommend, retrieval_fixes
from app.visibility import aggregate, summarize_question


class ScriptedWriter:
    def __init__(self, answers):
        self.answers = list(answers)
        self.prompts = []

    async def generate_text(self, prompt, json_output=False):
        self.prompts.append(prompt)
        item = self.answers.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class TestMemoryCheck:
    async def test_mentions_detected(self):
        writer = ScriptedWriter(["Try Acme Labs or Rival.", "Rival and G2.", "See acme-labs.com for this."])
        out = await memory_check(writer, "model-x", ["q1", "q2", "q3"], "acme-labs", "acme-labs.com")
        assert [m.mentioned for m in out] == [True, False, True]
        assert out[0].model == "model-x" and out[0].answer == "Try Acme Labs or Rival."
        assert writer.prompts[0] == PROMPT.format(question="q1")

    async def test_error_recorded_and_others_continue(self):
        writer = ScriptedWriter([EngineError("quota"), "Acme labs"])
        out = await memory_check(writer, "m", ["q1", "q2"], "acme-labs", "acme-labs.com")
        assert out[0].mentioned is None and "quota" in out[0].error and out[1].mentioned is True

    async def test_limit(self):
        writer = ScriptedWriter(["a", "b"])
        assert len(await memory_check(writer, "m", ["1", "2", "3"], "x", "x.com", limit=2)) == 2

    async def test_long_answer_trimmed(self):
        out = await memory_check(ScriptedWriter(["word " * 1000]), "m", ["q"], "x", "x.com")
        assert len(out[0].answer) <= 2000


def run(q, cited, retrieved, error=None):
    def cites(domains):
        return [Citation(url=f"https://{d}/", domain=d, position=i + 1) for i, d in enumerate(domains)]
    return EngineResult(engine="t", question=q, citations=cites(cited), retrieved=cites(retrieved), error=error)


class TestRetrievalScoring:
    def test_best_rank_across_runs(self):
        qr = summarize_question("q", [run("q", ["a.com"], ["a.com", "b.com", "me.com"]),
                                      run("q", ["a.com"], ["me.com", "a.com"])], "me.com")
        assert qr.retrieval_known and qr.own_retrieved_rank == 1

    def test_not_retrieved(self):
        qr = summarize_question("q", [run("q", ["a.com"], ["a.com"])], "me.com")
        assert qr.retrieval_known and qr.own_retrieved_rank is None

    def test_unknown_when_engine_reports_no_search(self):
        qr = summarize_question("q", [run("q", ["a.com"], [])], "me.com")
        assert not qr.retrieval_known

    def test_failed_runs_ignored(self):
        qr = summarize_question("q", [run("q", [], ["me.com"], error="x")], "me.com")
        assert not qr.retrieval_known and qr.own_retrieved_rank is None

    def test_aggregate_still_counts_citations_only(self):
        score, *_ = aggregate({"q": [run("q", ["a.com"], ["me.com"])]}, "me.com")
        assert score == 0.0


def qres(q, cited=0, rank=None, known=True, ok=1):
    return QuestionResult(question=q, ok_runs=ok, own_cited_runs=cited, own_rate=cited / max(ok, 1),
                          own_retrieved_rank=rank, retrieval_known=known)


class TestRetrievalFixes:
    def test_not_found_and_not_chosen(self):
        fixes = {f.rule_id: f for f in retrieval_fixes(
            [qres("unseen1"), qres("unseen2"), qres("seen", rank=4), qres("won", cited=1, rank=1)])}
        assert fixes["retrieval:not-found"].priority == "high" and fixes["retrieval:not-found"].impact == 2
        assert "“unseen1”; “unseen2”" in fixes["retrieval:not-found"].evidence
        assert fixes["retrieval:not-chosen"].priority == "medium"
        assert fixes["retrieval:not-chosen"].evidence == "#4 in results for “seen”"

    def test_skips_unknown_and_failed(self):
        assert retrieval_fixes([qres("a", known=False), qres("b", ok=0)]) == []


class TestMemoryFix:
    def test_never_mentioned(self):
        fix = memory_fix([MemoryResult(question="q", model="m", mentioned=False),
                          MemoryResult(question="q2", model="m", mentioned=False),
                          MemoryResult(question="q3", model="m", error="x")])
        assert fix.priority == "medium" and fix.evidence == "Mentioned in 0 of 2 answers (m)"

    @pytest.mark.parametrize("memory", [
        [], [MemoryResult(question="q", error="x")],
        [MemoryResult(question="q", mentioned=True), MemoryResult(question="q2", mentioned=False)],
    ])
    def test_no_fix(self, memory):
        assert memory_fix(memory) is None

    def test_wired_into_recommend(self):
        fixes = recommend([], [qres("q")], [], [], 0.0, [MemoryResult(question="q", model="m", mentioned=False)])
        assert [f.rule_id for f in fixes] == ["retrieval:not-found", "memory:unknown"]


class TestFakeRetrieved:
    async def test_simulated_includes_search_results_with_own_site(self):
        r = await FakeEngine(own_site="https://me.com").ask("some question")
        domains = [c.domain for c in r.retrieved]
        assert "me.com" in domains and len(r.retrieved) >= len(r.citations)
        assert all(c.url in SIMULATED_POOL or c.domain == "me.com" for c in r.retrieved)

    async def test_scripted_retrieved(self):
        r = await FakeEngine(script={"q": [["https://a.com"]]}, retrieved={"q": ["https://b.com", "https://a.com"]}).ask("q")
        assert [c.domain for c in r.retrieved] == ["b.com", "a.com"]

    async def test_scripted_without_retrieved_is_empty(self):
        assert (await FakeEngine(script={"q": [["https://a.com"]]}).ask("q")).retrieved == []


class TestSuggestJsonShapes:
    @pytest.mark.parametrize("text, expected", [
        (json.dumps({"questions": ["one q", "two q"]}), ["one q", "two q"]),
        (json.dumps({"items": ["x q"]}), ["x q"]),
        (json.dumps(["bare list q"]), ["bare list q"]),
    ])
    async def test_object_or_list(self, web, text, expected):
        async with web.client() as http:
            qs, source = await suggest_questions(http, "https://acme-labs.com", "acme-labs.com",
                                                 ScriptedWriter([text]), 5)
        assert (qs, source) == (expected, "ai")

    async def test_object_without_list_falls_back(self, web):
        async with web.client() as http:
            _, source = await suggest_questions(http, "https://acme-labs.com", "acme-labs.com",
                                                ScriptedWriter([json.dumps({"n": 1})]), 5)
        assert source == "heuristic"
