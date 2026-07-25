"""Phase 2b-2 — POST /jobs, GET /jobs/{id}, polling, and the /translate guard."""

import threading

import pytest

pytest.importorskip("fastapi", reason="needs the [api] extra")

from conftest import make_client, make_settings  # noqa: E402

from subtitle_translator.jobs import JobRunner, JobService, JobWorker  # noqa: E402
from subtitle_translator.providers import MockProvider, ProviderError  # noqa: E402
from subtitle_translator.repository import Repository  # noqa: E402


def payload(count=6):
    return [
        {
            "cue_index": i,
            "start_ms": i * 1000,
            "end_ms": (i + 1) * 1000,
            "english_text": f"Sentence number {i}.",
        }
        for i in range(count)
    ]


class HookableProvider(MockProvider):
    """MockProvider with a hook that fires *before* each window is translated.

    Lets a test poll the API from between two windows, which is the only way to
    observe progressive delivery deterministically without a real thread.
    """

    def __init__(self):
        super().__init__()
        self.hook = None

    def translate(self, request):
        if self.hook is not None:
            self.hook()
        return super().translate(request)


class ManualWorker(JobWorker):
    """A worker with no thread: the test decides exactly when a job runs.

    Keeps every API test deterministic — no sleeping, no polling for a
    background thread — while still exercising the real runner.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.enqueued = []

    def enqueue(self, job_id):
        self.enqueued.append(job_id)

    def drain(self):
        while self.enqueued:
            self.process_one(self.enqueued.pop(0))


@pytest.fixture
def jobs_client(tmp_path):
    """Client, worker, and provider sharing one temporary database."""
    repository = Repository(tmp_path / "db.sqlite")
    provider = HookableProvider()
    settings = make_settings()

    job_service = JobService(repository, model=settings.model, provider_name=settings.provider)
    runner = JobRunner(
        repository,
        provider_factory=lambda: provider,
        provider_name=settings.provider,
        model=settings.model,
        target_size=2,
        max_size=2,
        context=1,
        retry_backoff=(0.0, 0.0),
        stop_event=threading.Event(),
    )
    worker = ManualWorker(repository, runner)

    client = make_client(
        settings=settings,
        provider_factory=lambda: provider,
        repository=repository,
        job_service=job_service,
        worker=worker,
    )
    return client, worker, provider


# --- creation --------------------------------------------------------------


def test_create_returns_a_job_id_immediately(jobs_client):
    client, worker, provider = jobs_client

    response = client.post("/jobs", json={"video_id": "vid1", "cues": payload()})
    body = response.json()

    assert response.status_code == 201
    assert body["status"] == "queued"
    assert body["cache_hit"] is False
    assert body["total_cues"] == 6
    assert body["completed_cues"] == 0
    assert body["cues"] == []
    assert isinstance(body["job_id"], int)
    # The request returned before any translation happened.
    assert provider.calls == []
    assert worker.enqueued == [body["job_id"]]


def test_repeat_create_reuses_the_job_and_does_not_enqueue_twice(jobs_client):
    client, worker, _ = jobs_client
    body = {"video_id": "vid1", "cues": payload()}

    first = client.post("/jobs", json=body).json()
    second_response = client.post("/jobs", json=body)
    second = second_response.json()

    assert second_response.status_code == 200, "nothing was created"
    assert second["job_id"] == first["job_id"]
    assert worker.enqueued == [first["job_id"]], "a running job is never queued twice"


def test_completed_job_is_returned_as_a_cache_hit(jobs_client):
    client, worker, provider = jobs_client
    body = {"video_id": "vid1", "cues": payload()}

    created = client.post("/jobs", json=body).json()
    worker.drain()
    calls_after_run = len(provider.calls)

    again = client.post("/jobs", json=body)
    hit = again.json()

    assert again.status_code == 200
    assert hit["cache_hit"] is True
    assert hit["status"] == "completed"
    assert hit["job_id"] == created["job_id"]
    assert len(hit["cues"]) == 6, "a cache hit needs no polling at all"
    assert len(provider.calls) == calls_after_run, "a cache hit calls no provider"
    assert worker.enqueued == []


def test_invalid_cues_create_no_job(jobs_client):
    client, worker, _ = jobs_client
    bad = payload(3)
    bad[1]["end_ms"] = bad[1]["start_ms"]  # end must be after start

    response = client.post("/jobs", json={"video_id": "vid1", "cues": bad})

    assert response.status_code == 400
    assert response.json()["error_code"] == "invalid_cues"
    assert worker.enqueued == []


def test_too_many_cues_is_rejected(tmp_path):
    repository = Repository(tmp_path / "db.sqlite")
    settings = make_settings(max_cues=2)
    client = make_client(
        settings=settings,
        repository=repository,
        job_service=JobService(
            repository, model=settings.model, provider_name=settings.provider
        ),
    )

    response = client.post("/jobs", json={"video_id": "vid1", "cues": payload(5)})

    assert response.status_code == 413
    assert response.json()["error_code"] == "too_many_cues"


def test_jobs_require_persistence(client):
    """With the cache disabled there is no durable state, so jobs say so plainly."""
    response = client.post("/jobs", json={"video_id": "vid1", "cues": payload()})

    assert response.status_code == 503
    assert response.json()["error_code"] == "jobs_unavailable"


# --- polling ---------------------------------------------------------------


def test_status_reports_progress_and_completion(jobs_client):
    client, worker, _ = jobs_client
    job_id = client.post("/jobs", json={"video_id": "vid1", "cues": payload()}).json()["job_id"]

    queued = client.get(f"/jobs/{job_id}").json()
    assert queued["status"] == "queued"
    assert queued["done"] is False
    assert queued["progress"]["percent"] == 0.0

    worker.drain()
    finished = client.get(f"/jobs/{job_id}").json()

    assert finished["status"] == "completed"
    assert finished["done"] is True
    assert finished["progress"] == {
        "total_cues": 6,
        "speech_cues": 6,
        "completed_cues": 6,
        "failed_cues": 0,
        "percent": 100.0,
    }
    assert [c["cue_index"] for c in finished["cues"]] == list(range(6))
    assert finished["error"] is None


def test_polling_with_a_cursor_never_repeats_a_cue(jobs_client):
    """The core progressive-delivery guarantee: poll as often as you like.

    The provider hook polls twice from between every pair of windows, so the
    reads genuinely interleave with the worker's commits.
    """
    client, worker, provider = jobs_client
    job_id = client.post("/jobs", json={"video_id": "vid1", "cues": payload()}).json()["job_id"]

    seen = []
    state = {"cursor": None}

    def poll():
        params = {} if state["cursor"] is None else {"after_cue_index": state["cursor"]}
        body = client.get(f"/jobs/{job_id}", params=params).json()
        seen.extend(c["cue_index"] for c in body["cues"])
        state["cursor"] = body["next_cursor"]

    provider.hook = lambda: (poll(), poll())
    worker.drain()
    provider.hook = None

    # Cues arrive while the job is still running, not only at the end.
    assert seen, "progressive delivery returned nothing mid-run"
    assert len(seen) == len(set(seen)), "repeated polling must not duplicate cues"

    # The client's final cursor-less reconciliation read.
    final = client.get(f"/jobs/{job_id}").json()
    reconciled = set(seen) | {c["cue_index"] for c in final["cues"]}
    assert sorted(reconciled) == list(range(6))


def test_final_cursorless_read_reconciles_everything(jobs_client):
    client, worker, _ = jobs_client
    job_id = client.post("/jobs", json={"video_id": "vid1", "cues": payload()}).json()["job_id"]
    worker.drain()

    tail = client.get(f"/jobs/{job_id}", params={"after_cue_index": 3}).json()
    full = client.get(f"/jobs/{job_id}").json()

    assert [c["cue_index"] for c in tail["cues"]] == [4, 5]
    assert [c["cue_index"] for c in full["cues"]] == list(range(6))
    assert full["next_cursor"] == 5


def test_status_can_render_srt_on_demand(jobs_client):
    client, worker, _ = jobs_client
    job_id = client.post("/jobs", json={"video_id": "vid1", "cues": payload()}).json()["job_id"]
    worker.drain()

    body = client.get(f"/jobs/{job_id}", params={"include_srt": True}).json()

    assert "-->" in body["srt"]
    assert body["srt"].lstrip().startswith("1")
    assert body["srt"].count("-->") == 6


def test_srt_covers_the_whole_job_even_with_a_cursor(jobs_client):
    """A subtitle file of only the cues past a cursor would be useless."""
    client, worker, _ = jobs_client
    job_id = client.post("/jobs", json={"video_id": "vid1", "cues": payload()}).json()["job_id"]
    worker.drain()

    body = client.get(
        f"/jobs/{job_id}", params={"include_srt": True, "after_cue_index": 4}
    ).json()

    assert [c["cue_index"] for c in body["cues"]] == [5], "the cursor still applies to cues"
    assert body["srt"].count("-->") == 6, "but the SRT is the complete subtitle file"


def test_unknown_job_is_a_404(jobs_client):
    client, _, _ = jobs_client
    response = client.get("/jobs/999")

    assert response.status_code == 404
    assert response.json()["error_code"] == "job_not_found"


def test_reading_a_job_never_starts_work(jobs_client):
    client, worker, provider = jobs_client
    job_id = client.post("/jobs", json={"video_id": "vid1", "cues": payload()}).json()["job_id"]

    for _ in range(3):
        client.get(f"/jobs/{job_id}")

    assert provider.calls == [], "GET must never translate"


# --- failure surfaces ------------------------------------------------------


def test_failed_job_reports_a_sanitized_error_and_keeps_cues(tmp_path):
    repository = Repository(tmp_path / "db.sqlite")
    settings = make_settings()

    class FailsAfterFirstWindow(MockProvider):
        def translate(self, request):
            if len(self.calls) >= 1:
                raise ProviderError("secret-host.internal refused sk-or-abc123")
            return super().translate(request)

    provider = FailsAfterFirstWindow()
    runner = JobRunner(
        repository,
        provider_factory=lambda: provider,
        provider_name=settings.provider,
        model=settings.model,
        target_size=2,
        max_size=2,
        context=1,
        retry_backoff=(0.0, 0.0),
    )
    worker = ManualWorker(repository, runner)
    client = make_client(
        settings=settings,
        repository=repository,
        job_service=JobService(
            repository, model=settings.model, provider_name=settings.provider
        ),
        worker=worker,
    )

    job_id = client.post("/jobs", json={"video_id": "vid1", "cues": payload()}).json()["job_id"]
    worker.drain()
    body = client.get(f"/jobs/{job_id}").json()

    assert body["status"] == "failed"
    assert body["done"] is True
    assert body["error"]["error_code"] == "provider_error"
    assert "sk-or-abc123" not in body["error"]["message"]
    assert "secret-host" not in body["error"]["message"]
    assert len(body["cues"]) == 2, "work already done is still delivered"


def test_partial_job_is_not_reported_as_completed(tmp_path):
    repository = Repository(tmp_path / "db.sqlite")
    settings = make_settings()
    runner = JobRunner(
        repository,
        provider_factory=lambda: MockProvider(fault="always_bad"),
        provider_name=settings.provider,
        model=settings.model,
        target_size=2,
        max_size=2,
        context=1,
        retry_backoff=(0.0, 0.0),
    )
    worker = ManualWorker(repository, runner)
    client = make_client(
        settings=settings,
        repository=repository,
        job_service=JobService(
            repository, model=settings.model, provider_name=settings.provider
        ),
        worker=worker,
    )

    job_id = client.post("/jobs", json={"video_id": "vid1", "cues": payload()}).json()["job_id"]
    worker.drain()
    body = client.get(f"/jobs/{job_id}").json()

    assert body["status"] == "partial"
    assert body["failed_indices"]
    returned = {c["cue_index"] for c in body["cues"]}
    assert returned.isdisjoint(body["failed_indices"])


# --- interaction with the synchronous path ---------------------------------


def test_translate_is_refused_while_a_job_holds_the_identity(jobs_client):
    """Translating synchronously would delete the running job's committed cues."""
    client, worker, _ = jobs_client
    body = {"video_id": "vid1", "cues": payload()}
    client.post("/jobs", json=body)

    response = client.post("/translate", json=body)

    assert response.status_code == 409
    assert response.json()["error_code"] == "job_in_progress"


def test_translate_works_again_once_the_job_finishes(jobs_client):
    client, worker, provider = jobs_client
    body = {"video_id": "vid1", "cues": payload()}
    client.post("/jobs", json=body)
    worker.drain()
    calls_after_job = len(provider.calls)

    response = client.post("/translate", json=body)

    assert response.status_code == 200
    assert response.json()["cache_hit"] is True, "the job's work is reused"
    assert len(provider.calls) == calls_after_job


def test_a_synchronous_translation_is_a_cache_hit_for_a_later_job(jobs_client):
    """One database, one cache identity: either path can serve the other."""
    client, worker, provider = jobs_client
    body = {"video_id": "vid1", "cues": payload()}

    client.post("/translate", json=body)
    calls_after_translate = len(provider.calls)
    job = client.post("/jobs", json=body).json()

    assert job["cache_hit"] is True
    assert job["status"] == "completed"
    assert len(job["cues"]) == 6
    assert len(provider.calls) == calls_after_translate
    assert worker.enqueued == []
