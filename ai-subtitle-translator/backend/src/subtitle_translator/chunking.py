"""Context-window construction.

Groups the ordered *speech* cues into translation windows of roughly
40-70 cues (configurable), each carrying a limited amount of read-only
adjacent context so the model can understand complete sentences without
duplicating context cues in its output.

Invariant: every speech cue belongs to exactly one window's target set, and
target sets partition the speech cues in order with no gaps or overlaps.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .models import Cue


@dataclass
class Window:
    window_id: str
    target_cues: list[Cue]              # cues the model must translate
    context_before: list[Cue] = field(default_factory=list)
    context_after: list[Cue] = field(default_factory=list)

    @property
    def target_indices(self) -> list[int]:
        return [c.cue_index for c in self.target_cues]


def build_windows(
    cues: list[Cue],
    *,
    target_size: int = 50,
    max_size: int = 70,
    context: int = 2,
) -> list[Window]:
    """Partition speech cues into windows with adjacent read-only context.

    Only cues with is_speech=True become translation targets; non-speech cues
    (music markers, rolling duplicates) are excluded from windows entirely.
    """
    if target_size < 1 or max_size < target_size:
        raise ValueError("Require 1 <= target_size <= max_size.")

    speech = [c for c in cues if c.is_speech]
    windows: list[Window] = []
    i = 0
    n = len(speech)
    while i < n:
        end = min(i + target_size, n)
        # Never exceed max_size (target_size already <= max_size, so this is a
        # guard for future callers that tune the two independently).
        end = min(end, i + max_size)
        target = speech[i:end]
        before = speech[max(0, i - context):i]
        after = speech[end:end + context]
        windows.append(
            Window(
                window_id=f"w_{len(windows) + 1:04d}",
                target_cues=target,
                context_before=list(before),
                context_after=list(after),
            )
        )
        i = end
    return windows
