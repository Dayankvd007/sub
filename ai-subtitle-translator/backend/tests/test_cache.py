"""Phase 2b-1 — cache behaviour through the service layer.

The load-bearing assertion throughout is `MockProvider.calls`: a cache hit is
only proven by the provider never being called, not by the response shape.
"""

from subtitle_translator.providers import MockProvider
from subtitle_translator.repository import Repository
from subtitle_translator.service import TranslationService

CUES = [
    {"cue_index": 0, "start_ms": 0, "end_ms": 1000, "english_text": "Hello there."},
    {"cue_index": 1, "start_ms": 1000, "end_ms": 2000, "english_text": "This is a test."},
    {"cue_index": 2, "start_ms": 2000, "end_ms": 3000, "english_text": "Goodbye."},
]


class TrackingFactory:
    """Hands out MockProviders and records every one it created."""

    def __init__(self, fault=None):
        self.fault = fault
        self.providers = []

    def __call__(self):
        provider = MockProvider(fault=self.fault)
        self.providers.append(provider)
        return provider

    @property
    def total_calls(self):
        return sum(len(p.calls) for p in self.providers)


def build(tmp_path, factory, *, model="mock-model", cache=True):
    return TranslationService(
        provider_factory=factory,
        provider_name="mock",
        model=model,
        repository=Repository(tmp_path / "db.sqlite") if cache else None,
    )


def test_second_identical_request_makes_zero_provider_calls(tmp_path):
    factory = TrackingFactory()
    service = build(tmp_path, factory)

    first = service.translate(video_id="vid1", cues_payload=CUES)
    calls_after_first = factory.total_calls
    assert calls_after_first > 0
    assert first.cache_hit is False

    second = service.translate(video_id="vid1", cues_payload=CUES)

    assert second.cache_hit is True
    assert factory.total_calls == calls_after_first, "a cache hit must not call the provider"
    assert [c.cue_index for c in second.translated_cues] == [0, 1, 2]
    assert [c.persian_text for c in second.translated_cues] == [
        c.persian_text for c in first.translated_cues
    ]


def test_cache_hit_reports_no_model_work_in_stats(tmp_path):
    factory = TrackingFactory()
    service = build(tmp_path, factory)
    service.translate(video_id="vid1", cues_payload=CUES)

    hit = service.translate(video_id="vid1", cues_payload=CUES)

    assert hit.stats.provider_calls == 0
    assert hit.stats.corrective_retries == 0
    assert hit.stats.splits == 0
    assert hit.stats.translated == 3
    assert hit.usage is None


def test_changed_captions_invalidate_the_cache(tmp_path):
    factory = TrackingFactory()
    service = build(tmp_path, factory)
    service.translate(video_id="vid1", cues_payload=CUES)
    before = factory.total_calls

    edited = [dict(c) for c in CUES]
    edited[1]["english_text"] = "This sentence changed."
    result = service.translate(video_id="vid1", cues_payload=edited)

    assert result.cache_hit is False
    assert factory.total_calls > before


def test_changed_model_invalidates_the_cache(tmp_path):
    factory = TrackingFactory()
    db = tmp_path / "db.sqlite"
    first = TranslationService(
        provider_factory=factory, provider_name="mock", model="model-a",
        repository=Repository(db),
    )
    first.translate(video_id="vid1", cues_payload=CUES)
    before = factory.total_calls

    second = TranslationService(
        provider_factory=factory, provider_name="mock", model="model-b",
        repository=Repository(db),
    )
    result = second.translate(video_id="vid1", cues_payload=CUES)

    assert result.cache_hit is False
    assert factory.total_calls > before


def test_different_video_id_invalidates_the_cache(tmp_path):
    factory = TrackingFactory()
    service = build(tmp_path, factory)
    service.translate(video_id="vid1", cues_payload=CUES)
    before = factory.total_calls

    result = service.translate(video_id="vid2", cues_payload=CUES)

    assert result.cache_hit is False
    assert factory.total_calls > before


def test_partial_result_is_retranslated_not_served(tmp_path):
    """A partial run must never come back as a completed cache hit."""
    factory = TrackingFactory(fault="always_bad")
    service = build(tmp_path, factory)

    first = service.translate(video_id="vid1", cues_payload=CUES)
    assert first.status == "partial"
    before = factory.total_calls

    second = service.translate(video_id="vid1", cues_payload=CUES)

    assert second.cache_hit is False
    assert factory.total_calls > before


def test_cache_survives_service_restart(tmp_path):
    """A brand-new service and repository against the same file still hits."""
    db = tmp_path / "db.sqlite"
    first_factory = TrackingFactory()
    TranslationService(
        provider_factory=first_factory, provider_name="mock", model="mock-model",
        repository=Repository(db),
    ).translate(video_id="vid1", cues_payload=CUES)

    second_factory = TrackingFactory()
    restarted = TranslationService(
        provider_factory=second_factory, provider_name="mock", model="mock-model",
        repository=Repository(db),
    )
    result = restarted.translate(video_id="vid1", cues_payload=CUES)

    assert result.cache_hit is True
    assert second_factory.total_calls == 0
    assert len(result.translated_cues) == 3


def test_cache_hit_regenerates_srt(tmp_path):
    factory = TrackingFactory()
    service = build(tmp_path, factory)
    first = service.translate(video_id="vid1", cues_payload=CUES, include_srt=True)

    hit = service.translate(video_id="vid1", cues_payload=CUES, include_srt=True)
    assert hit.srt == first.srt

    without = service.translate(video_id="vid1", cues_payload=CUES, include_srt=False)
    assert without.srt is None


def test_disabled_cache_always_calls_the_provider(tmp_path):
    factory = TrackingFactory()
    service = build(tmp_path, factory, cache=False)

    service.translate(video_id="vid1", cues_payload=CUES)
    before = factory.total_calls
    result = service.translate(video_id="vid1", cues_payload=CUES)

    assert result.cache_hit is False
    assert factory.total_calls > before
