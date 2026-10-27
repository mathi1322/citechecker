"""Runtime settings, read from environment variables (see .env.example)."""

from __future__ import annotations

import os
from dataclasses import dataclass


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer, got {raw!r}") from exc


def _bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


ENGINES = ("auto", "open", "gemini", "demo")

# Per-engine defaults, used when RUNS_PER_QUESTION / DAILY_CALL_BUDGET aren't set.
# open: Tavily's free plan is 1,000 searches a month, and its results barely change between runs,
#       so each question is asked once. gemini: answers vary a lot between runs, so 3 runs.
ENGINE_DEFAULTS = {"open": (1, 30), "gemini": (3, 450), "demo": (3, 450)}


def _secret(name: str) -> str | None:
    return (os.environ.get(name) or "").strip() or None


@dataclass(frozen=True)
class Settings:
    # Which answer engine to use. "auto" picks the first one whose keys are set: open, then gemini, else demo.
    engine: str = "auto"
    tavily_api_key: str | None = None
    groq_api_key: str | None = None
    # Free Groq models that write the cited answer. Each has its own free quota; calls rotate between them.
    groq_models: tuple[str, ...] = ("openai/gpt-oss-120b", "qwen/qwen3.8-27b")
    gemini_api_key: str | None = None
    # Gemini with Google Search grounding: needs a paid (billing-enabled) Gemini project.
    gemini_model: str = "gemini-3.8-flash"
    # Model for the "AI memory" check (no web search). Works on Gemini's free tier.
    memory_model: str = "gemini-3.5-flash-lite"
    # How many questions the memory check asks (each is one call).
    memory_questions: int = 5
    # None = use the engine's default (see ENGINE_DEFAULTS).
    runs_per_question: int | None = None
    max_questions: int = 20
    default_questions: int = 10
    # Paid engine calls allowed per UTC day across all visitors. None = the engine's default.
    daily_call_budget: int | None = None
    # Report / quick-check starts allowed per visitor IP per hour.
    reports_per_hour: int = 5
    checks_per_hour: int = 30
    db_path: str = "data/citecheck.db"
    force_demo: bool = False
    http_timeout: float = 10.0
    concurrency: int = 4
    # Questions not cited that get the (slower) page-by-page gap analysis.
    max_gap_questions: int = 5
    winners_per_question: int = 3
    # Link to the code, shown in the page footer.
    source_url: str = ""
    # Behind Render/HF the visitor IP is in X-Forwarded-For.
    trusted_proxy_headers: bool = True

    @property
    def active_engine(self) -> str:
        """The engine that will actually answer: 'open', 'gemini' or 'demo' (simulated answers)."""
        has_open = bool(self.tavily_api_key and self.groq_api_key)
        if self.force_demo or self.engine == "demo":
            return "demo"
        if self.engine == "open":
            return "open" if has_open else "demo"
        if self.engine == "gemini":
            return "gemini" if self.gemini_api_key else "demo"
        if has_open:
            return "open"
        return "gemini" if self.gemini_api_key else "demo"

    @property
    def demo_mode(self) -> bool:
        """True when no engine has its keys set (or DEMO_MODE=1): answers are simulated."""
        return self.active_engine == "demo"

    @property
    def runs(self) -> int:
        if self.runs_per_question is not None:
            return self.runs_per_question
        return ENGINE_DEFAULTS[self.active_engine][0]

    @property
    def call_budget(self) -> int:
        if self.daily_call_budget is not None:
            return self.daily_call_budget
        return ENGINE_DEFAULTS[self.active_engine][1]


def load_settings() -> Settings:
    engine = os.environ.get("ENGINE", "auto").strip().lower() or "auto"
    if engine not in ENGINES:
        raise ValueError(f"ENGINE must be one of {', '.join(ENGINES)}, got {engine!r}")
    runs = _int("RUNS_PER_QUESTION", 0)
    budget = _int("DAILY_CALL_BUDGET", -1)
    groq_models = tuple(m.strip() for m in os.environ.get("GROQ_MODELS", "").split(",") if m.strip())
    return Settings(
        engine=engine,
        tavily_api_key=_secret("TAVILY_API_KEY"),
        groq_api_key=_secret("GROQ_API_KEY"),
        groq_models=groq_models or Settings.groq_models,
        gemini_api_key=_secret("GEMINI_API_KEY"),
        gemini_model=os.environ.get("GEMINI_MODEL", "gemini-3.8-flash"),
        memory_model=os.environ.get("MEMORY_MODEL", "gemini-3.5-flash-lite"),
        memory_questions=max(0, _int("MEMORY_QUESTIONS", 5)),
        runs_per_question=runs if runs > 0 else None,
        max_questions=max(1, _int("MAX_QUESTIONS", 20)),
        default_questions=max(1, _int("DEFAULT_QUESTIONS", 10)),
        daily_call_budget=budget if budget >= 0 else None,
        reports_per_hour=max(1, _int("REPORTS_PER_HOUR", 5)),
        checks_per_hour=max(1, _int("CHECKS_PER_HOUR", 30)),
        db_path=os.environ.get("DB_PATH", "data/citecheck.db"),
        force_demo=_bool("DEMO_MODE"),
        concurrency=max(1, _int("CONCURRENCY", 4)),
        source_url=os.environ.get("SOURCE_URL", ""),
    )
