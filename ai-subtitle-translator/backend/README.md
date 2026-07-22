# Phase 1 — Translation Engine CLI

A standalone command-line engine that converts English subtitle cues into a
natural Persian SRT. This is **only** the Phase 1 engine from
`../docs/roadmap.md` / `../docs/PRD.md` §19. It does **not** include FastAPI,
SQLite, WebSockets, a UI, or any server — those are Phase 2+.

## What it does

```
English cues (JSON3 / VTT / SRT / Phase-0 fixture)
  -> load + validate (dense cue_index, valid timing)
  -> conservative cleaning (tags, entities, non-speech markers)
  -> rolling-duplicate removal (auto-caption scroll)
  -> context-aware windows (~40-70 cues + read-only neighbours)
  -> provider translate (one strict-JSON call per window)
  -> validate coverage (every index exactly once, non-empty)
  -> bounded corrective retry, then split-on-failure
  -> Persian SRT (preserved timing/order, RTL-wrapped)
```

Translation is **never** cue-by-cue: each window carries adjacent context so
the model can understand complete sentences, while the cue-index contract keeps
timing intact.

## Install

The core engine and its tests have **no third-party dependencies** — they run
offline with a built-in deterministic mock provider. The real online provider
needs the Anthropic SDK:

```sh
cd backend
python -m pip install -e ".[anthropic,dev]"   # or just ".[dev]" for offline
```

## Run

Offline dry run (no API key, no cost — exercises the whole pipeline with a
deterministic stand-in translator):

```sh
python -m subtitle_translator.cli tests/fixtures/rolling_auto.json3.json \
  --provider mock -o out.srt
```

Real translation with the default model (`claude-sonnet-5`):

```sh
export ANTHROPIC_API_KEY=sk-ant-...
python -m subtitle_translator.cli captions.srt --title "My video" -o persian.srt
# or, after `pip install -e`:
subtitle-translate captions.srt -o persian.srt
```

Key flags: `--provider {anthropic,mock}`, `--model`, `--window-size`,
`--max-window-size`, `--context`, `--title`, `--no-rtl-wrap`. Exit code is `0`
on full success, `1` if any cue failed validation (reported on stderr), `2` for
input/setup errors, `3` for a provider/transport failure.

## Test

```sh
python -m pytest        # 39 tests, all offline
```

Covers the loaders (JSON3/VTT/SRT/Phase-0), cleaning, rolling-duplicate
removal, windowing, JSON-coverage validation (missing/duplicate/unexpected/
empty/non-JSON), SRT generation, and the full pipeline with the mock provider —
including corrective-retry recovery and bounded split-on-failure.

## Provider / model choice

Default provider **Anthropic**, default model **`claude-sonnet-5`**, chosen for
strong Persian/multilingual quality and reliable structured JSON at moderate
cost. The `providers.py` abstraction makes swapping the model or adding another
provider a small, isolated change. The final model selection is the owner's
Phase-1 experiment (roadmap P1-06 / P1-11) — see
`../docs/PHASE1_TRANSLATION_ENGINE_NOTES.md`.

## Not in this phase

No FastAPI, SQLite, caching layer, polling, extension integration, or progress
UI. The pipeline is written so Phase 2 can move it behind a local API
unchanged.
