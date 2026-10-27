from __future__ import annotations

from datetime import date

import pytest

from app.config import Settings
from app.jobs import Deps, default_deps
from app.storage import Storage
from tests.mockweb import MockWeb

TODAY = date(2026, 10, 27)


def pytest_collection_modifyitems(items):
    # Playwright's sync API keeps an event loop running for the whole session once it starts,
    # which breaks the async tests that come after it. Run browser tests last.
    items.sort(key=lambda item: item.get_closest_marker("ui") is not None)


@pytest.fixture
def web() -> MockWeb:
    return MockWeb(TODAY)


@pytest.fixture
def storage():
    s = Storage(":memory:")
    yield s
    s.close()


@pytest.fixture
def settings() -> Settings:
    return Settings(gemini_api_key="test-key", db_path=":memory:", runs_per_question=2, concurrency=2,
                    default_questions=5)


def make_deps(settings: Settings, storage: Storage, web: MockWeb, engine_factory=None) -> Deps:
    """The production wiring (default_deps), pointed at the mock web, with a fixed date."""
    deps = default_deps(settings, storage, transport=web.transport())
    deps.make_http = web.client
    deps.today = lambda: TODAY
    if engine_factory is not None:
        deps.make_engine = engine_factory
    return deps


@pytest.fixture(autouse=True)
def _no_retry_pause(monkeypatch):
    monkeypatch.setattr("app.engines.gemini.RETRY_DELAY", 0)


FAKE_DNS = {"metadata.internal": ["169.254.169.254"], "db.internal": ["10.0.0.5"],
            "mixed.internal": ["93.184.216.34", "127.0.0.1"], "v6.internal": ["::1"],
            "mapped.internal": ["::ffff:10.0.0.1"], "nodns.internal": []}


@pytest.fixture(autouse=True)
def _fake_dns(monkeypatch):
    """No test touches real DNS: every host is public except the *.internal names above."""
    async def resolve(host):
        if host == "unresolvable.internal":
            raise OSError("no such host")
        return FAKE_DNS.get(host, ["93.184.216.34"])
    monkeypatch.setattr("app.fetch.resolve", resolve)


@pytest.fixture
def deps(settings, storage, web) -> Deps:
    return make_deps(settings, storage, web)
