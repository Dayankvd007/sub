"""Runtime configuration.

Windowing, retry, and provider selection live here. Provider credentials are
read from the environment by the provider itself (ANTHROPIC_API_KEY) and are
never stored in this object.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Default initial model for the online provider. Chosen for strong Persian /
# multilingual quality and reliable structured JSON at moderate cost; the
# provider layer makes swapping this a one-line change, and the final model
# choice remains the owner's Phase-1 experiment (roadmap P1-06 / P1-11).
DEFAULT_MODEL = "claude-sonnet-5"


@dataclass
class TranslationConfig:
    provider_name: str = "anthropic"
    model: str = DEFAULT_MODEL
    target_size: int = 50
    max_size: int = 70
    context: int = 2
    # Corrective retries for the same window before splitting.
    max_retries: int = 1
    # How many times a failing window may be split before its cues are marked
    # failed. Bounds total model calls so one bad window cannot loop.
    max_split_depth: int = 2
    title: str | None = None
    glossary: dict[str, str] = field(default_factory=dict)
