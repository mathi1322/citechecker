"""Before/after: compare two reports for the same site to see whether changes worked."""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.models import Report


class QuestionDelta(BaseModel):
    question: str
    before: float | None
    after: float | None
    change: float | None


class Comparison(BaseModel):
    domain: str
    before_id: str
    after_id: str
    before_score: float | None
    after_score: float | None
    score_change: float | None
    questions: list[QuestionDelta] = Field(default_factory=list)
    fixed: list[str] = Field(default_factory=list)  # fix titles present before, gone after
    new_issues: list[str] = Field(default_factory=list)
    only_before: list[str] = Field(default_factory=list)  # questions asked in just one of the reports
    only_after: list[str] = Field(default_factory=list)


def _delta(a: float | None, b: float | None) -> float | None:
    return None if a is None or b is None else round(b - a, 3)


def compare_reports(before: Report, after: Report) -> Comparison:
    if before.domain != after.domain:
        raise ValueError("Both reports must be for the same website")
    if before.created_at > after.created_at:
        before, after = after, before
    b_rates = {qr.question: qr.own_rate for qr in before.question_results}
    a_rates = {qr.question: qr.own_rate for qr in after.question_results}
    common = [q for q in a_rates if q in b_rates]
    b_fixes = {f.rule_id: f.title for f in before.fixes}
    a_fixes = {f.rule_id: f.title for f in after.fixes}
    return Comparison(
        domain=after.domain,
        before_id=before.id,
        after_id=after.id,
        before_score=before.visibility_score,
        after_score=after.visibility_score,
        score_change=_delta(before.visibility_score, after.visibility_score),
        questions=[QuestionDelta(question=q, before=b_rates[q], after=a_rates[q],
                                 change=_delta(b_rates[q], a_rates[q])) for q in common],
        fixed=[title for rid, title in b_fixes.items() if rid not in a_fixes],
        new_issues=[title for rid, title in a_fixes.items() if rid not in b_fixes],
        only_before=[q for q in b_rates if q not in a_rates],
        only_after=[q for q in a_rates if q not in b_rates],
    )
