"""Dependency wiring: settings, provider construction, and the service.

Providers are constructed per request. `OpenRouterProvider` stores per-call
usage on the instance, so sharing one across concurrent requests would mix up
reported token counts and cost.
"""

from __future__ import annotations

from ..config import DEFAULT_MODEL
from ..providers import (
    AnthropicProvider,
    MockProvider,
    OpenRouterProvider,
    ProviderError,
    TranslationProvider,
)
from ..service import TranslationService
from .settings import Settings, load_settings

_settings: Settings | None = None


def get_settings() -> Settings:
    """Process-wide settings, loaded once. Overridable in tests."""
    global _settings
    if _settings is None:
        _settings = load_settings()
    return _settings


def reset_settings_cache() -> None:
    """Test hook: force the next get_settings() call to re-read the env."""
    global _settings
    _settings = None


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
    )
