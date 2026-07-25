# AI Subtitle Translator

Personal English-to-Persian YouTube subtitle translation tool.

Goal:
Watch English YouTube videos with natural Persian subtitles inside YouTube.

Current phase:
Phase 2a — API Foundation (Completed 2026-07-25). Next: Phase 2b —
persistence, caching, jobs, recovery, and polling.

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
- **Phase 2a — API Foundation** also lives in `backend/`; see
  `backend/README.md`. A localhost FastAPI service (`subtitle-api`) wraps the
  unchanged Phase 1 engine with `GET /health` and `POST /translate`, a
  framework-free `TranslationService` layer, Pydantic contracts,
  environment-based configuration, a CORS allowlist, request caps, and
  sanitized errors. 66 tests pass with the `[api]` extra; 47 pass and 2 skip
  without it, so the engine keeps zero required dependencies.

Quick start for the local API:

```sh
cd backend
python -m pip install -e ".[api,dev]"
export OPENROUTER_API_KEY=...                          # or use .env
export ALLOWED_ORIGINS=chrome-extension://<your-id>
subtitle-api                                           # http://127.0.0.1:8000
```

No SQLite, caching, job system, polling, or extension UI yet — those are
Phase 2b and Phase 3.
