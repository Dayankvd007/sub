"""Provider abstraction: one small internal contract around the model call.

The pipeline owns prompt construction, JSON parsing, validation, and retry.
A provider's only job is: given a system prompt, a user prompt, and the set of
required cue indexes, return the raw text the model produced. Parsing lives in
`validation.py` so it can be tested independently of any provider.

Providers that ship:
  * MockProvider    — deterministic, offline, no cost. Used by the whole test
                      suite and by `--provider mock`. Can simulate malformed
                      output to exercise the retry/split path.
  * AnthropicProvider — real online provider (lazy-imports the `anthropic`
                      SDK so the offline path needs no third-party deps).
  * OpenRouterProvider — real online provider talking to the OpenRouter
                      chat-completions API over stdlib `urllib` (no extra
                      dependency). Credentials come from OPENROUTER_API_KEY.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass


class ProviderError(RuntimeError):
    """Transport/credential/provider failure (distinct from malformed output)."""


@dataclass
class TranslationRequest:
    window_id: str
    system_prompt: str
    user_prompt: str
    required_indices: list[int]
    # Source cues for the required indexes, index -> english_text. Real
    # providers ignore this; the mock uses it to synthesize deterministic
    # Persian without a network call.
    source_by_index: dict[int, str]


class TranslationProvider:
    name = "base"

    def translate(self, request: TranslationRequest) -> str:  # pragma: no cover
        raise NotImplementedError


class MockProvider(TranslationProvider):
    """Deterministic offline provider.

    Normal mode returns valid JSON covering exactly the required indexes.
    Fault modes let tests exercise validation + retry/split:
      * "missing"      — omit one required index (first attempt only)
      * "duplicate"    — duplicate one index (first attempt only)
      * "extra"        — include an unrequested index (first attempt only)
      * "bad_json"     — return non-JSON text (first attempt only)
      * "empty_text"   — return an empty persian_text (first attempt only)
      * "always_bad"   — always omit one index (forces split + failure)
    A single fault (non-"always_bad") fires only on the first call for a given
    window, so a corrective retry succeeds — mirroring a model that fixes
    itself when told exactly what was wrong.
    """

    name = "mock"

    def __init__(self, fault: str | None = None):
        self.fault = fault
        self._seen_windows: set[str] = set()
        self.calls: list[str] = []

    def _persian(self, index: int, english: str) -> str:
        # Not real translation — a stable, recognizable stand-in that carries
        # the index so tests can assert coverage and ordering.
        return f"[fa {index}] {english}"

    def translate(self, request: TranslationRequest) -> str:
        self.calls.append(request.window_id)
        first_time = request.window_id not in self._seen_windows
        self._seen_windows.add(request.window_id)

        fault = self.fault
        active = fault is not None and (fault == "always_bad" or first_time)

        if active and fault == "bad_json":
            return "Sure! Here are the translations you asked for (not JSON)."

        cues = [
            {"cue_index": i, "persian_text": self._persian(i, request.source_by_index.get(i, ""))}
            for i in request.required_indices
        ]

        if active and fault in {"missing", "always_bad"} and cues:
            cues = cues[:-1]
        if active and fault == "duplicate" and cues:
            cues.append(dict(cues[0]))
        if active and fault == "extra":
            cues.append({"cue_index": max(request.required_indices) + 1000, "persian_text": "x"})
        if active and fault == "empty_text" and cues:
            cues[0] = {"cue_index": cues[0]["cue_index"], "persian_text": ""}

        return json.dumps({"window_id": request.window_id, "cues": cues}, ensure_ascii=False)


class AnthropicProvider(TranslationProvider):
    """Real provider using the Anthropic Messages API.

    Credentials come from the ANTHROPIC_API_KEY environment variable via the
    SDK's default resolution — never hardcoded. Thinking is disabled: subtitle
    translation does not benefit from it and it would add latency and cost.
    """

    name = "anthropic"

    def __init__(self, model: str, *, max_tokens: int = 8000):
        try:
            import anthropic  # noqa: F401  (lazy: offline path needs no dep)
        except ImportError as exc:  # pragma: no cover - exercised only online
            raise ProviderError(
                "The 'anthropic' package is required for the anthropic provider. "
                "Install it with: pip install 'subtitle-translator[anthropic]'"
            ) from exc
        self._anthropic = anthropic
        self.model = model
        self.max_tokens = max_tokens
        self._client = anthropic.Anthropic()

    def translate(self, request: TranslationRequest) -> str:  # pragma: no cover - online only
        try:
            message = self._client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                thinking={"type": "disabled"},
                system=request.system_prompt,
                messages=[{"role": "user", "content": request.user_prompt}],
            )
        except self._anthropic.APIError as exc:
            raise ProviderError(f"Anthropic API error: {exc}") from exc

        parts = [block.text for block in message.content if getattr(block, "type", None) == "text"]
        if not parts:
            raise ProviderError("Anthropic response contained no text block.")
        return "".join(parts)


class OpenRouterProvider(TranslationProvider):
    """Real provider using OpenRouter's OpenAI-compatible chat-completions API.

    Credentials come from the OPENROUTER_API_KEY environment variable — never
    hardcoded, never logged. Uses stdlib `urllib` instead of an SDK so the
    offline path still needs no third-party dependency. Requests
    `usage.include` so the response carries token counts and OpenRouter's own
    cost estimate, which the caller can surface for reporting.
    """

    name = "openrouter"

    BASE_URL = "https://openrouter.ai/api/v1"

    def __init__(self, model: str | None = None, *, max_tokens: int = 8000, timeout: float = 120.0):
        api_key = os.environ.get("OPENROUTER_API_KEY")
        if not api_key:
            raise ProviderError(
                "OPENROUTER_API_KEY environment variable is not set. "
                "Add it to your local .env or export it before running."
            )
        resolved_model = model or os.environ.get("OPENROUTER_MODEL")
        if not resolved_model:
            raise ProviderError(
                "No model specified for the openrouter provider. Pass --model or set OPENROUTER_MODEL."
            )
        self._api_key = api_key
        self.model = resolved_model
        self.max_tokens = max_tokens
        self.timeout = timeout
        # Populated after each call so the caller can report approximate cost.
        # NOTE: this is the MOST RECENT call only. A run translates one window
        # per call, so reporting this as a run total under-reports by roughly the
        # window count. Use `usage_totals` for per-run figures.
        self.last_usage: dict | None = None
        # Accumulated across every call this instance makes.
        self.usage_totals: dict = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "cost": 0.0,
            "calls": 0,
        }

    def _accumulate_usage(self, usage: dict | None) -> None:
        """Add one call's usage to the running totals, tolerating missing fields."""
        self.usage_totals["calls"] += 1
        if not usage:
            return
        for key in ("prompt_tokens", "completion_tokens"):
            value = usage.get(key)
            if isinstance(value, (int, float)):
                self.usage_totals[key] += int(value)
        cost = usage.get("cost")
        if isinstance(cost, (int, float)):
            self.usage_totals["cost"] += float(cost)

    def translate(self, request: TranslationRequest) -> str:  # pragma: no cover - online only
        payload = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "messages": [
                {"role": "system", "content": request.system_prompt},
                {"role": "user", "content": request.user_prompt},
            ],
            "usage": {"include": True},
        }
        http_request = urllib.request.Request(
            f"{self.BASE_URL}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(http_request, timeout=self.timeout) as response:
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise ProviderError(f"OpenRouter API error {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise ProviderError(f"OpenRouter request failed: {exc.reason}") from exc

        usage = body.get("usage")
        self.last_usage = usage
        self._accumulate_usage(usage)

        choices = body.get("choices") or []
        if not choices:
            raise ProviderError("OpenRouter response contained no choices.")
        content = choices[0].get("message", {}).get("content")
        if not content:
            raise ProviderError("OpenRouter response contained no content.")
        return content
