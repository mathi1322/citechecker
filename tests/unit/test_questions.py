import json

import pytest

import httpx

from app.engines.gemini import GeminiClient
from app.questions import MAX_QUESTION_CHARS, clean_questions, heuristic_questions, site_summary, suggest_questions
from tests.mockweb import GEMINI_URL, gemini_response, page


class TestClean:
    def test_dedupes_trims_caps(self):
        raw = ["  Best   labs? ", "best labs", "", 3, "x" * 400, "another", "third"]
        out = clean_questions(raw, 3)  # type: ignore[list-item]
        assert out == ["Best labs?", "x" * MAX_QUESTION_CHARS, "another"]


class TestSummary:
    def test_reads_title_description_headings(self):
        html = page("Acme | Lab platform", "<h1>Labs</h1><h2>Why</h2>",
                    head='<meta name="description" content=" Hands-on labs ">')
        assert site_summary(html) == {"title": "Acme | Lab platform", "description": "Hands-on labs",
                                      "headings": "Labs | Why"}

    @pytest.mark.parametrize("head, expected", [
        # JavaScript-framework style: no <title>, Open Graph written with name= (freeCodeCamp's Gatsby site does this)
        ('<meta content="Code Org" name="og:title"/><meta content="Learn to Code" name="og:description"/>',
         {"title": "Code Org", "description": "Learn to Code"}),
        ('<meta property="og:title" content="OG Title"><meta property="og:description" content="OG desc">',
         {"title": "OG Title", "description": "OG desc"}),
        ('<meta name="twitter:title" content="TW"><meta name="twitter:description" content="TW desc">',
         {"title": "TW", "description": "TW desc"}),
        ('<meta name="description" content="  "><meta property="og:description" content="fallback">',
         {"title": "", "description": "fallback"}),
    ])
    def test_meta_fallbacks(self, head, expected):
        html = f"<html><head>{head}</head><body></body></html>"
        out = site_summary(html)
        assert {"title": out["title"], "description": out["description"]} == expected

    def test_title_tag_wins_over_og_title(self):
        html = page("Real Title", "", head='<meta property="og:title" content="OG Title">')
        assert site_summary(html)["title"] == "Real Title"

    def test_og_description_and_empty(self):
        html = page("t", "", head='<meta property="og:description" content="OG text">')
        assert site_summary(html)["description"] == "OG text"
        assert site_summary("") == {"title": "", "description": "", "headings": ""}


class TestHeuristic:
    def test_uses_category_not_brand(self):
        summary = {"title": "Acme | Enterprise skilling platform", "description": "", "headings": ""}
        out = heuristic_questions(summary, "acme", 3)
        assert out == ["What is the best enterprise skilling platform?",
                       "Top enterprise skilling platform options compared",
                       "How do I choose an enterprise skilling platform?"]
        assert all("acme" not in q.lower() for q in out)

    def test_article_before_consonant(self):
        summary = {"title": "Acme | Cloud lab platform", "description": "", "headings": ""}
        assert heuristic_questions(summary, "acme", 3)[2] == "How do I choose a cloud lab platform?"

    def test_falls_back_to_description(self):
        summary = {"title": "Acme", "description": "Cloud cost monitoring for startups.", "headings": ""}
        assert "cloud cost monitoring for startups" in heuristic_questions(summary, "acme", 1)[0]

    def test_nothing_usable(self):
        assert heuristic_questions({"title": "Acme", "description": "", "headings": ""}, "acme", 5) == []


def gemini_with(web, answer_text, status=200):
    def handler(request):
        if str(request.url) == GEMINI_URL:
            if status != 200:
                return httpx.Response(status, json={"error": {"message": "x"}})
            return httpx.Response(200, json=gemini_response(answer_text, []))
        return web.handler(request)
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


class TestSuggest:
    async def test_ai_questions_filtered_for_brand(self, web):
        text = json.dumps(["best lab platform?", "Is Acme-labs good?", "how to choose labs"])
        async with gemini_with(web, text) as http:
            qs, source = await suggest_questions(http, "https://acme-labs.com", "acme-labs.com",
                                                 GeminiClient("k", http=http), 5)
        assert source == "ai" and qs == ["best lab platform?", "how to choose labs"]

    async def test_ai_bad_json_falls_back_to_heuristic(self, web):
        async with gemini_with(web, "not json") as http:
            qs, source = await suggest_questions(http, "https://acme-labs.com", "acme-labs.com",
                                                 GeminiClient("k", http=http), 3)
        assert source == "heuristic" and "hands-on lab platform for enterprise teams" in qs[0]

    async def test_ai_not_a_list_falls_back(self, web):
        async with gemini_with(web, '{"q": 1}') as http:
            _, source = await suggest_questions(http, "https://acme-labs.com", "acme-labs.com",
                                                GeminiClient("k", http=http), 3)
        assert source == "heuristic"

    async def test_ai_error_falls_back(self, web):
        async with gemini_with(web, "", status=429) as http:
            _, source = await suggest_questions(http, "https://acme-labs.com", "acme-labs.com",
                                                GeminiClient("k", http=http, retry_delay=0), 3)
        assert source == "heuristic"

    async def test_no_gemini_uses_heuristic(self, web):
        async with web.client() as http:
            qs, source = await suggest_questions(http, "https://acme-labs.com", "acme-labs.com", None, 2)
        assert source == "heuristic" and len(qs) == 2

    async def test_unreachable_site(self, web):
        async with web.client() as http:
            assert await suggest_questions(http, "https://down.com", "down.com", None) == ([], "none")

    async def test_empty_homepage_skips_ai(self, web):
        web.add("https://blank.com/", "<html></html>")
        async with gemini_with(web, '["x"]') as http:
            assert await suggest_questions(http, "https://blank.com", "blank.com",
                                           GeminiClient("k", http=http)) == ([], "none")
