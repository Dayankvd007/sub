"""Structured-output parsing and cue-coverage validation.

Kept separate from any provider so it can be unit-tested against synthetic
model output, including malformed cases. Never accepts partially valid output
silently: any missing, duplicate, unexpected, or empty cue fails visibly with
the exact offending index set.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass


class ValidationError(ValueError):
    """Raised when model output is not a valid, complete translation."""

    def __init__(self, message: str, *, correction: str | None = None):
        super().__init__(message)
        self.correction = correction or message


@dataclass
class ValidatedWindow:
    window_id: str | None
    translations: dict[int, str]  # cue_index -> persian_text


_JSON_OBJECT = re.compile(r"\{.*\}", re.DOTALL)


def _extract_json(raw: str) -> dict:
    text = raw.strip()
    # Tolerate a ```json fence but nothing looser than that.
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z0-9]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Last resort: grab the first {...} span. Guards against a stray leading or
    # trailing sentence while still rejecting genuinely non-JSON replies.
    match = _JSON_OBJECT.search(raw)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError as exc:
            raise ValidationError(
                "Response was not valid JSON.",
                correction="Return ONLY a single valid JSON object, with no surrounding text.",
            ) from exc
    raise ValidationError(
        "Response did not contain a JSON object.",
        correction="Return ONLY a single valid JSON object, with no surrounding text.",
    )


def validate_translation(raw: str, expected_indices: list[int]) -> ValidatedWindow:
    data = _extract_json(raw)

    if not isinstance(data, dict) or not isinstance(data.get("cues"), list):
        raise ValidationError(
            "JSON is missing a 'cues' array.",
            correction="Return an object with a 'cues' array of {cue_index, persian_text} items.",
        )

    expected = set(expected_indices)
    seen: dict[int, str] = {}
    duplicates: set[int] = set()
    unexpected: set[int] = set()
    empty: set[int] = set()

    for item in data["cues"]:
        if not isinstance(item, dict) or "cue_index" not in item or "persian_text" not in item:
            raise ValidationError(
                "A cue entry was malformed (needs integer cue_index and string persian_text).",
                correction="Each cue must be {\"cue_index\": <int>, \"persian_text\": <non-empty string>}.",
            )
        try:
            idx = int(item["cue_index"])
        except (TypeError, ValueError) as exc:
            raise ValidationError("cue_index was not an integer.") from exc
        text = item["persian_text"]
        if not isinstance(text, str) or text.strip() == "":
            empty.add(idx)
            continue
        if idx not in expected:
            unexpected.add(idx)
            continue
        if idx in seen:
            duplicates.add(idx)
            continue
        seen[idx] = text.strip()

    missing = expected - set(seen)

    problems: list[str] = []
    if missing:
        problems.append(f"missing indexes {sorted(missing)}")
    if duplicates:
        problems.append(f"duplicate indexes {sorted(duplicates)}")
    if unexpected:
        problems.append(f"unexpected indexes {sorted(unexpected)}")
    if empty:
        problems.append(f"empty persian_text for indexes {sorted(empty)}")

    if problems:
        detail = "; ".join(problems)
        raise ValidationError(
            f"Coverage check failed: {detail}.",
            correction=(
                "Return exactly one non-empty persian_text for each of these indexes "
                f"and no others: {sorted(expected)}. Problems: {detail}."
            ),
        )

    return ValidatedWindow(window_id=data.get("window_id"), translations=seen)
