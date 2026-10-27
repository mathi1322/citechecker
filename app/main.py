"""CiteCheck web app: routes for the pages and the JSON API (see docs/API.md)."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from app.citations import is_own, normalize_site
from app.compare import compare_reports
from app.competitors import normalize_competitors
from app.config import Settings, load_settings
from app.engines.paste import parse_pasted
from app.jobs import Deps, ReportRunner, default_deps, quick_check
from app.limits import RateLimiter, client_ip
from app.models import Report
from app.questions import clean_questions, suggest_questions
from app.storage import Storage

HERE = Path(__file__).parent
DEMO_FILE = HERE / "demo" / "demo_report.json"
DEMO_ID = "demo"
log = logging.getLogger("citecheck")


class CheckIn(BaseModel):
    site: str = Field(max_length=300)
    question: str = Field(min_length=3, max_length=300)


class QuestionsIn(BaseModel):
    site: str = Field(max_length=300)


class ReportIn(BaseModel):
    site: str = Field(max_length=300)
    questions: list[str] = Field(default_factory=list)
    competitors: list[str] = Field(default_factory=list, max_length=10)


class PasteIn(BaseModel):
    site: str = Field(max_length=300)
    text: str
    question: str = Field(default="", max_length=300)
    engine_label: str = Field(default="", max_length=60)


def _site(raw: str) -> tuple[str, str]:
    try:
        return normalize_site(raw)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def load_demo(storage: Storage, path: Path = DEMO_FILE) -> bool:
    """Load the pre-computed demo report (built with scripts/build_demo.py) so the first visit is instant."""
    if not path.exists():
        return False
    try:
        report = Report.model_validate_json(path.read_text())
    except ValueError:
        log.warning("demo report at %s is invalid; skipping", path)
        return False
    storage.save_report(report.model_copy(update={"id": DEMO_ID}))
    return True


def create_app(settings: Settings | None = None, deps: Deps | None = None, demo_path: Path = DEMO_FILE) -> FastAPI:
    settings = settings or (deps.settings if deps else load_settings())
    if deps is None:
        deps = default_deps(settings, Storage(settings.db_path))
    storage = deps.storage
    runner = ReportRunner(deps)
    report_limiter = RateLimiter(settings.reports_per_hour)
    check_limiter = RateLimiter(settings.checks_per_hour)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        storage.mark_stale_running()
        load_demo(storage, demo_path)
        yield

    app = FastAPI(title="CiteCheck", version="0.1.0", lifespan=lifespan,
                  description="Is your website cited in AI search answers? Who is cited instead, and why?")
    app.state.deps = deps
    app.state.runner = runner
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    templates = Jinja2Templates(directory=HERE / "templates")
    templates.env.filters["pct"] = lambda v: "–" if v is None else f"{v * 100:.0f}%"

    def limit(limiter: RateLimiter, request: Request) -> None:
        ip = client_ip(request, settings.trusted_proxy_headers)
        if not limiter.allow(ip):
            raise HTTPException(
                status_code=429,
                detail=f"You've hit this demo's limit. Try again in about {limiter.retry_after(ip) // 60 + 1} min.",
            )

    def common() -> dict:
        return {"demo_mode": settings.demo_mode, "has_demo": storage.get_report(DEMO_ID) is not None,
                "runs": settings.runs, "max_questions": settings.max_questions,
                "source_url": settings.source_url}

    # pages
    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request):
        return templates.TemplateResponse(request, "index.html", common())

    @app.get("/reports/{report_id}", response_class=HTMLResponse)
    async def report_page(request: Request, report_id: str):
        report = storage.get_report(report_id)
        if report is None:
            return templates.TemplateResponse(request, "not_found.html", common(), status_code=404)
        history = [r for r in storage.list_reports(report.domain) if r.id != report.id and r.status == "done"]
        return templates.TemplateResponse(request, "report.html", {**common(), "r": report, "history": history})

    @app.get("/demo")
    async def demo():
        if storage.get_report(DEMO_ID) is None:
            return RedirectResponse("/", status_code=303)
        return RedirectResponse(f"/reports/{DEMO_ID}", status_code=303)

    @app.get("/compare", response_class=HTMLResponse)
    async def compare_page(request: Request, a: str, b: str):
        try:
            comparison = _compare(a, b)
        except HTTPException as exc:
            return templates.TemplateResponse(request, "not_found.html", {**common(), "message": exc.detail},
                                              status_code=exc.status_code)
        return templates.TemplateResponse(request, "compare.html", {**common(), "c": comparison})

    # API
    @app.get("/healthz")
    async def healthz():
        return {"ok": True, "engine": settings.active_engine, "demo_mode": settings.demo_mode,
                "budget_remaining": deps.budget.remaining()}

    @app.post("/api/check")
    async def api_check(body: CheckIn, request: Request):
        base, domain = _site(body.site)
        limit(check_limiter, request)
        result, positions = await quick_check(deps, base, domain, body.question.strip())
        ranks = [c.position for c in result.retrieved if is_own(c.domain, domain)]
        return {"site": base, "domain": domain, "cited": bool(positions), "positions": positions,
                "retrieved_rank": min(ranks) if ranks else None, **result.model_dump(mode="json")}

    @app.post("/api/questions")
    async def api_questions(body: QuestionsIn, request: Request):
        base, domain = _site(body.site)
        limit(check_limiter, request)
        async with deps.make_http() as http:
            questions, source = await suggest_questions(http, base, domain, deps.make_writer(http),
                                                        settings.default_questions)
        return {"site": base, "domain": domain, "questions": questions, "source": source}

    @app.post("/api/reports", status_code=202)
    async def api_create_report(body: ReportIn, request: Request):
        base, domain = _site(body.site)
        questions = clean_questions(body.questions, settings.max_questions + 1)
        if len(questions) > settings.max_questions:
            raise HTTPException(422, f"Use at most {settings.max_questions} questions")
        limit(report_limiter, request)
        report = runner.create(base, domain, questions, normalize_competitors(body.competitors))
        runner.start(report)
        return {"id": report.id, "url": f"/reports/{report.id}", "status": report.status}

    @app.get("/api/reports/{report_id}")
    async def api_get_report(report_id: str):
        report = storage.get_report(report_id)
        if report is None:
            raise HTTPException(404, "Report not found")
        return JSONResponse(report.model_dump(mode="json"))

    @app.post("/api/paste")
    async def api_paste(body: PasteIn):
        base, domain = _site(body.site)
        try:
            result = parse_pasted(body.text, body.question, body.engine_label or "pasted answer")
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        positions = [c.position for c in result.citations if is_own(c.domain, domain)]
        return {"site": base, "domain": domain, "cited": bool(positions), "positions": positions,
                **result.model_dump(mode="json")}

    def _compare(a: str, b: str):
        ra, rb = storage.get_report(a), storage.get_report(b)
        if ra is None or rb is None:
            raise HTTPException(404, "One of those reports doesn't exist")
        if ra.status != "done" or rb.status != "done":
            raise HTTPException(409, "Both reports must be finished before comparing")
        try:
            return compare_reports(ra, rb)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.get("/api/compare")
    async def api_compare(a: str, b: str):
        return _compare(a, b).model_dump(mode="json")

    return app
