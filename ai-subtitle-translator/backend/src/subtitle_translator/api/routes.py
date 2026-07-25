"""API routes: GET /health, POST /translate, POST /jobs, GET /jobs/{id}.

Handlers are declared with `def`, not `async def`, on purpose: the provider call
and SQLite access are blocking I/O (stdlib urllib and sqlite3), so FastAPI must
run these in its threadpool rather than on the event loop.

`POST /translate` (synchronous, whole video in one request) and `POST /jobs`
(background, progressive) both remain available. They share one cache identity
and one database, so work done by either is reused by the other.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from .. import __version__ as _pkg_version
from ..jobs import JobService, JobWorker
from ..loaders import LoaderError
from ..prompts import PROMPT_VERSION
from ..providers import ProviderError
from ..service import JobInProgressError, TranslationService
from .deps import get_job_service, get_service, get_settings, get_worker
from .schemas import (
    HealthResponse,
    JobCreateRequest,
    JobCreateResponse,
    JobErrorOut,
    JobProgressOut,
    JobStatusResponse,
    StatsOut,
    TranslateRequest,
    TranslateResponse,
    TranslatedCueOut,
    UsageOut,
)
from .settings import Settings

router = APIRouter()


def _cues_out(cues) -> list[TranslatedCueOut]:
    return [
        TranslatedCueOut(
            cue_index=cue.cue_index,
            start_ms=cue.start_ms,
            end_ms=cue.end_ms,
            persian_text=cue.persian_text,
        )
        for cue in cues
    ]


def _require_jobs(job_service: JobService | None) -> JobService:
    if job_service is None:
        raise HTTPException(
            status_code=503,
            detail={
                "error_code": "jobs_unavailable",
                "message": (
                    "Jobs require local persistence, which is disabled. "
                    "Set SUBTITLE_CACHE_ENABLED=true, or use POST /translate."
                ),
            },
        )
    return job_service


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
    except JobInProgressError as exc:
        # Translating synchronously would rewrite the running job's cue rows and
        # destroy its committed progress.
        raise HTTPException(
            status_code=409,
            detail={"error_code": "job_in_progress", "message": str(exc)},
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


@router.post("/jobs", response_model=JobCreateResponse, status_code=201)
def create_job(
    payload: JobCreateRequest,
    response: Response,
    settings: Settings = Depends(get_settings),
    job_service: JobService | None = Depends(get_job_service),
    worker: JobWorker | None = Depends(get_worker),
) -> JobCreateResponse:
    """Create, reuse, or resume a translation job and return its id immediately.

    Idempotent through the cache identity: the same video, captions, model, and
    prompt version can never start a second translation.
    """
    jobs = _require_jobs(job_service)

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
        outcome = jobs.create_or_reuse(
            video_id=payload.video_id,
            cues_payload=[cue.model_dump() for cue in payload.cues],
            title=payload.title,
        )
    except LoaderError as exc:
        # Rejected at the boundary: no job record is created for a bad payload.
        raise HTTPException(
            status_code=400,
            detail={"error_code": "invalid_cues", "message": str(exc)},
        ) from exc

    if outcome.needs_worker and worker is not None:
        worker.enqueue(outcome.record.job_id)

    # 201 only when this request actually created the record; reuse, resume, and
    # a cache hit all address something that already existed.
    if outcome.action != "created":
        response.status_code = 200

    record = outcome.record
    return JobCreateResponse(
        job_id=record.job_id,
        video_id=record.video_id,
        status=record.status,
        cache_hit=outcome.cache_hit,
        total_cues=record.total_cues,
        speech_cues=record.speech_cues,
        completed_cues=record.completed_cues,
        prompt_version=record.prompt_version,
        provider=jobs.provider_name,
        model=record.model,
        cues=_cues_out(outcome.cues),
        created_at=record.created_at,
    )


@router.get("/jobs/{job_id}", response_model=JobStatusResponse)
def read_job(
    job_id: int,
    after_cue_index: int | None = Query(
        default=None,
        ge=0,
        description=(
            "Return only validated cues with a greater index. Omit for a full "
            "read — the client should do exactly that once `done` is true, to "
            "reconcile any cue that completed out of order."
        ),
    ),
    include_srt: bool = Query(default=False),
    job_service: JobService | None = Depends(get_job_service),
) -> JobStatusResponse:
    """Read persisted progress. Never creates work and never returns unvalidated text."""
    jobs = _require_jobs(job_service)

    status = jobs.status(job_id, after_cue_index=after_cue_index)
    if status is None:
        raise HTTPException(
            status_code=404,
            detail={"error_code": "job_not_found", "message": f"No job with id {job_id}."},
        )

    record = status.record
    error = (
        JobErrorOut(error_code=record.error_code, message=record.error_message or "")
        if record.error_code
        else None
    )

    return JobStatusResponse(
        job_id=record.job_id,
        video_id=record.video_id,
        status=record.status,
        done=record.done,
        progress=JobProgressOut(
            total_cues=record.total_cues,
            speech_cues=record.speech_cues,
            completed_cues=record.completed_cues,
            failed_cues=record.failed_cues,
            percent=record.percent,
        ),
        cues=_cues_out(status.cues),
        next_cursor=status.next_cursor,
        failed_indices=record.failed_indices,
        error=error,
        # Rendered from every validated cue, not just the ones past the cursor.
        srt=jobs.srt(job_id) if include_srt else None,
    )
