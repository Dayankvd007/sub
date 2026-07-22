"""Conservative caption cleaning.

Transforms cue text in place; never drops a cue (that would break the
cue_index contract downstream). A cue that reduces to a bare non-speech
marker is flagged is_speech=False instead of being removed, so the index map
stays complete and auditable.

Timestamps are never changed here.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import replace

from .models import Cue

# A cue whose ENTIRE content is one of these bracketed markers is non-speech.
_NON_SPEECH_ONLY = re.compile(
    r"^\s*[\[(]\s*(music|applause|laughter|cheering|silence|inaudible|"
    r"background noise|no audio)\s*[\])]\s*$",
    re.IGNORECASE,
)
# Inline markers to strip when they appear alongside real speech.
_INLINE_MARKER = re.compile(
    r"[\[(]\s*(music|applause|laughter|cheering|inaudible)\s*[\])]",
    re.IGNORECASE,
)
_TAG = re.compile(r"<[^>]+>")


def clean_text(raw: str) -> str:
    text = unicodedata.normalize("NFC", raw)
    text = _TAG.sub("", text)
    text = _INLINE_MARKER.sub(" ", text)
    # Collapse whitespace and normalize a few common noise characters.
    text = text.replace("​", "")  # zero-width space
    text = re.sub(r"\s+", " ", text).strip()
    return text


def clean_cues(cues: list[Cue]) -> tuple[list[Cue], list[str]]:
    """Return cleaned cues and a list of human-readable change notes.

    Cue count and ordering are preserved. Cues reduced to a non-speech marker
    are flagged is_speech=False (kept, not dropped).
    """
    cleaned: list[Cue] = []
    changes: list[str] = []
    for cue in cues:
        if _NON_SPEECH_ONLY.match(cue.english_text):
            changes.append(f"cue {cue.cue_index}: non-speech marker retained but not translated")
            cleaned.append(
                replace(
                    cue,
                    is_speech=False,
                    notes=[*cue.notes, "non-speech"],
                )
            )
            continue

        new_text = clean_text(cue.english_text)
        if not new_text:
            # Became empty for a reason other than a recognized marker — keep
            # the object, flag it, and record the uncertainty rather than
            # silently deleting possible speech.
            changes.append(f"cue {cue.cue_index}: empty after cleaning, flagged non-speech")
            cleaned.append(replace(cue, is_speech=False, notes=[*cue.notes, "empty-after-clean"]))
            continue

        if new_text != cue.english_text:
            changes.append(f"cue {cue.cue_index}: text normalized")
            cleaned.append(cue.with_text(new_text, note="cleaned"))
        else:
            cleaned.append(cue)
    return cleaned, changes
