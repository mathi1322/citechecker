from app.models import DomainStat, FeatureGap, GapResult, QuestionResult, TechCheck
from app.recommend import (
    competitor_fix,
    content_fixes,
    off_site_fix,
    recommend,
    technical_fixes,
)


def check(id, status, url=None, label="Homepage: x"):
    return TechCheck(id=id, label=label, status=status, detail=f"{id} detail", url=url)


def gap(key, is_gap, with_=2, total=3):
    return FeatureGap(key=key, label=key, winners_with=with_, winners_total=total, own_has=not is_gap, is_gap=is_gap)


def qr(question, cited=0, top=None, own_url="https://me.com/p", fallback=False, gaps=None, with_gap=True):
    g = GapResult(question=question, own_url=own_url, own_is_fallback=fallback, own_features=None,
                  gaps=gaps or []) if with_gap else None
    return QuestionResult(question=question, ok_runs=3, own_cited_runs=cited, own_rate=cited / 3,
                          top_domains=top or [], gap=g)


def stat(domain, rate, own=False):
    return DomainStat(domain=domain, runs_cited=int(rate * 10), questions_cited=1, citation_rate=rate,
                      share_of_voice=rate, avg_position=1.0, is_own=own)


class TestTechnical:
    def test_fail_and_warn_become_fixes_pass_does_not(self):
        fixes = technical_fixes([check("robots-search-bots", "fail"), check("canonical", "warn"),
                                 check("status", "pass"), check("sitemap", "info")])
        assert {(f.rule_id, f.priority) for f in fixes} == {("tech:robots-search-bots", "high"),
                                                             ("tech:canonical", "medium")}
        assert fixes[0].title == "Let AI search crawlers read your site"

    def test_grouped_per_check_with_pages(self):
        fixes = technical_fixes([check("noindex", "fail", "https://me.com/a"),
                                 check("noindex", "fail", "https://me.com/b")])
        assert len(fixes) == 1 and fixes[0].impact == 2
        assert fixes[0].evidence == "Affects: https://me.com/a, https://me.com/b"

    def test_high_check_that_only_warns_is_medium(self):
        assert technical_fixes([check("status", "warn")])[0].priority == "medium"

    def test_training_bots_low_and_unknown_medium(self):
        fixes = {f.rule_id: f for f in technical_fixes([check("robots-training-bots", "warn"),
                                                         check("brand-new", "fail", label="New thing")])}
        assert fixes["tech:robots-training-bots"].priority == "low"
        assert fixes["tech:brand-new"].priority == "medium" and fixes["tech:brand-new"].title == "New thing"
        assert fixes["tech:brand-new"].evidence == ""


class TestContent:
    def test_missing_page(self):
        fixes = content_fixes([qr("q1", fallback=True), qr("q2", fallback=True, cited=1), qr("q3")])
        missing = [f for f in fixes if f.rule_id == "content:missing-page"]
        assert len(missing) == 1 and missing[0].impact == 1 and "q1" in missing[0].evidence

    def test_feature_gaps_grouped_by_page(self):
        fixes = content_fixes([qr("q1", gaps=[gap("stats", True), gap("quotes", False)]),
                               qr("q2", gaps=[gap("stats", True, 3, 3)])])
        stats = [f for f in fixes if f.rule_id == "content:stats"]
        assert len(stats) == 1 and stats[0].impact == 2 and stats[0].priority == "medium"
        assert stats[0].evidence.startswith("5 of 6 pages")
        assert not [f for f in fixes if f.rule_id == "content:quotes"]

    def test_schema_gap_is_low(self):
        assert content_fixes([qr("q", gaps=[gap("schema", True)])])[0].priority == "low"

    def test_cited_questions_and_missing_gap_skipped(self):
        assert content_fixes([qr("q", cited=2, gaps=[gap("stats", True)]), qr("q2", with_gap=False),
                              qr("q3", own_url=None, gaps=[gap("stats", True)])]) == []


class TestOffSite:
    def test_fires_when_third_party_dominates(self):
        fix = off_site_fix([qr("q", top=[("g2.com", 3), ("reddit.com", 2), ("rival.com", 2)])])
        assert fix and fix.priority == "off-site" and "5 of 7" in fix.evidence and "g2.com" in fix.evidence

    def test_not_when_mostly_companies(self):
        assert off_site_fix([qr("q", top=[("g2.com", 1), ("rival.com", 5)])]) is None

    def test_ignores_questions_you_win_and_empty(self):
        assert off_site_fix([qr("q", cited=3, top=[("g2.com", 5)])]) is None
        assert off_site_fix([]) is None


class TestCompetitor:
    def test_study_leader(self):
        fix = competitor_fix([stat("rival.com", 0.6), stat("me.com", 0.1, own=True)], ["rival.com"])
        assert fix and "rival.com" in fix.title and "you were cited in 10%" in fix.evidence

    def test_not_when_you_lead_or_no_competitors(self):
        assert competitor_fix([stat("me.com", 0.7, own=True), stat("rival.com", 0.6)], ["rival.com"]) is None
        assert competitor_fix([stat("rival.com", 0.6)], []) is None

    def test_without_own_stat(self):
        fix = competitor_fix([stat("rival.com", 0.6)], ["rival.com"])
        assert fix and "you were" not in fix.evidence


class TestRecommend:
    def test_ordering(self):
        fixes = recommend(
            [check("sitemap", "warn"), check("robots-search-bots", "fail"), check("robots-training-bots", "warn")],
            [qr("q", top=[("g2.com", 5)], gaps=[gap("stats", True)])],
            [stat("rival.com", 0.5)], ["rival.com"], 10.0)
        assert [f.priority for f in fixes] == ["high", "medium", "medium", "off-site", "low", "low"]

    def test_keep_strong_when_well_cited(self):
        fixes = recommend([], [qr("q", cited=3)], [stat("me.com", 1.0, own=True)], [], 90.0)
        assert [f.rule_id for f in fixes] == ["keep:strong"]

    def test_no_keep_strong_with_high_fix(self):
        fixes = recommend([check("noindex", "fail")], [], [], [], 95.0)
        assert "keep:strong" not in {f.rule_id for f in fixes}

    def test_nothing_to_say(self):
        assert recommend([], [], [], [], None) == []
