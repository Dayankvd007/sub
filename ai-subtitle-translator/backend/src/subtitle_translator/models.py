"""Internal data model shared across the pipeline.

The cue contract mirrors Phase 0's output exactly: cue_index, start_ms,
end_ms, english_text. Persian is added downstream. cue_index is the stable
identity that must survive from load through translation to SRT — nothing in
the pipeline is allowed to renumber or drop it silently.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace


@dataclass
class Cue:
    cue_index: int
    start_ms: int
    end_ms: int
    english_text: str
    # True unless cleaning reduced the cue to nothing but a non-speech marker
    # (e.g. "[Music]"). Non-speech cues are not sent to the model and are not
    # rendered, but we keep the object so the index map stays auditable.
    is_speech: bool = True
    # Free-form notes about transformations applied (cleaning, dedup), kept so
    # the source remains auditable per the architecture doc.
    notes: list[str] = field(default_factory=list)

    def with_text(self, text: str, *, note: str | None = None) -> "Cue":
        notes = self.notes if note is None else [*self.notes, note]
        return replace(self, english_text=text, notes=notes)


@dataclass
class TranslatedCue:
    cue_index: int
    start_ms: int
    end_ms: int
    english_text: str
    persian_text: str
