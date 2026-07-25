"""Minimal .env loader (stdlib only).

Reads KEY=VALUE lines from a `.env` file into os.environ, without ever
logging, printing, or returning the values it loads. Existing environment
variables always win — a real `export` at the shell takes precedence over the
file. This keeps the CLI dependency-free (no python-dotenv) while still
letting a local `.env` supply provider credentials.
"""

from __future__ import annotations

import os
from pathlib import Path


def _parse_line(line: str) -> tuple[str, str] | None:
    line = line.strip()
    if not line or line.startswith("#") or "=" not in line:
        return None
    key, _, value = line.partition("=")
    key = key.strip()
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1]
    if not key:
        return None
    return key, value


def load_dotenv(start: Path | None = None, *, filename: str = ".env", max_levels: int = 6) -> Path | None:
    """Search `start` and its parents for `filename` and load it into os.environ.

    Returns the path loaded, or None if no file was found. Never overwrites a
    variable already present in the environment.
    """
    directory = (start or Path.cwd()).resolve()
    for _ in range(max_levels):
        candidate = directory / filename
        if candidate.is_file():
            for raw_line in candidate.read_text(encoding="utf-8").splitlines():
                parsed = _parse_line(raw_line)
                if parsed is None:
                    continue
                key, value = parsed
                os.environ.setdefault(key, value)
            return candidate
        if directory.parent == directory:
            break
        directory = directory.parent
    return None
