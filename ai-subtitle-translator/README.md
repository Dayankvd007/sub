# AI Subtitle Translator

Personal English-to-Persian YouTube subtitle translation tool.

Goal:
Watch English YouTube videos with natural Persian subtitles inside YouTube.

Current phase:
Phase 1 — Translation Engine CLI (implementation complete + offline-tested;
live-model quality gate pending owner)

- **Phase 0 — Caption Extraction** lives in `extension/`; see
  `extension/README.md` and `docs/PHASE0_EXPERIMENT_NOTES.md`. Verdict: GO
  (owner-validated); owner still to commit the raw capture fixtures.
- **Phase 1 — Translation Engine CLI** lives in `backend/`; see
  `backend/README.md` and `docs/PHASE1_TRANSLATION_ENGINE_NOTES.md`. The
  standalone English→Persian SRT engine (loaders, cleaning, rolling dedup,
  context windowing, strict-JSON validation, bounded retry/split, RTL SRT) is
  built and passes 39 offline tests; the Persian-quality exit gate needs the
  owner to run a real model with an API key (see that document).

No FastAPI, SQLite, or extension UI yet — those are Phase 2+.
