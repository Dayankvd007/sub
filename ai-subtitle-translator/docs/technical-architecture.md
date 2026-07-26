# AI Subtitle Translator — Technical Architecture v0.1

Practical technical blueprint for the personal English-to-Persian YouTube subtitle MVP

**Status:** Draft for technical validation. No implementation code is included.

**Source of truth:** AI Subtitle Translator — PRD v0.1.

**Purpose:** Translate approved product requirements into a practical implementation approach for a developer or AI coding assistant.

**Scope boundary:** One user, one computer, desktop Chrome, a local backend, and one selected online translation model. This is not a SaaS architecture.

### Decision-status convention

- **Decision —** a current MVP implementation choice unless a named experiment disproves it.
- **Assumption —** a belief that must be validated before dependent work expands.
- **Experiment —** a bounded test with observable evidence and an exit criterion.
- **Open question —** an unresolved choice that must not be silently treated as decided.

Precedence rule: the PRD governs product scope and acceptance. This architecture document governs the proposed implementation approach. When the two appear to conflict, stop and resolve the conflict before coding.

## 1. Architecture Overview

The MVP uses a browser-plus-local-service architecture. The Chrome extension operates inside the user’s authenticated YouTube browser session, where caption data and player state are available. It sends sanitized, ordered English subtitle cues to a FastAPI service bound to localhost. The backend owns deterministic cleaning, windowing, model calls, validation, persistence, and recovery. The extension then receives validated Persian cues and renders them in sync with the YouTube player.

```text
YouTube Browser Session
↓
Chrome Extension
↓
Local FastAPI Backend
↓
Translation Pipeline
↓
AI Translation API
↓
SQLite Cache
↓
Persian Subtitle Rendering
```

### Implemented flow as of Phase 2b-2

The backend half of the diagram above now exists in full: persistence, cache,
jobs, progressive delivery, and recovery. There are two entry points sharing one
database and one cache identity.

**Synchronous path — `POST /translate`** (Phase 2a/2b-1, unchanged):

```text
Chrome Extension
↓  POST /translate  (normalized cues, localhost, CORS allowlist)
API routes            (subtitle_translator/api/routes.py)
↓
TranslationService    (subtitle_translator/service.py — no web framework)
↓
Cache lookup          (fingerprint.py + repository.py → SQLite)
│                       hit  → stored Persian cues returned, no provider call
↓ miss                  active job for this identity → 409 job_in_progress
pipeline.translate_cues   (Phase 1 engine, unchanged)
↓
Provider abstraction      (TranslationProvider)
↓
OpenRouter API
↓
persist validated cues    (repository.py → SQLite)
```

**Job path — `POST /jobs` + `GET /jobs/{id}`** (Phase 2b-2):

```text
Chrome Extension
↓  POST /jobs                              returns job_id immediately
API routes → JobService    (jobs.py — no web framework)
↓
create / reuse / resume / cache-hit        (repository.py → SQLite)
↓  source cues persisted BEFORE any model work
doorbell queue.Queue  ──►  JobWorker       (one background thread, one job at a time)
                              ↓
                           JobRunner       clean → dedup → build_windows
                              ↓  per window: prompt → provider → validate
                              ↓            → COMMIT to SQLite → next window
                           Repository
                              ↑
Chrome Extension  ──────────  GET /jobs/{id}?after_cue_index=N   (~2 s polling)
```

The per-window commit is the whole design: it is simultaneously what makes
results available progressively and what makes a restart lose at most the
window in flight. SQLite is the durable queue; the in-memory queue is only a
wake-up signal, and startup recovery re-queues anything left unfinished.

### Main components

- **YouTube browser session —** provides the current video, caption-track context, playback clock, and player lifecycle.
- **Chrome extension —** detects the active watch page, captures captions, controls the user workflow, communicates with localhost, and renders Persian text.
- **Local FastAPI backend —** creates or reuses translation jobs and coordinates all processing outside the page.
- **Translation pipeline —** normalizes source cues, builds context-aware windows, calls the selected provider, validates results, and retries bounded failures.
- **AI translation API —** produces natural Persian under a strict structured-output contract.
- **SQLite cache —** stores job identity, status, source cues, validated Persian cues, and timestamps for reuse and recovery.
- **TextTrack plus RTL overlay —** uses the media clock for cue activation while presenting readable Persian inside the player.

### Why this architecture

- Caption extraction stays in the browser because the browser already has the relevant YouTube page state, selected caption track, and authenticated session context.
- Translation logic stays in Python because text normalization, model evaluation, validation, persistence, and SRT generation are easier to test independently from the browser.
- API credentials remain outside the extension and page environment.
- SQLite and polling are sufficient for one local user and preserve state without requiring cloud services, brokers, or streaming infrastructure.
- The browser and backend are separated by a small HTTP contract, allowing caption extraction, translation, and rendering to be tested independently.

### Intentionally excluded

- Cloud deployment, public APIs, accounts, authentication, billing, or multi-user data isolation.
- Speech-to-text, audio downloading, Whisper, or support for videos without existing English captions.
- A public Chrome Web Store release or support for browsers other than desktop Chrome.
- React, WebSockets, Server-Sent Events, Redis, Celery, message brokers, distributed workers, or enterprise observability.
- Multiple translation providers exposed in the extension UI.
- A local translation model, mobile application, collaboration, shared subtitle libraries, or analytics.

**Decision:** Use a Manifest V3 TypeScript extension, a local Python/FastAPI/SQLite backend, one configured online translation provider, simple polling, and TextTrack-driven synchronization.

**Assumption:** This split remains convenient for the project owner’s daily workflow and does not introduce unacceptable setup friction.

## 2. System Components

### Chrome Extension

**Technology:** TypeScript, Chrome Manifest V3, Vite, and plain HTML/TypeScript for the initial UI.

The extension is the only component that touches the YouTube page. It should stay thin: page-specific extraction and lifecycle logic belong here, while translation, validation, caching, and provider access belong in the backend.

#### Responsibilities

- Detect YouTube watch pages and determine the current video ID.
- Detect video changes caused by YouTube single-page application navigation.
- Discover whether a usable English caption track exists.
- Capture structured caption cues from the browser session.
- Check backend health, create or reuse a job, and poll for validated cues.
- Create TextTrack and VTTCue objects and display the active Persian text in a custom RTL overlay.
- Manage subtitle status, font size, retry messaging, and cleanup.
- Hide or suspend the overlay during ads and isolate state between videos.

#### Conceptual extension structure

The following is a responsibility map, not a final code scaffold:

- **manifest.json —** declares Manifest V3 permissions, YouTube matches, extension assets, and the service worker.
- **Content scripts —** run in Chrome’s isolated world, own DOM integration, validate messages from the page, and manage the overlay.
- **Background service worker —** handles extension lifecycle tasks, configuration, and message routing that should not depend on a page DOM.
- **Injected main-world script —** runs in the page’s JavaScript world for the caption-extraction experiment and passes only sanitized caption information across the boundary.
- **UI components —** provide the Persian subtitle control, state messages, and the basic font-size setting without a framework.
- **API client —** owns localhost health checks, job creation, polling, response validation, and cancellation on navigation.

The content script should treat every main-world message and every backend response as untrusted input: check the message source, a project-specific event name, video identity, field types, cue ordering, and payload size before using the data.

#### As implemented in Phase 3

| Module (`extension/src/`) | Responsibility |
| --- | --- |
| `content.ts` | Phase 0 watch-page/video-ID detection (unchanged) **plus** the single place a `VideoSession` is created or disposed. |
| `session.ts` | One `VideoSession` per video, owning the control, track, overlay, job controller, and every timer. |
| `subtitleControl.ts` / `controlState.ts` | The in-player button and its eight states; presentation logic is pure and unit-tested. |
| `backendClient.ts` / `apiTypes.ts` | Typed client plus runtime validation of every backend response. |
| `jobController.ts` | Job creation, cursor polling, dedup, reconciliation, backoff. |
| `subtitleTrack.ts` | Hidden `<track>`/VTTCue timing; `cuechange` -> active text. |
| `overlay.ts` | The right-to-left overlay; `textContent` only. |
| `messages.ts` | The content-script <-> service-worker contract. |
| `background.ts` | Stateless broker: inject the Phase 0 extractor for `sender.tab.id`, or forward one HTTP request. |

**Backend calls are proxied through the service worker.** An MV3 content
script's `fetch` is bound by the *page's* CORS context, so calling the backend
from a YouTube page would arrive with `Origin: https://www.youtube.com` and
force the backend's allowlist to include a public site. The worker holds the
extension origin plus `host_permissions`, and stays stateless because MV3
terminates idle workers — a polling loop there would silently stop.

The untrusted-input rule above is honoured in both directions: `apiTypes.ts`
validates every backend response field-by-field, dropping any cue that could not
become a safe VTTCue rather than repairing it.

### Other runtime components

- **FastAPI application —** exposes the small local API and coordinates job lifecycle. *(Phase 2a: implemented as `api/app.py` + `api/routes.py`; job lifecycle is Phase 2b-2.)*
- **Translation service layer —** one framework-free class between the API and the engine, so the validated pipeline stays reusable from the CLI and testable without FastAPI. *(Phase 2a: `service.py`.)*
- **Translation services —** implement cleaning, context windows, prompt construction, provider calls, and response repair.
- **SQLite repository —** provides transactional persistence and cache lookup. *(Phase 2b-1: `db.py` owns connections and schema; `repository.py` owns cache lookup and writes; `fingerprint.py` derives cache identity.)*
- **AI provider adapter —** isolates provider-specific request and response handling behind one internal contract.
- **Rendering controller —** maps validated translated cues to the current YouTube video and removes them when the lifecycle changes.

## 3. Caption Extraction Architecture

Caption extraction is the highest-risk dependency because YouTube is a changing third-party application and does not expose a stable extension-specific caption API. The architecture therefore treats extraction as Phase 0, not as a detail to solve after the backend is built.

### Why extraction happens inside the browser

- The active browser session already knows the video, available caption tracks, language choice, and playback context.
- Browser-side access avoids sending cookies, session headers, or browsing data to the local backend.
- The extension can observe the same page events and timed-text activity that occur during normal viewing.
- Failures can be tied to the current video and shown directly without creating an invalid backend job.

### Why the backend does not scrape YouTube

Direct backend retrieval would duplicate YouTube client behavior, may require fragile request parameters or session state, and would create a second failure surface unrelated to translation. It also increases the chance of accidentally handling cookies or authentication data. The backend should accept already structured cues and remain unaware of how YouTube delivered them.

**Decision:** The backend will not use YouTube as its primary caption source. It receives normalized cue payloads from the extension.

### Primary main-world experiment

A minimal main-world script should be injected early enough on a watch page to observe the caption-track discovery and timed-text delivery path used by the current YouTube player. The experiment may inspect page-exposed player data, observe or intercept timed-text fetch/XHR activity, or use another current browser-visible mechanism. The main-world script should emit only the selected track metadata and caption payload through a narrowly named window message or custom event. The isolated content script validates and normalizes the payload before it can leave the browser.

The experiment must record the actual mechanism that worked in the current desktop Chrome environment. The document intentionally does not declare a permanent extraction method before this observation.

### Candidate strategies, in test order

1. **Track metadata plus page-context retrieval.** Find the active video’s caption-track metadata from player-accessible data, then request the selected English timed-text resource from the page context.
2. **Timed-text response interception.** Observe or wrap the relevant page fetch/XHR path and copy a structured caption response when YouTube requests it.
3. **Player or transcript data fallback.** Use a verified player response or transcript-panel data source only if it produces complete timing and text; DOM scraping alone is considered fragile.
4. **Fixture import for development.** Allow JSON3, VTT, or SRT files to drive translation tests when YouTube extraction is not under test. This is a development path, not the in-player product workflow.
5. **External extractor as a diagnostic last resort.** A tool such as yt-dlp may help compare expected captions during investigation, but it is not adopted as the MVP’s primary runtime path without an explicit decision.

### Required Phase 0 experiment

**Assumption:** A browser-side extractor can produce complete, ordered, reusable caption cues in the owner’s normal desktop environment.

**Experiment:** Capture structured English caption data from at least one manually captioned video and one auto-captioned video.

- Test initial page load, enabling captions, page reload, seeking, and YouTube SPA navigation to another video.
- Record the chosen English track, track type, cue count, first and last timestamps, and any rolling-caption duplication.
- Compare the captured result with the visible transcript or another trusted fixture to detect missing ranges.
- Repeat the capture to determine whether the mechanism is stable enough for personal use.
- Store sanitized fixtures for later parser, cleaning, and regression tests; do not store cookies or request headers.

**Exit criterion:** Both caption types produce ordered cues with usable timing and no unexplained missing ranges. If this does not pass, stop before building the full extension or backend.

**Phase 0 result (owner-validated 2026-07-22):** PASS. The primary
main-world experiment (candidate strategy #1 below) was implemented in
`extension/`, unit-tested against synthetic json3 fixtures, and confirmed
working by the project owner's real two-video capture in their own Chrome.
No fallback strategy was required. The validated method is now the
**confirmed** extraction approach for Phase 3 (see
`docs/PHASE0_EXPERIMENT_NOTES.md` for details and the still-pending raw
fixture commit). Remaining assumption: the undocumented
`ytInitialPlayerResponse` / `getPlayerResponse()` shapes and the
`&fmt=json3` endpoint can change without notice, so this logic stays behind
the narrow extractor interface per NFR-010.

### Expected cue output

| **Field** | **Type** | **Meaning** |
| --- | --- | --- |
| cue_index | Integer | Stable zero- or one-based position assigned by the extractor and preserved through translation. |
| start_ms | Integer | Cue start time in milliseconds relative to the main video. |
| end_ms | Integer | Cue end time in milliseconds; it must be greater than start_ms. |
| english_text | String | Decoded English cue text before backend cleaning; non-empty after normalization. |

Normalization at the browser boundary should be conservative: decode the source format, derive a valid end time when the source provides duration, preserve source ordering, and reject obviously malformed timing. Rolling-duplicate removal and linguistic cleanup belong to the backend so they can be tested with fixtures.

### Fallback policy

Fallbacks should be narrow and observable. The extractor exposes one conceptual interface to the rest of the extension, but Phase 0 may test multiple implementations behind it. A fallback must produce the same cue contract and must never silently downgrade to incomplete transcript text. If no verified method works, the correct outcome is a visible unsupported state and a recorded blocker, not invented caption data.

## 4. Backend Architecture

The backend is a local FastAPI process bound to 127.0.0.1. It accepts structured caption cues, creates or reuses a translation job, processes windows in video order, persists validated results, and returns progress to the extension. It must remain independently testable with caption fixtures and without Chrome.

### Technology and process model

- Python for text processing, provider SDK integration, validation, and SRT generation.
- FastAPI for a small typed local HTTP interface and health checking.
- SQLite for durable local cache and interruption recovery.
- A simple in-process job coordinator for one user; no external queue or worker service.
- Environment variables or a local ignored configuration file for provider credentials and model selection.

**Decision:** Keep one local application process and one SQLite database for the MVP.

**Open question:** The exact in-process execution mechanism—synchronous task, thread, or asyncio worker—should be selected after the provider SDK’s behavior is measured. This does not justify a broker.

**Resolved in Phase 2b-2: one background thread**, processing one job at a time,
sequentially. The provider uses blocking stdlib `urllib` and `sqlite3` is
blocking too, so an asyncio worker would have required rewriting Phase-1-validated
provider code onto an async HTTP client and adding `aiosqlite` — new dependencies
for parallelism a single user does not need. Concurrency of one additionally
bounds provider cost and rate-limit exposure. No broker, as expected.

### Responsibilities

- Validate incoming video metadata, cache identity, cue indexes, timestamps, text, and request size.
- Create a new job or return a valid completed cache hit.
- Store source cues before model work begins.
- Normalize captions and remove high-confidence rolling duplicates.
- Build translation windows with adjacent context.
- Call the configured provider through an adapter.
- Validate response structure and cue coverage before any Persian text becomes available.
- Retry a failed window with a corrective instruction, then split it through a bounded path if needed.
- Persist completed cues and job progress transactionally.
- Return concise status, incremental cues, and actionable errors.
- Generate SRT from stored completed cues when that future endpoint is enabled.

### Conceptual modules

- **api/ —** route definitions, request/response schemas, origin checks, and error mapping.
- **translation/ —** caption normalization, duplicate cleanup, window construction, prompt assembly, provider abstraction, and orchestration.
- **validation/ —** schema checks, expected-index comparison, text checks, timing checks, and retry classification.
- **database/ —** SQLite connection management, migrations, repositories, transactions, and cache queries.
- **models/ —** internal cue, video/job, window, provider-result, and API contract types.
- **services/ —** job coordination, progress calculation, resume behavior, SRT generation, and configuration.

These names describe boundaries, not a required final package layout. Implement the smallest modules that keep YouTube-specific, provider-specific, persistence, and validation logic separable.

### Job lifecycle

```text
queued → processing → partial → completed
                         ↘ failed
```

- **queued —** the request is validated and persisted but translation has not begun.
- **processing —** a window is active and no translated range may yet be available.
- **partial —** one or more validated cues are stored and safe to return.
- **completed —** every expected cue index has a validated stored result.
- **failed —** processing stopped with a bounded, recorded error; existing completed cues remain available.

A process restart should recover persisted job state. Completed cues must not be translated again. Any cue or window left in a transient processing state may be returned to pending after startup, using a simple deterministic recovery rule.

#### As implemented in Phase 2b-2

The draft above drew `partial` as a mid-run state. Implementation resolved it
differently, because Phase 2b-1 had already given `partial` a *terminal* meaning
in storage (finished, with failed cues). Two meanings for one value would have
been a bug source, so mid-run progress is carried by `processing` plus counters:

```text
(new) ──► queued ──► processing ──┬──► completed
            ▲                     ├──► partial
            │                     └──► failed
            └── restart sweep, or re-POST /jobs to resume ──┘
```

- **queued —** validated and persisted, including every source cue; not started.
- **processing —** the worker owns it. `completed_cues` may already be above
  zero — this is the progressive-delivery state, and polling returns whatever
  is committed.
- **completed —** every speech cue has a validated stored result. The only
  status served as a cache hit.
- **partial —** *terminal.* The run finished but some indexes exhausted the
  bounded retry/split budget. Completed cues stay readable; re-posting resumes.
- **failed —** *terminal.* Stopped on a provider, transport, or internal error.
  Completed cues stay readable; re-posting resumes.

Cue-level status is `pending`, `completed`, `failed`, or `skipped` (non-speech
or rolling-duplicate — deliberately never sent to a model, as distinct from
"not attempted yet"). There is deliberately **no** cue-level `processing` value:
because commits happen at window granularity, a cue row is only ever pending or
terminal. That removes the need for the "return transient processing cues to
pending" recovery rule above — the state it cleans up is designed out.

Recovery: on startup the worker sweeps every job in `queued`/`processing`,
increments `attempt_count`, and re-queues it — or fails it once `attempt_count`
reaches its limit, so a job that crashes the process cannot retry forever at the
provider's expense.

#### Accepted recovery trade-off: windows are recomputed on resume

A resume reloads the stored source cues and translates **only the cues with no
committed translation**. It does not replay the original run's window layout:
the remaining cues are re-windowed from scratch, so **resumed window boundaries
and the context neighbours inside them may differ from the first run**.

This is a deliberate trade, and it favours resume:

- **What it buys.** No completed cue is ever sent to the provider again — not
  even one sitting inside a window that was only partially finished. A resume
  therefore costs **zero** duplicate provider calls. Preserving the original
  boundaries would have meant re-translating any partially completed window in
  full.
- **What it costs.** A cue translated after a resume may have seen slightly
  different surrounding context than it would have on an uninterrupted run.
  Context affects wording, not the index contract: every cue is still
  translated exactly once, timing is untouched, and validation is unchanged.
- **Why it is acceptable here.** Interruption is the rare path, the context
  window is only a couple of cues either side, and the alternative spends real
  money to make a rare path byte-reproducible.

This also follows from the decision to touch the Phase 1 engine only through an
optional callback: with no way to tell `translate_cues` to *skip* windows, the
runner passes it the remaining cues instead. Both constraints point the same way.

**Completed cues are never translated again.** The bounded, accepted loss is the
window in flight at the moment of a crash: its provider call was paid for and
its result is not persisted (≈70 cues, well under a cent).

### Local security boundary

- Bind to 127.0.0.1 by default, never 0.0.0.0.
- Allow only the expected extension origin and explicitly configured development origins.
- Keep API keys in backend configuration; never expose them in extension code, page messages, responses, or logs.
- Validate payload size and cue count before persistence or model calls.
- Do not accept cookies, YouTube request headers, or browser tokens in the API contract.

## 5. Translation Pipeline Architecture

The pipeline converts ordered English cues into validated Persian cues without losing the mapping to original timing. It is deterministic around the non-deterministic model call: every input, expected index set, prompt version, validation failure, and accepted result has a defined place in the workflow.

### Complete flow

1. **Normalize captions.** Normalize Unicode, whitespace, line breaks, HTML entities, and safe formatting noise while preserving cue order and timestamps.
2. **Remove rolling duplicates.** Compare adjacent auto-caption cues and remove only high-confidence repeated prefixes or overlaps. Preserve uncertain text rather than deleting possible speech.
3. **Group context windows.** Build target windows using an initial experimental range of roughly 40–70 cues or two to three minutes, constrained by text or token size.
4. **Add context.** Attach limited preceding and following cues, video title, optional glossary terms, and style instructions. Context-only cues must not be returned as target output.
5. **Send a structured prompt.** Provide the exact expected cue indexes and require natural Persian that preserves meaning while remaining readable within subtitle timing.
6. **Receive structured output.** Accept only a machine-readable collection containing one Persian result for every requested target cue index.
7. **Validate cue indexes.** Require exact set equality: no missing, duplicate, unknown, or non-integer cue indexes; reject empty or structurally invalid Persian.
8. **Retry failures.** Use a bounded corrective retry for the same window; if it still fails, split the target window and retry smaller parts. Never loop without a cap.
9. **Save results.** Commit only validated Persian cues and update progress in SQLite before returning them to the extension.

### Why cue-by-cue translation is rejected

Individual cues often contain fragments, pronouns without referents, weak punctuation, and boundaries that do not match Persian syntax. Translating each cue separately produces literal wording, inconsistent terminology, repeated phrases, and broken sentence flow. Context windows let the model understand the sentence and topic while the index contract preserves timing.

The target output still maps to original cue indexes. The model may use adjacent context to choose wording, but it may not merge away, invent, or silently omit expected indexes.

### Window construction

- Preserve chronological order and select the beginning of the video first.
- Use both cue count and approximate media duration as starting heuristics; enforce a provider-safe text/token ceiling.
- Include a small context overlap around the target range, clearly marked as context-only.
- Avoid splitting at an obvious sentence boundary when a nearby boundary is available, but do not make sentence reconstruction a blocking MVP dependency.
- Store the target index range and prompt version so failures can be reproduced.

**Assumption:** The initial 40–70 cue or two-to-three-minute range provides enough context without harming latency or structured-output reliability.

**Experiment:** Compare multiple window sizes on the same manual and auto-caption samples, measuring Persian quality, malformed-output rate, latency, and cost.

### Validation and repair

- Parse the response against a strict schema before reading translation content.
- Compare returned cue indexes with the expected target set.
- Reject duplicate, missing, extra, reordered-with-ambiguity, empty, or non-string results.
- Flag implausibly long Persian cues for targeted condensation rather than silently truncating them.
- Record a concise local failure category and provider request identifier when safely available.
- On repair, send the original expected index set and the exact structural defect; do not ask the model to reinterpret the entire job.
- After the bounded repair path is exhausted, mark the affected window failed and preserve all other completed cues.

### Progressive availability

The safest initial delivery policy is to expose only validated cues. The backend processes from the start of the video and can return completed ranges as soon as they are committed. Whether the first end-to-end extension waits for full completion or appends partial ranges is a milestone choice; the storage and API contract should support both without requiring WebSockets.

**Open question:** Should partial cues be returned as any validated range or only as the longest contiguous range from the start? Phase 2 should choose the simplest policy that cannot display gaps as if the job were complete.

**Resolved in Phase 2b-2: any validated range.** `GET /jobs/{id}` accepts an
`after_cue_index` cursor and returns any committed cue past it. Windows are
processed in video order, so cues normally *are* a contiguous prefix; a
contiguous-only policy would have added no safety while stalling delivery
forever on one permanently-failed window. The two guarantees that make the
looser policy safe:

1. The client inserts each `cue_index` at most once (already required by P3-04),
   so repeated or overlapping polls cannot duplicate a rendered cue.
2. When `done` becomes true the client makes **one final read with no cursor**
   and reconciles — which covers any cue that completed out of order, at the
   cost of a single extra request per job.

A gap is never presented as completion: `status` and `failed_indices` are
explicit, and only `completed` is served as a cache hit.

## 6. AI Translation Layer

### Provider abstraction

The backend should expose one small internal translation-provider contract: accept a prompt, structured target cues, model configuration, and output schema; return raw provider metadata plus parsed structured content or a categorized error. Provider-specific SDK objects, authentication, retry headers, and response parsing must not leak into the windowing or API layers.

- Select the provider and model in backend configuration, not in the extension UI.
- Store the actual model identifier used on every video/job record.
- Keep one active provider for normal MVP use, while allowing a second adapter to be tested without rewriting the pipeline.
- Do not silently switch models after an error because that changes quality, cost, and cache identity.

### Model-selection experiment

No model is permanently selected in v0.1. Phase 1 should compare current affordable candidates on the same representative windows from real target videos. The PRD suggests testing a suitable Gemini Flash model, a suitable small OpenAI model, and—if useful—a suitable Claude model as a higher-quality reference. Exact model versions must be chosen at experiment time because availability and pricing change.

| **Evaluation dimension** | **What to observe** |
| --- | --- |
| Persian quality | Naturalness, meaning fidelity, tone, idiomatic phrasing, and comfort over a complete educational video. |
| Terminology | Consistency for AI, business, marketing, branding, GEO, and other domain terms. |
| Segmentation | Whether text fits original timing without awkward fragments or meaning loss. |
| Reliability | Valid structured output rate, missing/duplicate index rate, and repair success. |
| Latency | Time to first validated window and total processing time for a representative video. |
| Cost | Estimated cost per hour of source captions and the effect of retries/context overhead. |

**Decision gate:** Choose a model only after the project owner reviews side-by-side or blind outputs and accepts the Persian for long-form viewing.

**Result (Phase 1 exit gate passed, 2026-07-25):** the provider abstraction has a second real implementation, `OpenRouterProvider`, alongside the existing `AnthropicProvider`; both read credentials only from the local environment. `OpenRouterProvider` with `google/gemini-3.1-flash-lite` was validated end-to-end on a real YouTube JSON3 capture from a business-related video (482 total cues, 478 speech cues, 10 windows): 478/478 translated, 0 failed, 0 retries, 0 splits, 0 validation errors, ~$0.0019 total cost. The owner reviewed the generated Persian SRT on the actual video and accepted the quality for the educational/business use case. `google/gemini-3.1-flash-lite` via OpenRouter is the selected Phase 1 model; the choice can be revisited later if evidence warrants a change. See `docs/PHASE1_TRANSLATION_ENGINE_NOTES.md` for the full record.

### Prompt versioning

- Assign every translation instruction set a stable prompt_version value.
- Version changes that can alter Persian output or structure invalidate cache reuse.
- Store the prompt version with each job and include it in model-comparison records.
- Keep prompt text centralized in the backend rather than duplicating it across routes or extension code.
- Record why a prompt version changed and which fixture cases were rerun.

### Structured output contract

Each target result contains only the original cue_index and its persian_text. Window metadata, timing, and English source remain backend-owned. The model must return exactly one object for every expected target cue index and no output for context-only cues.

- The response must parse as the provider-supported structured JSON form or an equivalently strict schema.
- cue_index must be an integer and belong to the expected target set.
- persian_text must be a non-empty string after normalization.
- Every expected index must appear exactly once.
- No prose, Markdown, explanations, timing edits, or new cue IDs may be accepted as translated output.

**Assumption:** At least one affordable candidate can reliably satisfy the structured contract while producing acceptable Persian.

**Open question:** Whether the selected provider’s native schema feature is more reliable than prompt-only JSON must be measured rather than assumed.

## 7. Database Architecture

SQLite stores the minimum state required for caching, progressive delivery, and restart recovery. For the MVP, a video record also represents one translation job for a specific cache identity. A separate enterprise-style jobs table is unnecessary unless implementation evidence shows a real ambiguity.

### Videos table

| **Field** | **Conceptual type** | **Purpose** |
| --- | --- | --- |
| id | Integer primary key | Internal job/video record identifier returned as job_id. |
| video_id | Text | YouTube video identifier. |
| title | Text | Best-effort local display title. |
| status | Text | queued, processing, partial, completed, or failed. |
| total_cues | Integer | Expected source cue count for progress and completion checks. |
| model | Text | Actual provider model identifier used. |
| prompt_version | Text | Translation prompt and output-contract version. |
| caption_fingerprint | Text | Hash representing the ordered source caption content and timing. |
| created_at | Timestamp | Record creation time. |
| updated_at | Timestamp | Last meaningful state or cue update. |

### Cues table

| **Field** | **Conceptual type** | **Purpose** |
| --- | --- | --- |
| video_record_id | Integer foreign key | Links the cue to videos.id. |
| cue_index | Integer | Original ordered cue index; unique within the video record. |
| start_ms | Integer | Original start time in milliseconds. |
| end_ms | Integer | Original end time in milliseconds. |
| english_text | Text | Canonical cleaned English text used by translation. |
| persian_text | Nullable text | Validated Persian output; null until completed. |
| translation_status | Text | pending, processing, completed, or failed. |

### Constraints and indexes

- Unique constraint on video_record_id plus cue_index.
- Cache lookup index covering video_id, caption_fingerprint, model, and prompt_version.
- Foreign-key enforcement between cues and the parent video record.
- start_ms must be non-negative and end_ms must be greater than start_ms.
- A job may be marked completed only when every expected cue is stored with completed status and non-empty Persian text.

If window-level retry metadata is needed during implementation, prefer the smallest recoverable representation. Do not add billing, account, analytics, audit-event, or distributed-worker tables.

#### Phase 2b-2 additions

Still two tables. `videos.id` is the `job_id`, and the UNIQUE cache identity is
therefore also the job's idempotency key — a repeated create cannot start a
second translation. Four nullable/defaulted columns were added to `videos`:

| Field | Purpose |
| --- | --- |
| speech_cues | Progress denominator. `total_cues` includes non-speech cues that are never translated, so it would never reach 100%. Set by the worker after cleaning and dedup; NULL while queued. |
| error_code | Typed error for `GET /jobs/{id}`. |
| error_message | Sanitized human-readable text. Never a provider body or stack trace. |
| attempt_count | Restart resumes so far. Bounds crash-loop retries. |

Deliberately **not** added: a jobs table (the videos record is the job); a
windows table (windows are a pure function of stored cues and config, so a
resume recomputes them); a denormalized completed-cue counter (a COUNT over a
≤5000-row table is free and cannot drift); per-cue sequence numbers (the cursor
policy in §5 makes them unnecessary); worker/lock/heartbeat columns (one process,
one worker).

Migration is a forward-only `PRAGMA user_version` check plus idempotent
`ALTER TABLE ... ADD COLUMN` — no framework, no version table. No backfill is
required: every pre-existing record was written by the synchronous path and is
already terminal, so NULL job columns are correct for it and the recovery sweep
never selects it.

### Caching logic

1. The extension sends video_id, ordered cues, and the caption fingerprint; the backend supplies the configured model and prompt version.
2. The backend looks for a completed record whose video_id, caption_fingerprint, model, and prompt_version all match.
3. On a complete match, POST /jobs returns the cached job and makes its cues available without a provider call.
4. On no match, create a new record and persist all source cues before translation.
5. On a partial matching record, return existing completed cues and resume only unfinished work when the recovery rules allow it.

The caption fingerprint should be computed from a canonical serialization of ordered cue indexes, timing, and normalized source text. The exact hash algorithm and whether the browser or backend is authoritative remain open, but the backend must verify or recompute enough information to prevent stale reuse.

**Decision:** Changing the caption fingerprint, model, or prompt_version creates a distinct cache identity. Old results may remain locally for inspection but are not returned as current.

**Open question:** Choose the exact canonicalization and hash method after Phase 0 reveals the stability of captured timing and text.

## 8. API Architecture

The API is local, small, and job-oriented. It does not expose provider credentials or YouTube session information. The extension can use it without a generated client; request and response schemas remain explicit and independently validated on both sides.

| **Endpoint** | **Purpose** | **Contract responsibility** |
| --- | --- | --- |
| POST /jobs | Create or reuse a translation job. | Accept video metadata, caption identity, and ordered cues; return job_id, status, cache_hit, total_cues, completed_cues, and any already available translated cues. |
| GET /jobs/{id} | Get status and translated cues. | Return current state, progress, newly completed cues after an optional cursor, and a concise recoverable or terminal error. |
| GET /health | Check backend availability. | Return service availability and minimal version information; never return configuration secrets. |
| GET /jobs/{id}/srt | Future SRT export. | When enabled and complete, generate Persian SRT from stored cues; do not maintain a separate permanent SRT record. |

### POST /jobs responsibilities

- Require video_id, optional title, caption_fingerprint, and an ordered non-empty cue collection.
- Validate unique cue indexes, monotonic order, valid timing, English source language intent, text shape, and request-size limits.
- Derive the effective model and prompt version from backend configuration; client-provided values are informational only unless explicitly allowed for experiments.
- Return an existing completed cache hit immediately when the full identity matches.
- Create a persisted queued or processing job when no valid result exists.
- Return a stable job_id that the extension can poll after navigation-safe state checks.

### GET /jobs/{id} responsibilities

- Return status, total_cues, completed_cues, and a progress value derived from persisted state.
- Support an optional cursor such as after_cue_index or last_seen_update so repeated polling need not resend every cue.
- Return only validated Persian cues.
- Differentiate recoverable waiting/retry states from terminal failure.
- Return not found when the identifier is invalid; never create work from a GET request.

### Polling contract

The extension may poll approximately every two seconds while a job is active. It stops on completion, terminal failure, tab teardown, video change, or an explicit user cancellation of display. A small backoff may be used when the backend is temporarily unavailable. WebSockets and Server-Sent Events are intentionally excluded for one local user.

**Decision:** Use polling for v0.1.

**Assumption:** Two-second polling provides acceptable progress responsiveness without meaningful local overhead.

**Implemented in Phase 2b-2.** `POST /jobs` and `GET /jobs/{id}` exist as
specified above, with these concrete resolutions:

- `job_id` is `videos.id`, exposed directly. Adequate for a localhost
  single-user tool; no opaque token is warranted.
- The create response is `201` only when a record was actually created; reuse,
  resume, and cache hit all return `200`.
- The cursor is `after_cue_index` (see §5). `next_cursor` is echoed back so the
  client never has to compute it.
- `GET /jobs/{id}?include_srt=true` renders SRT from **every** validated cue,
  ignoring the cursor — a subtitle file of only the tail would be useless. A
  separate `GET /jobs/{id}/srt` endpoint was therefore not built; the contract
  remains free for it later.
- `POST /translate` is unchanged except for one guard: it returns
  `409 job_in_progress` when an active job holds the same cache identity,
  because `Repository.save()` rewrites a record's cues wholesale and would
  otherwise delete the running job's committed progress.
- `503 jobs_unavailable` when persistence is disabled — jobs are inseparable
  from durable state, so the API says so rather than pretending.
- Cancellation (`DELETE /jobs/{id}`) and record retention are deliberately out
  of scope: a job whose viewer navigated away simply finishes and populates the
  cache.

### Error and security contract

- Use ordinary HTTP status codes for invalid requests, missing jobs, origin rejection, and unexpected server errors.
- Use a stable response error code for client behavior and a concise human-readable message for the extension UI.
- Do not return raw provider prompts, API keys, headers, cookies, or complete stack traces.
- Enforce localhost binding and an allowlist for the extension origin plus explicit development origins.
- Treat repeated equivalent POST requests as idempotent through the cache identity rather than starting duplicate translation work.

## 9. Subtitle Rendering Architecture

The preferred renderer separates synchronization from presentation. A TextTrack containing VTTCue objects follows the video element’s media clock. The native track remains hidden so YouTube or the browser does not draw its default captions. A custom overlay listens for active-cue changes and renders Persian with right-to-left styling.

### Preferred approach

1. Find the active HTMLVideoElement and create or reuse one project-owned TextTrack.
2. Set the track to a mode that allows cue activation without native subtitle display.
3. Create one VTTCue for every validated Persian cue using start_ms and end_ms converted to seconds.
4. Listen for cue activation changes and render the current Persian string into a project-owned overlay.
5. Use safe text insertion rather than interpreting subtitle text as HTML.
6. Remove the track, cues, listeners, observers, overlay, and polling state when the video identity changes.

### RTL overlay behavior

- Use right-to-left direction, centered visual alignment, Persian-capable system fonts, readable contrast, and a constrained maximum width.
- Use CSS that survives ordinary and fullscreen player layouts without covering core player controls.
- Persist only the basic font-size preference in extension storage.
- Render only validated text and clear the overlay when no cue is active.
- If two cues overlap, define a deterministic display order or combine them safely; do not let DOM order decide accidentally.

### Playback and YouTube lifecycle handling

- **Pause and resume —** TextTrack cue activation follows media currentTime; the visible cue remains stable while paused and advances when playback resumes.
- **Seeking —** activeCues should update after currentTime changes. Clear stale overlay text immediately and let the new active cue render.
- **Playback speed —** cue timing is based on media time, so no custom speed multiplier should be applied.
- **Fullscreen —** ensure the overlay belongs to, or can be moved into, the actual fullscreen/player container and remains above the video but below essential controls.
- **YouTube SPA navigation —** use a video-ID lifecycle controller to cancel polling and detach every project-owned object before initializing the new watch page.
- **Ads —** hide the Persian overlay during ad playback and restore it for main content. The exact reliable ad-state signal is an experiment because YouTube markup and events can change.
- **Progressive results —** append only newly validated VTTCue objects and prevent duplicate cue insertion on repeated polls.

**Assumption:** TextTrack activation is reliable enough across pause, seek, speed, and fullscreen events in the owner’s Chrome environment.

**Experiment:** Run playback tests on the manual-caption and auto-caption fixtures, including repeated seeking, 0.75×/1×/1.5×/2× speed, fullscreen entry/exit, and at least one ad transition when available.

**Fallback:** If cuechange behavior is unreliable, a requestAnimationFrame or timeupdate-driven lookup may be evaluated as a narrow renderer fallback. It is not the preferred design and must not be implemented preemptively.

### Phase 3 result (implemented 2026-07-26; gate pending)

The preferred approach above was implemented unchanged, with two specifics
worth recording:

- **A removable `<track>` element, not `video.addTextTrack()`.** Tracks created
  by `addTextTrack()` cannot be removed — there is no `removeTextTrack` API —
  and YouTube reuses the same `<video>` element across SPA navigations, so that
  approach leaks one orphan track per video watched. Removing the element
  removes the track with it.
- **The overlay is mounted inside `#movie_player`**, the element YouTube makes
  fullscreen, so fullscreen and theater mode need no handling and percentage
  layout makes a resize listener unnecessary. `pointer-events: none` keeps the
  full-width overlay from swallowing clicks meant for play/pause.

The `cuechange` fallback was **not** needed: a real-Chromium harness confirms
cues activating from the media clock and the visible line advancing with
playback. Overlapping cues resolve deterministically (latest start, then higher
cue index) rather than by DOM order.

Ads, font-size preferences, condensation, and resize tuning remain Phase 4.

## 10. Data Flow

```text
User clicks subtitle button
↓
Extension captures captions
↓
Backend receives cues
↓
Translation job created or reused
↓
AI translation
↓
Validation
↓
SQLite storage
↓
Extension polling
↓
Persian subtitles appear
```

### End-to-end responsibilities

1. **User action.** The subtitle control is enabled only when the extension is on a watch page and the local workflow can be attempted.
2. **Capture.** The extension selects a usable English track and produces the canonical cue contract.
3. **Job request.** The extension checks /health, computes or supplies the caption identity, and sends metadata plus ordered cues to POST /jobs.
4. **Cache decision.** The backend validates the request and either returns a matching job or persists a new one.
5. **Translation.** The job coordinator cleans cues, builds the earliest pending window, and calls the configured model.
6. **Validation.** The backend accepts only a complete valid index set, follows the bounded repair path when necessary, and records failures visibly.
7. **Persistence.** Validated cues are committed to SQLite before progress is exposed.
8. **Polling.** The extension requests job state and only cues it has not already inserted.
9. **Rendering.** The extension maps cue timing to VTTCue, cue activation updates the RTL overlay, and lifecycle cleanup protects against stale subtitles.

### Failure-path principle

Every boundary fails closed. Missing captions do not create a job; an unavailable backend does not leak credentials into the extension; invalid model output is not rendered; a failed window does not erase completed cues; and a video change invalidates client state before new captions can appear.

## 11. Folder Structure Proposal

This is a practical repository proposal, not a locked scaffold. Create folders only when their first real file is needed.

```text
ai-subtitle-translator/
├── extension/
├── backend/
├── docs/
├── tests/
└── README.md
```

### Folder purposes

- **extension/ —** Manifest V3 source, Vite configuration, content-script lifecycle, main-world extraction bridge, service worker, UI, API client, and extension-specific tests.
- **backend/ —** FastAPI application, translation pipeline, provider adapter, validation, SQLite access, configuration, SRT generation, and backend-specific tests.
- **docs/ —** Approved product and technical documents, experiment notes, prompt/model comparison results, and the technical decisions log. Do not turn this into a documentation project.
- **tests/ —** Cross-component fixtures and end-to-end checks, including sanitized manual-caption and auto-caption samples. Unit tests that belong clearly to one component may live beside that component.
- **README.md —** Minimal local setup, how to run the backend, how to load the unpacked extension, required environment variables, and the current tested workflow.

The repository should remain one project. Separate repositories, shared-package publishing, Docker orchestration, and generated SDKs are not justified for the MVP.

## 12. Development Strategy

Development follows risk order. Each milestone must produce evidence and meet its exit criterion before later phases become the main focus. Phase numbers below condense the PRD’s robust-playback and convenience work into a single Phase 4 polish milestone; SRT remains optional until the in-player workflow is stable.

### Phase 0 — Caption Extraction Experiment

**Goal:** Prove that the extension can reliably capture structured captions from the current YouTube browser environment.

**Technical tasks:** 

- Build the smallest Manifest V3 experiment that detects watch pages and the active video ID.
- Test main-world access to caption-track metadata and timed-text activity.
- Normalize captured manual and auto captions into cue_index, start_ms, end_ms, and english_text.
- Exercise reload, caption enablement, seek, and SPA navigation.
- Create sanitized fixtures and document the working mechanism, required permissions, timing, and observed fragility.

**Deliverable:** Structured caption fixtures from at least one manually captioned video and one auto-captioned video, plus a short experiment record.

**Exit criteria:** Capture is repeatable in the owner’s desktop Chrome, cue ordering and timing are usable, and no unexplained range is missing. Otherwise stop and revise the extraction approach.

### Phase 1 — Translation CLI

**Goal:** Prove Persian quality and the deterministic translation pipeline independently from Chrome and FastAPI.

**Technical tasks:** 

- Parse representative JSON3, VTT, or SRT input fixtures.
- Implement conservative normalization and rolling-duplicate cleanup.
- Build experimental context windows and the strict output contract.
- Implement a provider abstraction, prompt versioning, validation, corrective retry, and split-on-failure.
- Generate a complete Persian SRT for review.
- Compare candidate models on identical real-video windows for quality, terminology, segmentation, reliability, latency, and cost.

**Deliverable:** A CLI that converts a representative caption file into a complete, reviewable Persian SRT and a recorded model-selection result.

**Exit criteria:** Every expected cue index is present exactly once, structural failures are bounded and visible, and the project owner considers the Persian comfortable for a complete educational video.

**Phase 1 result (owner-approved 2026-07-25): PASS.** `OpenRouterProvider`
(`OPENROUTER_API_KEY`/`OPENROUTER_MODEL` from the local environment only) was
validated on a real YouTube JSON3 subtitle capture from a business-related
video: 482 total cues, 478 speech / 4 non-speech, 10 windows, 478/478
translated, 0 failed, 0 retries, 0 splits, 0 validation errors (~1,132 prompt
+ 1,080 completion tokens, ~$0.0019 total cost). The owner reviewed the
generated Persian SRT on the actual video and accepted the quality for the
educational/business use case. Selected model: `google/gemini-3.1-flash-lite`
via OpenRouter — revisitable later if evidence warrants a change. See
`docs/PHASE1_TRANSLATION_ENGINE_NOTES.md`.

### Phase 2 — Local Backend

**Goal:** Expose the validated translation engine through a recoverable local API and cache.

**Technical tasks:** 

- Create localhost-only FastAPI configuration and GET /health.
- Implement POST /jobs and GET /jobs/{id} with explicit schemas.
- Create the SQLite videos and cues tables, constraints, cache lookup, and migration path.
- Persist source cues before translation and validated cues before returning them.
- Implement in-process job coordination, progressive state, bounded failure handling, and restart recovery.
- Test cache hits, partial jobs, invalid requests, provider failures, and backend restart.

**Deliverable:** A local API that accepts fixture cues, translates them progressively, persists results, resumes safely, and reuses a valid completed cache.

**Exit criteria:** A full fixture job completes through the API; a process restart preserves completed cues; a repeated identical job produces a cache hit without a model call; invalid output never reaches the client.

Phase 2 is delivered in two stages so the HTTP contract can be proven before
storage design is committed.

#### Phase 2a — API Foundation (completed 2026-07-25)

`subtitle-api`: a localhost FastAPI service over the unchanged Phase 1 engine.

- `GET /health` — status, version, prompt version, provider, model, and whether
  credentials are configured. Never the key itself.
- `POST /translate` — accepts the extension's normalized cues, runs the full
  pipeline synchronously, and returns validated Persian cues, stats, optional
  SRT, and usage. Errors are typed `{error_code, message}`.
- `TranslationService` between the API and the engine; Pydantic contracts;
  environment-based settings; explicit CORS allowlist; `MAX_CUES` and
  `MAX_BODY_BYTES` caps; sanitized provider errors; 127.0.0.1 binding.

**Evidence:** 66 tests pass with the `[api]` extra (39 pre-existing + 27 new);
47 pass and 2 skip without it, so the engine keeps zero required dependencies.
A real uvicorn run returned a valid Persian SRT and usage stats.

**Contract note for Phase 3:** client `cue_index` values are preserved exactly
and echoed back — the extension owns cue identity. The response may omit
indexes (non-speech cues are never translated; failures are reported in
`failed_indices`), so the renderer must treat gaps as "no subtitle".

#### Phase 2b-1 — Persistence and Cache (completed 2026-07-25)

SQLite videos/cues tables, cache identity and reuse, restart-safe storage, and
the usage-aggregation correction. Built before the job system because the cache
removes the repeat-video wait with no threads, no new endpoints, and no
lifecycle states.

**Layer responsibilities**

- **`db.py` — database ownership.** Connections, the schema from §7, and
  `PRAGMA foreign_keys=ON`. Every operation opens a **short-lived connection**
  rather than sharing one: FastAPI already runs `def` handlers in a threadpool,
  so there is no shared connection to guard, and the same holds when the
  Phase 2b-2 worker thread is added. The database lives in a per-user data
  directory (`SUBTITLE_DB_PATH`), never inside the repository.
- **`repository.py` — persistence and lookup.** Finds a completed record for a
  cache identity and upserts on that identity, so a retranslation supersedes
  the previous result instead of duplicating it. Source English cues are stored
  for **every** cue — including non-speech and failed ones — so the index map
  stays complete and auditable, and so Phase 2b-2 can resume and generate SRT
  from storage.
- **`fingerprint.py` — cache identity.** sha256 over loader-normalized cues.

**Cache invalidation rules**

- Identity is `video_id` + `caption_fingerprint` + `model` + `prompt_version`.
  Changing any one of them is a different identity; an old result is never
  returned as current.
- The fingerprint is taken over **loader-normalized** cues (post
  `cues_from_payload`, pre-cleaning). Cleaning and dedup are deterministic, so
  an identical fingerprint implies identical cleaned input. Cosmetic whitespace
  differences do not miss the cache; any change to text, timing, or ordering
  does.
- Only a **fully completed** record is served. A partial result is persisted —
  groundwork for Phase 2b-2 recovery — but is treated as a miss and
  retranslated, never presented as if the job had finished.
- Lookup uses the *configured* model and runs **before the provider is
  constructed**, so a cache hit costs no provider call and needs no API key.

**Evidence:** 96 tests pass with the `[api]` extra (66 pre-existing + 30 new);
73 pass and 3 skip without it. Cache hits are asserted by provider call count.
A real uvicorn run returned `cache_hit=false, provider_calls=1` then
`cache_hit=true, provider_calls=0`.

#### Phase 2b-2 — Jobs and Progressive Delivery (completed 2026-07-25)

The job system (`POST /jobs`, `GET /jobs/{id}`), a single sequential background
worker, restart recovery, and the polling contract. The latency-measurement
entry condition was **waived by the owner**: the justification for jobs is
progressive delivery and reliable long-running translation, not latency alone.

**Layer responsibilities**

- **`jobs.py` — the whole job layer**, framework-free like `service.py`.
  `JobService` (create/reuse/resume/cache-hit + status reads), `JobRunner` (one
  job: clean → dedup → windows → commit each window), `JobWorker` (one thread,
  a doorbell queue, and the startup recovery sweep).
- **`repository.py` — additive job methods.** `find_completed` and `save` are
  untouched, so Phase 2b-1's cache evidence still holds.
- **`pipeline.py` — one optional keyword-only `on_window_done` callback.** This
  is the single change to the Phase 1 engine, and it is inert by default: the
  CLI and `POST /translate` pass nothing and behave exactly as before. The
  alternatives — duplicating the clean/dedup/window orchestration inside the
  job layer, or refactoring `translate_cues` into a generator — were both larger
  and riskier for the same result.

**Why one thread and not an async queue**

The provider is blocking stdlib `urllib`, and `sqlite3` is blocking too, so an
asyncio design would have required rewriting the Phase-1-validated provider onto
an async HTTP client and adding `aiosqlite` — new dependencies to buy
parallelism a single user does not need. Concurrency of one also *bounds* cost
and rate-limit exposure. `db.py` had already been written for this (a
short-lived connection per operation, no shared state).

**Recovery model**

SQLite is the queue of record; `queue.Queue` is only a doorbell and is allowed
to die with the process. Duplicate provider calls are prevented at five points:
the cache hit short-circuits before the provider is constructed; an active job
returns its existing `job_id` instead of enqueuing again; the worker re-checks
status at pickup so a duplicate doorbell ring is dropped; worker concurrency is
one; and a resume skips every cue with a committed translation.

**Evidence:** 153 tests pass with the `[api]` extra (96 pre-existing + 57 new);
112 pass and 4 skip without it, so the engine keeps zero required dependencies.
Against a real uvicorn process with the real worker thread: a 400-cue job
returned a `job_id` in 0.04 s, delivered cues while still `processing`, and
completed with all 400 present exactly once across cursor-based polling with no
duplicates. A 5000-cue job was then **`SIGKILL`ed mid-translation** with 1700
cues committed; after restart, 3850 committed cues had survived and the sweep
resumed it to completion with all 5000 cues present exactly once.

### Phase 3 — Chrome Extension

**Goal:** Deliver the first complete in-player workflow using the proven extractor and backend.

**Technical tasks:** 

- Implement watch-page and video-ID lifecycle detection.
- Use the validated caption-capture method and show a no-captions state when necessary.
- Add the minimal Persian subtitle button, backend health state, and status messaging.
- Send cues to POST /jobs and poll GET /jobs/{id}.
- Create TextTrack/VTTCue objects and render a basic safe RTL overlay.
- Support the complete-result path first if that reduces integration risk; keep the API compatible with later partial cues.

**Deliverable:** A first end-to-end extension that displays a complete validated Persian translation inside the YouTube player.

**Exit criteria:** The manual-caption and auto-caption test videos can be translated and displayed; basic pause, resume, seek, speed, fullscreen, and video navigation do not show stale or incorrectly timed text.

### Phase 4 — Progressive Delivery and Polish

**Goal:** Make the personal workflow reliable enough for regular full-video use without expanding product scope.

**Technical tasks:** 

- Append newly validated cues without duplication while the job continues.
- Harden SPA cleanup, repeated navigation, ads, fullscreen, seeking, playback-speed changes, and overlay resizing.
- Recover cleanly from temporary backend, network, and provider interruptions.
- Improve user-visible states for partial, completed, no captions, backend unavailable, retrying, and failed.
- Persist the font-size preference and add targeted long-cue condensation based on observed problems.
- Enable on-demand SRT export only after the in-player path is stable and the feature is still useful.

**Deliverable:** A reliable personal viewing workflow with progressive results, cache reuse, recovery, and minimal convenience settings.

**Exit criteria:** The PRD success criteria pass across a representative video set, the owner can complete normal viewing sessions without routine manual recovery, and no SaaS, cloud, account, or unnecessary framework work has been introduced.

### Milestone rule

At the end of every phase, record what was observed, the decision it changed or confirmed, fixtures added, known failure cases, and the exact next authorized phase. A phase deliverable is evidence, not permission to skip its exit gate.

## 13. Technical Decisions Log

This initial log captures current implementation choices. Status must be updated when an experiment changes a choice; do not rewrite history without recording the reason.

| **Decision** | **Reason** | **Status** |
| --- | --- | --- |
| Local backend | Keeps credentials, persistence, and processing on the owner’s computer and avoids cloud infrastructure. | Current decision |
| FastAPI | Provides a small typed Python HTTP layer around the translation engine. | Implemented in Phase 2a (`subtitle-api`) |
| SQLite | Sufficient durable storage for one user, cache reuse, and restart recovery. | Implemented in Phase 2b-1 |
| Cache implemented before the job system | The cache removes the repeat-video wait with no threads, no new endpoints, and no lifecycle states — the highest value for the least risk. Jobs introduce the project's first concurrency and are gated on measured latency. | Current decision (2026-07-25) |
| Backend generates caption fingerprints | Derived from cues the backend already receives, so the client contract is unchanged and no client-supplied hash is trusted. | Current decision (2026-07-25) |
| SQLite database stored outside the repository | `SUBTITLE_DB_PATH` defaults to a per-user data directory, so local translation data is never committed. | Current decision (2026-07-25) |
| A cache hit requires no provider call or credentials | Lookup uses the configured model and runs before the provider is constructed, so a repeat request costs nothing and works without an API key. | Current decision (2026-07-25) |
| Phase 1 translation pipeline remains unchanged | The cache wraps `translate_cues()` rather than modifying it; the nine pipeline modules are untouched, so Phase 1's validation evidence still holds. | Current decision (2026-07-25) |
| Usage aggregated per request, not per last window | `OpenRouterProvider` overwrote `last_usage` on every call, so a multi-window run reported one window's tokens and cost as the run total. Totals now accumulate across all windows. Historical Phase 1 figures are affected — see the correction note in `docs/roadmap.md`. | Current decision (2026-07-25) |
| Phase 2 split into 2a / 2b | Ship an extension-callable API first; defer persistence, caching, jobs, recovery, and polling so the HTTP contract is proven before storage design is committed. | Current decision (2026-07-25) |
| Phase 1 engine unchanged; API layer is additive | A framework-free `TranslationService` wraps `pipeline.translate_cues` rather than modifying it, so the engine keeps its Phase 1 validation evidence and stays usable from the CLI. Phase 1 engine files are byte-identical after Phase 2a. | Current decision (2026-07-25) |
| Localhost single-user service | Bound to 127.0.0.1 with an explicit CORS allowlist, no accounts, no authentication, no multi-user isolation — matching the personal-MVP scope. | Current decision (2026-07-25) |
| Provider credentials remain environment-only | Providers read `OPENROUTER_API_KEY` / `ANTHROPIC_API_KEY` from the environment themselves; the settings object never stores a key, and only its *presence* is reported by `GET /health`. Provider error text is sanitized before it reaches a response. | Current decision (2026-07-25) |
| Synchronous `POST /translate` in Phase 2a | Simple and adequate for one local user; `status` is job-shaped so async jobs and polling can be added in Phase 2b-2 without breaking the extension. Cost: a long video holds the connection for minutes. | Revisited in Phase 2b-2: kept unchanged alongside `POST /jobs`, plus a 409 guard |
| Job layer beside `TranslationService`, not underneath it | `POST /translate` keeps its Phase 2a/2b-1 contract and test evidence while `POST /jobs` adds progressive delivery. Both share one database and one cache identity, so work done by either is reused by the other. | Current decision (2026-07-25) |
| A `videos` record is the job record | `videos.id` is the `job_id`, so the existing UNIQUE cache identity doubles as the job's idempotency key — a repeated create cannot start a second translation. No jobs table, per architecture §7. | Current decision (2026-07-25) |
| One background thread, one job at a time | The provider is blocking stdlib `urllib` and `sqlite3` is blocking; async would mean rewriting validated Phase 1 code and adding dependencies to buy parallelism a single user does not need. Concurrency of one also bounds cost and rate-limit exposure. No Redis, Celery, or broker. | Current decision (2026-07-25) |
| Commit every translation window to SQLite | One property delivers both features: results become pollable while translation continues, and a crash loses at most the window in flight. | Current decision (2026-07-25) |
| SQLite is the queue of record; the in-memory queue is only a doorbell | The startup recovery sweep rediscovers every non-terminal job, so queue durability is a non-problem and no broker is needed. | Current decision (2026-07-25) |
| `partial` is terminal, not a mid-run state | Phase 2b-1 already stored `partial` to mean "finished with failed cues"; giving it a second mid-run meaning would have been a bug source. Mid-run progress is `processing` plus counters. Supersedes the §4 draft diagram. | Current decision (2026-07-25) |
| Progressive delivery returns any validated range | With a client-side `cue_index` dedup (already required by P3-04) and one final cursor-less reconciliation read, this is safe and cannot stall forever on a failed window, unlike a contiguous-prefix-only policy. Answers the §5 open question. | Current decision (2026-07-25) |
| Phase 1 engine gains only an optional `on_window_done` callback | Per-window commits need a hook into the window loop. The callback is inert by default, so the CLI and `POST /translate` behave exactly as before and every Phase 1 test passes unmodified. The alternatives — duplicating orchestration or refactoring `translate_cues` into a generator — were larger and riskier. | Current decision (2026-07-25) |
| `POST /translate` returns 409 while a job holds the same identity | `Repository.save()` rewrites a record's cues wholesale and would delete the running job's committed progress. A guard, not a contract change. | Current decision (2026-07-25) |
| Bounded failure at every level | Malformed output uses the Phase 1 retry/split (ending `partial`); a provider transport failure is retried twice (2 s, 8 s) from the last committed window; a job interrupted past `attempt_count` is failed rather than retried forever. Translated cues always survive. | Current decision (2026-07-25) |
| No cancellation endpoint, no retention policy | A job whose viewer navigated away finishes and populates the cache, which is the useful outcome for a personal tool. The database grows by roughly one row per translated video. | Current decision (2026-07-25); revisit only on evidence |
| Extension owns `cue_index` | The backend preserves and echoes client cue indexes exactly and never renumbers, so the extension can map results back to its captured cues. | Current decision (2026-07-25) |
| FastAPI as an optional `[api]` extra | Keeps the Phase 1 engine and its tests free of required third-party dependencies; the engine suite still runs when the extra is absent. | Current decision (2026-07-25) |
| Backend calls proxied through the MV3 service worker | An MV3 content script's fetch is bound by the page's CORS context and would send `Origin: https://www.youtube.com`. The worker holds the extension origin and host permissions, and stays stateless because MV3 terminates idle workers. | Current decision (Phase 3, 2026-07-26) |
| One `VideoSession` owns the active-video lifecycle | Control, track, overlay, job controller, and timers die together; `dispose()` is synchronous so sessions can never overlap. | Current decision (Phase 3, 2026-07-26) |
| Three stale-state guards: disposed flag, AbortController, response identity | The identity check — comparing `video_id` inside each response with the session's own — is what actually prevents cross-video contamination. | Current decision (Phase 3, 2026-07-26) |
| Removable `<track>` element instead of `addTextTrack()` | `addTextTrack()` tracks cannot be removed and accumulate on YouTube's reused `<video>` across navigations. | Current decision (Phase 3, 2026-07-26) |
| Cue dedup by `cue_index`, independent of the polling cursor | The cursor is an optimisation; the dedup set is the guarantee. One cursor-less read on a terminal status reconciles late cues. | Current decision (Phase 3, 2026-07-26) |
| Translation starts only on explicit user click | Only a health check runs automatically. No capture, job, or provider spend without the user asking. | Current decision (Phase 3, 2026-07-26) |
| One single-entry IIFE build per extension entry | A multi-entry build emitted `import` statements for a shared module; MV3 content scripts are classic scripts, so that stops the content script loading entirely. | Current decision (Phase 3, 2026-07-26) |
| YouTube's native captions left untouched | Auto-disabling requires driving undocumented player internals. English CC may overlap the Persian overlay; the user turns it off. | Current decision (Phase 3, 2026-07-26); revisit in Phase 4 |
| TypeScript extension | Improves contract and lifecycle safety in a changing browser integration. | Current decision |
| Browser-side caption capture | Uses the active YouTube session and avoids backend scraping or cookie handling. | Boundary decided; method validated (Phase 0, owner-validated 2026-07-22): main-world json3 fetch. |
| TextTrack synchronization | Delegates cue activation to the media clock while allowing a custom Persian overlay. | Current decision; validate in Phase 3 |
| Polling instead of WebSockets | Simple and adequate for one local client and incremental results. | Current decision |
| No React initially | The MVP UI is too small to justify framework complexity. | Current decision |
| `OpenRouterProvider` added | Second `TranslationProvider` implementation (OpenAI-compatible chat-completions API) so a wider set of models can be trialed without touching the pipeline; credentials read only from the local `OPENROUTER_API_KEY`/`OPENROUTER_MODEL` environment. | Current decision |
| Model selection: `google/gemini-3.1-flash-lite` via OpenRouter | Passed the Phase 1 exit gate on a real YouTube JSON3 capture (478/478 cues translated, 0 failures, ~$0.0019 total cost); owner reviewed the Persian SRT on the actual video and accepted quality. | Current decision (Phase 1, 2026-07-25); revisitable later if evidence warrants a change |

## 14. Open Technical Questions

These questions are intentionally unresolved. Each should be answered by the phase that can produce direct evidence.

### Resolve in Phase 0

- What is the most reliable caption extraction method in the owner’s current YouTube and Chrome environment?
- Can one method cover both manually captioned and auto-captioned videos, or is a narrow fallback required?
- Which captured fields remain stable enough to form the caption fingerprint?

### Resolve in Phase 1

- Which current translation model gives the best acceptable balance of Persian quality, cost, latency, and structured-output reliability?
- What exact chunk size and context overlap work best for manual and rolling auto captions?
- How much Persian condensation is acceptable before meaning or tone degrades?
- Should Persian subtitle segmentation remain one output per source cue for the MVP, or is limited redistribution required for readability?
- Does an optional manual glossary materially improve the target content enough to justify MVP support?
- Should translation windows run sequentially or with limited concurrency after rate limits and ordering are measured?

### Resolve in Phases 2–4

- ~~Should progressive delivery expose any validated range or only a contiguous range from the start?~~ **Resolved (Phase 2b-2): any validated range**, with client-side `cue_index` dedup and one final cursor-less reconciliation read. See §5.
- ~~What polling cursor produces the simplest reliable incremental response?~~ **Resolved (Phase 2b-2): `after_cue_index`**, with `next_cursor` echoed back. No per-cue sequence column was needed.
- Which ad-state signal is reliable enough to hide and restore the overlay?
- What overlay dimensions and font-size defaults are most readable across the owner’s common player sizes?
- Is SRT export needed immediately after the core workflow or only as a later convenience?

None of these questions should delay Phase 0 unless it affects caption extraction. Later questions should not be answered through speculative infrastructure.

## 15. Developer Handoff

This document is ready to guide implementation only after review and approval. The first implementation assignment should be Phase 0, not the complete product.

### Implementation Rules

#### Scope and sequencing

- Do not build beyond the MVP or add features from the future-improvements list without explicit approval.

#### Architecture discipline

- Validate assumptions before expanding dependent code. Caption capture and translation quality are mandatory gates.
- Keep modules small and boundaries explicit: YouTube extraction, API contracts, translation logic, provider integration, validation, persistence, and rendering should not be tangled.
- Avoid unnecessary infrastructure. Do not add cloud hosting, containers, brokers, distributed workers, WebSockets, React, authentication, or analytics unless an observed requirement is approved.

#### Security and data integrity

- Keep API keys and provider configuration in the backend only.
- Bind the backend to localhost and enforce allowed origins.
- Validate all page messages, API requests, provider output, and cue-index coverage before data crosses the next boundary.
- Persist validated progress before exposing it and never render unvalidated translation.

#### Testing and change control

- Use sanitized manual-caption and auto-caption fixtures for regression tests.
- Bound retries and splitting so a malformed window cannot loop or create uncontrolled cost.
- Do not silently change the model, prompt version, extraction method, or cache identity.
- Record technical decisions, experiment evidence, failure cases, and the reason for every architecture change.
- Prefer the smallest reproducible test before implementation polish.

### Handoff sequence

1. Approve the PRD and this Technical Architecture document.
2. Implement Phase 0 only.
3. Review captured fixtures and the extraction experiment record.
4. Authorize Phase 1 only after the Phase 0 exit criterion passes.
5. Continue phase by phase, updating the decisions log when evidence changes the blueprint.

**Stop condition:** If a phase exit criterion fails, do not compensate by building later layers. Record the blocker, test a narrower alternative, and request a decision.

**Definition of done for this document:** A developer or coding assistant can identify system boundaries, data contracts, validation rules, persistence behavior, rendering strategy, milestone gates, and unresolved experiments without treating any untested YouTube or model behavior as confirmed.
