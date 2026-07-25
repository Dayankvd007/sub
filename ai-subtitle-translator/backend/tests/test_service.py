"""Phase 2a — translation service layer, exercised without FastAPI.

Everything here runs offline against MockProvider: no API key, no cost.
"""

import pytest

from subtitle_translator.loaders import LoaderError
from subtitle_translator.providers import MockProvider, ProviderError
from subtitle_translator.service import TranslationService


def payload(*rows):
    return [
        {"cue_index": i, "start_ms": s, "end_ms": e, "english_text": t}
        for (i, s, e, t) in rows
    ]


def build_service(provider_factory=None, **kwargs):
    return TranslationService(
        provider_factory=provider_factory or (lambda: MockProvider()),
        provider_name=kwargs.pop("provider_name", "mock"),
        model=kwargs.pop("model", "mock-model"),
        **kwargs,
    )


def test_translates_every_speech_cue():
    service = build_service()
    outcome = service.translate(
        video_id="vid1",
        cues_payload=payload(
            (0, 0, 1000, "Hello there."),
            (1, 1000, 2000, "This is a test."),
            (2, 2000, 3000, "Goodbye."),
        ),
    )

    assert outcome.status == "completed"
    assert outcome.failed_indices == []
    assert outcome.stats.translated == 3
    assert [c.cue_index for c in outcome.translated_cues] == [0, 1, 2]
    assert all(c.persian_text for c in outcome.translated_cues)


def test_preserves_client_cue_indexes_exactly():
    """Decision C: the extension owns cue identity; the backend must not renumber."""
    service = build_service()
    sparse = payload(
        (5, 0, 1000, "First."),
        (9, 1000, 2000, "Second."),
        (12, 2000, 3000, "Third."),
    )

    outcome = service.translate(video_id="vid1", cues_payload=sparse)

    assert [c.cue_index for c in outcome.translated_cues] == [5, 9, 12]


def test_includes_srt_when_requested():
    service = build_service()
    outcome = service.translate(
        video_id="vid1",
        cues_payload=payload((0, 0, 1000, "Hello.")),
        include_srt=True,
    )
    assert outcome.srt is not None
    assert "-->" in outcome.srt


def test_omits_srt_when_not_requested():
    service = build_service()
    outcome = service.translate(
        video_id="vid1",
        cues_payload=payload((0, 0, 1000, "Hello.")),
        include_srt=False,
    )
    assert outcome.srt is None


def test_persistent_provider_failure_reports_partial_not_completed():
    service = build_service(provider_factory=lambda: MockProvider(fault="always_bad"))
    outcome = service.translate(
        video_id="vid1",
        cues_payload=payload(
            (0, 0, 1000, "One."),
            (1, 1000, 2000, "Two."),
            (2, 2000, 3000, "Three."),
        ),
    )

    assert outcome.status == "partial"
    assert outcome.failed_indices, "failed cues must be reported, never silently dropped"
    assert outcome.stats.splits > 0


def test_invalid_payload_raises_loader_error():
    service = build_service()
    with pytest.raises(LoaderError):
        service.translate(video_id="vid1", cues_payload=[])


def test_provider_error_propagates():
    def boom():
        raise ProviderError("no credentials")

    service = build_service(provider_factory=boom)
    with pytest.raises(ProviderError):
        service.translate(video_id="vid1", cues_payload=payload((0, 0, 1000, "Hi.")))


def test_reports_prompt_version_and_model():
    service = build_service(model="test-model")
    outcome = service.translate(
        video_id="vid1", cues_payload=payload((0, 0, 1000, "Hello."))
    )
    assert outcome.prompt_version == "v1"
    assert outcome.provider == "mock"
    assert outcome.model == "test-model"
