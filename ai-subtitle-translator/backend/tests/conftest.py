"""Shared fixtures.

The Phase 2a helpers need the optional `[api]` extra. This module must stay
importable without it — a conftest that raises at import time would break
collection of the whole suite, including the Phase 1 engine tests, which are
required to run with zero third-party dependencies. So the FastAPI imports are
guarded here, and each API test module calls `pytest.importorskip("fastapi")`
to skip itself cleanly when the extra is missing.
"""

import pytest

try:
    from fastapi.testclient import TestClient

    from subtitle_translator.api.app import create_app
    from subtitle_translator.api.deps import get_service, get_settings
    from subtitle_translator.api.settings import Settings

    FASTAPI_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised only without the [api] extra
    FASTAPI_AVAILABLE = False

from subtitle_translator.providers import MockProvider
from subtitle_translator.service import TranslationService


def make_settings(**overrides):
    defaults = dict(
        provider="mock",
        model="mock-model",
        allowed_origins=["chrome-extension://testextensionid"],
        max_cues=100,
        max_body_bytes=5 * 1024 * 1024,
    )
    defaults.update(overrides)
    return Settings(**defaults)


def make_client(settings=None, provider_factory=None):
    """Build a TestClient wired to MockProvider — offline, no key, no cost."""
    settings = settings or make_settings()
    app = create_app(settings)

    service = TranslationService(
        provider_factory=provider_factory or (lambda: MockProvider()),
        provider_name=settings.provider,
        model=settings.model,
    )

    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_service] = lambda: service
    return TestClient(app)


@pytest.fixture
def client():
    return make_client()


@pytest.fixture
def cues():
    return [
        {"cue_index": 0, "start_ms": 0, "end_ms": 1000, "english_text": "Hello there."},
        {"cue_index": 1, "start_ms": 1000, "end_ms": 2000, "english_text": "This is a test."},
        {"cue_index": 2, "start_ms": 2000, "end_ms": 3000, "english_text": "Goodbye."},
    ]
