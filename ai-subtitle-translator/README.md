# AI Subtitle Translator

Personal English-to-Persian YouTube subtitle translation tool.

Goal:
Watch English YouTube videos with natural Persian subtitles inside YouTube.

Current phase:
Phase 3 — Chrome Extension Integration (**in progress**). P3-01 through P3-06
are implemented and tested; the P3-07 exit gate is **not yet passed** — it needs
the owner's own Chrome on one real manually captioned and one real
auto-captioned YouTube video. Phase 4 has not begun.

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
  sanitized errors.
- **Phase 2b-1 — Persistence and Cache** adds a local SQLite cache: a repeated
  request for the same video, captions, model, and prompt version returns the
  stored translation with **no provider call**. The database lives in a
  per-user data directory (`SUBTITLE_DB_PATH`), never inside the repository.
- **Phase 2b-2 — Jobs and Progressive Delivery** adds `POST /jobs` and
  `GET /jobs/{id}`: a single sequential background worker thread translates one
  video at a time and commits **every translation window to SQLite as it
  finishes**, so the extension can poll for validated Persian cues (cursor
  `after_cue_index`) while the rest is still being translated, and a crash loses
  at most the window in flight. SQLite is the durable queue of record — the
  in-memory queue is only a doorbell, and on startup the worker re-queues
  anything left unfinished and resumes it **without re-translating a single
  completed cue**. Job creation is idempotent through the Phase 2b-1 cache
  identity, so a completed video returns `cache_hit` with every cue inline and
  no provider call, and `POST /translate` returns `409 job_in_progress` rather
  than clobbering a running job. 153 tests pass with the `[api]` extra; 112 pass
  and 4 skip without it, so the engine keeps zero required dependencies. Real
  uvicorn evidence covers progressive polling and a `SIGKILL` restart recovery —
  see `docs/PHASE2B2_JOBS_NOTES.md`.

  One accepted trade-off is worth knowing before Phase 3: on resume the worker
  re-windows the remaining cues rather than replaying the original layout, so
  resumed window boundaries and context may differ from the first run. That is
  what makes a resume cost zero duplicate provider calls.

- **Phase 3 — Chrome Extension Integration** connects the two halves: an
  in-player **FA** button with eight states, backend calls proxied through the
  MV3 service worker, `POST /jobs` with the client's `cue_index` preserved,
  ~2 s cursor polling with duplicate-proof cue delivery, a hidden `<track>` as
  the timing source, and a right-to-left Persian overlay inside the player.
  Translation starts **only on an explicit click**. 123 extension tests pass
  offline, and a Playwright harness drives the *built* extension in real
  Chromium against the real backend — 38 checks, including real VTTCue
  activation from the media clock, an 83 ms cache hit, and no leaked controls,
  overlays, or TextTracks after eight rapid SPA navigations. See
  `docs/PHASE3_EXTENSION_NOTES.md`.

**Phase 3 is not finished.** The P3-07 gate requires the owner to run one real
manually captioned and one real auto-captioned YouTube video in their own
Chrome, covering pause, resume, seek, 0.75x/1x/1.5x/2x playback, fullscreen, and
in-app navigation. Ads, subtitle style preferences, and reliability polish are
Phase 4 and have not been started.

Quick start:

```sh
# backend
cd backend
python -m pip install -e ".[api,dev]"
export OPENROUTER_API_KEY=...                          # or use .env
export ALLOWED_ORIGINS=chrome-extension://<your-id>    # from chrome://extensions
subtitle-api                                           # http://127.0.0.1:8000

# extension
cd ../extension
npm install && npm run build                           # load dist/ unpacked
```

Then open a YouTube video with English captions and click **FA** in the player
control bar. See `extension/README.md` for the full setup.

> **Note:** the Phase 1 token and cost figures above are under-reported — they
> describe one translation window, not the full run. The reporting defect is
> fixed in Phase 2b-1; the historical numbers are left unchanged pending a
> separate correction PR. See "Known documentation corrections" in
> `docs/roadmap.md`.
