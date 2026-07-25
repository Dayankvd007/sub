# Phase 2b-2 — Jobs and Progressive Delivery: implementation record

**Status:** Completed and **merged** 2026-07-25 (PR #11). Exit gate P2-08 passed.
The backend is complete. **Phase 3 — Chrome Extension is Not Started.**

**Goal:** support long-running translations with a job lifecycle, progressive
delivery, polling, and restart recovery — without leaving personal-MVP scope.

## Shipped surface, at a glance

| Item | As merged |
| --- | --- |
| `POST /jobs` | Creates, reuses, resumes, or cache-hits a job; returns `job_id` immediately. `201` when created, `200` otherwise. |
| `GET /jobs/{id}` | Reads persisted progress: status, progress counters, validated cues past an `after_cue_index` cursor, `failed_indices`, typed error. Never creates work. |
| Processing model | **One sequential background worker thread.** One job at a time, windows in video order. No broker, no external queue, no new dependency. |
| Durability | **SQLite is the queue of record.** The in-memory `queue.Queue` is only a doorbell and may be lost freely. |
| Persistence granularity | **Per window.** Each translation window is committed as it finishes — the property that yields both progressive delivery and crash safety. |
| Startup recovery | Sweeps every job left `queued`/`processing`, increments `attempt_count`, re-queues — or fails it past the limit so a crash loop cannot burn provider budget. |
| Progressive polling | Cursor `after_cue_index` over **any validated range**, plus client-side `cue_index` dedup, plus one final cursor-less reconciliation read once `done` is true. |
| Idempotent creation | The Phase 2b-1 UNIQUE cache identity is the job's idempotency key — a repeat cannot start a second translation. |
| Cache hit | A completed record returns `cache_hit: true` with every cue inline: no worker, no provider call, no polling, no API key. |
| `POST /translate` | Contract unchanged, plus `409 job_in_progress` when an active job holds the same cache identity. |
| `?include_srt=true` | Renders SRT from **all** validated cues, **regardless of any cursor** — a file containing only the tail past a cursor would be useless. |
| `GET /jobs/{id}/srt` | **Deferred.** Not built; `?include_srt=true` covers export and the path stays free for later. |
| Phase 1 engine | Changed **only** through the approved optional `on_window_done` callback in `pipeline.py`. No other engine file changed. |

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

**No other Phase 1 engine file changed.** `cleaning.py`, `dedup.py`,
`chunking.py`, `prompts.py`, `providers.py`, `validation.py`, `srt.py`,
`loaders.py`, `models.py`, `config.py`, and `fingerprint.py` are byte-identical
to the pre-Phase-2b-2 commit, verified by `git diff --stat`. `cli.py` is
unchanged too, so the CLI still exercises the engine exactly as Phase 1 did.

## Accepted recovery trade-off: windows are recomputed on resume

This is the one behavioural difference a reader should not have to discover from
the code.

**What happens.** A resume reloads the stored source cues and translates only
the cues with **no committed translation**. It does not replay the original
run's window layout — the remaining cues are re-windowed from scratch. So
**resumed window boundaries, and the context neighbours inside them, may differ
from the original run.**

**What it buys.** No completed cue is ever sent to the provider a second time —
not even one sitting inside a window that was only partially finished. A resume
therefore costs **zero** duplicate provider calls.

**What it costs.** A cue translated after a resume may have seen slightly
different surrounding context than it would have on an uninterrupted run.
Context influences wording only: every cue is still translated exactly once, the
index contract is preserved, timing is untouched, and validation is identical.

**Why this is the right trade here.** The alternative — preserving the original
boundaries — means re-translating any partially completed window in full, i.e.
spending real money on every resume to make a rare path byte-reproducible.
Interruption is the exception, and the context window is only a couple of cues
either side.

It also falls out of the approved engine constraint: with no way to tell
`translate_cues` to *skip* windows, the runner passes it the remaining cues
instead. Both the cost argument and the engine constraint point the same way,
which is why this was not treated as a compromise.

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
