import json

import pytest

from subtitle_translator.validation import ValidationError, validate_translation


def _payload(cues, window_id="w_0001"):
    return json.dumps({"window_id": window_id, "cues": cues}, ensure_ascii=False)


def test_valid_payload_returns_translations():
    raw = _payload([{"cue_index": 0, "persian_text": "سلام"}, {"cue_index": 1, "persian_text": "دنیا"}])
    result = validate_translation(raw, [0, 1])
    assert result.translations == {0: "سلام", 1: "دنیا"}
    assert result.window_id == "w_0001"


def test_json_fence_is_tolerated():
    raw = "```json\n" + _payload([{"cue_index": 0, "persian_text": "سلام"}]) + "\n```"
    assert validate_translation(raw, [0]).translations == {0: "سلام"}


def test_surrounding_prose_is_recovered():
    raw = "Here is the JSON:\n" + _payload([{"cue_index": 0, "persian_text": "سلام"}]) + "\nHope this helps!"
    assert validate_translation(raw, [0]).translations == {0: "سلام"}


def test_missing_index_fails_with_index_set():
    raw = _payload([{"cue_index": 0, "persian_text": "سلام"}])
    with pytest.raises(ValidationError) as exc:
        validate_translation(raw, [0, 1])
    assert "missing" in exc.value.correction.lower()
    assert "1" in exc.value.correction


def test_duplicate_index_fails():
    raw = _payload([{"cue_index": 0, "persian_text": "a"}, {"cue_index": 0, "persian_text": "b"}])
    with pytest.raises(ValidationError):
        validate_translation(raw, [0])


def test_unexpected_index_fails():
    raw = _payload([{"cue_index": 0, "persian_text": "a"}, {"cue_index": 9, "persian_text": "b"}])
    with pytest.raises(ValidationError):
        validate_translation(raw, [0])


def test_empty_text_fails():
    raw = _payload([{"cue_index": 0, "persian_text": "   "}])
    with pytest.raises(ValidationError):
        validate_translation(raw, [0])


def test_non_json_fails():
    with pytest.raises(ValidationError):
        validate_translation("I cannot help with that.", [0])


def test_missing_cues_array_fails():
    with pytest.raises(ValidationError):
        validate_translation('{"window_id": "w_0001"}', [0])
