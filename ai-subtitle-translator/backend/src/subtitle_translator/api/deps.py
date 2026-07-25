"""Dependency wiring: settings, provider construction, and the service.

Providers are constructed per request. `OpenRouterProvider` stores per-call
usage on the instance, so sharing one across concurrent requests would mix up
reported token counts and cost.
"""

from __future__ import annotations

import threading

from ..config import DEFAULT_MODEL
from ..jobs import JobRunner, JobService, JobWorker
from ..providers import (
    AnthropicProvider,
    MockProvider,
    OpenRouterProvider,
    ProviderError,
    TranslationProvider,
)
from ..repository import Repository
from ..service import TranslationService
from .settings import Settings, load_settings

_settings: Settings | None = None
_repository: Repository | None = None
_worker: JobWorker | None = None


def get_settings() -> Settings:
    """Process-wide settings, loaded once. Overridable in tests."""
    global _settings
    if _settings is None:
        _settings = load_settings()
    return _settings


def reset_settings_cache() -> None:
    """Test hook: force the next get_settings()/get_repository() call to rebuild."""
    global _settings, _repository, _worker
    _settings = None
    _repository = None
    _worker = None


def get_repository() -> Repository | None:
    """Process-wide repository, or None when caching is disabled.

    The Repository holds no connection — each method opens its own — so reusing
    one instance across threads is safe.
    """
    global _repository
    settings = get_settings()
    if not settings.cache_enabled:
        return None
    if _repository is None:
        _repository = Repository(settings.db_path)
    return _repository


def build_provider(provider_name: str, model: str | None) -> TranslationProvider:
    if provider_name == "mock":
        return MockProvider()
    if provider_name == "anthropic":
        return AnthropicProvider(model=model or DEFAULT_MODEL)
    if provider_name == "openrouter":
        return OpenRouterProvider(model=model)
    raise ProviderError(f"Unknown provider: {provider_name!r}")


def get_service() -> TranslationService:
    """Build the translation service from current settings.

    Overridden in tests via `app.dependency_overrides` so the whole API suite
    runs offline against MockProvider with no API key and no cost.
    """
    settings = get_settings()
    return TranslationService(
        provider_factory=lambda: build_provider(settings.provider, settings.model),
        provider_name=settings.provider,
        model=settings.model,
        target_size=settings.target_size,
        max_size=settings.max_size,
        context=settings.context,
        repository=get_repository(),
    )


def get_job_service() -> JobService | None:
    """Job service, or None when persistence is disabled.

    Jobs are inseparable from persistence: progress, recovery, and polling all
    read committed rows, so there is nothing coherent to return without a
    database. The route turns None into a typed 503 rather than pretending.
    """
    repository = get_repository()
    if repository is None:
        return None
    settings = get_settings()
    return JobService(
        repository,
        model=settings.model,
        provider_name=settings.provider,
    )


def get_worker() -> JobWorker | None:
    """Process-wide job worker, built once. None when jobs cannot run.

    With the worker disabled, `POST /jobs` still records jobs — they simply wait
    in the database until a process starts with it enabled, whose recovery sweep
    picks them up.
    """
    global _worker
    repository = get_repository()
    settings = get_settings()
    if repository is None or not settings.job_worker_enabled:
        return None
    if _worker is None:
        # Shared so a shutdown can abandon a run *between windows* rather than
        # mid-video: the worker sets it, the runner observes it after each commit.
        stop_event = threading.Event()
        runner = JobRunner(
            repository,
            provider_factory=lambda: build_provider(settings.provider, settings.model),
            provider_name=settings.provider,
            model=settings.model,
            target_size=settings.target_size,
            max_size=settings.max_size,
            context=settings.context,
            provider_retries=settings.job_provider_retries,
            stop_event=stop_event,
        )
        _worker = JobWorker(
            repository,
            runner,
            max_resume_attempts=settings.job_max_resume_attempts,
            stop_event=stop_event,
        )
    return _worker
