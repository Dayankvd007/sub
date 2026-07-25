# Backend — Translation Engine CLI (Phase 1) + Local API (Phase 2a/2b-1/2b-2)

Converts English subtitle cues into a natural Persian SRT, usable two ways:

- **Phase 1 — CLI** (`subtitle-translate`): the standalone engine, no server.
- **Phase 2a — local API** (`subtitle-api`): a localhost FastAPI service over
  the *same* engine, ready for the Chrome extension to call.

Phase 2b-1 adds local SQLite persistence and a translation cache. Phase 2b-2
adds background translation jobs (`POST /jobs`, `GET /jobs/{id}`) with
progressive delivery, polling, and restart recovery.

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
python -m pip install -e ".[dev]"             # mock + openrouter providers (CLI only)
python -m pip install -e ".[api,dev]"         # + the Phase 2a local API service
python -m pip install -e ".[anthropic,dev]"   # if also comparing against Anthropic
```

Copy `.env.example` to `.env` and fill in `OPENROUTER_API_KEY` locally. `.env`
is git-ignored; keys are read from the environment only and never appear in a
response, log line, or committed file.

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

## Run the local API (Phase 2a)

```sh
python -m pip install -e ".[api,dev]"
export OPENROUTER_API_KEY=...                          # or put it in .env
export ALLOWED_ORIGINS=chrome-extension://<your-id>    # from chrome://extensions
subtitle-api                                           # http://127.0.0.1:8000
```

Bound to `127.0.0.1` by default, never `0.0.0.0`. Configuration comes from the
environment — see `.env.example` for every variable.

### `GET /health`

```json
{ "status": "ok", "version": "0.2.0", "prompt_version": "v1",
  "provider": "openrouter", "model": "google/gemini-3.1-flash-lite",
  "provider_configured": true }
```

`provider_configured` reports only *whether* a key exists — never the key.

### `POST /translate`

```jsonc
// request
{ "video_id": "dQw4w9WgXcQ",
  "title": "optional context for the model",
  "cues": [ { "cue_index": 0, "start_ms": 0, "end_ms": 1200,
              "english_text": "Hello and welcome." } ],
  "options": { "include_srt": true, "rtl_wrap": true } }
```

```jsonc
// response
{ "video_id": "dQw4w9WgXcQ", "status": "completed", "cache_hit": false,
  "prompt_version": "v1", "provider": "openrouter", "model": "…",
  "stats": { "total_cues": 482, "speech_cues": 478, "non_speech_cues": 4,
             "windows": 10, "translated": 478, "failed": 0,
             "provider_calls": 10, "corrective_retries": 0, "splits": 0 },
  "failed_indices": [],
  "cues": [ { "cue_index": 0, "start_ms": 0, "end_ms": 1200,
              "persian_text": "…" } ],
  "srt": "1\n00:00:00,000 --> …",
  // usage is the total across all windows, not the last call
  "usage": { "prompt_tokens": 11320, "completion_tokens": 10800,
             "cost": 0.019, "calls": 10 } }
```

Contract notes for the extension:

- **`cue_index` is yours.** Whatever indexes you send are echoed back
  unchanged; the backend never renumbers.
- **The response may omit indexes you sent.** Non-speech cues (`[Music]`) are
  never translated, and anything that failed validation appears in
  `failed_indices` rather than being faked. Render gaps as "no subtitle".
- **`status` is `"completed"` or `"partial"`.**
- **This call is synchronous** and runs the whole video in one request — expect
  minutes for a long video. For progressive results, use `POST /jobs` below.
  This endpoint returns `409 job_in_progress` if a background job is already
  translating the same video, captions, model, and prompt version.
- **Repeat requests are served from the local cache.** An identical video,
  captions, model, and prompt version returns the stored translation with
  `cache_hit: true` and no provider call. Changing any of those retranslates.
  A partial result is never served as complete.

### `POST /jobs` (Phase 2b-2)

Same request body as `POST /translate`. Returns immediately with a `job_id`
instead of holding the connection for the whole video.

```jsonc
// 201 Created — new job
{ "job_id": 42, "video_id": "dQw4w9WgXcQ", "status": "queued",
  "cache_hit": false, "total_cues": 482, "speech_cues": null,
  "completed_cues": 0, "prompt_version": "v1", "provider": "openrouter",
  "model": "…", "cues": [], "created_at": "2026-07-25T…" }
```

Returns **200** rather than 201 when nothing was created:

| Existing state for this cache identity | Result |
|---|---|
| completed | `cache_hit: true`, every cue inline — no worker, no provider, no polling |
| queued / processing | the same `job_id`; a second translation is never started |
| partial / failed | resumed — completed cues are kept and only the rest is translated |

### `GET /jobs/{id}` (Phase 2b-2)

Poll roughly every 2 s while `done` is false. `?after_cue_index=N` returns only
newer cues; `?include_srt=true` renders the full subtitle file.

```jsonc
{ "job_id": 42, "video_id": "dQw4w9WgXcQ", "status": "processing",
  "done": false,
  "progress": { "total_cues": 482, "speech_cues": 478, "completed_cues": 100,
                "failed_cues": 0, "percent": 20.9 },
  "cues": [ { "cue_index": 51, "start_ms": …, "end_ms": …, "persian_text": "…" } ],
  "next_cursor": 100, "failed_indices": [], "error": null, "srt": null }
```

Statuses: `queued` (waiting) → `processing` (translating, cues arriving) →
`completed` (all speech cues done) / `partial` (finished, some cues failed) /
`failed` (terminal error, translated cues kept). `done` is true for the last
three.

Polling contract for the extension:

- **Only validated cues are ever returned.** Failed indexes appear in
  `failed_indices`, never as invented Persian.
- **Cursor + client-side dedup.** Cues normally arrive in video order, but
  after `done` becomes true, make **one final read with no cursor** and
  reconcile — that covers any cue that completed out of order.
- **A read never starts work.** `GET` is pure; only `POST /jobs` queues.
- **Resume is just re-posting.** The same payload after a failure resumes the
  job rather than restarting it.

Errors are always `{"error_code", "message"}`: `400 invalid_cues`,
`404 job_not_found`, `409 job_in_progress`, `413 too_many_cues` /
`payload_too_large`, `422` schema violations, `502 provider_error` (sanitized —
upstream details are never echoed), `503 jobs_unavailable` (persistence
disabled), `500 internal_error`.

## Cache (Phase 2b-1)

Completed translations are stored in SQLite so reopening a video costs no
provider call and no wait.

- `SUBTITLE_DB_PATH` — database location. Defaults to a per-user data
  directory (never inside the repository).
- `SUBTITLE_CACHE_ENABLED` — set to `false` to always call the provider.

Cache identity is `video_id` + caption fingerprint + model + `prompt_version`;
the fingerprint is computed by the backend from the cues it receives, so the
client sends nothing extra. A cache hit needs no API key.

## Jobs and recovery (Phase 2b-2)

A `videos` record *is* a job record — `videos.id` is the `job_id` — so the
UNIQUE cache identity doubles as the job's idempotency key and the same video
can never be translated twice at once.

One background thread runs one job at a time, sequentially, **committing each
translation window to SQLite as it finishes**. That single property gives both
features at once: cues can be polled while the rest is still translating, and a
crash loses at most the window in flight.

**SQLite is the queue of record.** The in-memory queue is only a doorbell and is
allowed to die with the process: on startup the worker sweeps every job left
`queued`/`processing` and re-queues it. Resume reloads the stored source cues,
re-derives the same deterministic windows, and translates only what has no
committed translation — so **completed cues are never paid for twice**.

Failure handling is bounded at every level: malformed model output gets the
Phase 1 corrective retry then split (ending `partial`); a provider transport
failure is retried twice (2 s, 8 s) from the last committed window before the
job ends `failed`; and a job interrupted more than
`SUBTITLE_JOB_MAX_RESUME_ATTEMPTS` times is failed rather than retried forever.
In every case, cues already translated remain readable.

- `SUBTITLE_JOB_WORKER_ENABLED` — run the worker in this process (default true).
- `SUBTITLE_JOB_MAX_RESUME_ATTEMPTS` — restart resumes before giving up (3).
- `SUBTITLE_JOB_PROVIDER_RETRIES` — provider retries per job (2).

## Test

```sh
python -m pytest        # 153 tests, all offline
```

Covers the loaders (JSON3/VTT/SRT/Phase-0/API payload), cleaning,
rolling-duplicate removal, windowing, JSON-coverage validation
(missing/duplicate/unexpected/empty/non-JSON), SRT generation, and the full
pipeline with the mock provider — including corrective-retry recovery and
bounded split-on-failure. The Phase 2a/2b-1 suites add the service layer, both
endpoints, persistence, fingerprinting, cache invalidation, restart safety, and
usage aggregation: index preservation, partial results, payload/cue-count caps, CORS
allowlist behaviour, and the guarantee that credentials and provider error
details never reach a response. The Phase 2b-2 suites add the schema migration,
job lifecycle, window-by-window commits, resume without re-translation, provider
retry, the restart-recovery sweep and its attempt limit, both job endpoints, and
duplicate-free polling. No API key and no network are required.

The engine keeps **zero required third-party dependencies**: without the `[api]`
extra, 112 tests pass and 4 skip.

## Provider / model choice

Two real providers exist behind the same `TranslationProvider` contract:
**Anthropic** (default model `claude-sonnet-5`) and **OpenRouter** (model set
via `--model` or `OPENROUTER_MODEL`). `OpenRouterProvider` with
`google/gemini-3.1-flash-lite` passed the Phase 1 quality exit gate on a real
YouTube JSON3 subtitle capture (482 cues, 478 speech): 478/478 translated, 0
failed, 0 retries, 0 splits, 0 validation errors, ~$0.0019 total cost. The
owner reviewed the generated Persian SRT on the actual video and accepted the
quality. The `providers.py` abstraction makes swapping the model or adding
another provider a small, isolated change. **Phase 1 is completed:** selected
model is `google/gemini-3.1-flash-lite` via OpenRouter (roadmap P1-06 / P1-11)
— see `../docs/PHASE1_TRANSLATION_ENGINE_NOTES.md`.

The API service defaults to the Phase 1 selection
(`SUBTITLE_API_PROVIDER=openrouter`, `SUBTITLE_API_MODEL=google/gemini-3.1-flash-lite`).
The CLI keeps its own separate defaults, unchanged.

## Not in this phase (Phase 3 and later)

No extension UI integration: the subtitle button, TextTrack/VTTCue rendering,
the RTL overlay, and SPA/ad/fullscreen lifecycle handling are Phase 3 and
Phase 4. Job cancellation (`DELETE /jobs/{id}`), record retention/pruning, and
`GET /jobs/{id}/srt` as a separate endpoint are deliberately out of scope —
`GET /jobs/{id}?include_srt=true` already covers export.
