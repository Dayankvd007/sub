from pathlib import Path

from subtitle_translator.cleaning import clean_cues
from subtitle_translator.dedup import remove_rolling_duplicates
from subtitle_translator.loaders import load_cues
from subtitle_translator.models import Cue

FIX = Path(__file__).parent / "fixtures"


def _cue(i, text):
    return Cue(cue_index=i, start_ms=i * 1000, end_ms=i * 1000 + 900, english_text=text)


def test_rolling_prefix_and_overlap_reconstruct_spoken_text():
    cues = load_cues(FIX / "rolling_auto.json3.json")
    cues, _ = clean_cues(cues)
    deduped, changes = remove_rolling_duplicates(cues)
    assert [c.english_text for c in deduped] == [
        "so today",
        "we're going",
        "to talk",
        "about captions",
        "and translation",
    ]
    assert all(c.is_speech for c in deduped)
    assert len(changes) == 4  # cues 1..4 were trimmed


def test_exact_duplicate_flagged_non_speech():
    cues = [_cue(0, "hello world"), _cue(1, "hello world"), _cue(2, "hello world again")]
    deduped, _ = remove_rolling_duplicates(cues)
    assert deduped[1].is_speech is False  # exact repeat
    assert deduped[0].is_speech is True
    # cue 2 is a growth of cue 1's words -> trimmed to the new tail.
    assert deduped[2].english_text == "again"


def test_uncertain_single_word_overlap_preserved():
    # A single-word overlap is below the confidence threshold (2 words), so the
    # text is preserved unchanged rather than risk deleting real speech.
    cues = [_cue(0, "the cat"), _cue(1, "cat dog bird")]
    deduped, _ = remove_rolling_duplicates(cues)
    assert deduped[1].english_text == "cat dog bird"
