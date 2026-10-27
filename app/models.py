"""Data shapes shared across the app. All are pydantic so they serialise straight to JSON/SQLite."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field


class Citation(BaseModel):
    url: str
    title: str = ""
    domain: str
    position: int  # 1-based order in the answer's source list


class EngineResult(BaseModel):
    engine: str
    question: str
    answer: str = ""
    citations: list[Citation] = Field(default_factory=list)
    search_queries: list[str] = Field(default_factory=list)
    error: str | None = None  # set when the run failed; failed runs are left out of scores
    simulated: bool = False
    # Search results the engine read before answering, in rank order (open engine only).
    # Being here but not in `citations` means the AI saw your page and chose not to cite it.
    retrieved: list[Citation] = Field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.error is None


class DomainStat(BaseModel):
    domain: str
    runs_cited: int  # runs whose sources include this domain at least once
    questions_cited: int
    citation_rate: float  # runs_cited / successful runs, 0..1
    share_of_voice: float  # this domain's citations / all citations, 0..1
    avg_position: float | None
    is_own: bool = False
    is_third_party: bool = False


class PageFeatures(BaseModel):
    url: str
    ok: bool = True
    error: str | None = None
    word_count: int = 0
    direct_answer: bool = False
    stats_count: int = 0
    question_headings: int = 0
    headings_count: int = 0
    has_list: bool = False
    has_table: bool = False
    outbound_links: int = 0
    quotes: int = 0
    schema_types: list[str] = Field(default_factory=list)
    modified: date | None = None
    fresh: bool | None = None  # None when no date could be found
    js_dependent: bool = False


class FeatureGap(BaseModel):
    key: str
    label: str
    winners_with: int
    winners_total: int
    own_has: bool | None  # None when the user's page could not be read
    is_gap: bool


class GapResult(BaseModel):
    question: str
    own_url: str | None
    own_is_fallback: bool  # True when no specific page matched, so the homepage was used
    own_features: PageFeatures | None
    winner_features: list[PageFeatures] = Field(default_factory=list)
    gaps: list[FeatureGap] = Field(default_factory=list)


class QuestionResult(BaseModel):
    question: str
    runs: list[EngineResult] = Field(default_factory=list)
    ok_runs: int = 0
    own_cited_runs: int = 0
    own_rate: float | None = None  # None when every run failed
    top_domains: list[tuple[str, int]] = Field(default_factory=list)
    top_urls: list[str] = Field(default_factory=list)
    # Best search rank of your site across runs (1 = top result); None if never in the results.
    own_retrieved_rank: int | None = None
    retrieval_known: bool = False  # True when the engine reports its search results
    gap: GapResult | None = None


Status = Literal["pass", "warn", "fail", "info"]


class TechCheck(BaseModel):
    id: str
    label: str
    status: Status
    detail: str
    url: str | None = None


class MemoryResult(BaseModel):
    """Does the AI name you from what it learned in training, with no web search?"""

    question: str
    model: str = ""
    answer: str = ""
    mentioned: bool | None = None  # None when the call failed
    error: str | None = None


Priority = Literal["high", "medium", "off-site", "low"]


class Fix(BaseModel):
    rule_id: str
    priority: Priority
    title: str
    detail: str
    evidence: str = ""
    impact: int = 1  # how many questions/pages this touches; used to order fixes


class Report(BaseModel):
    id: str
    site: str
    domain: str
    created_at: datetime
    status: Literal["queued", "running", "done", "error"] = "queued"
    progress: float = 0.0
    message: str = ""
    engine: str = ""
    simulated: bool = False
    runs_per_question: int = 3
    questions: list[str] = Field(default_factory=list)
    competitors: list[str] = Field(default_factory=list)
    visibility_score: float | None = None  # 0..100
    questions_cited: int = 0
    # Questions where your site was in the search results the engine read (None if the engine doesn't say).
    questions_retrieved: int | None = None
    memory: list[MemoryResult] = Field(default_factory=list)
    domain_stats: list[DomainStat] = Field(default_factory=list)
    question_results: list[QuestionResult] = Field(default_factory=list)
    technical: list[TechCheck] = Field(default_factory=list)
    fixes: list[Fix] = Field(default_factory=list)
    error: str | None = None
