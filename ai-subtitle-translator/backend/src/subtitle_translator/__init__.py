"""Translation engine: English subtitle cues -> natural Persian SRT.

Phase 1 provides the standalone CLI + library: the pipeline is deterministic
around a single non-deterministic model call, with strict structured-output
validation and bounded retry/split. Phase 2a adds a local FastAPI service over
the same engine (`subtitle_translator.api`, optional `[api]` extra). No SQLite,
job system, or cloud deployment — those are later phases.
"""

from .models import Cue, TranslatedCue

__version__ = "0.2.0"

__all__ = ["Cue", "TranslatedCue", "__version__"]
