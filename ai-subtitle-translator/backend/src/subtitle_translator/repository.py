"""Persistence and cache lookup for translated videos.

Cache identity is `video_id` + `caption_fingerprint` + `model` + `prompt_version`
(architecture §7). Changing any one of them is a different identity, so an old
result is never silently returned as current.

Only a fully completed record is ever served as a cache hit. Partial results are
persisted — they are the groundwork for Phase 2b-2 recovery — but are treated as
a miss and retranslated in place. Resuming partial work is explicitly Phase 2b-2.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .db import connect, init_schema
from .models import Cue, TranslatedCue

STATUS_COMPLETED = "completed"
STATUS_PARTIAL = "partial"

CUE_COMPLETED = "completed"
CUE_FAILED = "failed"
CUE_PENDING = "pending"


@dataclass
class CachedTranslation:
    """A completed record, reconstructed for reuse."""

    video_id: str
    title: str | None
    model: str
    prompt_version: str
    total_cues: int
    translated_cues: list[TranslatedCue]


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
