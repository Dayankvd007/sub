"""Phase 2b-1 — repository persistence, cache identity, and restart safety."""

from subtitle_translator.models import Cue, TranslatedCue
from subtitle_translator.repository import Repository

IDENTITY = dict(
    video_id="vid1",
    caption_fingerprint="fp-abc",
    model="mock-model",
    prompt_version="v1",
)


def source(*indexes):
    return [
        Cue(cue_index=i, start_ms=i * 1000, end_ms=(i + 1) * 1000, english_text=f"line {i}")
        for i in indexes
    ]


def translated(*indexes):
    return [
        TranslatedCue(
            cue_index=i,
            start_ms=i * 1000,
            end_ms=(i + 1) * 1000,
            english_text=f"line {i}",
            persian_text=f"fa {i}",
        )
        for i in indexes
    ]


def save_complete(repo, **overrides):
    args = dict(
        **IDENTITY,
        title="A video",
        source_cues=source(0, 1, 2),
        translated_cues=translated(0, 1, 2),
        failed_indices=[],
    )
    args.update(overrides)
    repo.save(**args)


def test_roundtrip_returns_stored_cues(tmp_path):
    repo = Repository(tmp_path / "db.sqlite")
    save_complete(repo)

    cached = repo.find_completed(**IDENTITY)
    assert cached is not None
    assert cached.total_cues == 3
    assert [c.cue_index for c in cached.translated_cues] == [0, 1, 2]
    assert [c.persian_text for c in cached.translated_cues] == ["fa 0", "fa 1", "fa 2"]


def test_miss_when_nothing_stored(tmp_path):
    repo = Repository(tmp_path / "db.sqlite")
    assert repo.find_completed(**IDENTITY) is None


def test_each_identity_field_invalidates_the_cache(tmp_path):
    repo = Repository(tmp_path / "db.sqlite")
    save_complete(repo)

    for field, value in (
        ("video_id", "other-video"),
        ("caption_fingerprint", "fp-different"),
        ("model", "other-model"),
        ("prompt_version", "v2"),
    ):
        probe = dict(IDENTITY)
        probe[field] = value
        assert repo.find_completed(**probe) is None, f"{field} must invalidate the cache"

    assert repo.find_completed(**IDENTITY) is not None


def test_partial_result_is_never_served_as_a_hit(tmp_path):
    repo = Repository(tmp_path / "db.sqlite")
    repo.save(
        **IDENTITY,
        title="A video",
        source_cues=source(0, 1, 2),
        translated_cues=translated(0, 1),
        failed_indices=[2],
    )
    assert repo.find_completed(**IDENTITY) is None


def test_retranslation_upserts_instead_of_duplicating(tmp_path):
    repo = Repository(tmp_path / "db.sqlite")
    repo.save(
        **IDENTITY,
        title="A video",
        source_cues=source(0, 1, 2),
        translated_cues=translated(0, 1),
        failed_indices=[2],
    )
    save_complete(repo)  # same identity, now complete

    cached = repo.find_completed(**IDENTITY)
    assert cached is not None
    assert len(cached.translated_cues) == 3


def test_source_cues_are_persisted_for_non_speech_and_failed(tmp_path):
    """Decision E: every source cue is stored, so the index map stays complete."""
    db = tmp_path / "db.sqlite"
    repo = Repository(db)
    repo.save(
        **IDENTITY,
        title="A video",
        source_cues=source(0, 1, 2),
        translated_cues=translated(0, 2),  # index 1 never translated
        failed_indices=[1],
    )

    from subtitle_translator.db import connect

    with connect(db) as conn:
        rows = conn.execute(
            "SELECT cue_index, english_text, persian_text, translation_status"
            " FROM cues ORDER BY cue_index"
        ).fetchall()

    assert [r["cue_index"] for r in rows] == [0, 1, 2]
    assert all(r["english_text"] for r in rows)
    assert rows[1]["persian_text"] is None
    assert rows[1]["translation_status"] == "failed"


def test_survives_restart(tmp_path):
    """A fresh Repository against the same file sees previously stored work."""
    db = tmp_path / "db.sqlite"
    save_complete(Repository(db))

    reopened = Repository(db)
    cached = reopened.find_completed(**IDENTITY)

    assert cached is not None
    assert len(cached.translated_cues) == 3
