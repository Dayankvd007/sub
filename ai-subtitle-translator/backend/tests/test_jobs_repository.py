"""Phase 2b-2 — job persistence: creation, idempotency, window commits, recovery."""

from subtitle_translator.models import Cue, TranslatedCue
from subtitle_translator.repository import (
    CUE_COMPLETED,
    CUE_FAILED,
    CUE_PENDING,
    CUE_SKIPPED,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_PARTIAL,
    STATUS_PROCESSING,
    STATUS_QUEUED,
    Repository,
)

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


def create(repo, cues=None, **overrides):
    args = dict(**IDENTITY, title="A video", source_cues=cues or source(0, 1, 2))
    args.update(overrides)
    return repo.create_or_resume_job(**args)


def cue_statuses(repo, job_id):
    from subtitle_translator.db import connect

    with connect(repo.db_path) as conn:
        rows = conn.execute(
            "SELECT cue_index, translation_status FROM cues"
            " WHERE video_record_id = ? ORDER BY cue_index",
            (job_id,),
        ).fetchall()
    return {r["cue_index"]: r["translation_status"] for r in rows}


# --- creation and idempotency ---------------------------------------------


def test_create_persists_source_cues_before_any_model_work(tmp_path):
    repo = Repository(tmp_path / "db.sqlite")
    job, action = create(repo)

    assert action == "created"
    assert job.status == STATUS_QUEUED
    assert job.total_cues == 3
    assert job.speech_cues is None, "the worker computes this after cleaning"
    assert job.completed_cues == 0
    # Storing every cue up front is what makes restart recovery possible.
    assert cue_statuses(repo, job.job_id) == {0: CUE_PENDING, 1: CUE_PENDING, 2: CUE_PENDING}


def test_repeat_create_reuses_the_active_job(tmp_path):
    repo = Repository(tmp_path / "db.sqlite")
    first, _ = create(repo)
    second, action = create(repo)

    assert action == "reused"
    assert second.job_id == first.job_id, "the cache identity is the idempotency key"


def test_completed_job_is_a_cache_hit(tmp_path):
    repo = Repository(tmp_path / "db.sqlite")
    job, _ = create(repo)
    repo.mark_processing(job.job_id, speech_cues=3, skipped_indices=[])
    repo.save_window(job.job_id, translated(0, 1, 2), [])
    repo.finish_job(job.job_id)

    again, action = create(repo)

    assert action == "cache_hit"
    assert again.status == STATUS_COMPLETED
    assert again.job_id == job.job_id


def test_partial_job_is_resumed_keeping_completed_cues(tmp_path):
    repo = Repository(tmp_path / "db.sqlite")
    job, _ = create(repo)
    repo.mark_processing(job.job_id, speech_cues=3, skipped_indices=[])
    repo.save_window(job.job_id, translated(0, 1), [2])
    repo.finish_job(job.job_id)
    assert repo.get_job(job.job_id).status == STATUS_PARTIAL

    resumed, action = create(repo)

    assert action == "resumed"
    assert resumed.status == STATUS_QUEUED
    assert resumed.completed_cues == 2, "already-translated cues are never discarded"
    assert repo.completed_indices(job.job_id) == {0, 1}


def test_a_different_identity_creates_a_separate_job(tmp_path):
    repo = Repository(tmp_path / "db.sqlite")
    first, _ = create(repo)
    other, action = create(repo, caption_fingerprint="fp-different")

    assert action == "created"
    assert other.job_id != first.job_id


# --- progress and window commits ------------------------------------------


def test_mark_processing_records_the_denominator_and_skips_non_speech(tmp_path):
    repo = Repository(tmp_path / "db.sqlite")
    job, _ = create(repo)

    repo.mark_processing(job.job_id, speech_cues=2, skipped_indices=[1])
    reread = repo.get_job(job.job_id)

    assert reread.status == STATUS_PROCESSING
    assert reread.speech_cues == 2
    assert cue_statuses(repo, job.job_id)[1] == CUE_SKIPPED


def test_each_window_is_committed_independently(tmp_path):
    repo = Repository(tmp_path / "db.sqlite")
    job, _ = create(repo, cues=source(0, 1, 2, 3))
    repo.mark_processing(job.job_id, speech_cues=4, skipped_indices=[])

    repo.save_window(job.job_id, translated(0, 1), [])
    after_first = repo.get_job(job.job_id)
    repo.save_window(job.job_id, translated(2, 3), [])
    after_second = repo.get_job(job.job_id)

    assert after_first.completed_cues == 2
    assert after_first.status == STATUS_PROCESSING, "still running, not yet terminal"
    assert after_second.completed_cues == 4
    assert [c.cue_index for c in repo.job_cues(job.job_id)] == [0, 1, 2, 3]


def test_percent_is_measured_against_speech_cues(tmp_path):
    repo = Repository(tmp_path / "db.sqlite")
    job, _ = create(repo, cues=source(0, 1, 2, 3))
    # Two of the four cues are non-speech, so they must not drag progress down.
    repo.mark_processing(job.job_id, speech_cues=2, skipped_indices=[2, 3])

    repo.save_window(job.job_id, translated(0), [])
    assert repo.get_job(job.job_id).percent == 50.0

    repo.save_window(job.job_id, translated(1), [])
    assert repo.get_job(job.job_id).percent == 100.0


def test_finish_job_marks_completed_and_skips_leftover_pending(tmp_path):
    repo = Repository(tmp_path / "db.sqlite")
    job, _ = create(repo)
    repo.mark_processing(job.job_id, speech_cues=2, skipped_indices=[])
    repo.save_window(job.job_id, translated(0, 1), [])

    final = repo.finish_job(job.job_id)

    assert final.status == STATUS_COMPLETED
    assert final.error_code is None
    # Cue 2 was never translated, so it was non-speech for this run.
    assert cue_statuses(repo, job.job_id)[2] == CUE_SKIPPED


def test_finish_job_reports_partial_when_a_cue_failed(tmp_path):
    repo = Repository(tmp_path / "db.sqlite")
    job, _ = create(repo)
    repo.mark_processing(job.job_id, speech_cues=3, skipped_indices=[])
    repo.save_window(job.job_id, translated(0, 1), [2])

    final = repo.finish_job(job.job_id)

    assert final.status == STATUS_PARTIAL
    assert final.error_code == "partial_translation"
    assert final.failed_indices == [2]
    assert cue_statuses(repo, job.job_id)[2] == CUE_FAILED


def test_a_resume_that_fixes_a_failure_ends_completed(tmp_path):
    """Terminal status comes from persisted state, not from one run's stats."""
    repo = Repository(tmp_path / "db.sqlite")
    job, _ = create(repo)
    repo.mark_processing(job.job_id, speech_cues=3, skipped_indices=[])
    repo.save_window(job.job_id, translated(0, 1), [2])
    repo.finish_job(job.job_id)

    create(repo)  # resume
    repo.save_window(job.job_id, translated(2), [])
    final = repo.finish_job(job.job_id)

    assert final.status == STATUS_COMPLETED
    assert final.failed_indices == []


def test_fail_job_preserves_translated_cues(tmp_path):
    repo = Repository(tmp_path / "db.sqlite")
    job, _ = create(repo)
    repo.mark_processing(job.job_id, speech_cues=3, skipped_indices=[])
    repo.save_window(job.job_id, translated(0), [])

    failed = repo.fail_job(job.job_id, error_code="provider_error", error_message="unreachable")

    assert failed.status == STATUS_FAILED
    assert failed.error_code == "provider_error"
    assert failed.completed_cues == 1
    assert [c.cue_index for c in repo.job_cues(job.job_id)] == [0]


# --- cursor reads ----------------------------------------------------------


def test_cursor_returns_only_newer_cues(tmp_path):
    repo = Repository(tmp_path / "db.sqlite")
    job, _ = create(repo, cues=source(0, 1, 2, 3))
    repo.mark_processing(job.job_id, speech_cues=4, skipped_indices=[])
    repo.save_window(job.job_id, translated(0, 1, 2, 3), [])

    assert [c.cue_index for c in repo.job_cues(job.job_id, after_cue_index=1)] == [2, 3]
    assert [c.cue_index for c in repo.job_cues(job.job_id, after_cue_index=3)] == []
    # No cursor is the client's final reconciliation read.
    assert [c.cue_index for c in repo.job_cues(job.job_id)] == [0, 1, 2, 3]


def test_only_validated_cues_are_readable(tmp_path):
    repo = Repository(tmp_path / "db.sqlite")
    job, _ = create(repo)
    repo.mark_processing(job.job_id, speech_cues=3, skipped_indices=[])
    repo.save_window(job.job_id, translated(0), [1])

    # Failed and untouched cues never surface as translations.
    assert [c.cue_index for c in repo.job_cues(job.job_id)] == [0]


# --- guards and recovery ---------------------------------------------------


def test_has_active_job_tracks_the_lifecycle(tmp_path):
    repo = Repository(tmp_path / "db.sqlite")
    assert repo.has_active_job(**IDENTITY) is False

    job, _ = create(repo)
    assert repo.has_active_job(**IDENTITY) is True

    repo.mark_processing(job.job_id, speech_cues=3, skipped_indices=[])
    assert repo.has_active_job(**IDENTITY) is True

    repo.save_window(job.job_id, translated(0, 1, 2), [])
    repo.finish_job(job.job_id)
    assert repo.has_active_job(**IDENTITY) is False, "a terminal job is not active"


def test_list_resumable_finds_only_unfinished_jobs(tmp_path):
    repo = Repository(tmp_path / "db.sqlite")
    interrupted, _ = create(repo)
    repo.mark_processing(interrupted.job_id, speech_cues=3, skipped_indices=[])

    done, _ = create(repo, video_id="vid2")
    repo.mark_processing(done.job_id, speech_cues=3, skipped_indices=[])
    repo.save_window(done.job_id, translated(0, 1, 2), [])
    repo.finish_job(done.job_id)

    assert [j.job_id for j in repo.list_resumable()] == [interrupted.job_id]


def test_requeue_counts_the_attempt(tmp_path):
    repo = Repository(tmp_path / "db.sqlite")
    job, _ = create(repo)
    repo.mark_processing(job.job_id, speech_cues=3, skipped_indices=[])

    repo.requeue_for_resume(job.job_id)
    reread = repo.get_job(job.job_id)

    assert reread.status == STATUS_QUEUED
    assert reread.attempt_count == 1


def test_job_survives_a_reopened_repository(tmp_path):
    db = tmp_path / "db.sqlite"
    repo = Repository(db)
    job, _ = create(repo)
    repo.mark_processing(job.job_id, speech_cues=3, skipped_indices=[])
    repo.save_window(job.job_id, translated(0), [])

    reopened = Repository(db)
    reread = reopened.get_job(job.job_id)

    assert reread.completed_cues == 1
    assert reopened.completed_indices(job.job_id) == {0}
    assert cue_statuses(reopened, job.job_id)[0] == CUE_COMPLETED
