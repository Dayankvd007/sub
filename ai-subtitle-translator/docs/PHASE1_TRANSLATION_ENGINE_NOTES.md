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
  with fault modes), `AnthropicProvider` (real, lazy-imports the SDK), and
  `OpenRouterProvider` (real, OpenAI-compatible chat-completions API; reads
  `OPENROUTER_API_KEY`/`OPENROUTER_MODEL` from the local environment only).
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

### OpenRouter provider (added, PR merged)

`OpenRouterProvider` implements the same `TranslationProvider` contract against
OpenRouter's OpenAI-compatible chat-completions API. Credentials come only from
the local `OPENROUTER_API_KEY` environment variable (never hardcoded, never
bundled); the model is read from `--model` or `OPENROUTER_MODEL`.

A real end-to-end run was made with `--provider openrouter` and
`google/gemini-3.1-flash-lite` against one real SRT fixture and one real JSON3
fixture:

- Both runs translated every expected cue.
- Zero retries, zero splits, zero `failed_indices`, zero validation errors.
- Combined cost for both runs was approximately $0.0007.
- All 39 existing pytest tests still pass (offline, mock provider — unaffected
  by the new provider).

This confirms the provider abstraction and pipeline work correctly against a
second real backend, and gives one clean structural data point for
`google/gemini-3.1-flash-lite`. It is **not** the full P1-06 model comparison
and **not** the P1-11 quality exit gate: those require a longer real YouTube
capture and the owner's manual read of the Persian output (naturalness,
fidelity, terminology, segmentation, comfort over long-form viewing). A second
model is only worth comparing if this one turns out not to be good enough on
that longer review.

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

- **No long-form, full-video quality review has been run yet.** A real SRT
  fixture and a real JSON3 fixture were translated successfully via
  `OpenRouterProvider` with `google/gemini-3.1-flash-lite` (see above), but
  that is a structural smoke test on fixtures, not the representative
  full-length educational video required for the Phase-1 exit gate
  (roadmap P1-11). The owner still needs to run a longer real YouTube
  subtitle capture and manually judge naturalness, fidelity, terminology,
  segmentation, and comfort for long-form viewing.
- **The model comparison (P1-06) is not finished.** One real model
  (`google/gemini-3.1-flash-lite` via OpenRouter) has clean structural results;
  a second candidate (e.g. Claude Haiku 4.5 or another Gemini Flash variant)
  should only be compared if this one is not good enough on the long-form
  review, and the final model-selection decision still needs to be recorded.
- **No real Phase-0 fixtures** are committed yet, so Phase-1 regression tests
  run against synthetic samples. The test suite will pick up real
  `extension/tests/fixtures/real-*.json` fixtures if/when the owner commits
  them (they load via the JSON3 / Phase-0 adapters).
- Persian readability/condensation and any characters-per-second threshold are
  intentionally deferred (PRD §11.7); windowing keeps one-to-one cue mapping for
  now.

## Phase 1 verdict

**Status: PHASE 1 IN PROGRESS — NOT COMPLETED.**

The engine, validation, recovery, and SRT generation are implemented and
tested (39 pytest tests). A real online provider now exists beyond Anthropic —
`OpenRouterProvider`, merged — and has been proven against real fixtures: one
real SRT fixture and one real JSON3 fixture both translated fully with
`google/gemini-3.1-flash-lite`, zero retries/splits/failed indices/validation
errors, ~$0.0007 combined cost. All 39 existing tests still pass.

That is real evidence, but it is not the Phase-1 exit gate. Per roadmap P1-11,
Phase 1 can only be marked fully passed once the owner runs a complete,
representative, longer real YouTube subtitle capture through the CLI and
manually judges the Persian as comfortable — naturalness, fidelity,
terminology, segmentation, and long-form viewing comfort — with every expected
cue present exactly once, and records the final model-selection decision.
**None of that manual, long-form review has happened yet, so Phase 1 remains
In Progress, not Completed.**

### Next step (owner action required)

1. `cd backend && python -m pip install -e ".[dev]"` (no extra SDK needed for
   `--provider openrouter`; use `".[anthropic,dev]"` only if comparing against
   Anthropic).
2. `export OPENROUTER_API_KEY=...` and optionally `export
   OPENROUTER_MODEL=google/gemini-3.1-flash-lite`.
3. Run a **longer, representative real YouTube subtitle capture** (not just a
   short fixture) through the CLI:
   `subtitle-translate <captions> --provider openrouter --title "<video>" -o persian.srt`.
4. Manually review the Persian output for naturalness, fidelity, terminology,
   segmentation, and comfort over long-form viewing.
5. Only if `google/gemini-3.1-flash-lite` is not good enough on that review,
   compare a second model (e.g. `--model claude-haiku-4-5` via
   `--provider anthropic`, or another OpenRouter model).
6. Record the final model-selection decision here and in
   `docs/roadmap.md`, and only then mark the Phase-1 exit gate (P1-11) passed.
