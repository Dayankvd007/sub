"""SQLite connection handling and schema bootstrap.

The schema follows `docs/technical-architecture.md` §7 — two tables, no
job/billing/audit tables. Phase 2b-2 keeps that shape: a `videos` record *is* a
job record (`videos.id` is the `job_id`), so the job system adds four nullable
columns rather than a table.

Threading: FastAPI runs `def` handlers in a threadpool, so requests already
arrive on different threads, and the Phase 2b-2 worker adds one more. Rather
than sharing one connection with `check_same_thread=False` plus a lock, every
operation opens a short-lived connection through `connect()`. There is no
shared mutable state to guard.
"""

from __future__ import annotations

import os
import sqlite3
import sys
from contextlib import contextmanager
from pathlib import Path

# Bumped when the schema changes. 1 = Phase 2b-1 (implicit; never stamped),
# 2 = Phase 2b-2 job columns.
SCHEMA_VERSION = 2

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
    updated_at          TEXT    NOT NULL,
    -- Phase 2b-2. Nullable/defaulted so an existing database can be migrated
    -- in place with ALTER TABLE ADD COLUMN (see _migrate).
    speech_cues         INTEGER,
    error_code          TEXT,
    error_message       TEXT,
    attempt_count       INTEGER NOT NULL DEFAULT 0
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


# Columns added in schema version 2. Each entry must be a valid
# `ALTER TABLE videos ADD COLUMN <name> <definition>` — so any NOT NULL column
# needs a constant DEFAULT, which SQLite requires for ADD COLUMN.
_V2_VIDEO_COLUMNS = (
    ("speech_cues", "INTEGER"),
    ("error_code", "TEXT"),
    ("error_message", "TEXT"),
    ("attempt_count", "INTEGER NOT NULL DEFAULT 0"),
)


def _migrate(conn: sqlite3.Connection) -> None:
    """Bring an existing database up to SCHEMA_VERSION. Idempotent, forward-only.

    `CREATE TABLE IF NOT EXISTS` does nothing to a table that already exists, so
    a Phase 2b-1 database needs the new columns added explicitly. No data
    backfill is required: every pre-existing record was written by the
    synchronous path and is already terminal, so NULL job columns are correct
    for it and the recovery sweep never selects it.
    """
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version >= SCHEMA_VERSION:
        return

    existing = {row["name"] for row in conn.execute("PRAGMA table_info(videos)")}
    for name, definition in _V2_VIDEO_COLUMNS:
        if name not in existing:
            conn.execute(f"ALTER TABLE videos ADD COLUMN {name} {definition}")

    # PRAGMA does not accept a bound parameter; the value is a module constant.
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")


def init_schema(db_path: str | Path) -> None:
    with connect(db_path) as conn:
        conn.executescript(SCHEMA)
        _migrate(conn)
