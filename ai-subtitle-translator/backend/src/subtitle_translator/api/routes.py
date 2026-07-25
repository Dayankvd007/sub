"""API routes: GET /health and POST /translate.

Both handlers are declared with `def`, not `async def`, on purpose: the
provider call is blocking I/O (stdlib urllib), so FastAPI must run these in its
threadpool rather than on the event loop.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from .. import __version__ as _pkg_version
from ..loaders import LoaderError
from ..prompts import PROMPT_VERSION
from ..providers import ProviderError
from ..service import TranslationService
from .deps import get_service, get_settings
from .schemas import (
    HealthResponse,
    StatsOut,
    TranslateRequest,
    TranslateResponse,
    TranslatedCueOut,
    UsageOut,
)
from .settings import Settings

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
def health(settings: Settings = Depends(get_settings)) -> HealthResponse:
    return HealthResponse(
        status="ok",
        version=_pkg_version,
        prompt_version=PROMPT_VERSION,
        provider=settings.provider,
        model=settings.model,
        provider_configured=settings.api_key_present,
    )


@router.post("/translate", response_model=TranslateResponse)
def translate(
    payload: TranslateRequest,
    settings: Settings = Depends(get_settings),
    service: TranslationService = Depends(get_service),
) -> TranslateResponse:
    if len(payload.cues) > settings.max_cues:
        raise HTTPException(
            status_code=413,  # payload too large
            detail={
                "error_code": "too_many_cues",
                "message": (
                    f"{len(payload.cues)} cues exceeds the configured limit of {settings.max_cues}."
                ),
            },
        )

    try:
        outcome = service.translate(
            video_id=payload.video_id,
            cues_payload=[cue.model_dump() for cue in payload.cues],
            title=payload.title,
            include_srt=payload.options.include_srt,
            rtl_wrap=payload.options.rtl_wrap,
        )
    except LoaderError as exc:
        raise HTTPException(
            status_code=400,
            detail={"error_code": "invalid_cues", "message": str(exc)},
        ) from exc
    except ProviderError as exc:
        # Sanitized: the upstream body may echo request content, so it is
        # logged-by-exception only, never returned verbatim.
        raise HTTPException(
            status_code=502,
            detail={
                "error_code": "provider_error",
                "message": "The translation provider could not be reached or rejected the request.",
            },
        ) from exc

    stats = outcome.stats
    usage = outcome.usage or None

    return TranslateResponse(
        video_id=outcome.video_id,
        status=outcome.status,
        cache_hit=outcome.cache_hit,
        prompt_version=outcome.prompt_version,
        provider=outcome.provider,
        model=outcome.model,
        stats=StatsOut(
            total_cues=stats.total_cues,
            speech_cues=stats.speech_cues,
            non_speech_cues=stats.non_speech_cues,
            windows=stats.windows,
            translated=stats.translated,
            failed=len(stats.failed_indices),
            provider_calls=stats.provider_calls,
            corrective_retries=stats.corrective_retries,
            splits=stats.splits,
        ),
        failed_indices=outcome.failed_indices,
        cues=[
            TranslatedCueOut(
                cue_index=cue.cue_index,
                start_ms=cue.start_ms,
                end_ms=cue.end_ms,
                persian_text=cue.persian_text,
            )
            for cue in outcome.translated_cues
        ],
        srt=outcome.srt,
        usage=UsageOut(**{k: usage.get(k) for k in ("prompt_tokens", "completion_tokens", "cost")})
        if usage
        else None,
    )
