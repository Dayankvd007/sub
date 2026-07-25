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
offline with a built-in deterministic mock provider. `--provider openrouter`
needs no extra SDK (plain HTTP); `--provider anthropic` needs the Anthropic
SDK:

```sh
cd backend
python -m pip install -e ".[dev]"             # mock + openrouter providers
python -m pip install -e ".[anthropic,dev]"   # if also comparing against Anthropic
```

## Run

Offline dry run (no API key, no cost — exercises the whole pipeline with a
deterministic stand-in translator):

```sh
python -m subtitle_translator.cli tests/fixtures/rolling_auto.json3.json \
  --provider mock -o out.srt
```

Real translation via OpenRouter (currently the provider under evaluation for
Phase 1 — see below):

```sh
export OPENROUTER_API_KEY=sk-or-...
export OPENROUTER_MODEL=google/gemini-3.1-flash-lite   # or pass --model
python -m subtitle_translator.cli captions.srt --provider openrouter \
  --title "My video" -o persian.srt
```

`OPENROUTER_API_KEY` and `OPENROUTER_MODEL` are read only from the local
environment (or a local, gitignored `.env` file) — never hardcoded or bundled.

Real translation via Anthropic with the default model (`claude-sonnet-5`):

```sh
export ANTHROPIC_API_KEY=sk-ant-...
python -m subtitle_translator.cli captions.srt --provider anthropic \
  --title "My video" -o persian.srt
# or, after `pip install -e`:
subtitle-translate captions.srt --provider anthropic -o persian.srt
```

Key flags: `--provider {anthropic,openrouter,mock}`, `--model`,
`--window-size`, `--max-window-size`, `--context`, `--title`, `--no-rtl-wrap`.
Exit code is `0` on full success, `1` if any cue failed validation (reported on
stderr), `2` for input/setup errors, `3` for a provider/transport failure.

## Test

```sh
python -m pytest        # 39 tests, all offline
```

Covers the loaders (JSON3/VTT/SRT/Phase-0), cleaning, rolling-duplicate
removal, windowing, JSON-coverage validation (missing/duplicate/unexpected/
empty/non-JSON), SRT generation, and the full pipeline with the mock provider —
including corrective-retry recovery and bounded split-on-failure.

## Provider / model choice

Two real providers exist behind the same `TranslationProvider` contract:
**Anthropic** (default model `claude-sonnet-5`) and **OpenRouter** (model set
via `--model` or `OPENROUTER_MODEL`). An `OpenRouterProvider` run against a
real SRT fixture and a real JSON3 fixture with `google/gemini-3.1-flash-lite`
translated every expected cue with no retries, splits, or validation errors,
at roughly $0.0007 combined cost — a clean structural result, but **not**
Phase 1's quality exit gate. The `providers.py` abstraction makes swapping the
model or adding another provider a small, isolated change. **Phase 1 is still
in progress, not completed:** the final model selection is still pending the
owner's manual review of a longer real YouTube capture (roadmap P1-06 / P1-11)
— see `../docs/PHASE1_TRANSLATION_ENGINE_NOTES.md`.

## Not in this phase

No FastAPI, SQLite, caching layer, polling, extension integration, or progress
UI. The pipeline is written so Phase 2 can move it behind a local API
unchanged.
