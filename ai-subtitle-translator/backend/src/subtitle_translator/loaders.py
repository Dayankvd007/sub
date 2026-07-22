"""Input adapters: JSON3 / VTT / SRT / Phase-0 fixture -> ordered Cue list.

All adapters produce the same contract: cues in source order, re-indexed to a
dense zero-based cue_index, with valid timing (end_ms > start_ms >= 0). Text
is decoded but NOT linguistically cleaned here — cleaning is a separate,
testable stage.
"""

from __future__ import annotations

import html
import json
import re
import unicodedata
from pathlib import Path

from .models import Cue


class LoaderError(ValueError):
    """Raised when an input file cannot be parsed into valid cues."""


def load_cues(path: str | Path) -> list[Cue]:
    """Load cues from a file, dispatching on extension and content shape."""
    p = Path(path)
    suffix = p.suffix.lower()
    if suffix not in {".json", ".json3", ".vtt", ".srt"}:
        raise LoaderError(f"Unsupported input extension: {suffix!r} (expected .json/.json3/.vtt/.srt)")

    text = p.read_text(encoding="utf-8")
    if suffix in {".json", ".json3"}:
        return _load_json(text)
    if suffix == ".vtt":
        return parse_vtt(text)
    return parse_srt(text)


def _load_json(text: str) -> list[Cue]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise LoaderError(f"Invalid JSON: {exc}") from exc
    if isinstance(data, dict) and "events" in data:
        return parse_json3(data)
    if isinstance(data, dict) and "cues" in data:
        return parse_phase0_fixture(data)
    raise LoaderError("JSON has neither an 'events' array (json3) nor a 'cues' array (Phase 0 fixture).")


# --- normalization helpers -------------------------------------------------

def _norm_text(raw: str) -> str:
    decoded = html.unescape(raw)
    decoded = unicodedata.normalize("NFC", decoded)
    decoded = decoded.replace("\n", " ")
    return re.sub(r"\s+", " ", decoded).strip()


def _reindex(rows: list[tuple[int, int, str]]) -> list[Cue]:
    """Validate timing and assign dense zero-based indexes in source order."""
    cues: list[Cue] = []
    for i, (start_ms, end_ms, text) in enumerate(rows):
        if start_ms < 0:
            raise LoaderError(f"Cue {i}: negative start_ms ({start_ms}).")
        if end_ms <= start_ms:
            raise LoaderError(f"Cue {i}: end_ms ({end_ms}) must be greater than start_ms ({start_ms}).")
        if not text:
            raise LoaderError(f"Cue {i}: empty text after decoding.")
        cues.append(Cue(cue_index=i, start_ms=start_ms, end_ms=end_ms, english_text=text))
    return cues


# --- Phase 0 fixture -------------------------------------------------------

def parse_phase0_fixture(data: dict) -> list[Cue]:
    rows: list[tuple[int, int, str]] = []
    for entry in data.get("cues", []):
        text = _norm_text(str(entry.get("english_text", "")))
        rows.append((int(entry["start_ms"]), int(entry["end_ms"]), text))
    if not rows:
        raise LoaderError("Phase 0 fixture contained no cues.")
    return _reindex(rows)


# --- YouTube json3 ---------------------------------------------------------

def parse_json3(data: dict) -> list[Cue]:
    rows: list[tuple[int, int, str]] = []
    for event in data.get("events", []):
        if "tStartMs" not in event:
            continue
        segs = event.get("segs")
        if not segs:
            continue
        text = _norm_text("".join(seg.get("utf8", "") for seg in segs))
        if not text:
            continue
        start_ms = int(event["tStartMs"])
        duration = int(event.get("dDurationMs", 0))
        if duration <= 0:
            continue
        rows.append((start_ms, start_ms + duration, text))
    if not rows:
        raise LoaderError("json3 payload produced no usable cues.")
    return _reindex(rows)


# --- SRT -------------------------------------------------------------------

_SRT_TIME = re.compile(
    r"(?P<h>\d{1,2}):(?P<m>\d{2}):(?P<s>\d{2})[,.](?P<ms>\d{1,3})"
)
_TAG = re.compile(r"<[^>]+>")


def _timestamp_to_ms(h: str, m: str, s: str, ms: str) -> int:
    return ((int(h) * 60 + int(m)) * 60 + int(s)) * 1000 + int(ms.ljust(3, "0"))


def parse_srt(text: str) -> list[Cue]:
    rows: list[tuple[int, int, str]] = []
    blocks = re.split(r"\r?\n\r?\n+", text.strip())
    for block in blocks:
        lines = [ln for ln in block.splitlines() if ln.strip() != ""]
        if not lines:
            continue
        # Optional leading sequence number.
        if lines[0].strip().isdigit():
            lines = lines[1:]
        if not lines:
            continue
        times = _SRT_TIME.findall(lines[0])
        if len(times) < 2:
            continue
        start_ms = _timestamp_to_ms(*times[0])
        end_ms = _timestamp_to_ms(*times[1])
        body = _TAG.sub("", " ".join(lines[1:]))
        text_norm = _norm_text(body)
        if not text_norm:
            continue
        rows.append((start_ms, end_ms, text_norm))
    if not rows:
        raise LoaderError("SRT file produced no usable cues.")
    return _reindex(rows)


# --- WebVTT ----------------------------------------------------------------

_VTT_TIME = re.compile(
    r"(?:(?P<h>\d{1,2}):)?(?P<m>\d{2}):(?P<s>\d{2})[.,](?P<ms>\d{1,3})"
)


def parse_vtt(text: str) -> list[Cue]:
    rows: list[tuple[int, int, str]] = []
    body = re.sub(r"^WEBVTT[^\n]*\n", "", text.strip(), count=1)
    blocks = re.split(r"\r?\n\r?\n+", body.strip())
    for block in blocks:
        lines = [ln for ln in block.splitlines() if ln.strip() != ""]
        if not lines:
            continue
        # Skip NOTE and STYLE blocks.
        if lines[0].startswith(("NOTE", "STYLE", "REGION")):
            continue
        # A cue may start with an optional identifier line before the timing.
        timing_idx = next((i for i, ln in enumerate(lines) if "-->" in ln), None)
        if timing_idx is None:
            continue
        times = _VTT_TIME.findall(lines[timing_idx])
        if len(times) < 2:
            continue
        start_ms = _timestamp_to_ms(times[0][0] or "0", times[0][1], times[0][2], times[0][3])
        end_ms = _timestamp_to_ms(times[1][0] or "0", times[1][1], times[1][2], times[1][3])
        body_text = _TAG.sub("", " ".join(lines[timing_idx + 1:]))
        text_norm = _norm_text(body_text)
        if not text_norm:
            continue
        rows.append((start_ms, end_ms, text_norm))
    if not rows:
        raise LoaderError("VTT file produced no usable cues.")
    return _reindex(rows)
