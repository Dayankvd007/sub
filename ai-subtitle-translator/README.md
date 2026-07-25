# AI Subtitle Translator

Personal English-to-Persian YouTube subtitle translation tool.

Goal:
Watch English YouTube videos with natural Persian subtitles inside YouTube.

Current phase:
Phase 1 — Translation Engine CLI (Completed 2026-07-25)

- **Phase 0 — Caption Extraction** lives in `extension/`; see
  `extension/README.md` and `docs/PHASE0_EXPERIMENT_NOTES.md`. Verdict: GO
  (owner-validated); owner still to commit the raw capture fixtures.
- **Phase 1 — Translation Engine CLI** lives in `backend/`; see
  `backend/README.md` and `docs/PHASE1_TRANSLATION_ENGINE_NOTES.md`. The
  standalone English→Persian SRT engine (loaders, cleaning, rolling dedup,
  context windowing, strict-JSON validation, bounded retry/split, RTL SRT) is
  built and passes 39 tests. `OpenRouterProvider` was added and merged, and
  validated on a real YouTube JSON3 subtitle capture (482 cues, 478 speech)
  with `google/gemini-3.1-flash-lite`: 478/478 translated, 0 failed, 0
  retries, 0 splits, 0 validation errors, ~$0.0019 total cost. The owner
  reviewed the generated Persian SRT on the actual video and accepted the
  quality. Phase-1 exit gate approved; selected model:
  `google/gemini-3.1-flash-lite` via OpenRouter (see that document).

No FastAPI, SQLite, or extension UI yet — those are Phase 2+.
