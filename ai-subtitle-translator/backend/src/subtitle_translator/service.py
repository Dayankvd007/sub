"""Translation service layer.

Sits between the API and the Phase 1 engine:

    API -> TranslationService -> pipeline.translate_cues -> provider

It imports no web framework, so it can be tested without FastAPI and reused by
any other caller. The Phase 1 engine (cleaning, dedup, chunking, prompts,
validation, retry/split, SRT) is used as-is and is not modified here.

Phase 2b-1 adds an optional cache *around* `translate_cues`: the fingerprint is
computed from the received cues, a completed record for the same identity is
returned without any provider call, and otherwise the result is persisted after
translation. Passing no repository disables persistence entirely.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from .config import TranslationConfig
from .fingerprint import fingerprint_cues
from .loaders import cues_from_payload
from .pipeline import PipelineStats, translate_cues
from .prompts import PROMPT_VERSION
from .providers import TranslationProvider
from .repository import Repository
from .srt import to_srt

# A provider factory rather than a provider instance: OpenRouterProvider holds
# per-call mutable state (`last_usage`, `usage_totals`), so each request gets
# its own. It is only invoked on a cache miss, so a cache hit needs no
# credentials.
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
    cache_hit: bool = False


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
        repository: Repository | None = None,
    ):
        self._provider_factory = provider_factory
        self._provider_name = provider_name
        self._model = model
        self._target_size = target_size
        self._max_size = max_size
        self._context = context
        self._repository = repository

    def translate(
        self,
        *,
        video_id: str,
        cues_payload: list[dict],
        title: str | None = None,
        include_srt: bool = True,
        rtl_wrap: bool = True,
    ) -> TranslationOutcome:
        """Run the full pipeline for one request, reusing a cached result if valid.

        Raises LoaderError for an invalid payload and ProviderError for a
        transport/credential failure; the API layer maps both to HTTP codes.
        """
        cues = cues_from_payload(cues_payload)
        fingerprint = fingerprint_cues(cues)

        # Cache identity uses the *configured* model, so the lookup happens
        # before the provider is constructed — a hit therefore costs no provider
        # call and needs no API key.
        if self._repository is not None:
            cached = self._repository.find_completed(
                video_id=video_id,
                caption_fingerprint=fingerprint,
                model=self._model,
                prompt_version=PROMPT_VERSION,
            )
            if cached is not None:
                return self._cached_outcome(cached, include_srt=include_srt, rtl_wrap=rtl_wrap)

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

        if self._repository is not None:
            self._repository.save(
                video_id=video_id,
                title=title,
                caption_fingerprint=fingerprint,
                model=self._model,
                prompt_version=PROMPT_VERSION,
                source_cues=cues,
                translated_cues=result.translated_cues,
                failed_indices=result.stats.failed_indices,
            )

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
            usage=self._usage_for(provider),
            cache_hit=False,
        )

    # --- helpers -----------------------------------------------------------

    @staticmethod
    def _usage_for(provider: TranslationProvider) -> dict | None:
        """Per-request totals across every window, not the last call's figures."""
        totals = getattr(provider, "usage_totals", None)
        if not totals or not totals.get("calls"):
            return None
        return dict(totals)

    def _cached_outcome(
        self, cached, *, include_srt: bool, rtl_wrap: bool
    ) -> TranslationOutcome:
        translated = cached.translated_cues
        # Stats describe *this* request, which did no model work — so the call,
        # retry, split, and window counts are genuinely zero rather than a replay
        # of the original run's numbers.
        stats = PipelineStats(
            prompt_version=cached.prompt_version,
            total_cues=cached.total_cues,
            speech_cues=len(translated),
            non_speech_cues=max(cached.total_cues - len(translated), 0),
            windows=0,
            translated=len(translated),
        )
        return TranslationOutcome(
            video_id=cached.video_id,
            status="completed",
            prompt_version=cached.prompt_version,
            provider=self._provider_name,
            model=cached.model,
            stats=stats,
            translated_cues=translated,
            failed_indices=[],
            srt=to_srt(translated, rtl_wrap=rtl_wrap) if include_srt else None,
            usage=None,
            cache_hit=True,
        )
