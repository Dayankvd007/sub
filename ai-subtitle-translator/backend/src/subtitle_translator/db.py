"""SQLite connection handling and schema bootstrap.

Phase 2b-1: persistence and cache only. The schema follows
`docs/technical-architecture.md` §7 exactly — two tables, no job/billing/audit
tables. The `status` column already accommodates Phase 2b-2's `queued` and
`processing` values, so adding the job system needs no migration.

Threading: FastAPI runs `def` handlers in a threadpool, so requests already
arrive on different threads. Rather than sharing one connection with
`check_same_thread=False` plus a lock, every operation opens a short-lived
connection through `connect()`. There is no shared mutable state, which stays
correct when the Phase 2b-2 worker thread is added.
"""

from __future__ import annotations

import os
import sqlite3
import sys
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS videos (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id            TEXT    NOT NULL,
    title               TEXT,
    status              TEXT    NOT NULL,
    total_cues          INTEGER NOT NULL,
    model               TEXT    NOT NULL,
    prompt_version      TEXT    NOT NULL,
    caption_fingerprint TEXT    NOT NULL,
    created_at          TEXT    NOT NULL,
    updated_at          TEXT    NOT NULL
);

-- One record per cache identity. UNIQUE (rather than a plain index) makes the
-- identity authoritative and gives upsert-on-retranslate for free.
CREATE UNIQUE INDEX IF NOT EXISTS idx_videos_cache_identity
    ON videos (video_id, caption_fingerprint, model, prompt_version);

CREATE TABLE IF NOT EXISTS cues (
    video_record_id     INTEGER NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
    cue_index           INTEGER NOT NULL,
    start_ms            INTEGER NOT NULL CHECK (start_ms >= 0),
    end_ms              INTEGER NOT NULL CHECK (end_ms > start_ms),
    english_text        TEXT    NOT NULL,
    persian_text        TEXT,
    translation_status  TEXT    NOT NULL,
    PRIMARY KEY (video_record_id, cue_index)
);
"""


def default_db_path() -> Path:
    """Per-user data directory — never inside the repository."""
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return base / "subtitle-translator" / "translations.db"


@contextmanager
def connect(db_path: str | Path):
    """Open a short-lived connection, committing on success and rolling back on error."""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_schema(db_path: str | Path) -> None:
    with connect(db_path) as conn:
        conn.executescript(SCHEMA)
