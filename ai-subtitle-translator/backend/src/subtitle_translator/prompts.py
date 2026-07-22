"""Versioned prompt assembly.

The prompt version participates in cache identity in later phases, so it is a
stable constant here. The system text is loaded from prompts/translation_prompt_v1.md
so the human-readable spec and the runtime prompt cannot drift apart.
"""

from __future__ import annotations

import json
from pathlib import Path

from .chunking import Window

PROMPT_VERSION = "v1"

_PROMPT_FILE = Path(__file__).resolve().parents[2] / "prompts" / "translation_prompt_v1.md"


def _load_system_prompt() -> str:
    return _PROMPT_FILE.read_text(encoding="utf-8")


SYSTEM_PROMPT = _load_system_prompt()


def build_user_prompt(
    window: Window,
    *,
    title: str | None = None,
    glossary: dict[str, str] | None = None,
) -> str:
    """Build the per-window user message.

    Context cues are clearly labeled read-only; only the required cues carry
    the indexes the model must return.
    """
    lines: list[str] = [f"window_id: {window.window_id}"]
    if title:
        lines.append(f"video_title: {title}")
    if glossary:
        lines.append("glossary (English -> Persian, keep consistent):")
        for en, fa in glossary.items():
            lines.append(f"  - {en} => {fa}")

    if window.context_before:
        lines.append("")
        lines.append("CONTEXT BEFORE (read-only, do NOT translate or output):")
        for cue in window.context_before:
            lines.append(f"  [{cue.cue_index}] {cue.english_text}")

    lines.append("")
    lines.append("REQUIRED cues to translate (output exactly these indexes):")
    for cue in window.target_cues:
        lines.append(f"  [{cue.cue_index}] {cue.english_text}")

    if window.context_after:
        lines.append("")
        lines.append("CONTEXT AFTER (read-only, do NOT translate or output):")
        for cue in window.context_after:
            lines.append(f"  [{cue.cue_index}] {cue.english_text}")

    lines.append("")
    lines.append(
        "Return ONLY the JSON object described in the system instructions, "
        f"with window_id {window.window_id!r} and one entry per REQUIRED index: "
        f"{json.dumps(window.target_indices)}."
    )
    return "\n".join(lines)
