import pytest

from app.engines.base import EngineError
from app.engines.fake import SIMULATED_POOL, FakeEngine
from app.engines.paste import MAX_PASTE_CHARS, parse_pasted


class TestPaste:
    def test_markdown_links_and_bare_urls_in_order(self):
        text = ("Top picks: [Pluralsight](https://www.pluralsight.com/labs) and Udemy.\n"
                "Sources:\n[1] https://www.udemy.com/business/. \n[2] https://g2.com/x),")
        r = parse_pasted(text, "best labs", "ChatGPT")
        assert [c.domain for c in r.citations] == ["pluralsight.com", "udemy.com", "g2.com"]
        assert r.citations[0].title == "Pluralsight"
        assert r.citations[1].url == "https://www.udemy.com/business/"
        assert r.citations[2].url == "https://g2.com/x"
        assert r.engine == "ChatGPT" and r.question == "best labs"
        assert "https://" not in r.answer and "Pluralsight" in r.answer

    def test_markdown_url_not_counted_twice(self):
        r = parse_pasted("[a](https://a.com/x) then https://a.com/x again")
        assert len(r.citations) == 1

    def test_no_urls_gives_empty_citations(self):
        r = parse_pasted("An answer with no links at all.")
        assert r.citations == [] and r.ok

    @pytest.mark.parametrize("text", ["", "   \n ", None])
    def test_empty_input_rejected(self, text):
        with pytest.raises(ValueError, match="Paste"):
            parse_pasted(text)  # type: ignore[arg-type]

    def test_too_long_rejected(self):
        with pytest.raises(ValueError, match="too long"):
            parse_pasted("x" * (MAX_PASTE_CHARS + 1))

    def test_junk_and_trailing_punctuation(self):
        r = parse_pasted("see https://example.com/page?. ** http://x **https://b.org/a_*")
        assert [c.url for c in r.citations] == ["https://example.com/page", "https://b.org/a"]

    def test_default_label_and_long_answer_truncated(self):
        r = parse_pasted("word " * 2000 + " https://a.com", engine_label="  ")
        assert r.engine == "pasted answer" and len(r.answer) <= 4000


class TestFakeEngine:
    async def test_scripted_runs_cycle(self):
        engine = FakeEngine(script={"q": [["https://a.com"], ["https://b.com"]]})
        domains = [(await engine.ask("q")).citations[0].domain for _ in range(3)]
        assert domains == ["a.com", "b.com", "a.com"]

    async def test_scripted_exception(self):
        engine = FakeEngine(script={"q": [EngineError("quota")]})
        with pytest.raises(EngineError):
            await engine.ask("q")

    async def test_default_list(self):
        r = await FakeEngine(default=["https://x.com", "https://x.com"], simulated=False).ask("any")
        assert [c.domain for c in r.citations] == ["x.com"] and not r.simulated and r.engine == "fake"

    async def test_simulated_is_deterministic_and_labelled(self):
        a = await FakeEngine(own_site="https://me.com").ask("question one")
        b = await FakeEngine(own_site="https://me.com").ask("question one")
        assert a.citations == b.citations
        assert a.simulated and "simulated" in a.engine
        assert all(c.url in SIMULATED_POOL or c.domain == "me.com" for c in a.citations)

    async def test_simulated_sometimes_includes_own_site(self):
        engine = FakeEngine(own_site="https://me.com")
        seen = set()
        for i in range(30):
            seen |= {c.domain for c in (await engine.ask(f"q{i}")).citations}
        assert "me.com" in seen
