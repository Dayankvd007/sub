"""Request/response contracts for the local API.

`TranslateResponse.status` was made job-shaped in Phase 2a ("completed" /
"partial") so the Phase 2b-2 job endpoints could be added without changing the
fields the extension already reads. They were: `POST /translate` keeps its
contract exactly, and `POST /jobs` / `GET /jobs/{id}` are new alongside it.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class CueIn(BaseModel):
    """One normalized English cue, exactly as the extension produces it."""

    model_config = ConfigDict(extra="forbid")

    cue_index: int = Field(ge=0)
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)
    english_text: str = Field(min_length=1)


class TranslateOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    include_srt: bool = True
    rtl_wrap: bool = True
    # Honoured only when ALLOW_CLIENT_MODEL_OVERRIDE is enabled; informational
    # otherwise (architecture §8).
    provider: str | None = None
    model: str | None = None


class TranslateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    video_id: str = Field(min_length=1, max_length=64)
    title: str | None = Field(default=None, max_length=500)
    cues: list[CueIn] = Field(min_length=1)
    options: TranslateOptions = Field(default_factory=TranslateOptions)


class TranslatedCueOut(BaseModel):
    cue_index: int
    start_ms: int
    end_ms: int
    persian_text: str


class StatsOut(BaseModel):
    total_cues: int
    speech_cues: int
    non_speech_cues: int
    windows: int
    translated: int
    failed: int
    provider_calls: int
    corrective_retries: int
    splits: int


class UsageOut(BaseModel):
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    cost: float | None = None


class TranslateResponse(BaseModel):
    video_id: str
    status: str  # "completed" | "partial"
    # True when the result was served from the local cache with no provider call.
    cache_hit: bool = False
    prompt_version: str
    provider: str
    model: str
    stats: StatsOut
    failed_indices: list[int]
    cues: list[TranslatedCueOut]
    srt: str | None = None
    usage: UsageOut | None = None


class JobCreateRequest(BaseModel):
    """Identical in shape to TranslateRequest: the extension sends one payload.

    `options.include_srt` / `rtl_wrap` are not used at creation time — SRT is
    rendered on demand by GET /jobs/{id}?include_srt=true once cues exist.
    """

    model_config = ConfigDict(extra="forbid")

    video_id: str = Field(min_length=1, max_length=64)
    title: str | None = Field(default=None, max_length=500)
    cues: list[CueIn] = Field(min_length=1)
    options: TranslateOptions = Field(default_factory=TranslateOptions)


class JobCreateResponse(BaseModel):
    job_id: int
    video_id: str
    # queued | processing | completed | partial | failed
    status: str
    # True when a completed translation already existed: no worker, no provider.
    cache_hit: bool = False
    total_cues: int
    # None until the worker has run cleaning and dedup.
    speech_cues: int | None = None
    completed_cues: int
    prompt_version: str
    provider: str
    model: str
    # Populated on a cache hit, a reuse, or a resume; empty for a new job.
    cues: list[TranslatedCueOut] = []
    created_at: str


class JobProgressOut(BaseModel):
    total_cues: int
    speech_cues: int | None = None
    completed_cues: int
    failed_cues: int
    percent: float


class JobErrorOut(BaseModel):
    error_code: str
    message: str


class JobStatusResponse(BaseModel):
    job_id: int
    video_id: str
    status: str
    # True once the status is terminal — the client's signal to stop polling and
    # make one final cursor-less read to reconcile.
    done: bool
    progress: JobProgressOut
    cues: list[TranslatedCueOut]
    # Pass back as `after_cue_index`. None when nothing has been returned yet.
    next_cursor: int | None = None
    failed_indices: list[int]
    error: JobErrorOut | None = None
    srt: str | None = None


class HealthResponse(BaseModel):
    status: str
    version: str
    prompt_version: str
    provider: str
    model: str
    # Presence of credentials only — never the key, never a prefix.
    provider_configured: bool


class ErrorResponse(BaseModel):
    error_code: str
    message: str
