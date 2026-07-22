"""Phase 1 translation engine: English subtitle cues -> natural Persian SRT.

Standalone CLI + library. No FastAPI, no SQLite, no server — those are later
phases. The pipeline is deterministic around a single non-deterministic model
call, with strict structured-output validation and bounded retry/split.
"""

from .models import Cue, TranslatedCue

__all__ = ["Cue", "TranslatedCue"]
