"""Persistence and cache lookup for translated videos.

Cache identity is `video_id` + `caption_fingerprint` + `model` + `prompt_version`
(architecture §7). Changing any one of them is a different identity, so an old
result is never silently returned as current.

Only a fully completed record is ever served as a cache hit. Partial results are
persisted and resumed by the Phase 2b-2 job system, but are never presented as
if the job had finished.

Phase 2b-2: a `videos` record *is* a job record — `videos.id` is the `job_id`,
and the UNIQUE cache identity is therefore also the job's idempotency key, so a
repeated create cannot start a second translation of the same thing. The
synchronous cache path (`find_completed` / `save`) is unchanged; the job methods
below are additive and write at *window* granularity so committed progress
survives a restart.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .db import connect, init_schema
from .models import Cue, TranslatedCue

STATUS_QUEUED = "queued"
STATUS_PROCESSING = "processing"
STATUS_COMPLETED = "completed"
STATUS_PARTIAL = "partial"
STATUS_FAILED = "failed"

#: Non-terminal: the job is waiting for, or owned by, the worker.
ACTIVE_STATUSES = (STATUS_QUEUED, STATUS_PROCESSING)
#: Terminal: the worker is finished with this job.
TERMINAL_STATUSES = (STATUS_COMPLETED, STATUS_PARTIAL, STATUS_FAILED)

CUE_COMPLETED = "completed"
CUE_FAILED = "failed"
CUE_PENDING = "pending"
# Non-speech or rolling-duplicate cues: deliberately never sent to a model, as
# distinct from "not attempted yet". Keeps a resume from chasing them forever.
CUE_SKIPPED = "skipped"


@dataclass
class CachedTranslation:
    """A completed record, reconstructed for reuse."""

    video_id: str
    title: str | None
    model: str
    prompt_version: str
    total_cues: int
    translated_cues: list[TranslatedCue]


@dataclass
class JobRecord:
    """A `videos` row read as a job, with live counts derived from its cues."""

    job_id: int
    video_id: str
    title: str | None
    status: str
    model: str
    prompt_version: str
    caption_fingerprint: str
    total_cues: int
    speech_cues: int | None
    completed_cues: int
    failed_cues: int
    attempt_count: int
    error_code: str | None
    error_message: str | None
    created_at: str
    updated_at: str
    failed_indices: list[int] = field(default_factory=list)

    @property
    def done(self) -> bool:
        return self.status in TERMINAL_STATUSES

    @property
    def percent(self) -> float:
        """Progress against the *speech* cue count, which is the real denominator.

        `total_cues` includes non-speech cues that are never translated, so it
        would never reach 100%. Reported as 0.0 until the worker has computed
        the speech count.
        """
        if self.status == STATUS_COMPLETED:
            return 100.0
        if not self.speech_cues:
            return 0.0
        resolved = min(self.completed_cues + self.failed_cues, self.speech_cues)
        return round(100.0 * resolved / self.speech_cues, 1)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Repository:
    """Thread-safe by construction: every method opens its own connection."""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        init_schema(self.db_path)

    # --- cache lookup ------------------------------------------------------

    def find_completed(
        self, *, video_id: str, caption_fingerprint: str, model: str, prompt_version: str
    ) -> CachedTranslation | None:
        """Return a completed record for this exact identity, or None.

        A record whose status is not `completed`, or that has any cue marked
        failed, is deliberately not returned — a partial result must never be
        presented as if the job had finished.
        """
        with connect(self.db_path) as conn:
            row = conn.execute(
                """
                SELECT id, video_id, title, model, prompt_version, total_cues, status
                FROM videos
                WHERE video_id = ? AND caption_fingerprint = ?
                  AND model = ? AND prompt_version = ?
                """,
                (video_id, caption_fingerprint, model, prompt_version),
            ).fetchone()

            if row is None or row["status"] != STATUS_COMPLETED:
                return None

            failed = conn.execute(
                "SELECT COUNT(*) AS n FROM cues WHERE video_record_id = ? AND translation_status = ?",
                (row["id"], CUE_FAILED),
            ).fetchone()["n"]
            if failed:
                return None

            cue_rows = conn.execute(
                """
                SELECT cue_index, start_ms, end_ms, english_text, persian_text
                FROM cues
                WHERE video_record_id = ? AND persian_text IS NOT NULL AND persian_text != ''
                ORDER BY cue_index
                """,
                (row["id"],),
            ).fetchall()

            if not cue_rows:
                return None

            return CachedTranslation(
                video_id=row["video_id"],
                title=row["title"],
                model=row["model"],
                prompt_version=row["prompt_version"],
                total_cues=row["total_cues"],
                translated_cues=[
                    TranslatedCue(
                        cue_index=c["cue_index"],
                        start_ms=c["start_ms"],
                        end_ms=c["end_ms"],
                        english_text=c["english_text"],
                        persian_text=c["persian_text"],
                    )
                    for c in cue_rows
                ],
            )

    # --- persistence -------------------------------------------------------

    def save(
        self,
        *,
        video_id: str,
        title: str | None,
        caption_fingerprint: str,
        model: str,
        prompt_version: str,
        source_cues: list[Cue],
        translated_cues: list[TranslatedCue],
        failed_indices: list[int],
    ) -> None:
        """Persist source cues plus whatever was translated, upserting on identity.

        Source English is stored for every cue — including non-speech and failed
        ones — so the index map stays complete and auditable, and so Phase 2b-2
        can resume and generate SRT from storage. The stored `english_text` is
        the loader-normalized text the fingerprint was computed over, which keeps
        the two consistent.
        """
        status = STATUS_COMPLETED if not failed_indices else STATUS_PARTIAL
        persian_by_index = {tc.cue_index: tc.persian_text for tc in translated_cues}
        failed = set(failed_indices)
        now = _now()

        with connect(self.db_path) as conn:
            existing = conn.execute(
                """
                SELECT id FROM videos
                WHERE video_id = ? AND caption_fingerprint = ?
                  AND model = ? AND prompt_version = ?
                """,
                (video_id, caption_fingerprint, model, prompt_version),
            ).fetchone()

            if existing is None:
                cursor = conn.execute(
                    """
                    INSERT INTO videos (
                        video_id, title, status, total_cues, model,
                        prompt_version, caption_fingerprint, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        video_id,
                        title,
                        status,
                        len(source_cues),
                        model,
                        prompt_version,
                        caption_fingerprint,
                        now,
                        now,
                    ),
                )
                video_record_id = cursor.lastrowid
            else:
                video_record_id = existing["id"]
                conn.execute(
                    "UPDATE videos SET title = ?, status = ?, total_cues = ?, updated_at = ? WHERE id = ?",
                    (title, status, len(source_cues), now, video_record_id),
                )
                # Rewrite cues wholesale: simpler and safer than diffing, and a
                # retranslation supersedes whatever was stored before.
                conn.execute("DELETE FROM cues WHERE video_record_id = ?", (video_record_id,))

            conn.executemany(
                """
                INSERT INTO cues (
                    video_record_id, cue_index, start_ms, end_ms,
                    english_text, persian_text, translation_status
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        video_record_id,
                        cue.cue_index,
                        cue.start_ms,
                        cue.end_ms,
                        cue.english_text,
                        persian_by_index.get(cue.cue_index),
                        CUE_COMPLETED
                        if cue.cue_index in persian_by_index
                        else (CUE_FAILED if cue.cue_index in failed else CUE_PENDING),
                    )
                    for cue in source_cues
                ],
            )

    # --- jobs (Phase 2b-2) -------------------------------------------------

    def create_or_resume_job(
        self,
        *,
        video_id: str,
        title: str | None,
        caption_fingerprint: str,
        model: str,
        prompt_version: str,
        source_cues: list[Cue],
    ) -> tuple[JobRecord, str]:
        """Resolve a create request against the cache identity, in one transaction.

        Returns the record plus what happened:

        - ``cache_hit`` — a completed record already exists; nothing is touched.
        - ``reused``    — a job for this identity is already queued/processing,
                          so the same job_id is returned and no second run starts.
        - ``resumed``   — a partial/failed record is re-queued; completed cues
                          are kept, so the worker only translates what is left.
        - ``created``   — a new record plus every source cue, stored *before*
                          any model work, which is what makes recovery possible.
        """
        now = _now()
        with connect(self.db_path) as conn:
            row = conn.execute(
                """
                SELECT id, status FROM videos
                WHERE video_id = ? AND caption_fingerprint = ?
                  AND model = ? AND prompt_version = ?
                """,
                (video_id, caption_fingerprint, model, prompt_version),
            ).fetchone()

            if row is None:
                cursor = conn.execute(
                    """
                    INSERT INTO videos (
                        video_id, title, status, total_cues, model, prompt_version,
                        caption_fingerprint, created_at, updated_at, attempt_count
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
                    """,
                    (
                        video_id,
                        title,
                        STATUS_QUEUED,
                        len(source_cues),
                        model,
                        prompt_version,
                        caption_fingerprint,
                        now,
                        now,
                    ),
                )
                job_id = int(cursor.lastrowid)
                # Every cue starts pending; the worker reclassifies non-speech
                # cues as skipped once cleaning and dedup have run.
                conn.executemany(
                    """
                    INSERT INTO cues (
                        video_record_id, cue_index, start_ms, end_ms,
                        english_text, persian_text, translation_status
                    ) VALUES (?, ?, ?, ?, ?, NULL, ?)
                    """,
                    [
                        (
                            job_id,
                            cue.cue_index,
                            cue.start_ms,
                            cue.end_ms,
                            cue.english_text,
                            CUE_PENDING,
                        )
                        for cue in source_cues
                    ],
                )
                return self._read_job(conn, job_id), "created"

            job_id = int(row["id"])

            if row["status"] == STATUS_COMPLETED:
                failed = conn.execute(
                    "SELECT COUNT(*) AS n FROM cues"
                    " WHERE video_record_id = ? AND translation_status = ?",
                    (job_id, CUE_FAILED),
                ).fetchone()["n"]
                if not failed:
                    return self._read_job(conn, job_id), "cache_hit"

            if row["status"] in ACTIVE_STATUSES:
                return self._read_job(conn, job_id), "reused"

            # partial / failed (or a completed record that somehow holds a
            # failed cue): re-queue and keep whatever was already translated.
            conn.execute(
                """
                UPDATE videos
                SET status = ?, error_code = NULL, error_message = NULL, updated_at = ?
                WHERE id = ?
                """,
                (STATUS_QUEUED, now, job_id),
            )
            return self._read_job(conn, job_id), "resumed"

    def has_active_job(
        self, *, video_id: str, caption_fingerprint: str, model: str, prompt_version: str
    ) -> bool:
        """True when a queued/processing job holds this cache identity.

        Guards the synchronous path: `save()` rewrites a record's cues wholesale,
        which would delete a running job's committed progress.
        """
        with connect(self.db_path) as conn:
            row = conn.execute(
                f"""
                SELECT 1 FROM videos
                WHERE video_id = ? AND caption_fingerprint = ?
                  AND model = ? AND prompt_version = ?
                  AND status IN ({",".join("?" * len(ACTIVE_STATUSES))})
                """,
                (video_id, caption_fingerprint, model, prompt_version, *ACTIVE_STATUSES),
            ).fetchone()
        return row is not None

    def get_job(self, job_id: int) -> JobRecord | None:
        with connect(self.db_path) as conn:
            return self._read_job(conn, job_id)

    def load_source_cues(self, job_id: int) -> list[Cue]:
        """Rebuild the job's source cues from storage, in cue order.

        `english_text` is the loader-normalized text the fingerprint was computed
        over, so cleaning, dedup, and windowing are reproduced exactly.
        """
        with connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT cue_index, start_ms, end_ms, english_text FROM cues"
                " WHERE video_record_id = ? ORDER BY cue_index",
                (job_id,),
            ).fetchall()
        return [
            Cue(
                cue_index=r["cue_index"],
                start_ms=r["start_ms"],
                end_ms=r["end_ms"],
                english_text=r["english_text"],
            )
            for r in rows
        ]

    def completed_indices(self, job_id: int) -> set[int]:
        """Indexes already translated — the cues a resume must not pay for again."""
        with connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT cue_index FROM cues"
                " WHERE video_record_id = ? AND translation_status = ?",
                (job_id, CUE_COMPLETED),
            ).fetchall()
        return {r["cue_index"] for r in rows}

    def mark_processing(self, job_id: int, *, speech_cues: int, skipped_indices: list[int]) -> None:
        """Claim the job and record the progress denominator.

        Non-speech cues move from pending to skipped so "pending" means exactly
        "still owed a translation".
        """
        now = _now()
        with connect(self.db_path) as conn:
            conn.execute(
                "UPDATE videos SET status = ?, speech_cues = ?, updated_at = ? WHERE id = ?",
                (STATUS_PROCESSING, speech_cues, now, job_id),
            )
            if skipped_indices:
                conn.executemany(
                    "UPDATE cues SET translation_status = ?"
                    " WHERE video_record_id = ? AND cue_index = ? AND translation_status = ?",
                    [(CUE_SKIPPED, job_id, idx, CUE_PENDING) for idx in skipped_indices],
                )

    def save_window(
        self, job_id: int, translated: list[TranslatedCue], failed_indices: list[int]
    ) -> None:
        """Commit one window's outcome. Committed means safe to return.

        One transaction per window: a crash loses at most the window in flight,
        never a window that has already been reported as progress.
        """
        if not translated and not failed_indices:
            return
        now = _now()
        with connect(self.db_path) as conn:
            if translated:
                conn.executemany(
                    "UPDATE cues SET persian_text = ?, translation_status = ?"
                    " WHERE video_record_id = ? AND cue_index = ?",
                    [
                        (cue.persian_text, CUE_COMPLETED, job_id, cue.cue_index)
                        for cue in translated
                    ],
                )
            if failed_indices:
                conn.executemany(
                    "UPDATE cues SET translation_status = ?"
                    " WHERE video_record_id = ? AND cue_index = ?",
                    [(CUE_FAILED, job_id, idx) for idx in failed_indices],
                )
            conn.execute("UPDATE videos SET updated_at = ? WHERE id = ?", (now, job_id))

    def finish_job(self, job_id: int) -> JobRecord:
        """Close a run that reached the end of its windows.

        Terminal status comes from persisted cue state rather than one run's
        stats, so a resume that fixes earlier failures correctly ends completed.
        """
        now = _now()
        with connect(self.db_path) as conn:
            # Anything still pending was classified non-speech by this run.
            conn.execute(
                "UPDATE cues SET translation_status = ?"
                " WHERE video_record_id = ? AND translation_status = ?",
                (CUE_SKIPPED, job_id, CUE_PENDING),
            )
            failed = conn.execute(
                "SELECT COUNT(*) AS n FROM cues"
                " WHERE video_record_id = ? AND translation_status = ?",
                (job_id, CUE_FAILED),
            ).fetchone()["n"]

            if failed:
                conn.execute(
                    """
                    UPDATE videos
                    SET status = ?, error_code = ?, error_message = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        STATUS_PARTIAL,
                        "partial_translation",
                        f"{failed} cue(s) could not be translated within the "
                        "bounded retry and split budget.",
                        now,
                        job_id,
                    ),
                )
            else:
                conn.execute(
                    """
                    UPDATE videos
                    SET status = ?, error_code = NULL, error_message = NULL, updated_at = ?
                    WHERE id = ?
                    """,
                    (STATUS_COMPLETED, now, job_id),
                )
            return self._read_job(conn, job_id)

    def fail_job(self, job_id: int, *, error_code: str, error_message: str) -> JobRecord:
        """Mark a job terminally failed, preserving every cue already translated."""
        now = _now()
        with connect(self.db_path) as conn:
            conn.execute(
                """
                UPDATE videos
                SET status = ?, error_code = ?, error_message = ?, updated_at = ?
                WHERE id = ?
                """,
                (STATUS_FAILED, error_code, error_message, now, job_id),
            )
            return self._read_job(conn, job_id)

    def list_resumable(self) -> list[JobRecord]:
        """Jobs left non-terminal by a previous process — the restart sweep's input."""
        with connect(self.db_path) as conn:
            rows = conn.execute(
                f"SELECT id FROM videos WHERE status IN"
                f" ({','.join('?' * len(ACTIVE_STATUSES))}) ORDER BY id",
                ACTIVE_STATUSES,
            ).fetchall()
            return [self._read_job(conn, int(r["id"])) for r in rows]

    def requeue_for_resume(self, job_id: int) -> None:
        """Return an interrupted job to the queue, counting the attempt.

        `attempt_count` is what stops a job that crashes the process from being
        retried forever at the provider's expense.
        """
        now = _now()
        with connect(self.db_path) as conn:
            conn.execute(
                """
                UPDATE videos
                SET status = ?, attempt_count = attempt_count + 1, updated_at = ?
                WHERE id = ?
                """,
                (STATUS_QUEUED, now, job_id),
            )

    def job_cues(self, job_id: int, *, after_cue_index: int | None = None) -> list[TranslatedCue]:
        """Validated cues for a job, optionally only those past a cursor.

        Only rows holding non-empty Persian are returned, so unvalidated text can
        never reach a client. Omitting the cursor returns everything, which is
        the client's final reconciliation read.
        """
        sql = [
            "SELECT cue_index, start_ms, end_ms, english_text, persian_text FROM cues",
            "WHERE video_record_id = ? AND translation_status = ?",
            "AND persian_text IS NOT NULL AND persian_text != ''",
        ]
        params: list = [job_id, CUE_COMPLETED]
        if after_cue_index is not None:
            sql.append("AND cue_index > ?")
            params.append(after_cue_index)
        sql.append("ORDER BY cue_index")

        with connect(self.db_path) as conn:
            rows = conn.execute(" ".join(sql), params).fetchall()
        return [
            TranslatedCue(
                cue_index=r["cue_index"],
                start_ms=r["start_ms"],
                end_ms=r["end_ms"],
                english_text=r["english_text"],
                persian_text=r["persian_text"],
            )
            for r in rows
        ]

    # --- internals ---------------------------------------------------------

    @staticmethod
    def _read_job(conn, job_id: int) -> JobRecord | None:
        row = conn.execute("SELECT * FROM videos WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            return None

        counts = conn.execute(
            """
            SELECT
                COALESCE(SUM(translation_status = ?), 0) AS completed,
                COALESCE(SUM(translation_status = ?), 0) AS failed
            FROM cues WHERE video_record_id = ?
            """,
            (CUE_COMPLETED, CUE_FAILED, job_id),
        ).fetchone()

        failed_rows = conn.execute(
            "SELECT cue_index FROM cues"
            " WHERE video_record_id = ? AND translation_status = ? ORDER BY cue_index",
            (job_id, CUE_FAILED),
        ).fetchall()

        return JobRecord(
            job_id=job_id,
            video_id=row["video_id"],
            title=row["title"],
            status=row["status"],
            model=row["model"],
            prompt_version=row["prompt_version"],
            caption_fingerprint=row["caption_fingerprint"],
            total_cues=row["total_cues"],
            speech_cues=row["speech_cues"],
            completed_cues=int(counts["completed"]),
            failed_cues=int(counts["failed"]),
            attempt_count=row["attempt_count"],
            error_code=row["error_code"],
            error_message=row["error_message"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            failed_indices=[r["cue_index"] for r in failed_rows],
        )
