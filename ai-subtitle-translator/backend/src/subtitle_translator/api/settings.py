"""Server settings for the local API, read from the environment.

Credentials are deliberately absent from this object: providers read
OPENROUTER_API_KEY / ANTHROPIC_API_KEY from the environment themselves, so a
key can never be logged, serialized into a response, or captured in a settings
dump. Only the *presence* of a key is ever exposed (GET /health).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from ..db import default_db_path
from ..envfile import load_dotenv

# Phase 1 exit-gate selection (docs/PHASE1_TRANSLATION_ENGINE_NOTES.md).
# The CLI keeps its own defaults; this is the service default only.
DEFAULT_API_PROVIDER = "openrouter"
DEFAULT_API_MODEL = "google/gemini-3.1-flash-lite"

# Phase 2a caps. Bound the worst-case cost and memory of a single request.
DEFAULT_MAX_CUES = 5000
DEFAULT_MAX_BODY_BYTES = 5 * 1024 * 1024  # 5 MB

_API_KEY_ENV_VAR = {
    "openrouter": "OPENROUTER_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "mock": None,
}


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_list(name: str) -> list[str]:
    raw = os.environ.get(name, "")
    return [item.strip() for item in raw.split(",") if item.strip()]


@dataclass
class Settings:
    host: str = "127.0.0.1"
    port: int = 8000
    provider: str = DEFAULT_API_PROVIDER
    model: str = DEFAULT_API_MODEL
    # Chrome extension origins look like chrome-extension://<id>. The id is only
    # known once the unpacked extension is loaded, so it must be configured.
    allowed_origins: list[str] = field(default_factory=list)
    max_cues: int = DEFAULT_MAX_CUES
    max_body_bytes: int = DEFAULT_MAX_BODY_BYTES
    # Architecture §8: client-provided model values are informational unless
    # explicitly enabled for experiments.
    allow_client_model_override: bool = False
    target_size: int = 50
    max_size: int = 70
    context: int = 2
    # Phase 2b-1: local persistence + cache. The database lives in a per-user
    # data directory, never inside the repository.
    db_path: str = ""
    cache_enabled: bool = True

    @property
    def api_key_present(self) -> bool:
        """True when the selected provider has credentials available.

        Never exposes the key itself — only whether one is configured.
        """
        var = _API_KEY_ENV_VAR.get(self.provider, None)
        if var is None:
            return self.provider == "mock"
        return bool(os.environ.get(var))


def load_settings() -> Settings:
    """Build settings from the environment, after loading a local .env."""
    load_dotenv(Path(__file__))  # never overrides real environment variables
    return Settings(
        host=os.environ.get("SUBTITLE_API_HOST", "127.0.0.1"),
        port=_env_int("SUBTITLE_API_PORT", 8000),
        provider=os.environ.get("SUBTITLE_API_PROVIDER", DEFAULT_API_PROVIDER),
        model=os.environ.get("SUBTITLE_API_MODEL")
        or os.environ.get("OPENROUTER_MODEL")
        or DEFAULT_API_MODEL,
        allowed_origins=_env_list("ALLOWED_ORIGINS"),
        max_cues=_env_int("MAX_CUES", DEFAULT_MAX_CUES),
        max_body_bytes=_env_int("MAX_BODY_BYTES", DEFAULT_MAX_BODY_BYTES),
        allow_client_model_override=_env_bool("ALLOW_CLIENT_MODEL_OVERRIDE", False),
        db_path=os.environ.get("SUBTITLE_DB_PATH") or str(default_db_path()),
        cache_enabled=_env_bool("SUBTITLE_CACHE_ENABLED", True),
    )
