"""Ask the engine every question several times and turn the answers into visibility numbers."""

from __future__ import annotations

import asyncio
import logging
from collections import Counter, defaultdict
from collections.abc import Awaitable, Callable

from app.citations import is_own
from app.competitors import is_third_party
from app.engines.base import FRIENDLY_ERRORS, Engine, EngineError
from app.limits import DailyBudget, utc_day
from app.models import DomainStat, EngineResult, QuestionResult
from app.storage import Storage

ProgressFn = Callable[[int, int], Awaitable[None] | None]
log = logging.getLogger("citecheck")


class AskService:
    """Wraps an engine with the answer cache and the daily budget. `ask` never raises: a failed
    run comes back as an EngineResult with `error` set, so one bad call can't sink a whole report."""

    def __init__(
        self,
        engine: Engine,
        storage: Storage | None = None,
        budget: DailyBudget | None = None,
        day: Callable[[], str] = utc_day,
    ):
        self.engine = engine
        self.storage = storage
        self.budget = budget
        self.day = day

    def _key(self, question: str, run: int) -> str:
        return f"{self.engine.name}|{self.day()}|{run}|{' '.join(question.lower().split())}"

    async def ask(self, question: str, run: int = 0) -> EngineResult:
        use_cache = self.storage is not None and not self.engine.simulated
        if use_cache:
            cached = self.storage.cache_get(self._key(question, run))
            if cached is not None:
                return EngineResult.model_validate(cached)
        if self.engine.costly and self.budget is not None and not self.budget.try_consume(1):
            return EngineResult(engine=self.engine.name, question=question, error=FRIENDLY_ERRORS["budget"])
        try:
            result = await self.engine.ask(question)
        except EngineError as exc:
            log.warning("engine %s failed on %r: %s", self.engine.name, question, exc)
            return EngineResult(engine=self.engine.name, question=question, error=exc.friendly)
        except Exception as exc:  # noqa: BLE001  (a bug in one run shouldn't kill the report)
            return EngineResult(engine=self.engine.name, question=question,
                                error=f"Unexpected error: {type(exc).__name__}")
        if use_cache:
            self.storage.cache_put(self._key(question, run), result.model_dump(mode="json"))
        return result


async def run_questions(
    service: AskService,
    questions: list[str],
    runs: int,
    concurrency: int = 4,
    on_progress: ProgressFn | None = None,
) -> dict[str, list[EngineResult]]:
    sem = asyncio.Semaphore(max(1, concurrency))
    total = len(questions) * runs
    done = 0
    results: dict[str, list[EngineResult | None]] = {q: [None] * runs for q in questions}

    async def one(q: str, r: int) -> None:
        nonlocal done
        async with sem:
            results[q][r] = await service.ask(q, r)
        done += 1
        if on_progress is not None:
            maybe = on_progress(done, total)
            if asyncio.iscoroutine(maybe):
                await maybe

    await asyncio.gather(*(one(q, r) for q in questions for r in range(runs)))
    return {q: [res for res in runs_ if res is not None] for q, runs_ in results.items()}


def summarize_question(question: str, runs: list[EngineResult], own_domain: str) -> QuestionResult:
    ok = [r for r in runs if r.ok]
    own_cited = sum(1 for r in ok if any(is_own(c.domain, own_domain) for c in r.citations))
    domains: Counter[str] = Counter()
    urls: Counter[str] = Counter()
    for r in ok:
        for dom in {c.domain for c in r.citations}:
            domains[dom] += 1
        for c in r.citations:
            urls[c.url] += 1
    top_domains = sorted(domains.items(), key=lambda kv: (-kv[1], kv[0]))
    top_urls = [u for u, _ in sorted(urls.items(), key=lambda kv: (-kv[1], kv[0]))]
    retrieval_known = any(r.retrieved for r in ok)
    ranks = [c.position for r in ok for c in r.retrieved if is_own(c.domain, own_domain)]
    return QuestionResult(
        question=question,
        runs=runs,
        ok_runs=len(ok),
        own_cited_runs=own_cited,
        own_rate=(own_cited / len(ok)) if ok else None,
        top_domains=top_domains,
        top_urls=top_urls,
        retrieval_known=retrieval_known,
        own_retrieved_rank=min(ranks) if ranks else None,
    )


def aggregate(
    results: dict[str, list[EngineResult]], own_domain: str
) -> tuple[float | None, int, list[DomainStat], list[QuestionResult]]:
    """Return (visibility score 0..100 or None, questions where you were cited, domain stats, per-question results).

    Visibility score = average over questions of (runs citing you / successful runs).
    """
    question_results = [summarize_question(q, runs, own_domain) for q, runs in results.items()]
    rated = [qr.own_rate for qr in question_results if qr.own_rate is not None]
    score = round(100 * sum(rated) / len(rated), 1) if rated else None
    questions_cited = sum(1 for qr in question_results if qr.own_cited_runs > 0)

    ok_runs = [r for runs in results.values() for r in runs if r.ok]
    total_citations = sum(len(r.citations) for r in ok_runs)
    runs_cited: Counter[str] = Counter()
    mentions: Counter[str] = Counter()
    positions: dict[str, list[int]] = defaultdict(list)
    q_cited: dict[str, set[str]] = defaultdict(set)
    for r in ok_runs:
        first_pos: dict[str, int] = {}
        for c in r.citations:
            mentions[c.domain] += 1
            first_pos.setdefault(c.domain, c.position)
        for dom, pos in first_pos.items():
            runs_cited[dom] += 1
            positions[dom].append(pos)
            q_cited[dom].add(r.question)

    own = own_domain.lower()
    stats = [
        DomainStat(
            domain=dom,
            runs_cited=runs_cited[dom],
            questions_cited=len(q_cited[dom]),
            citation_rate=runs_cited[dom] / len(ok_runs),
            share_of_voice=mentions[dom] / total_citations if total_citations else 0.0,
            avg_position=round(sum(positions[dom]) / len(positions[dom]), 2),
            is_own=dom == own,
            is_third_party=is_third_party(dom),
        )
        for dom in runs_cited
    ]
    if own and own not in runs_cited:
        stats.append(DomainStat(domain=own, runs_cited=0, questions_cited=0, citation_rate=0.0,
                                share_of_voice=0.0, avg_position=None, is_own=True))
    stats.sort(key=lambda s: (-s.runs_cited, -s.share_of_voice, s.domain))
    return score, questions_cited, stats, question_results
