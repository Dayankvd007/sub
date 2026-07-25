"""Caption fingerprinting for cache identity.

Computed by the backend from the cues it receives (Phase 2b-1 decision B), so
the client contract is unchanged and no client-supplied hash has to be trusted.

The hash is taken over **loader-normalized** cues — the output of
`cues_from_payload`, before cleaning and rolling-duplicate removal. Those later
stages are deterministic, so identical fingerprints imply identical cleaned
input. Normalizing first means cosmetic whitespace differences in the capture do
not miss the cache, while any real change to timing, ordering, or wording does.
"""

from __future__ import annotations

import hashlib

from .models import Cue


def fingerprint_cues(cues: list[Cue]) -> str:
    """Return a stable sha256 hex digest of ordered cue identity, timing, and text."""
    payload = "\n".join(
        f"{cue.cue_index}|{cue.start_ms}|{cue.end_ms}|{cue.english_text}" for cue in cues
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
