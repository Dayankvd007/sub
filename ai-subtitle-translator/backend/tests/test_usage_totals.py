"""Phase 2b-1 — usage must be aggregated per run, not reported per last call.

`OpenRouterProvider` translates one window per call and overwrites `last_usage`
each time, so reporting `last_usage` as a run total under-reports by roughly the
window count. These tests pin the aggregate.
"""

import json
from unittest.mock import patch

from subtitle_translator.providers import OpenRouterProvider, TranslationRequest
from subtitle_translator.service import TranslationService


class FakeResponse:
    def __init__(self, payload):
        self._body = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def response_for(request, *, prompt_tokens, completion_tokens, cost):
    cues = [
        {"cue_index": i, "persian_text": f"fa {i}"} for i in request.required_indices
    ]
    return FakeResponse(
        {
            "choices": [
                {"message": {"content": json.dumps({"window_id": request.window_id, "cues": cues})}}
            ],
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "cost": cost,
            },
        }
    )


def make_provider(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key-not-real")
    return OpenRouterProvider(model="test/model")


def a_request(window_id="w0", indexes=(0, 1)):
    return TranslationRequest(
        window_id=window_id,
        system_prompt="sys",
        user_prompt="user",
        required_indices=list(indexes),
        source_by_index={i: f"line {i}" for i in indexes},
    )


def test_totals_accumulate_across_calls(monkeypatch):
    provider = make_provider(monkeypatch)

    with patch("urllib.request.urlopen") as urlopen:
        urlopen.side_effect = lambda req, timeout=None: response_for(
            a_request(), prompt_tokens=100, completion_tokens=50, cost=0.001
        )
        for i in range(10):
            provider.translate(a_request(window_id=f"w{i}"))

    assert provider.usage_totals["calls"] == 10
    assert provider.usage_totals["prompt_tokens"] == 1000
    assert provider.usage_totals["completion_tokens"] == 500
    assert abs(provider.usage_totals["cost"] - 0.01) < 1e-9

    # The regression this guards: last_usage is one call, not the run.
    assert provider.last_usage["prompt_tokens"] == 100
    assert provider.usage_totals["prompt_tokens"] != provider.last_usage["prompt_tokens"]


def test_totals_tolerate_missing_usage_fields(monkeypatch):
    provider = make_provider(monkeypatch)

    with patch("urllib.request.urlopen") as urlopen:
        urlopen.return_value = FakeResponse(
            {"choices": [{"message": {"content": '{"window_id":"w0","cues":[]}'}}]}
        )
        provider.translate(a_request())

    assert provider.usage_totals["calls"] == 1
    assert provider.usage_totals["prompt_tokens"] == 0
    assert provider.usage_totals["cost"] == 0.0


def test_service_reports_aggregated_usage():
    """The service must surface run totals, not the final window's numbers."""

    class StubProvider:
        name = "stub"
        model = "stub-model"

        def __init__(self):
            self.last_usage = None
            self.usage_totals = {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "cost": 0.0,
                "calls": 0,
            }

        def translate(self, request):
            self.last_usage = {"prompt_tokens": 100, "completion_tokens": 50, "cost": 0.001}
            self.usage_totals["calls"] += 1
            self.usage_totals["prompt_tokens"] += 100
            self.usage_totals["completion_tokens"] += 50
            self.usage_totals["cost"] += 0.001
            return json.dumps(
                {
                    "window_id": request.window_id,
                    "cues": [
                        {"cue_index": i, "persian_text": f"fa {i}"}
                        for i in request.required_indices
                    ],
                }
            )

    stub = StubProvider()
    service = TranslationService(
        provider_factory=lambda: stub,
        provider_name="stub",
        model="stub-model",
        target_size=2,
        max_size=2,
        context=0,
    )
    cues = [
        {"cue_index": i, "start_ms": i * 1000, "end_ms": (i + 1) * 1000,
         "english_text": f"line {i}"}
        for i in range(6)
    ]

    outcome = service.translate(video_id="vid1", cues_payload=cues)

    assert outcome.stats.provider_calls > 1, "need multiple windows for this to be meaningful"
    assert outcome.usage["calls"] == outcome.stats.provider_calls
    assert outcome.usage["prompt_tokens"] == 100 * outcome.stats.provider_calls
    assert outcome.usage["cost"] > stub.last_usage["cost"]


def test_provider_without_usage_reports_none():
    from subtitle_translator.providers import MockProvider

    service = TranslationService(
        provider_factory=lambda: MockProvider(),
        provider_name="mock",
        model="mock-model",
    )
    outcome = service.translate(
        video_id="vid1",
        cues_payload=[
            {"cue_index": 0, "start_ms": 0, "end_ms": 1000, "english_text": "Hello."}
        ],
    )
    assert outcome.usage is None
