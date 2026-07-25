"""Request/response contracts for the local API.

Forward-compatibility note (Phase 2b): `TranslateResponse.status` already
carries a job-shaped value ("completed" / "partial"). When the job system,
persistence, and polling land, `job_id` and further status values can be added
without changing the fields the extension already reads.
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
    prompt_version: str
    provider: str
    model: str
    stats: StatsOut
    failed_indices: list[int]
    cues: list[TranslatedCueOut]
    srt: str | None = None
    usage: UsageOut | None = None


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
