"""Translation service layer.

Sits between the API and the Phase 1 engine:

    API -> TranslationService -> pipeline.translate_cues -> provider

It imports no web framework, so it can be tested without FastAPI and reused by
any other caller. The Phase 1 engine (cleaning, dedup, chunking, prompts,
validation, retry/split, SRT) is used as-is and is not modified here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from .config import TranslationConfig
from .loaders import cues_from_payload
from .pipeline import PipelineStats, translate_cues
from .prompts import PROMPT_VERSION
from .providers import TranslationProvider
from .srt import to_srt

# A provider factory rather than a provider instance: OpenRouterProvider holds
# per-call mutable state (`last_usage`), so each request gets its own.
ProviderFactory = Callable[[], TranslationProvider]


@dataclass
class TranslationOutcome:
    """Everything the API needs to build a response."""

    video_id: str
    status: str  # "completed" | "partial"
    prompt_version: str
    provider: str
    model: str
    stats: PipelineStats
    translated_cues: list  # list[TranslatedCue]
    failed_indices: list[int] = field(default_factory=list)
    srt: str | None = None
    usage: dict | None = None


class TranslationService:
    """Turns a validated cue payload into translated cues, stats, and SRT."""

    def __init__(
        self,
        provider_factory: ProviderFactory,
        *,
        provider_name: str,
        model: str,
        target_size: int = 50,
        max_size: int = 70,
        context: int = 2,
    ):
        self._provider_factory = provider_factory
        self._provider_name = provider_name
        self._model = model
        self._target_size = target_size
        self._max_size = max_size
        self._context = context

    def translate(
        self,
        *,
        video_id: str,
        cues_payload: list[dict],
        title: str | None = None,
        include_srt: bool = True,
        rtl_wrap: bool = True,
    ) -> TranslationOutcome:
        """Run the full pipeline for one request.

        Raises LoaderError for an invalid payload and ProviderError for a
        transport/credential failure; the API layer maps both to HTTP codes.
        """
        cues = cues_from_payload(cues_payload)
        provider = self._provider_factory()

        config = TranslationConfig(
            provider_name=self._provider_name,
            model=self._model,
            target_size=self._target_size,
            max_size=self._max_size,
            context=self._context,
            title=title,
        )

        result = translate_cues(cues, provider, config)

        srt_text = to_srt(result.translated_cues, rtl_wrap=rtl_wrap) if include_srt else None

        return TranslationOutcome(
            video_id=video_id,
            status="completed" if result.ok else "partial",
            prompt_version=PROMPT_VERSION,
            provider=self._provider_name,
            # Prefer what the provider actually resolved (e.g. OPENROUTER_MODEL).
            model=getattr(provider, "model", self._model) or self._model,
            stats=result.stats,
            translated_cues=result.translated_cues,
            failed_indices=result.stats.failed_indices,
            srt=srt_text,
            usage=getattr(provider, "last_usage", None),
        )
