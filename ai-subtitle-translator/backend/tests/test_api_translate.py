"""Phase 2a — POST /translate."""

import pytest

pytest.importorskip("fastapi", reason="needs the [api] extra")

from conftest import make_client, make_settings

from subtitle_translator.providers import MockProvider, ProviderError


def test_translate_returns_all_cues_and_stats(client, cues):
    response = client.post("/translate", json={"video_id": "vid1", "cues": cues})
    assert response.status_code == 200

    body = response.json()
    assert body["video_id"] == "vid1"
    assert body["status"] == "completed"
    assert body["prompt_version"] == "v1"
    assert body["failed_indices"] == []
    assert [c["cue_index"] for c in body["cues"]] == [0, 1, 2]
    assert all(c["persian_text"] for c in body["cues"])

    stats = body["stats"]
    assert stats["total_cues"] == 3
    assert stats["translated"] == 3
    assert stats["failed"] == 0
    assert stats["windows"] >= 1


def test_translate_preserves_client_cue_indexes(client):
    """Decision C: indexes are echoed back exactly as received."""
    sparse = [
        {"cue_index": 5, "start_ms": 0, "end_ms": 1000, "english_text": "First."},
        {"cue_index": 9, "start_ms": 1000, "end_ms": 2000, "english_text": "Second."},
        {"cue_index": 12, "start_ms": 2000, "end_ms": 3000, "english_text": "Third."},
    ]
    response = client.post("/translate", json={"video_id": "vid1", "cues": sparse})

    assert response.status_code == 200
    assert [c["cue_index"] for c in response.json()["cues"]] == [5, 9, 12]


def test_translate_includes_srt_by_default(client, cues):
    body = client.post("/translate", json={"video_id": "vid1", "cues": cues}).json()
    assert body["srt"] is not None
    assert "-->" in body["srt"]


def test_translate_omits_srt_when_disabled(client, cues):
    body = client.post(
        "/translate",
        json={"video_id": "vid1", "cues": cues, "options": {"include_srt": False}},
    ).json()
    assert body["srt"] is None


def test_translate_reports_partial_status_on_persistent_failure(cues):
    client = make_client(provider_factory=lambda: MockProvider(fault="always_bad"))
    body = client.post("/translate", json={"video_id": "vid1", "cues": cues}).json()

    assert body["status"] == "partial"
    assert body["failed_indices"]
    assert body["stats"]["failed"] == len(body["failed_indices"])


def test_duplicate_cue_index_is_rejected(client):
    duplicate = [
        {"cue_index": 0, "start_ms": 0, "end_ms": 1000, "english_text": "One."},
        {"cue_index": 0, "start_ms": 1000, "end_ms": 2000, "english_text": "Two."},
    ]
    response = client.post("/translate", json={"video_id": "vid1", "cues": duplicate})

    assert response.status_code == 400
    assert response.json()["error_code"] == "invalid_cues"


def test_non_increasing_cue_index_is_rejected(client):
    out_of_order = [
        {"cue_index": 3, "start_ms": 0, "end_ms": 1000, "english_text": "One."},
        {"cue_index": 1, "start_ms": 1000, "end_ms": 2000, "english_text": "Two."},
    ]
    response = client.post("/translate", json={"video_id": "vid1", "cues": out_of_order})

    assert response.status_code == 400
    assert response.json()["error_code"] == "invalid_cues"


def test_invalid_timing_is_rejected_by_schema(client):
    bad = [{"cue_index": 0, "start_ms": 5000, "end_ms": 1000, "english_text": "Backwards."}]
    response = client.post("/translate", json={"video_id": "vid1", "cues": bad})

    # end_ms <= start_ms is a semantic rule, caught by the loader.
    assert response.status_code == 400
    assert response.json()["error_code"] == "invalid_cues"


def test_empty_cue_list_is_rejected(client):
    response = client.post("/translate", json={"video_id": "vid1", "cues": []})
    assert response.status_code == 422  # schema: min_length=1


def test_missing_required_field_is_rejected(client):
    response = client.post("/translate", json={"cues": []})
    assert response.status_code == 422


def test_too_many_cues_is_rejected(cues):
    client = make_client(make_settings(max_cues=2))
    response = client.post("/translate", json={"video_id": "vid1", "cues": cues})

    assert response.status_code == 413
    assert response.json()["error_code"] == "too_many_cues"


def test_oversized_body_is_rejected(cues):
    client = make_client(make_settings(max_body_bytes=50))
    response = client.post("/translate", json={"video_id": "vid1", "cues": cues})

    assert response.status_code == 413
    assert response.json()["error_code"] == "payload_too_large"


def test_provider_failure_returns_502_without_leaking_details(cues):
    def boom():
        raise ProviderError("OpenRouter API error 401: {'key': 'sk-or-secret'}")

    client = make_client(provider_factory=boom)
    response = client.post("/translate", json={"video_id": "vid1", "cues": cues})

    assert response.status_code == 502
    assert response.json()["error_code"] == "provider_error"
    assert "sk-or-secret" not in response.text
    assert "401" not in response.text


def test_cors_allows_configured_origin(client, cues):
    origin = "chrome-extension://testextensionid"
    response = client.post(
        "/translate", json={"video_id": "vid1", "cues": cues}, headers={"Origin": origin}
    )
    assert response.headers.get("access-control-allow-origin") == origin


def test_cors_does_not_allow_unconfigured_origin(client, cues):
    response = client.post(
        "/translate",
        json={"video_id": "vid1", "cues": cues},
        headers={"Origin": "https://evil.example.com"},
    )
    # No ACAO header -> the browser refuses to hand the response to the page.
    assert "access-control-allow-origin" not in response.headers


def test_cors_never_uses_wildcard(client, cues):
    response = client.post(
        "/translate",
        json={"video_id": "vid1", "cues": cues},
        headers={"Origin": "chrome-extension://testextensionid"},
    )
    assert response.headers.get("access-control-allow-origin") != "*"
