"""Phase 2b-1 — caption fingerprinting."""

from subtitle_translator.fingerprint import fingerprint_cues
from subtitle_translator.loaders import cues_from_payload


def payload(*rows):
    return [
        {"cue_index": i, "start_ms": s, "end_ms": e, "english_text": t}
        for (i, s, e, t) in rows
    ]


BASE = payload((0, 0, 1000, "Hello there."), (1, 1000, 2000, "This is a test."))


def fp(rows):
    return fingerprint_cues(cues_from_payload(rows))


def test_is_stable_for_identical_input():
    assert fp(BASE) == fp(BASE)


def test_ignores_cosmetic_whitespace():
    """Loader normalization runs first, so trivial whitespace must not miss the cache."""
    spaced = payload((0, 0, 1000, "Hello   there."), (1, 1000, 2000, "This is a\ttest."))
    assert fp(spaced) == fp(BASE)


def test_changes_when_text_changes():
    changed = payload((0, 0, 1000, "Hello there!"), (1, 1000, 2000, "This is a test."))
    assert fp(changed) != fp(BASE)


def test_changes_when_timing_changes():
    changed = payload((0, 0, 1500, "Hello there."), (1, 1500, 2000, "This is a test."))
    assert fp(changed) != fp(BASE)


def test_changes_when_cue_index_changes():
    changed = payload((5, 0, 1000, "Hello there."), (6, 1000, 2000, "This is a test."))
    assert fp(changed) != fp(BASE)


def test_changes_when_a_cue_is_added():
    longer = BASE + payload((2, 2000, 3000, "Goodbye."))
    assert fp(longer) != fp(BASE)
