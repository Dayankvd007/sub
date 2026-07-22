"""Rolling-duplicate removal for auto-generated (asr) captions.

YouTube auto-captions scroll: each cue tends to repeat the tail of the
previous cue's words and append a few newly spoken ones. This stage keeps
only the newly spoken portion when the overlap is high-confidence, and
preserves the text unchanged when it is not — never deleting possible speech.

Like cleaning, this transforms text in place and never drops a cue. A cue
that is an exact repeat of the previous one (no new words) is flagged
is_speech=False so it is not translated or rendered twice.
"""

from __future__ import annotations

from dataclasses import replace

from .models import Cue

_MIN_OVERLAP_WORDS = 2


def _words(text: str) -> list[str]:
    return text.split()


def _prefix_overlap(prev: list[str], cur: list[str]) -> int:
    """Largest k where the last k words of prev equal the first k of cur."""
    max_k = min(len(prev), len(cur))
    for k in range(max_k, 0, -1):
        if [w.lower() for w in prev[-k:]] == [w.lower() for w in cur[:k]]:
            return k
    return 0


def remove_rolling_duplicates(cues: list[Cue]) -> tuple[list[Cue], list[str]]:
    """Return de-rolled cues plus change notes. Count/order preserved."""
    result: list[Cue] = []
    changes: list[str] = []
    prev_words: list[str] = []

    for cue in cues:
        if not cue.is_speech:
            result.append(cue)
            # Do not update prev_words from a non-speech cue.
            continue

        cur_words = _words(cue.english_text)

        # Case 1: previous text is a full word-prefix of the current text
        # (cue grew by appending) -> keep only the appended tail.
        if (
            prev_words
            and len(cur_words) > len(prev_words)
            and [w.lower() for w in cur_words[: len(prev_words)]] == [w.lower() for w in prev_words]
        ):
            new_tail = cur_words[len(prev_words):]
            result.append(cue.with_text(" ".join(new_tail), note="rolling-prefix-trimmed"))
            changes.append(f"cue {cue.cue_index}: trimmed rolling prefix ({len(prev_words)} words)")
            prev_words = cur_words
            continue

        # Case 2: exact repeat -> no new speech.
        if prev_words and [w.lower() for w in cur_words] == [w.lower() for w in prev_words]:
            result.append(replace(cue, is_speech=False, notes=[*cue.notes, "rolling-duplicate"]))
            changes.append(f"cue {cue.cue_index}: exact rolling duplicate, flagged non-speech")
            # prev_words unchanged.
            continue

        # Case 3: scrolling window overlap -> trim the leading overlap only
        # when it is confidently long enough.
        k = _prefix_overlap(prev_words, cur_words)
        if k >= _MIN_OVERLAP_WORDS and k < len(cur_words):
            new_tail = cur_words[k:]
            result.append(cue.with_text(" ".join(new_tail), note=f"rolling-overlap-trimmed({k})"))
            changes.append(f"cue {cue.cue_index}: trimmed {k}-word rolling overlap")
            prev_words = cur_words
            continue

        # Otherwise: uncertain -> preserve unchanged.
        result.append(cue)
        prev_words = cur_words

    return result, changes
