from subtitle_translator.models import TranslatedCue
from subtitle_translator.srt import format_timestamp, to_srt


def _tc(i, start, end, fa):
    return TranslatedCue(cue_index=i, start_ms=start, end_ms=end, english_text=f"en{i}", persian_text=fa)


def test_format_timestamp():
    assert format_timestamp(0) == "00:00:00,000"
    assert format_timestamp(3_661_042) == "01:01:01,042"


def test_to_srt_structure_and_order():
    cues = [_tc(1, 3000, 5000, "دو"), _tc(0, 0, 2000, "یک")]
    out = to_srt(cues, rtl_wrap=False)
    blocks = out.strip().split("\n\n")
    assert len(blocks) == 2
    # Sorted by start_ms -> "یک" first, sequence numbers are 1-based.
    assert blocks[0].startswith("1\n00:00:00,000 --> 00:00:02,000\nیک")
    assert blocks[1].startswith("2\n00:00:03,000 --> 00:00:05,000\nدو")


def test_rtl_wrap_adds_directional_marks():
    out = to_srt([_tc(0, 0, 1000, "سلام")], rtl_wrap=True)
    assert "‫" in out and "‬" in out


def test_empty_input_produces_empty_string():
    assert to_srt([]) == ""
