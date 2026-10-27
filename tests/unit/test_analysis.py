import pytest

"""matching, gap and competitors."""

from app.competitors import derive_competitors, is_third_party, normalize_competitors, third_party_kind
from app.gap import FEATURES, compare
from app.matching import best_page, slug_keywords
from app.models import PageFeatures

HOME = "https://acme.com/"
URLS = [
    "https://acme.com/",
    "https://acme.com/labs/hands-on-lab-platform",
    "https://acme.com/blog/hands-on-labs-for-enterprise-teams-complete-guide-2026",
    "https://acme.com/pricing.html",
    "https://acme.com/",  # duplicate root has no keywords
]


class TestMatching:
    def test_slug_keywords(self):
        assert slug_keywords("https://a.com/blog/Best_Lab-Platforms.html") == {"blog", "lab", "platform"}

    def test_best_match(self):
        url, score, fallback = best_page("best hands-on lab platform", URLS, HOME)
        assert url == "https://acme.com/labs/hands-on-lab-platform" and not fallback and score == 1.0

    def test_tie_prefers_shorter_url(self):
        urls = ["https://acme.com/a/very/long/pricing-page", "https://acme.com/pricing"]
        assert best_page("pricing", urls, HOME)[0] == "https://acme.com/pricing"

    def test_no_match_falls_back_to_homepage(self):
        url, score, fallback = best_page("cheapest flights to paris", URLS, HOME)
        assert (url, fallback) == (HOME, True) and score == 0

    def test_weak_match_falls_back(self):
        url, _, fallback = best_page("pricing for enterprise cloud security compliance training", URLS, HOME)
        assert fallback and url == HOME

    def test_stopword_only_question(self):
        assert best_page("what is the best?", URLS, HOME) == (HOME, 0.0, True)

    def test_no_sitemap(self):
        assert best_page("labs", [], HOME)[2] is True


def feat(**kw):
    return PageFeatures(url=kw.pop("url", "https://w.com"), **kw)


RICH = dict(direct_answer=True, stats_count=5, question_headings=2, fresh=True, has_list=True,
            outbound_links=3, quotes=1, word_count=1200, schema_types=["Article"])


class TestGap:
    def test_majority_features_are_gaps(self):
        winners = [feat(**RICH), feat(**RICH), feat(word_count=100)]
        result = compare("q", feat(url="https://me.com"), winners)
        gaps = {g.key: g for g in result.gaps}
        assert set(gaps) == {k for k, _, _ in FEATURES}
        assert all(g.is_gap for g in gaps.values())
        assert gaps["stats"].winners_with == 2 and gaps["stats"].winners_total == 3
        assert result.own_url == "https://me.com"

    def test_own_has_feature_is_not_gap(self):
        result = compare("q", feat(**RICH), [feat(**RICH)])
        assert not any(g.is_gap for g in result.gaps) and all(g.own_has for g in result.gaps)

    def test_minority_feature_is_not_gap(self):
        winners = [feat(stats_count=5), feat(), feat()]
        gaps = {g.key: g for g in compare("q", feat(), winners).gaps}
        assert not gaps["stats"].is_gap

    def test_unreadable_winners_ignored(self):
        winners = [feat(ok=False, error="HTTP 403"), feat(**RICH), feat(**RICH)]
        gaps = {g.key: g for g in compare("q", feat(), winners).gaps}
        assert gaps["stats"].winners_total == 2 and gaps["stats"].is_gap

    def test_one_page_is_not_a_pattern(self):
        for winners in ([feat(**RICH)], [feat(**RICH), feat()]):
            assert not any(g.is_gap for g in compare("q", feat(), winners).gaps)

    def test_no_readable_winners_means_no_gaps(self):
        result = compare("q", feat(), [feat(ok=False)])
        assert not any(g.is_gap for g in result.gaps)

    def test_unreadable_own_page(self):
        result = compare("q", feat(ok=False), [feat(**RICH), feat(**RICH)])
        assert all(g.own_has is None and not g.is_gap for g in result.gaps)

    def test_missing_own_page_and_fallback_flag(self):
        result = compare("q", None, [], own_is_fallback=True)
        assert result.own_url is None and result.own_is_fallback


class TestCompetitors:
    def test_third_party_kinds(self):
        assert third_party_kind("https://www.g2.com/x") == "review site"
        assert third_party_kind("old.reddit.com") == "community"
        assert third_party_kind("en.wikipedia.org") == "encyclopedia"
        assert is_third_party("youtube.com") and not is_third_party("pluralsight.com")
        assert third_party_kind("https://finance.yahoo.com/news") == "portal"
        assert third_party_kind("www.prnewswire.com") == "press release"
        assert third_party_kind("www.researchgate.net") == "research"

    @pytest.mark.parametrize("domain", ["stanford.edu", "cs.stanford.edu", "data.gov", "ox.ac.uk", "nic.in",
                                        "www.gov.uk", "iitm.ac.in"])
    def test_institutions_are_third_party(self, domain):
        assert third_party_kind(domain) == "education / government"

    def test_lookalike_institution_is_not(self):
        assert third_party_kind("edu-courses.com") is None

    def test_normalize_competitors(self):
        assert normalize_competitors(["https://www.Udemy.com/business", "udemy.com", " ", "nodot", None or ""]) == \
            ["udemy.com"]
        assert normalize_competitors(None) == []

    def test_derive_user_first_then_cited(self):
        ranked = ["g2.com", "me.com", "rival.com", "reddit.com", "other.com", "third.com"]
        out = derive_competitors(ranked, "me.com", ["given.com", "me.com"], limit=2)
        assert out == ["given.com", "rival.com", "other.com"]

    def test_derive_no_duplicates(self):
        assert derive_competitors(["rival.com"], "me.com", ["rival.com"]) == ["rival.com"]
