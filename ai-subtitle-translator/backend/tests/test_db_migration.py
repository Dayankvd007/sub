"""Phase 2b-2 — the schema-version migration onto an existing 2b-1 database."""

import sqlite3

from subtitle_translator.db import SCHEMA_VERSION, connect, init_schema

JOB_COLUMNS = {"speech_cues", "error_code", "error_message", "attempt_count"}

# The Phase 2b-1 videos table, verbatim — what an existing local database holds.
V1_SCHEMA = """
CREATE TABLE videos (
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
CREATE TABLE cues (
    video_record_id     INTEGER NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
    cue_index           INTEGER NOT NULL,
    start_ms            INTEGER NOT NULL,
    end_ms              INTEGER NOT NULL,
    english_text        TEXT    NOT NULL,
    persian_text        TEXT,
    translation_status  TEXT    NOT NULL,
    PRIMARY KEY (video_record_id, cue_index)
);
"""


def columns(db, table="videos"):
    with connect(db) as conn:
        return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}


def user_version(db):
    with connect(db) as conn:
        return conn.execute("PRAGMA user_version").fetchone()[0]


def make_v1_database(db):
    conn = sqlite3.connect(db)
    conn.executescript(V1_SCHEMA)
    conn.execute(
        "INSERT INTO videos (video_id, title, status, total_cues, model,"
        " prompt_version, caption_fingerprint, created_at, updated_at)"
        " VALUES ('vid1', 'Old video', 'completed', 2, 'mock-model', 'v1', 'fp', 'then', 'then')"
    )
    conn.execute(
        "INSERT INTO cues VALUES (1, 0, 0, 1000, 'hello', 'fa hello', 'completed')"
    )
    conn.commit()
    conn.close()


def test_fresh_database_has_the_job_columns(tmp_path):
    db = tmp_path / "db.sqlite"
    init_schema(db)

    assert JOB_COLUMNS <= columns(db)
    assert user_version(db) == SCHEMA_VERSION


def test_existing_database_is_upgraded_in_place(tmp_path):
    db = tmp_path / "db.sqlite"
    make_v1_database(db)
    assert not (JOB_COLUMNS & columns(db)), "precondition: a 2b-1 database"

    init_schema(db)

    assert JOB_COLUMNS <= columns(db)
    assert user_version(db) == SCHEMA_VERSION


def test_migration_preserves_existing_rows(tmp_path):
    db = tmp_path / "db.sqlite"
    make_v1_database(db)

    init_schema(db)

    with connect(db) as conn:
        video = conn.execute("SELECT * FROM videos").fetchone()
        cue = conn.execute("SELECT * FROM cues").fetchone()

    assert video["video_id"] == "vid1"
    assert video["status"] == "completed"
    # No backfill: a pre-existing record is already terminal, so NULL job
    # columns are correct for it and the recovery sweep never selects it.
    assert video["speech_cues"] is None
    assert video["error_code"] is None
    assert video["attempt_count"] == 0
    assert cue["persian_text"] == "fa hello"


def test_migration_is_idempotent(tmp_path):
    db = tmp_path / "db.sqlite"
    make_v1_database(db)

    for _ in range(3):
        init_schema(db)

    assert JOB_COLUMNS <= columns(db)
    with connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) AS n FROM videos").fetchone()["n"] == 1
