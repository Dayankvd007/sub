# Phase 1 Notes — Translation Engine CLI

Source of truth: `docs/PRD.md` §11, §19 (Phase 1); `docs/technical-architecture.md`
§5, §6, §12 (Phase 1); `docs/roadmap.md` (P1-01 … P1-11). This document records
what was built, the model/prompt/chunking decisions, test results, and the
honest current status of the Phase 1 exit gate.

## Implementation approach

A standalone Python package + CLI in `backend/` (`subtitle_translator`), with no
FastAPI, SQLite, server, or UI. The pipeline is deterministic around a single
non-deterministic model call:

```
load -> clean -> remove rolling duplicates -> context windows ->
provider translate -> validate coverage -> bounded retry -> split-on-failure ->
Persian SRT
```

Module map:

- `loaders.py` — JSON3 / VTT / SRT / Phase-0-fixture adapters -> one cue
  contract (dense zero-based `cue_index`, valid timing). Text is decoded but
  not linguistically cleaned here.
- `cleaning.py` — conservative, in-place normalization (Unicode NFC, tag/entity
  stripping, whitespace, non-speech markers). Never drops a cue; a cue reduced
  to a bare marker is flagged `is_speech=False` so the index map stays complete.
- `dedup.py` — rolling-duplicate removal for auto-captions. Trims the repeated
  prefix/overlap and keeps only newly spoken words; preserves text when the
  overlap is below a confidence threshold; exact repeats are flagged non-speech.
- `chunking.py` — partitions speech cues into windows (~40-70 cues) with
  read-only adjacent context. Invariant: every speech cue is in exactly one
  window's target set.
- `prompts.py` + `prompts/translation_prompt_v1.md` — versioned prompt
  (`PROMPT_VERSION = "v1"`); the runtime prompt is loaded from the Markdown spec
  so the two cannot drift.
- `providers.py` — provider abstraction. `MockProvider` (deterministic, offline,
  with fault modes) and `AnthropicProvider` (real, lazy-imports the SDK).
- `validation.py` — strict JSON parse + cue-coverage check. Fails visibly on
  missing / duplicate / unexpected / empty indexes with the exact offending set,
  and returns a targeted correction message for the retry.
- `pipeline.py` — orchestration with bounded corrective retry and split-on-
  failure; produces `TranslatedCue`s and per-run stats.
- `srt.py` — Persian SRT with preserved timing/order and RTL wrapping.
- `cli.py` — argparse CLI (`subtitle-translate`).

### Why cue-by-cue translation is rejected (implemented as designed)

Windows carry adjacent context marked read-only; the model translates only the
required indexes but may use context to choose wording. The index contract
keeps original timing while allowing sentence-level understanding — matching
PRD §11 and architecture §5.

## Model choice

**Provider: Anthropic. Initial default model: `claude-sonnet-5`.**

Rationale, against the PRD's criteria (Persian quality > cost, with reliability):

- **Persian quality** — a strong multilingual model, suitable for natural,
  idiomatic Persian on long-form educational content (NFR-001 prioritizes
  quality).
- **Structured-output reliability** — reliably returns strict JSON, which the
  pipeline requires (FR-009, validation stage).
- **Cost** — $3/$15 per 1M tokens (introductory $2/$10 through 2026-08-31);
  cheaper candidates are noted below for the owner's comparison. Thinking is
  disabled in the adapter (translation does not benefit from it; it would add
  latency and cost).

The `providers.py` abstraction keeps the model a one-line config change
(`config.DEFAULT_MODEL`) and allows a second provider (e.g. a Gemini Flash or
OpenAI-small adapter) to be added without touching the pipeline. **The final
selection remains the owner's Phase-1 experiment (roadmap P1-06):** cheaper
candidates to compare are **Claude Haiku 4.5** ($1/$5) and **Gemini Flash**;
these should be scored on naturalness, terminology, segmentation, malformed-
output rate, latency, and cost on real target-video windows before locking a
choice.

Credentials come only from `ANTHROPIC_API_KEY` via the SDK's default
resolution — never hardcoded, never bundled.

## Prompt version

`v1` (`backend/prompts/translation_prompt_v1.md`). Requires: natural Persian,
meaning preservation, cue-index preservation, terminology consistency,
JSON-only output, no commentary. Any change that can alter Persian output or
structure must bump to `v2` rather than editing `v1` in place, because the
version participates in cache identity in Phase 2.

## Chunking strategy

Initial experimental window: **target 50 cues, max 70, 2 context cues per
side** (config-tunable via `--window-size` / `--max-window-size` / `--context`).
This sits inside the PRD's 40-70 range. Window size vs. quality/latency/cost is
an explicit Phase-1 experiment (roadmap "Translation window size") the owner
should run on real manual + auto samples; the values are defaults, not a fixed
decision.

## Validation and recovery

- Strict JSON extraction (tolerates a single ```json fence or light surrounding
  prose, rejects genuinely non-JSON).
- Exact set-equality coverage check; missing/duplicate/unexpected/empty all
  fail with the offending indexes named.
- One corrective retry per window with a targeted instruction describing the
  exact defect; on repeated failure the window is split and each half retried,
  bounded by `max_split_depth` so one bad window cannot loop or run up cost.
- Cues that still fail are reported in `stats.failed_indices` (and CLI exit
  code 1) — never silently dropped, never rendered as if complete.

## Test results

`python -m pytest` → **39 passed**, fully offline (deterministic
`MockProvider`, no API key, no cost). Coverage:

- `test_loaders.py` — JSON3/VTT/SRT/Phase-0 parsing, tag stripping, dense
  indexing, cross-format equivalence, bad-timing and unsupported-extension
  errors.
- `test_cleaning.py` — tag/marker stripping, non-speech flagging (not dropping),
  timestamps unchanged.
- `test_dedup.py` — rolling prefix + scroll-overlap reconstruction of spoken
  text, exact-duplicate flagging, sub-threshold overlap preserved.
- `test_chunking.py` — windows partition speech cues exactly once, non-speech
  excluded, adjacent read-only context.
- `test_validation.py` — valid / fenced / prose-wrapped JSON, and
  missing/duplicate/unexpected/empty/non-JSON failures.
- `test_srt.py` — timestamp formatting, ordering, RTL marks, empty input.
- `test_pipeline.py` — full translate of every speech cue; rolling-caption
  English is de-duplicated in output; non-speech cue excluded; corrective-retry
  recovery from a missing index and from bad JSON; **bounded, visible**
  persistent failure that exercises the split path; long sentence split across
  tiny windows still translated exactly once.
- `test_cli.py` — CLI writes an SRT via the mock provider; load-error exit code.

End-to-end offline demo (`--provider mock` on the rolling-auto fixture) produced
a valid, ordered, RTL-wrapped SRT whose English source lines were correctly
de-rolled to `so today / we're going / to talk / about captions / and
translation`.

## Limitations / what is NOT yet done

- **No live-model quality review has been run.** This build sandbox has no
  Anthropic API key and cannot reach the provider, so a real Persian
  translation of a full educational video — the substance of the Phase-1 exit
  gate (roadmap P1-11) — has **not** been produced or judged. The `anthropic`
  provider code is written to the current SDK but is exercised only by the
  owner running it with a key.
- **No model comparison has been run** (P1-06). Only a default was chosen with
  documented reasoning; Haiku 4.5 / Gemini Flash comparison is pending.
- **No real Phase-0 fixtures** are committed yet, so Phase-1 regression tests
  run against synthetic samples. The test suite will pick up real
  `extension/tests/fixtures/real-*.json` fixtures if/when the owner commits
  them (they load via the JSON3 / Phase-0 adapters).
- Persian readability/condensation and any characters-per-second threshold are
  intentionally deferred (PRD §11.7); windowing keeps one-to-one cue mapping for
  now.

## Phase 1 verdict

**Status: IMPLEMENTATION COMPLETE and offline-tested; quality exit gate
PENDING the owner's live-model run.**

Per roadmap P1-11, Phase 1 can only be marked fully passed after the owner runs
a complete representative educational video through the CLI with a real model
and judges the Persian comfortable, with every expected cue present exactly
once. The engine, validation, recovery, and SRT generation are done and
verified offline; the quality judgment is the owner's and is not yet performed.

### Next step (owner action required)

1. `cd backend && python -m pip install -e ".[anthropic,dev]"`.
2. `export ANTHROPIC_API_KEY=...`.
3. Run a representative caption file (ideally a real Phase-0 fixture):
   `subtitle-translate <captions> --title "<video>" -o persian.srt`.
4. Review the Persian for naturalness, fidelity, terminology, segmentation, and
   structural integrity; optionally compare `--model claude-haiku-4-5` and a
   Gemini Flash adapter for the P1-06 model decision.
5. Record the outcome here and, if accepted, mark the Phase-1 exit gate passed.
