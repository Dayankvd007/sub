from subtitle_translator.chunking import build_windows
from subtitle_translator.models import Cue


def _cues(n, non_speech_at=()):
    out = []
    for i in range(n):
        out.append(
            Cue(
                cue_index=i,
                start_ms=i * 1000,
                end_ms=i * 1000 + 900,
                english_text=f"line {i}",
                is_speech=i not in non_speech_at,
            )
        )
    return out


def test_windows_partition_speech_cues_exactly_once():
    cues = _cues(10)
    windows = build_windows(cues, target_size=4, max_size=4, context=1)
    covered = [idx for w in windows for idx in w.target_indices]
    assert covered == list(range(10))  # ordered, no gaps, no overlap
    assert len(windows) == 3  # 4 + 4 + 2


def test_non_speech_cues_excluded_from_targets():
    cues = _cues(6, non_speech_at={2, 4})
    windows = build_windows(cues, target_size=10, max_size=10, context=1)
    covered = [idx for w in windows for idx in w.target_indices]
    assert covered == [0, 1, 3, 5]


def test_context_is_adjacent_and_read_only():
    cues = _cues(9)
    windows = build_windows(cues, target_size=3, max_size=3, context=2)
    second = windows[1]
    assert second.target_indices == [3, 4, 5]
    assert [c.cue_index for c in second.context_before] == [1, 2]
    assert [c.cue_index for c in second.context_after] == [6, 7]
