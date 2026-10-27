"""The report pipeline, start to finish. Runs as a background task; the UI polls for progress."""

from __future__ import annotations

import asyncio
import logging
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timezone
from urllib.parse import urlsplit

import httpx

from app.citations import is_own
from app.competitors import derive_competitors
from app.config import Settings
from app.content import extract_features
from app.engines.base import Engine
from app.engines.fake import FakeEngine
from app.engines.gemini import GeminiClient, GeminiEngine
from app.engines.open_engine import GroqClient, OpenEngine, TavilyClient
from app.fetch import FetchResult, RobotsCache, fetch, make_client, polite_fetch
from app.gap import compare
from app.limits import DailyBudget
from app.matching import best_page
from app.memory import TextWriter, memory_check
from app.models import EngineResult, PageFeatures, Report
from app.questions import suggest_questions
from app.recommend import recommend
from app.storage import Storage
from app.technical import check_page, check_robots, load_sitemap, page_problems
from app.visibility import AskService, aggregate, run_questions

log = logging.getLogger("citecheck")


class ReportError(Exception):
    """A failure with a message that's safe and useful to show the user."""


@dataclass
class Deps:
    settings: Settings
    storage: Storage
    budget: DailyBudget
    make_http: Callable[[], httpx.AsyncClient]
    make_engine: Callable[[httpx.AsyncClient, str], Engine]
    # A free text model for question suggestions (None = use homepage heuristics).
    make_writer: Callable[[httpx.AsyncClient], TextWriter | None]
    # (model, label) for the AI memory check (None = skip it).
    make_memory: Callable[[httpx.AsyncClient], tuple[TextWriter, str] | None]
    today: Callable[[], date] = date.today


def default_deps(settings: Settings, storage: Storage, transport: httpx.AsyncBaseTransport | None = None) -> Deps:
    def make_http() -> httpx.AsyncClient:
        return make_client(settings.http_timeout, transport)

    def groq(http: httpx.AsyncClient) -> GroqClient | None:
        if not settings.groq_api_key:
            return None
        return GroqClient(settings.groq_api_key, settings.groq_models, http)

    def gemini_plain(http: httpx.AsyncClient) -> GeminiClient | None:
        if not settings.gemini_api_key:
            return None
        return GeminiClient(settings.gemini_api_key, settings.memory_model, http=http)

    def make_engine(http: httpx.AsyncClient, own_site: str) -> Engine:
        engine = settings.active_engine
        if engine == "open":
            return OpenEngine(TavilyClient(settings.tavily_api_key or "", http), groq(http))  # type: ignore[arg-type]
        if engine == "gemini":
            return GeminiEngine(GeminiClient(settings.gemini_api_key or "", settings.gemini_model, http=http), http)
        return FakeEngine(own_site=own_site)

    def make_writer(http: httpx.AsyncClient) -> TextWriter | None:
        if settings.force_demo:
            return None
        return groq(http) or gemini_plain(http)

    def make_memory(http: httpx.AsyncClient) -> tuple[TextWriter, str] | None:
        if settings.force_demo or settings.memory_questions == 0:
            return None
        if gemini := gemini_plain(http):
            return gemini, settings.memory_model
        if client := groq(http):
            return client, client.models[0]
        return None

    return Deps(settings=settings, storage=storage, budget=DailyBudget(storage, settings.call_budget),
                make_http=make_http, make_engine=make_engine, make_writer=make_writer, make_memory=make_memory)


SKIP_REASONS = {
    "robots": "skipped: the site's robots.txt asks CiteCheckBot not to fetch it",
    "blocked": "skipped: not a public web address",
}


def features_for(page: FetchResult, question: str, today: date) -> PageFeatures:
    if not page.ok:
        reason = SKIP_REASONS.get(page.error or "") or (page.error if page.status is None else f"HTTP {page.status}")
        return PageFeatures(url=page.url, ok=False, error=reason)
    return extract_features(page.url, page.text, page.headers, question, today)


async def quick_check(deps: Deps, base_url: str, domain: str, question: str) -> tuple[EngineResult, list[int]]:
    """Step 1 of the tool: ask one question once. Returns the answer and where your site appears (positions)."""
    async with deps.make_http() as http:
        engine = deps.make_engine(http, base_url)
        result = await AskService(engine, deps.storage, deps.budget).ask(question)
    positions = [c.position for c in result.citations if is_own(c.domain, domain)]
    return result, positions


class ReportRunner:
    def __init__(self, deps: Deps):
        self.deps = deps
        self._tasks: set[asyncio.Task] = set()

    def create(self, base_url: str, domain: str, questions: list[str], competitors: list[str]) -> Report:
        report = Report(
            id=secrets.token_urlsafe(8),
            site=base_url,
            domain=domain,
            created_at=datetime.now(timezone.utc),
            runs_per_question=self.deps.settings.runs,
            questions=questions,
            competitors=competitors,
            simulated=self.deps.settings.demo_mode,
            message="Waiting to start",
        )
        self.deps.storage.save_report(report)
        return report

    def start(self, report: Report) -> asyncio.Task:
        task = asyncio.create_task(self.run(report.id))
        self._tasks.add(task)  # keep a reference so the task isn't garbage-collected
        task.add_done_callback(self._tasks.discard)
        return task

    def _update(self, report: Report, progress: float, message: str) -> None:
        report.status, report.progress, report.message = "running", round(progress, 3), message
        self.deps.storage.save_report(report)

    async def run(self, report_id: str) -> Report:
        report = self.deps.storage.get_report(report_id)
        if report is None:
            raise ReportError(f"Report {report_id} not found")
        try:
            async with self.deps.make_http() as http:
                await self._pipeline(report, http)
            report.status, report.progress, report.message = "done", 1.0, "Done"
        except ReportError as exc:
            report.status, report.error, report.message = "error", str(exc), "Failed"
        except Exception:  # noqa: BLE001
            log.exception("report %s failed", report_id)
            report.status, report.message = "error", "Failed"
            report.error = "Something went wrong while building this report. Please try again."
        self.deps.storage.save_report(report)
        return report

    async def _pipeline(self, report: Report, http: httpx.AsyncClient) -> None:
        s, deps = self.deps.settings, self.deps
        base, domain = report.site, report.domain
        today = deps.today()

        self._update(report, 0.03, "Checking your site")
        home = await fetch(http, base + "/")
        if home.status is None:
            raise ReportError(f"Couldn't reach {base} ({home.error}). Check the address and try again.")
        robots = await fetch(http, base + "/robots.txt")
        robot_checks, listed_sitemaps = check_robots(robots, base)
        sitemap_check, sitemap_urls = await load_sitemap(http, base, listed_sitemaps)
        technical = robot_checks + [sitemap_check] + check_page(home, "Homepage")

        if not report.questions:
            self._update(report, 0.08, "Writing buyer questions from your homepage")
            questions, _ = await suggest_questions(http, base, domain, deps.make_writer(http), s.default_questions)
            if not questions:
                raise ReportError("Couldn't tell what this site offers from its homepage. "
                                  "Add your own questions and run again.")
            report.questions = questions

        engine = deps.make_engine(http, base)
        report.engine = getattr(engine, "name", "engine")
        report.simulated = bool(getattr(engine, "simulated", False))
        service = AskService(engine, deps.storage, deps.budget)

        def progress(done: int, total: int) -> None:
            self._update(report, 0.1 + 0.6 * done / total, f"Asking the AI engine: {done} of {total} answers")

        concurrency = min(s.concurrency, getattr(engine, "max_concurrency", s.concurrency))
        results = await run_questions(service, report.questions, s.runs, concurrency, progress)
        score, questions_cited, stats, question_results = aggregate(results, domain)
        if score is None:
            first_error = next((r.error for runs in results.values() for r in runs if r.error), "unknown error")
            raise ReportError(f"Every AI request failed: {first_error}")
        engine_names = {r.engine for runs in results.values() for r in runs if r.ok}
        if engine_names:
            report.engine = sorted(engine_names)[0]

        competitors = derive_competitors([st.domain for st in stats], domain, report.competitors)

        pages: dict[str, FetchResult] = {base + "/": home}
        robots = RobotsCache(http)

        async def get(url: str) -> FetchResult:
            # Your own pages are fetched because you asked for the audit; other sites' pages
            # only when their robots.txt allows it.
            if url not in pages:
                pages[url] = await (fetch(http, url) if is_own(url, domain) else polite_fetch(http, robots, url))
            return pages[url]

        lost = [qr for qr in question_results if qr.own_cited_runs == 0 and qr.ok_runs > 0]
        lost = lost[: s.max_gap_questions]
        checked = {base + "/"}
        for i, qr in enumerate(lost):
            self._update(report, 0.72 + 0.23 * i / len(lost), f"Comparing pages for question {i + 1} of {len(lost)}")
            own_url, _, fallback = best_page(qr.question, sitemap_urls, base + "/")
            own_page = await get(own_url)
            winners: list[PageFeatures] = []
            if not report.simulated:
                urls = [u for u in qr.top_urls if not is_own(u, domain)][: s.winners_per_question]
                fetched = await asyncio.gather(*(get(u) for u in urls))
                winners = [features_for(p, qr.question, today) for p in fetched]
            qr.gap = compare(qr.question, features_for(own_page, qr.question, today), winners, fallback)
            if own_url not in checked:
                checked.add(own_url)
                label = "Page " + (urlsplit(own_url).path or "/")
                technical += page_problems(check_page(own_page, label), own_url, label)

        memory = deps.make_memory(http)
        if memory is not None:
            self._update(report, 0.96, "Checking what the AI knows about you without searching")
            writer, label = memory
            report.memory = await memory_check(writer, label, report.questions, domain.split(".")[0], domain,
                                               s.memory_questions)

        known = [qr for qr in question_results if qr.retrieval_known]
        report.questions_retrieved = sum(1 for qr in known if qr.own_retrieved_rank is not None) if known else None
        report.visibility_score = score
        report.questions_cited = questions_cited
        report.domain_stats = stats
        report.question_results = question_results
        report.competitors = competitors
        report.technical = technical
        report.fixes = recommend(technical, question_results, stats, competitors, score, report.memory)
