"""Phase 2b-1 — cache behaviour through POST /translate."""

import pytest

pytest.importorskip("fastapi", reason="needs the [api] extra")

from conftest import make_client  # noqa: E402

from subtitle_translator.providers import MockProvider  # noqa: E402
from subtitle_translator.repository import Repository  # noqa: E402


class TrackingFactory:
    def __init__(self):
        self.providers = []

    def __call__(self):
        provider = MockProvider()
        self.providers.append(provider)
        return provider

    @property
    def total_calls(self):
        return sum(len(p.calls) for p in self.providers)


@pytest.fixture
def cached_client(tmp_path):
    factory = TrackingFactory()
    client = make_client(
        provider_factory=factory, repository=Repository(tmp_path / "db.sqlite")
    )
    return client, factory


def test_first_request_is_a_miss_and_second_is_a_hit(cached_client, cues):
    client, factory = cached_client
    body = {"video_id": "vid1", "cues": cues}

    first = client.post("/translate", json=body).json()
    assert first["cache_hit"] is False
    calls_after_first = factory.total_calls
    assert calls_after_first > 0

    second = client.post("/translate", json=body).json()

    assert second["cache_hit"] is True
    assert factory.total_calls == calls_after_first, "a cache hit must not call the provider"
    assert second["cues"] == first["cues"]
    assert second["stats"]["provider_calls"] == 0


def test_uncached_client_reports_cache_hit_false(client, cues):
    body = client.post("/translate", json={"video_id": "vid1", "cues": cues}).json()
    assert body["cache_hit"] is False


def test_changed_captions_miss_the_cache(cached_client, cues):
    client, factory = cached_client
    client.post("/translate", json={"video_id": "vid1", "cues": cues})
    before = factory.total_calls

    edited = [dict(c) for c in cues]
    edited[0]["english_text"] = "Something different entirely."
    response = client.post("/translate", json={"video_id": "vid1", "cues": edited}).json()

    assert response["cache_hit"] is False
    assert factory.total_calls > before


def test_cache_hit_still_returns_srt(cached_client, cues):
    client, _ = cached_client
    body = {"video_id": "vid1", "cues": cues}
    first = client.post("/translate", json=body).json()
    second = client.post("/translate", json=body).json()

    assert second["cache_hit"] is True
    assert second["srt"] == first["srt"]
    assert "-->" in second["srt"]
