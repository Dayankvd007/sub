"""Translation jobs: creation, background execution, and restart recovery.

Phase 2b-2. The job layer sits *beside* `TranslationService`, not underneath it:
`POST /translate` still runs a whole video synchronously, while a job persists
its work window by window so results can be delivered progressively and survive
a restart.

    POST /jobs -> JobService  (validate, persist source cues, return job_id)
                      |
                      v  doorbell (queue.Queue -- not the durable queue)
                  JobWorker  (one background thread, one job at a time)
                      |
                      v
                  JobRunner  (clean -> dedup -> windows -> commit each window)
                      |
                      v
                  Repository / SQLite  <- GET /jobs/{id} reads committed rows

**SQLite is the queue of record.** The in-memory queue is only a wake-up signal
and is deliberately allowed to die with the process: on startup the worker
sweeps every job left `queued`/`processing` and re-enqueues it, so nothing
depends on the queue surviving.

Concurrency is exactly one job at a time. For a single local user that bounds
provider cost and rate-limit exposure, and it means no window can ever be
translated twice concurrently.
"""

from __future__ import annotations

import logging
import queue
import threading
from dataclasses import dataclass, field
from typing import Callable

from .cleaning import clean_cues
from .config import TranslationConfig
from .dedup import remove_rolling_duplicates
from .fingerprint import fingerprint_cues
from .loaders import cues_from_payload
from .models import Cue, TranslatedCue
from .pipeline import translate_cues
from .prompts import PROMPT_VERSION
from .providers import ProviderError, TranslationProvider
from .repository import ACTIVE_STATUSES, JobRecord, Repository, STATUS_QUEUED
from .srt import to_srt

logger = logging.getLogger(__name__)

ProviderFactory = Callable[[], TranslationProvider]

#: How many times a job may be re-queued by the restart sweep before it is
#: failed. Without this, a job that crashes the process is retried forever.
DEFAULT_MAX_RESUME_ATTEMPTS = 3

#: Job-level retries for a transport failure, and the wait before each one.
DEFAULT_PROVIDER_RETRIES = 2
DEFAULT_RETRY_BACKOFF = (2.0, 8.0)


class _WorkerStopping(Exception):
    """Raised between windows to abandon a run cleanly during shutdown."""


@dataclass
class JobOutcome:
    """Result of a create request, plus whatever is already translated."""

    record: JobRecord
    action: str  # created | reused | resumed | cache_hit
    cues: list[TranslatedCue] = field(default_factory=list)

    @property
    def cache_hit(self) -> bool:
        return self.action == "cache_hit"

    @property
    def needs_worker(self) -> bool:
        """True when this request actually put work into the queue."""
        return self.action in ("created", "resumed")


@dataclass
class JobStatus:
    """A polling read: persisted progress plus cues past the client's cursor."""

    record: JobRecord
    cues: list[TranslatedCue]
    next_cursor: int | None


class JobService:
    """Framework-free job creation and status reads. Imports no web framework."""

    def __init__(
        self,
        repository: Repository,
        *,
        model: str,
        provider_name: str,
        prompt_version: str = PROMPT_VERSION,
    ):
        self._repository = repository
        self._model = model
        self._provider_name = provider_name
        self._prompt_version = prompt_version

    @property
    def model(self) -> str:
        return self._model

    @property
    def provider_name(self) -> str:
        return self._provider_name

    @property
    def prompt_version(self) -> str:
        return self._prompt_version

    def create_or_reuse(
        self, *, video_id: str, cues_payload: list[dict], title: str | None = None
    ) -> JobOutcome:
        """Create, reuse, resume, or cache-hit a job for this cache identity.

        Raises LoaderError for an invalid payload, before any record is written.
        """
        cues = cues_from_payload(cues_payload)
        fingerprint = fingerprint_cues(cues)

        record, action = self._repository.create_or_resume_job(
            video_id=video_id,
            title=title,
            caption_fingerprint=fingerprint,
            model=self._model,
            prompt_version=self._prompt_version,
            source_cues=cues,
        )

        # A brand-new job has nothing translated yet; every other outcome may.
        cues_out = [] if action == "created" else self._repository.job_cues(record.job_id)
        return JobOutcome(record=record, action=action, cues=cues_out)

    def status(self, job_id: int, *, after_cue_index: int | None = None) -> JobStatus | None:
        record = self._repository.get_job(job_id)
        if record is None:
            return None

        cues = self._repository.job_cues(job_id, after_cue_index=after_cue_index)
        next_cursor = cues[-1].cue_index if cues else after_cue_index
        return JobStatus(record=record, cues=cues, next_cursor=next_cursor)

    def srt(self, job_id: int, *, rtl_wrap: bool = True) -> str | None:
        """Render SRT from *every* validated cue, ignoring any polling cursor.

        A subtitle file covering only the tail past a cursor would be useless, so
        this deliberately does not reuse the incremental read.
        """
        cues = self._repository.job_cues(job_id)
        return to_srt(cues, rtl_wrap=rtl_wrap) if cues else None


class JobRunner:
    """Executes one job: rebuilds it from storage and commits every window."""

    def __init__(
        self,
        repository: Repository,
        provider_factory: ProviderFactory,
        *,
        provider_name: str,
        model: str,
        target_size: int = 50,
        max_size: int = 70,
        context: int = 2,
        provider_retries: int = DEFAULT_PROVIDER_RETRIES,
        retry_backoff: tuple[float, ...] = DEFAULT_RETRY_BACKOFF,
        stop_event: threading.Event | None = None,
    ):
        self._repository = repository
        self._provider_factory = provider_factory
        self._provider_name = provider_name
        self._model = model
        self._target_size = target_size
        self._max_size = max_size
        self._context = context
        self._provider_retries = provider_retries
        self._retry_backoff = retry_backoff
        self._stop_event = stop_event

    def run(self, job_id: int) -> JobRecord | None:
        """Translate everything this job still owes, committing window by window."""
        job = self._repository.get_job(job_id)
        if job is None or job.status not in ACTIVE_STATUSES:
            # Already terminal, or gone. Never redo finished work.
            return job

        source = self._repository.load_source_cues(job_id)
        speech_indices, skipped_indices = self._classify(source)
        self._repository.mark_processing(
            job_id, speech_cues=len(speech_indices), skipped_indices=skipped_indices
        )

        provider = self._provider_factory()
        config = TranslationConfig(
            provider_name=self._provider_name,
            model=self._model,
            target_size=self._target_size,
            max_size=self._max_size,
            context=self._context,
            title=job.title,
        )

        attempts = self._provider_retries + 1
        for attempt in range(attempts):
            # Recomputed every attempt, so a retry resumes from what is committed
            # rather than restarting the video.
            remaining = self._remaining(source, speech_indices, job_id)
            if not remaining:
                break
            try:
                translate_cues(
                    remaining,
                    provider,
                    config,
                    on_window_done=self._committer(job_id),
                )
                break
            except _WorkerStopping:
                # Shutdown between windows. Committed windows are safe and the
                # job stays non-terminal, so the next start resumes it.
                logger.info("job %s interrupted by shutdown; will resume", job_id)
                return self._repository.get_job(job_id)
            except ProviderError as exc:
                if attempt + 1 >= attempts:
                    logger.warning("job %s failed after %s attempts: %s", job_id, attempts, exc)
                    return self._repository.fail_job(
                        job_id,
                        error_code="provider_error",
                        # Sanitized: the upstream body may echo request content.
                        error_message=(
                            "The translation provider could not be reached or "
                            "rejected the request. Cues translated so far are kept."
                        ),
                    )
                if self._sleep(self._retry_backoff[min(attempt, len(self._retry_backoff) - 1)]):
                    return self._repository.get_job(job_id)

        return self._repository.finish_job(job_id)

    # --- internals ---------------------------------------------------------

    @staticmethod
    def _classify(source: list[Cue]) -> tuple[set[int], list[int]]:
        """Split stored cues into translatable and never-to-be-translated.

        Cleaning and dedup are deterministic, so running them here reproduces
        exactly what the pipeline will decide, and gives the progress
        denominator before any provider call.
        """
        cleaned, _ = clean_cues(source)
        deduped, _ = remove_rolling_duplicates(cleaned)
        speech = {cue.cue_index for cue in deduped if cue.is_speech}
        skipped = [cue.cue_index for cue in deduped if not cue.is_speech]
        return speech, skipped

    def _remaining(
        self, source: list[Cue], speech_indices: set[int], job_id: int
    ) -> list[Cue]:
        """Speech cues with no committed translation yet, in original order."""
        done = self._repository.completed_indices(job_id)
        return [
            cue for cue in source if cue.cue_index in speech_indices and cue.cue_index not in done
        ]

    def _committer(self, job_id: int):
        def commit(translated: list[TranslatedCue], failed_indices: list[int]) -> None:
            # Commit first: a window that finished must not be lost to shutdown.
            self._repository.save_window(job_id, translated, failed_indices)
            if self._stop_event is not None and self._stop_event.is_set():
                raise _WorkerStopping()

        return commit

    def _sleep(self, seconds: float) -> bool:
        """Wait out a backoff. Returns True if a shutdown interrupted it."""
        if self._stop_event is None:
            threading.Event().wait(seconds)
            return False
        return self._stop_event.wait(seconds)


class JobWorker:
    """One background thread draining a doorbell queue of job ids."""

    def __init__(
        self,
        repository: Repository,
        runner: JobRunner,
        *,
        max_resume_attempts: int = DEFAULT_MAX_RESUME_ATTEMPTS,
        stop_event: threading.Event | None = None,
    ):
        self._repository = repository
        self._runner = runner
        self._max_resume_attempts = max_resume_attempts
        self._stop_event = stop_event or threading.Event()
        self._queue: queue.Queue[int | None] = queue.Queue()
        self._thread: threading.Thread | None = None

    @property
    def stop_event(self) -> threading.Event:
        return self._stop_event

    def enqueue(self, job_id: int) -> None:
        """Ring the doorbell. Safe to call more than once for the same job."""
        self._queue.put(job_id)

    def start(self) -> None:
        """Recover interrupted jobs, then begin processing."""
        if self._thread is not None:
            return
        self.recover()
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._loop, name="subtitle-job-worker", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 30.0) -> None:
        """Signal shutdown and wait for the in-flight window to commit."""
        self._stop_event.set()
        self._queue.put(None)
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None

    def recover(self) -> list[int]:
        """Re-queue jobs a previous process left unfinished.

        This is what makes the in-memory queue disposable: durable state lives in
        SQLite, and every non-terminal job is rediscovered here.
        """
        requeued: list[int] = []
        for job in self._repository.list_resumable():
            if job.attempt_count >= self._max_resume_attempts:
                logger.warning(
                    "job %s exceeded %s resume attempts; failing it",
                    job.job_id,
                    self._max_resume_attempts,
                )
                self._repository.fail_job(
                    job.job_id,
                    error_code="resume_limit_exceeded",
                    error_message=(
                        f"Translation was interrupted {job.attempt_count} times without "
                        "finishing. Cues translated so far are kept; start it again to retry."
                    ),
                )
                continue
            self._repository.requeue_for_resume(job.job_id)
            self.enqueue(job.job_id)
            requeued.append(job.job_id)
        return requeued

    def process_one(self, job_id: int) -> JobRecord | None:
        """Run one job with the worker's guards. Also the synchronous test entry."""
        job = self._repository.get_job(job_id)
        if job is None or job.status != STATUS_QUEUED:
            # A duplicate doorbell ring, or a job already finished/claimed.
            return job
        try:
            return self._runner.run(job_id)
        except Exception:  # pragma: no cover - defensive; never kill the worker
            logger.exception("job %s failed unexpectedly", job_id)
            return self._repository.fail_job(
                job_id,
                error_code="internal_error",
                error_message="Translation stopped on an internal error.",
            )

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                job_id = self._queue.get(timeout=0.25)
            except queue.Empty:
                continue
            if job_id is None:
                break
            self.process_one(job_id)
