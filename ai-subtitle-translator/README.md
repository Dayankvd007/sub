# AI Subtitle Translator

Personal English-to-Persian YouTube subtitle translation tool.

Goal:
Watch English YouTube videos with natural Persian subtitles inside YouTube.

Current phase:
Phase 1 — Translation Engine CLI (In Progress: engine built and tested; a real
OpenRouter run passed on fixtures; long-form quality review by the owner still
pending — not yet Completed)

- **Phase 0 — Caption Extraction** lives in `extension/`; see
  `extension/README.md` and `docs/PHASE0_EXPERIMENT_NOTES.md`. Verdict: GO
  (owner-validated); owner still to commit the raw capture fixtures.
- **Phase 1 — Translation Engine CLI** lives in `backend/`; see
  `backend/README.md` and `docs/PHASE1_TRANSLATION_ENGINE_NOTES.md`. The
  standalone English→Persian SRT engine (loaders, cleaning, rolling dedup,
  context windowing, strict-JSON validation, bounded retry/split, RTL SRT) is
  built and passes 39 tests. An `OpenRouterProvider` was added and merged; a
  real SRT fixture and a real JSON3 fixture both translated fully via
  `google/gemini-3.1-flash-lite` (all cues, no retries/splits/failures, ~$0.0007
  total). The Persian-quality exit gate still needs the owner to run a longer
  real YouTube capture and manually review naturalness, fidelity, terminology,
  segmentation, and long-form comfort before Phase 1 can be marked Completed
  (see that document).

No FastAPI, SQLite, or extension UI yet — those are Phase 2+.
