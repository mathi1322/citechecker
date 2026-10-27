from datetime import datetime, timedelta, timezone

import pytest
from starlette.requests import Request

from app.compare import compare_reports
from app.limits import DailyBudget, RateLimiter, client_ip, utc_day
from app.models import Fix, QuestionResult, Report
from app.storage import Storage

NOW = datetime(2026, 10, 27, 10, tzinfo=timezone.utc)


def report(id="r1", domain="me.com", status="done", score=10.0, created=NOW, questions=None, fixes=None):
    return Report(id=id, site=f"https://{domain}", domain=domain, created_at=created, status=status,
                  visibility_score=score, question_results=questions or [], fixes=fixes or [])


class TestStorage:
    def test_report_round_trip_and_update(self, storage):
        storage.save_report(report())
        storage.save_report(report(score=55.0))
        assert storage.get_report("r1").visibility_score == 55.0
        assert storage.get_report("missing") is None

    def test_list_reports_newest_first_per_domain(self, storage):
        storage.save_report(report("old", created=NOW - timedelta(days=2)))
        storage.save_report(report("new"))
        storage.save_report(report("other", domain="x.com"))
        assert [r.id for r in storage.list_reports("me.com")] == ["new", "old"]

    def test_mark_stale_running(self, storage):
        storage.save_report(report("a", status="running"))
        storage.save_report(report("b", status="queued"))
        storage.save_report(report("c"))
        assert storage.mark_stale_running() == 2
        assert storage.get_report("a").status == "error" and "restarted" in storage.get_report("a").error
        assert storage.get_report("c").status == "done"

    def test_cache(self, storage):
        assert storage.cache_get("k") is None
        storage.cache_put("k", {"a": 1})
        storage.cache_put("k", {"a": 2})
        assert storage.cache_get("k") == {"a": 2}

    def test_consume_is_bounded(self, storage):
        assert storage.consume("c", "d1", 2, 3)
        assert not storage.consume("c", "d1", 2, 3)
        assert storage.consume("c", "d1", 1, 3)
        assert storage.count("c", "d1") == 3 and storage.count("c", "d2") == 0

    def test_file_database_persists(self, tmp_path):
        path = str(tmp_path / "sub" / "db.sqlite")
        s = Storage(path)
        s.save_report(report())
        s.close()
        s2 = Storage(path)
        assert s2.get_report("r1") is not None
        s2.close()


class TestLimits:
    def test_rate_limiter_window(self):
        now = {"t": 0.0}
        rl = RateLimiter(2, window=60, clock=lambda: now["t"])
        assert rl.allow("ip") and rl.allow("ip") and not rl.allow("ip")
        assert rl.allow("other")
        assert rl.retry_after("ip") == 61 and rl.retry_after("nobody") == 0
        now["t"] = 60.0
        assert rl.allow("ip")

    def test_daily_budget_rollover(self, storage):
        day = {"d": "2026-10-27"}
        b = DailyBudget(storage, 2, day=lambda: day["d"])
        assert b.try_consume() and b.try_consume() and not b.try_consume()
        assert b.used() == 2 and b.remaining() == 0
        day["d"] = "2026-10-28"
        assert b.remaining() == 2 and b.try_consume()

    def test_utc_day_format(self):
        assert len(utc_day()) == 10 and utc_day()[4] == "-"

    def _request(self, headers=None, client=("1.2.3.4", 1)):
        scope = {"type": "http", "headers": [(k.encode(), v.encode()) for k, v in (headers or {}).items()],
                 "client": client}
        return Request(scope)

    def test_client_ip(self):
        assert client_ip(self._request({"x-forwarded-for": "9.9.9.9, 10.0.0.1"})) == "9.9.9.9"
        assert client_ip(self._request({"x-forwarded-for": "9.9.9.9"}), trust_proxy=False) == "1.2.3.4"
        assert client_ip(self._request()) == "1.2.3.4"
        assert client_ip(self._request(client=None)) == "unknown"


def qres(q, rate):
    return QuestionResult(question=q, own_rate=rate)


class TestCompare:
    def test_before_after(self):
        before = report("b", score=10.0, created=NOW - timedelta(days=30),
                        questions=[qres("q1", 0.0), qres("q2", 1 / 3), qres("old", 0.0)],
                        fixes=[Fix(rule_id="tech:noindex", priority="high", title="Remove noindex", detail=""),
                               Fix(rule_id="content:stats", priority="medium", title="Add stats", detail="")])
        after = report("a", score=40.0, questions=[qres("q1", 2 / 3), qres("q2", None), qres("new", 1.0)],
                       fixes=[Fix(rule_id="content:stats", priority="medium", title="Add stats", detail=""),
                              Fix(rule_id="tech:canonical", priority="medium", title="Fix canonical", detail="")])
        c = compare_reports(after, before)  # order doesn't matter: sorted by date
        assert (c.before_id, c.after_id, c.score_change) == ("b", "a", 30.0)
        deltas = {q.question: q.change for q in c.questions}
        assert deltas == {"q1": pytest.approx(0.667, abs=0.001), "q2": None}
        assert c.fixed == ["Remove noindex"] and c.new_issues == ["Fix canonical"]
        assert c.only_before == ["old"] and c.only_after == ["new"]

    def test_different_domains_rejected(self):
        with pytest.raises(ValueError, match="same website"):
            compare_reports(report("a"), report("b", domain="x.com"))

    def test_missing_scores(self):
        assert compare_reports(report("a", score=None), report("b")).score_change is None
