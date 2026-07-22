"""Persian SRT generation.

Preserves timing and order. Because Persian is right-to-left, each rendered
line is wrapped in an explicit RTL embedding (U+202B ... U+202C) by default so
players that don't auto-detect bidi still lay the text out correctly; this can
be turned off for players that handle bidi themselves.
"""

from __future__ import annotations

from .models import TranslatedCue

_RLE = "‫"  # RIGHT-TO-LEFT EMBEDDING
_PDF = "‬"  # POP DIRECTIONAL FORMATTING


def format_timestamp(ms: int) -> str:
    if ms < 0:
        raise ValueError("Timestamp cannot be negative.")
    hours, rem = divmod(ms, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    seconds, millis = divmod(rem, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"


def to_srt(cues: list[TranslatedCue], *, rtl_wrap: bool = True) -> str:
    """Render validated Persian cues to an SRT string.

    Cues are emitted in cue_index order with sequential SRT sequence numbers.
    Cues must have valid, non-overlapping-enough timing (end > start); callers
    pass only validated, translated cues.
    """
    ordered = sorted(cues, key=lambda c: (c.start_ms, c.cue_index))
    blocks: list[str] = []
    for seq, cue in enumerate(ordered, start=1):
        if cue.end_ms <= cue.start_ms:
            raise ValueError(f"Cue {cue.cue_index}: end_ms must be greater than start_ms.")
        text = cue.persian_text.strip()
        if rtl_wrap:
            text = f"{_RLE}{text}{_PDF}"
        blocks.append(
            f"{seq}\n{format_timestamp(cue.start_ms)} --> {format_timestamp(cue.end_ms)}\n{text}"
        )
    return "\n\n".join(blocks) + ("\n" if blocks else "")
