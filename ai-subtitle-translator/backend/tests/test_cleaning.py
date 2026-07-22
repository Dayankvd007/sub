from subtitle_translator.cleaning import clean_cues, clean_text
from subtitle_translator.models import Cue


def _cue(i, text):
    return Cue(cue_index=i, start_ms=i * 1000, end_ms=i * 1000 + 900, english_text=text)


def test_clean_text_strips_tags_and_inline_markers():
    assert clean_text("<b>Hello</b> [Applause] world") == "Hello world"
    assert clean_text("multiple   spaces\nand newlines") == "multiple spaces and newlines"


def test_non_speech_only_marker_is_flagged_not_dropped():
    cues, changes = clean_cues([_cue(0, "Hi there"), _cue(1, "[Music]"), _cue(2, "bye")])
    assert len(cues) == 3  # nothing dropped
    assert cues[1].is_speech is False
    assert cues[0].is_speech is True and cues[2].is_speech is True
    assert any("cue 1" in c for c in changes)


def test_empty_after_cleaning_is_flagged_not_deleted():
    cues, _ = clean_cues([_cue(0, "&nbsp;"), _cue(1, "real speech")])
    # "&nbsp;" was already decoded to a space by the loader in practice; here we
    # pass it raw, and cleaning collapses it to empty -> flagged, not removed.
    assert len(cues) == 2
    assert cues[1].is_speech is True


def test_timestamps_never_change():
    original = _cue(0, "<i>hello</i>   world")
    cues, _ = clean_cues([original])
    assert cues[0].start_ms == original.start_ms
    assert cues[0].end_ms == original.end_ms
    assert cues[0].english_text == "hello world"
