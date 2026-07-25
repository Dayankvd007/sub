"""Phase 2b-2 — the job runner and worker: progressive commits, retries, recovery.

Everything here is offline and synchronous: `JobWorker.process_one` runs the job
on the calling thread, so ordering is deterministic and no test sleeps on a
background thread. The worker thread itself is exercised separately at the end.
"""

import threading
import time

from subtitle_translator.jobs import JobRunner, JobService, JobWorker
from subtitle_translator.models import Cue
from subtitle_translator.providers import MockProvider, ProviderError
from subtitle_translator.repository import (
    CUE_SKIPPED,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_PARTIAL,
    STATUS_PROCESSING,
    STATUS_QUEUED,
    Repository,
)

IDENTITY = dict(model="mock-model", prompt_version="v1")


def payload(count=6, *, text=lambda i: f"Sentence number {i}."):
    return [
        {
            "cue_index": i,
            "start_ms": i * 1000,
            "end_ms": (i + 1) * 1000,
            "english_text": text(i),
        }
        for i in range(count)
    ]


def make_service(repo):
    return JobService(repo, model="mock-model", provider_name="mock")


def make_runner(repo, provider, *, target_size=2, **kwargs):
    """A runner with small windows, so a short fixture still spans several."""
    kwargs.setdefault("retry_backoff", (0.0, 0.0))
    return JobRunner(
        repo,
        provider_factory=lambda: provider,
        provider_name="mock",
        model="mock-model",
        target_size=target_size,
        max_size=target_size,
        context=1,
        **kwargs,
    )


def create_job(repo, cues=None, video_id="vid1"):
    outcome = make_service(repo).create_or_reuse(
        video_id=video_id, cues_payload=cues or payload(), title="A video"
    )
    return outcome.record.job_id


# --- a full run ------------------------------------------------------------


def test_full_job_completes_and_translates_every_speech_cue(tmp_path):
    repo = Repository(tmp_path / "db.sqlite")
    job_id = create_job(repo)
    provider = MockProvider()

    final = make_runner(repo, provider).run(job_id)

    assert final.status == STATUS_COMPLETED
    assert final.speech_cues == 6
    assert final.completed_cues == 6
    assert final.percent == 100.0
    assert [c.cue_index for c in repo.job_cues(job_id)] == list(range(6))
    assert len(provider.calls) == 3, "six cues at two per window"


def test_results_are_committed_window_by_window(tmp_path):
    """Progress must be persisted as it happens, not accumulated in memory."""
    repo = Repository(tmp_path / "db.sqlite")
    job_id = create_job(repo)

    seen_before_each_call = []

    class ObservingProvider(MockProvider):
        def translate(self, request):
            seen_before_each_call.append(repo.get_job(job_id).completed_cues)
            return super().translate(request)

    make_runner(repo, ObservingProvider()).run(job_id)

    # Window N starts with exactly the previous windows' cues already durable.
    assert seen_before_each_call == [0, 2, 4]


def test_a_partially_run_job_is_readable_mid_flight(tmp_path):
    repo = Repository(tmp_path / "db.sqlite")
    job_id = create_job(repo)
    snapshots = []

    class ObservingProvider(MockProvider):
        def translate(self, request):
            job = repo.get_job(job_id)
            snapshots.append((job.status, [c.cue_index for c in repo.job_cues(job_id)]))
            return super().translate(request)

    make_runner(repo, ObservingProvider()).run(job_id)

    assert snapshots[1] == (STATUS_PROCESSING, [0, 1])
    assert snapshots[2] == (STATUS_PROCESSING, [0, 1, 2, 3])


def test_non_speech_cues_are_skipped_not_left_pending(tmp_path):
    repo = Repository(tmp_path / "db.sqlite")
    cues = payload(4)
    cues[2]["english_text"] = "[Music]"
    job_id = create_job(repo, cues)

    final = make_runner(repo, MockProvider()).run(job_id)

    assert final.status == STATUS_COMPLETED
    assert final.speech_cues == 3, "the music marker is not a translation target"
    from subtitle_translator.db import connect

    with connect(repo.db_path) as conn:
        status = conn.execute(
            "SELECT translation_status FROM cues WHERE video_record_id = ? AND cue_index = 2",
            (job_id,),
        ).fetchone()[0]
    assert status == CUE_SKIPPED


# --- resume ---------------------------------------------------------------


def test_resume_never_pays_for_already_translated_cues(tmp_path):
    repo = Repository(tmp_path / "db.sqlite")
    job_id = create_job(repo)

    # First run stops after one window, as an interruption would.
    class StopAfterOne(MockProvider):
        def translate(self, request):
            if len(self.calls) >= 1:
                raise ProviderError("connection lost")
            return super().translate(request)

    make_runner(repo, StopAfterOne(), provider_retries=0).run(job_id)
    assert repo.completed_indices(job_id) == {0, 1}

    # Resume with a fresh provider and watch exactly which indexes it is asked for.
    asked = []

    class Recording(MockProvider):
        def translate(self, request):
            asked.extend(request.required_indices)
            return super().translate(request)

    repo.requeue_for_resume(job_id)
    final = make_runner(repo, Recording()).run(job_id)

    assert final.status == STATUS_COMPLETED
    assert sorted(asked) == [2, 3, 4, 5], "already-committed cues are never re-sent"


def test_resuming_a_finished_job_does_no_work(tmp_path):
    repo = Repository(tmp_path / "db.sqlite")
    job_id = create_job(repo)
    make_runner(repo, MockProvider()).run(job_id)

    second = MockProvider()
    repo.requeue_for_resume(job_id)
    final = make_runner(repo, second).run(job_id)

    assert final.status == STATUS_COMPLETED
    assert second.calls == [], "nothing left to translate means no provider call"


def test_a_terminal_job_is_not_rerun(tmp_path):
    repo = Repository(tmp_path / "db.sqlite")
    job_id = create_job(repo)
    make_runner(repo, MockProvider()).run(job_id)

    second = MockProvider()
    make_runner(repo, second).run(job_id)  # still 'completed', never re-queued

    assert second.calls == []


# --- failure handling ------------------------------------------------------


def test_provider_failure_is_retried_then_recovers(tmp_path):
    repo = Repository(tmp_path / "db.sqlite")
    job_id = create_job(repo)

    class FlakyProvider(MockProvider):
        def __init__(self):
            super().__init__()
            self.failures = 0

        def translate(self, request):
            # Fail once part-way in, then behave.
            if self.failures == 0 and len(self.calls) == 2:
                self.failures += 1
                raise ProviderError("temporary network failure")
            return super().translate(request)

    final = make_runner(repo, FlakyProvider()).run(job_id)

    assert final.status == STATUS_COMPLETED
    assert final.completed_cues == 6


def test_persistent_provider_failure_fails_the_job_but_keeps_cues(tmp_path):
    repo = Repository(tmp_path / "db.sqlite")
    job_id = create_job(repo)

    class FailsAfterFirstWindow(MockProvider):
        def translate(self, request):
            if len(self.calls) >= 1:
                raise ProviderError("provider unreachable")
            return super().translate(request)

    provider = FailsAfterFirstWindow()
    final = make_runner(repo, provider, provider_retries=2).run(job_id)

    assert final.status == STATUS_FAILED
    assert final.error_code == "provider_error"
    assert "unreachable" not in (final.error_message or ""), "upstream text is not echoed"
    assert final.completed_cues == 2, "committed work survives a terminal failure"
    assert [c.cue_index for c in repo.job_cues(job_id)] == [0, 1]


def test_unfixable_model_output_ends_partial_and_is_never_returned(tmp_path):
    repo = Repository(tmp_path / "db.sqlite")
    job_id = create_job(repo)

    final = make_runner(repo, MockProvider(fault="always_bad")).run(job_id)

    assert final.status == STATUS_PARTIAL
    assert final.error_code == "partial_translation"
    assert final.failed_indices, "failures are reported, not faked"
    readable = {c.cue_index for c in repo.job_cues(job_id)}
    assert readable.isdisjoint(final.failed_indices), "invalid output never reaches a client"


# --- worker: recovery sweep and the thread --------------------------------


def make_worker(repo, provider, **kwargs):
    stop_event = threading.Event()
    runner = make_runner(repo, provider, stop_event=stop_event)
    return JobWorker(repo, runner, stop_event=stop_event, **kwargs)


def test_recovery_requeues_a_job_left_processing(tmp_path):
    """Simulates a restart: the record is mid-flight and the queue is empty."""
    repo = Repository(tmp_path / "db.sqlite")
    job_id = create_job(repo)
    repo.mark_processing(job_id, speech_cues=6, skipped_indices=[])
    repo.save_window(job_id, [], [])

    worker = make_worker(repo, MockProvider())
    requeued = worker.recover()

    assert requeued == [job_id]
    reread = repo.get_job(job_id)
    assert reread.status == STATUS_QUEUED
    assert reread.attempt_count == 1


def test_recovery_resumes_without_retranslating(tmp_path):
    repo = Repository(tmp_path / "db.sqlite")
    job_id = create_job(repo)

    class DiesAfterOne(MockProvider):
        def translate(self, request):
            if len(self.calls) >= 1:
                raise ProviderError("process died")
            return super().translate(request)

    make_runner(repo, DiesAfterOne(), provider_retries=0).run(job_id)
    repo.mark_processing(job_id, speech_cues=6, skipped_indices=[])  # left mid-flight

    provider = MockProvider()
    worker = make_worker(repo, provider)
    for resumed_id in worker.recover():
        worker.process_one(resumed_id)

    final = repo.get_job(job_id)
    assert final.status == STATUS_COMPLETED
    assert len(provider.calls) == 2, "only the two unfinished windows are translated"


def test_recovery_gives_up_after_the_attempt_limit(tmp_path):
    repo = Repository(tmp_path / "db.sqlite")
    job_id = create_job(repo)
    repo.mark_processing(job_id, speech_cues=6, skipped_indices=[])
    repo.save_window(job_id, [], [])

    worker = make_worker(repo, MockProvider(), max_resume_attempts=2)
    worker.recover()  # attempt 1
    repo.mark_processing(job_id, speech_cues=6, skipped_indices=[])
    worker.recover()  # attempt 2
    repo.mark_processing(job_id, speech_cues=6, skipped_indices=[])
    assert worker.recover() == []  # limit reached

    final = repo.get_job(job_id)
    assert final.status == STATUS_FAILED
    assert final.error_code == "resume_limit_exceeded"


def test_a_duplicate_doorbell_ring_does_not_translate_twice(tmp_path):
    repo = Repository(tmp_path / "db.sqlite")
    job_id = create_job(repo)
    provider = MockProvider()
    worker = make_worker(repo, provider)

    worker.process_one(job_id)
    calls_after_first = len(provider.calls)
    worker.process_one(job_id)  # the same id delivered again

    assert len(provider.calls) == calls_after_first


def test_completed_jobs_are_not_resumable(tmp_path):
    repo = Repository(tmp_path / "db.sqlite")
    job_id = create_job(repo)
    worker = make_worker(repo, MockProvider())
    worker.process_one(job_id)

    assert worker.recover() == []


def test_worker_thread_drains_the_queue(tmp_path):
    """The one test that uses the real thread, to prove start/stop works."""
    repo = Repository(tmp_path / "db.sqlite")
    job_id = create_job(repo)

    worker = make_worker(repo, MockProvider())
    worker.start()
    try:
        worker.enqueue(job_id)
        # Wait for the *terminal* state: stopping earlier would legitimately
        # abandon the run between windows, which is a different test.
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not repo.get_job(job_id).done:
            time.sleep(0.02)
        assert repo.get_job(job_id).done, "worker did not finish the job"
    finally:
        worker.stop(timeout=10)

    final = repo.get_job(job_id)
    assert final.status == STATUS_COMPLETED
    assert final.completed_cues == 6


def test_shutdown_between_windows_keeps_the_job_resumable(tmp_path):
    repo = Repository(tmp_path / "db.sqlite")
    job_id = create_job(repo)
    stop_event = threading.Event()

    class StopsTheWorld(MockProvider):
        def translate(self, request):
            result = super().translate(request)
            if len(self.calls) == 1:
                stop_event.set()  # shutdown requested during the first window
            return result

    runner = make_runner(repo, StopsTheWorld(), stop_event=stop_event)
    runner.run(job_id)

    reread = repo.get_job(job_id)
    assert reread.status == STATUS_PROCESSING, "left non-terminal for the next start"
    assert reread.completed_cues == 2, "the window in flight still committed"
