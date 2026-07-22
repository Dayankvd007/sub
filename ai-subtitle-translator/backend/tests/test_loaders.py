from pathlib import Path

import pytest

from subtitle_translator.loaders import (
    LoaderError,
    load_cues,
    parse_phase0_fixture,
)

FIX = Path(__file__).parent / "fixtures"


def test_load_srt_strips_tags_and_indexes_densely():
    cues = load_cues(FIX / "sample.srt")
    assert [c.cue_index for c in cues] == [0, 1, 2, 3, 4]
    assert cues[0].english_text == "Welcome back to the channel,"
    assert cues[2].english_text == "structured caption extraction."  # <i> stripped
    assert cues[3].english_text == "[Music]"  # markers are cleaning's job, not loading's
    for c in cues:
        assert c.end_ms > c.start_ms >= 0


def test_load_vtt_skips_notes_and_identifier_lines():
    cues = load_cues(FIX / "sample.vtt")
    assert [c.cue_index for c in cues] == [0, 1, 2, 3]
    assert cues[2].english_text == "structured caption extraction."
    assert cues[0].start_ms == 40


def test_load_json3_skips_position_only_events():
    cues = load_cues(FIX / "sample.json3.json")
    # First event has no segs and is skipped; 4 spoken events remain.
    assert [c.cue_index for c in cues] == [0, 1, 2, 3]
    assert cues[0].english_text == "Welcome back to the channel,"
    assert cues[0].start_ms == 40
    assert cues[0].end_ms == 40 + 2960


def test_all_three_formats_yield_equivalent_core_cues():
    srt = load_cues(FIX / "sample.srt")
    vtt = load_cues(FIX / "sample.vtt")
    j3 = load_cues(FIX / "sample.json3.json")
    # Compare the three cues common to all fixtures (welcome / today / structured).
    assert [c.english_text for c in vtt[:3]] == [c.english_text for c in j3[:3]]
    assert [c.english_text for c in srt[:3]] == [c.english_text for c in j3[:3]]


def test_phase0_fixture_shape():
    data = {
        "cues": [
            {"cue_index": 0, "start_ms": 0, "end_ms": 1000, "english_text": "Hello world"},
            {"cue_index": 1, "start_ms": 1000, "end_ms": 2000, "english_text": "second cue"},
        ]
    }
    cues = parse_phase0_fixture(data)
    assert len(cues) == 2
    assert cues[1].english_text == "second cue"


def test_unsupported_extension_raises():
    with pytest.raises(LoaderError):
        load_cues(FIX / "sample.txt")


def test_invalid_timing_raises(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text('{"cues": [{"start_ms": 5000, "end_ms": 1000, "english_text": "x"}]}', encoding="utf-8")
    with pytest.raises(LoaderError):
        load_cues(bad)
