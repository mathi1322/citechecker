"""What every answer engine looks like to the rest of the app."""

from __future__ import annotations

from typing import Literal, Protocol

from app.models import EngineResult

ErrorKind = Literal["quota", "auth", "timeout", "server", "bad_response", "config", "budget"]

FRIENDLY_ERRORS: dict[str, str] = {
    "quota": "The AI engine's quota is used up (or this model has no free search quota). Try again later.",
    "auth": "The AI engine rejected the API key. Check GEMINI_API_KEY.",
    "timeout": "The AI engine took too long to answer.",
    "server": "The AI engine had a temporary error.",
    "bad_response": "The AI engine returned an answer we couldn't read.",
    "config": "No AI engine is configured.",
    "budget": "This demo's daily limit is used up. Try again tomorrow, or run it yourself (see README).",
}


class EngineError(Exception):
    def __init__(self, kind: ErrorKind, detail: str = ""):
        self.kind = kind
        self.detail = detail
        super().__init__(f"{kind}: {detail}" if detail else kind)

    @property
    def friendly(self) -> str:
        return FRIENDLY_ERRORS.get(self.kind, str(self))


class Engine(Protocol):
    name: str
    # True when each call costs real API quota (counted against the daily budget).
    costly: bool
    simulated: bool

    async def ask(self, question: str) -> EngineResult:
        """Answer the question with web sources. Raises EngineError on failure."""
        ...
