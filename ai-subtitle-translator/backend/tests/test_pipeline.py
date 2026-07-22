from pathlib import Path

from subtitle_translator.config import TranslationConfig
from subtitle_translator.loaders import load_cues
from subtitle_translator.pipeline import translate_cues
from subtitle_translator.providers import MockProvider
from subtitle_translator.srt import to_srt

FIX = Path(__file__).parent / "fixtures"


def test_full_pipeline_translates_every_speech_cue():
    cues = load_cues(FIX / "sample.json3.json")
    result = translate_cues(cues, MockProvider())
    assert result.ok
    assert result.stats.translated == 4
    assert result.stats.failed_indices == []
    # Every translated cue keeps its original timing and index.
    assert [c.cue_index for c in result.translated_cues] == [0, 1, 2, 3]
    srt = to_srt(result.translated_cues)
    assert srt.count("-->") == 4


def test_pipeline_uses_deduped_english_for_rolling_captions():
    cues = load_cues(FIX / "rolling_auto.json3.json")
    result = translate_cues(cues, MockProvider())
    english = [c.english_text for c in result.translated_cues]
    assert english == ["so today", "we're going", "to talk", "about captions", "and translation"]


def test_non_speech_cue_is_not_translated_but_others_are():
    cues = load_cues(FIX / "sample.srt")  # cue index 3 is "[Music]"
    result = translate_cues(cues, MockProvider())
    translated_indexes = [c.cue_index for c in result.translated_cues]
    assert 3 not in translated_indexes
    assert translated_indexes == [0, 1, 2, 4]
    assert result.stats.non_speech_cues == 1


def test_corrective_retry_recovers_from_missing_index():
    cues = load_cues(FIX / "sample.json3.json")
    result = translate_cues(cues, MockProvider(fault="missing"))
    assert result.ok
    assert result.stats.corrective_retries >= 1
    assert result.stats.translated == 4


def test_corrective_retry_recovers_from_bad_json():
    cues = load_cues(FIX / "sample.json3.json")
    result = translate_cues(cues, MockProvider(fault="bad_json"))
    assert result.ok
    assert result.stats.corrective_retries >= 1


def test_persistent_failure_is_bounded_and_visible():
    cues = load_cues(FIX / "sample.json3.json")
    result = translate_cues(cues, MockProvider(fault="always_bad"))
    assert not result.ok
    assert result.stats.failed_indices  # reported, not silently dropped
    assert result.stats.splits > 0  # split path was exercised
    # Bounded: no infinite loop. A 4-cue window can split at most into
    # 4-> (2,2) -> (1,1)x2, so provider calls stay comfortably finite.
    assert result.stats.provider_calls < 100


def test_long_sentence_split_across_cues_preserved_by_windowing():
    cues = load_cues(FIX / "sample.json3.json")
    # Force tiny windows so the "sentence" spans multiple windows; every cue
    # must still be translated exactly once.
    config = TranslationConfig(target_size=1, max_size=1, context=1)
    result = translate_cues(cues, MockProvider(), config)
    assert result.ok
    assert [c.cue_index for c in result.translated_cues] == [0, 1, 2, 3]
    assert result.stats.windows == 4
