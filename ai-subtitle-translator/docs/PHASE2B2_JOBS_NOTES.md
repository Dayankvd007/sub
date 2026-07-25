# Phase 2b-2 — Jobs and Progressive Delivery: implementation record

**Status:** Completed 2026-07-25. Exit gate P2-08 passed; Phase 3 may begin.

**Goal:** support long-running translations with a job lifecycle, progressive
delivery, polling, and restart recovery — without leaving personal-MVP scope.

## What was built

A job layer *beside* the existing synchronous path, not underneath it.

```text
POST /jobs ──► JobService ──► SQLite (source cues persisted)   ──► job_id returned
                   │
                   ▼ doorbell queue.Queue
              JobWorker (one thread)
                   ▼
              JobRunner  clean → dedup → build_windows
                   ▼  per window: translate → validate → COMMIT → next
              Repository / SQLite
                   ▲
GET /jobs/{id} ────┘   status + progress + cues after a cursor
```

`POST /translate` still runs a whole video synchronously and is unchanged apart
from one guard. Both paths share one database and one cache identity, so work
done by either is reused by the other.

## The decisions that shaped it

**A `videos` record is the job record.** `videos.id` is the `job_id`, so the
UNIQUE cache identity from Phase 2b-1 doubles as the job's idempotency key. A
repeated create cannot start a second translation — the database enforces it,
not application logic. No jobs table (architecture §7), no windows table
(windows are a pure function of stored cues and config, so a resume recomputes
them).

**Commit every window.** This single property delivers both features at once:
results become pollable while translation continues, *and* a crash loses at most
the window in flight. Everything else follows from it.

**SQLite is the queue of record.** The in-memory `queue.Queue` is only a
doorbell and is allowed to die with the process — on startup the worker sweeps
every job left `queued`/`processing` and re-queues it. This is what makes a
broker unnecessary rather than merely undesirable.

**One thread, one job at a time.** The provider uses blocking stdlib `urllib`
and `sqlite3` is blocking, so an asyncio worker would have meant rewriting
Phase-1-validated provider code onto an async HTTP client and adding
`aiosqlite`. Concurrency of one also *bounds* provider cost and rate-limit
exposure. `db.py` had already been written for exactly this (a short-lived
connection per operation, no shared state).

**`partial` is terminal.** The architecture draft drew it as a mid-run state,
but Phase 2b-1 already stored it to mean "finished, with failed cues". Two
meanings for one value would have been a bug source, so mid-run progress is
carried by `processing` plus counters. There is also no cue-level `processing`
value: with window-granularity commits a cue row is only ever pending or
terminal, which designs away the "return transient processing cues to pending"
recovery rule entirely.

**Progressive delivery returns any validated range**, via an `after_cue_index`
cursor. Windows run in video order so cues normally *are* a contiguous prefix,
but a contiguous-only policy would stall forever on one permanently failed
window while adding no safety. Two guarantees make the looser policy safe: the
client dedups by `cue_index` (already required by P3-04), and it makes one final
cursor-less read once `done` is true.

## The one change to the Phase 1 engine

`translate_cues` gained an optional keyword-only `on_window_done` callback.
It is inert by default — the CLI and `POST /translate` pass nothing — so every
Phase 1 test passes unmodified and the engine's validation evidence still holds.

The alternatives considered and rejected: duplicating the clean/dedup/window
orchestration inside the job layer (two copies of the logic to keep in sync), or
refactoring `translate_cues` into a generator (a larger change to validated code
for the same result).

One consequence worth recording: because the runner cannot tell the pipeline to
*skip* windows, a resume passes only the not-yet-completed cues, so windows are
recomputed over the remainder rather than being identical to the original run.
This is strictly better than the alternative — a partially completed window is
never re-translated, so a resume costs **zero** duplicate provider calls. The
trade-off is that on a resume, window boundaries differ from the first run, so
context neighbours differ slightly.

## Failure and recovery model

| Failure | Handling |
| --- | --- |
| Invalid payload | 400 at the boundary; **no job record is created** |
| Malformed model output | Phase 1 corrective retry → split → `failed_indices`; job ends `partial` |
| Provider transport failure | 2 retries (2 s, 8 s), each resuming from the last committed window; then `failed` with translated cues kept |
| Process restart mid-run | Startup sweep re-queues; committed windows survive; resume translates only what is owed |
| Repeated interruption | `attempt_count` reaches `SUBTITLE_JOB_MAX_RESUME_ATTEMPTS` → `failed` with `resume_limit_exceeded`, so a crash loop cannot burn API budget |
| Unexpected exception | `failed` with `internal_error`; the worker thread survives |

Duplicate provider calls are prevented at five points: the cache hit
short-circuits before the provider is constructed; an active job returns its
existing `job_id` instead of enqueuing again; the worker re-checks status at
pickup so a duplicate doorbell ring is dropped; worker concurrency is one; and a
resume skips every cue with a committed translation.

**Accepted, bounded loss:** the window in flight when a process is killed. Its
provider call was paid for and its result is not persisted (≈70 cues, well under
a cent). Preventing even that would require pre-recording requests and
reconciling — real complexity for a negligible saving.

## Database changes

Two tables still. Four nullable/defaulted columns added to `videos`:
`speech_cues` (progress denominator — `total_cues` includes non-speech cues and
would never reach 100 %), `error_code`, `error_message` (sanitized), and
`attempt_count`. Migration is a forward-only `PRAGMA user_version` check plus
idempotent `ALTER TABLE ... ADD COLUMN` — no framework, no version table. No
backfill: pre-existing records are all terminal, so NULL job columns are correct
and the recovery sweep never selects them.

## Evidence

**Tests:** 153 pass with the `[api]` extra (96 pre-existing + 57 new). Without
it, 112 pass and 4 skip — the engine keeps zero required third-party
dependencies. New coverage: schema migration onto a real 2b-1 database; job
creation/reuse/resume/cache-hit; window-by-window commits observed mid-run;
progress measured against speech cues; resume asserted by *which indexes the
provider is asked for*; provider retry and terminal failure; the recovery sweep
and its attempt limit; shutdown between windows; both endpoints; duplicate-free
polling interleaved with the worker; and the 409 guard.

**Real uvicorn process, real worker thread** (mock provider, so offline and
free — 22 checks):

- `POST /jobs` for 400 cues returned a `job_id` in **0.04 s** with status
  `queued` — asynchronous, not synchronous.
- Cursor-based polling delivered cues **while the job was still `processing`**
  and finished with all 400 present **exactly once, zero duplicates**.
- Repeat `POST /jobs` → `cache_hit: true`, 200 not 201, same `job_id`, all 400
  cues inline, no provider call. `POST /translate` for the same payload →
  `cache_hit: true`, `provider_calls: 0`.
- 404 `job_not_found`; 400 `invalid_cues` with **no job record created**.
- **Restart recovery:** a 5000-cue job was `SIGKILL`ed mid-translation with 1700
  cues committed (no graceful shutdown at all). After restart, 3850 committed
  cues had survived, the sweep resumed it, and it completed with all 5000 cues
  present exactly once — with no committed cue re-translated.

## Deliberately not built

Cancellation (`DELETE /jobs/{id}`), record retention/pruning, a separate
`GET /jobs/{id}/srt` endpoint (`?include_srt=true` covers export), parallel
window translation, and anything in Phase 3. Scope stayed a personal, local,
single-user tool: no broker, no external queue, no new dependency of any kind.

## Open for Phase 3

- Real-world polling interval: 2 s is assumed, not measured against a real
  video's window cadence.
- Whether the extension should render progressively from the first window or
  wait for `done` on the first integration (roadmap P3-04 allows either;
  progressive is P4-01).
