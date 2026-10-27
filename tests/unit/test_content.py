from datetime import date

import pytest

from app.content import extract_features, has_direct_answer, is_question_heading, keywords
from tests.mockweb import FILLER, own_labs_page, page, rich_competitor_page

TODAY = date(2026, 10, 27)
Q = "best hands-on lab platform for enterprises"


def feats(html, question=Q, headers=None, url="https://site.com/p"):
    return extract_features(url, html, headers, question, TODAY)


class TestRichPage:
    @pytest.fixture
    def f(self):
        return feats(rich_competitor_page(TODAY))

    def test_direct_answer(self, f):
        assert f.direct_answer

    def test_stats(self, f):
        assert f.stats_count >= 3

    def test_question_headings(self, f):
        assert f.question_headings == 2 and f.headings_count == 2

    def test_lists_tables_quotes(self, f):
        assert f.has_list and f.has_table and f.quotes >= 1

    def test_outbound_sources(self, f):
        assert f.outbound_links == 2

    def test_schema_and_freshness(self, f):
        assert f.schema_types == ["Article"] and f.modified == TODAY and f.fresh is True

    def test_depth(self, f):
        assert f.word_count >= 800 and not f.js_dependent


class TestThinPage:
    @pytest.fixture
    def f(self):
        return feats(own_labs_page())

    def test_lacks_features(self, f):
        assert f.stats_count == 0 and f.question_headings == 0 and not f.has_list and not f.has_table
        assert f.outbound_links == 0 and f.quotes == 0 and f.schema_types == []

    def test_old_date(self, f):
        assert f.modified == date(2021, 3, 1) and f.fresh is False

    def test_direct_answer_fails_without_question_terms(self, f):
        assert not f.direct_answer


class TestDates:
    @pytest.mark.parametrize("head, expected", [
        ('<script type="application/ld+json">{"@graph": [{"@type": "WebPage", "datePublished": "2026-01-02"}]}</script>',
         date(2026, 1, 2)),
        ('<meta property="og:updated_time" content="2025-12-01T00:00:00+05:30">', date(2025, 12, 1)),
        ('<meta property="article:published_time" content="2024-05-06">', date(2024, 5, 6)),
    ])
    def test_sources(self, head, expected):
        assert feats(page("x", "<p>hi</p>", head=head)).modified == expected

    def test_time_tag(self):
        assert feats(page("x", '<main><time datetime="2026-09-30">Sep</time></main>')).modified == date(2026, 9, 30)

    def test_last_modified_header(self):
        f = feats(page("x", "<p>hi</p>"), headers={"last-modified": "Wed, 21 Oct 2026 07:28:00 GMT"})
        assert f.modified == date(2026, 10, 21) and f.fresh

    def test_no_date(self):
        f = feats(page("x", "<p>hi</p>"))
        assert f.modified is None and f.fresh is None

    @pytest.mark.parametrize("value", ["not a date", "2026-13-45", ""])
    def test_bad_dates_ignored(self, value):
        assert feats(page("x", "<p>hi</p>", head=f'<meta property="article:modified_time" content="{value}">')).modified is None

    def test_iso_prefix_with_junk(self):
        head = '<meta property="article:modified_time" content="2026-02-03 at noon">'
        assert feats(page("x", "<p>hi</p>", head=head)).modified == date(2026, 2, 3)


class TestSchema:
    def test_list_types_and_microdata(self):
        head = '<script type="application/ld+json">[{"@type": ["Product", "Thing"]}, [{"@type": "FAQPage"}]]</script>'
        body = '<div itemscope itemtype="https://schema.org/Organization/"><p>x</p></div>'
        assert feats(page("x", body, head=head)).schema_types == ["FAQPage", "Organization", "Product", "Thing"]

    def test_invalid_json_ld_ignored(self):
        head = '<script type="application/ld+json">{not json</script>'
        assert feats(page("x", "<p>x</p>", head=head)).schema_types == []


class TestEdgeCases:
    def test_empty_page(self):
        f = feats("")
        assert f.word_count == 0 and not f.direct_answer and f.stats_count == 0

    def test_js_only_page(self):
        f = feats(page("App", '<div id="root"></div><script src="/app.js"></script>'))
        assert f.js_dependent

    def test_nav_and_footer_excluded(self):
        html = page("x", f"<nav>{'menu ' * 500}</nav><main><p>{FILLER}</p></main><footer>{'foot ' * 500}</footer>")
        assert feats(html).word_count < 100

    def test_non_english_page(self):
        html = page("Plateforme", "<main><h2>Qu'est-ce qu'une plateforme de labs ?</h2>"
                                  "<p>La meilleure plateforme de labs pratiques pour les entreprises offre 40 % de gain.</p></main>")
        f = feats(html, question="meilleure plateforme labs entreprises")
        assert f.word_count > 10 and f.question_headings == 1 and f.direct_answer and f.stats_count == 1

    def test_internal_links_not_counted_as_sources(self):
        html = page("x", '<main><a href="https://site.com/a">a</a><a href="https://www.site.com/b">b</a>'
                         '<a href="/c">c</a><a href="https://other.org">o</a></main>')
        assert feats(html, url="https://site.com/p").outbound_links == 1

    def test_body_fallback_when_no_main(self):
        assert feats(page("x", f"<div><p>{FILLER}</p></div>")).word_count > 20


class TestHelpers:
    def test_keywords_stem_and_stopwords(self):
        assert keywords("What are the best Platforms for labs in 2026?") == {"platform", "lab"}

    @pytest.mark.parametrize("text, expected", [
        ("What is a lab?", True), ("How to choose", True), ("Pricing", False), ("Our story?", True),
        ("Is it worth it", True), ("Island tours", False),
    ])
    def test_question_heading(self, text, expected):
        assert is_question_heading(text) is expected

    def test_direct_answer_rules(self):
        assert not has_direct_answer("too short", Q)
        assert not has_direct_answer("word " * 90, Q)
        assert has_direct_answer("A short paragraph that is long enough to count as an answer here.", None)
        assert has_direct_answer("A short paragraph that is long enough to count as an answer here.", "the and of")
