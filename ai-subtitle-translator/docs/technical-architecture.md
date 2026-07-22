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

### Other runtime components

- **FastAPI application —** exposes the small local API and coordinates job lifecycle.
- **Translation services —** implement cleaning, context windows, prompt construction, provider calls, and response repair.
- **SQLite repository —** provides transactional persistence and cache lookup.
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
| FastAPI | Provides a small typed Python HTTP layer around the translation engine. | Current decision |
| SQLite | Sufficient durable storage for one user, cache reuse, and restart recovery. | Current decision |
| TypeScript extension | Improves contract and lifecycle safety in a changing browser integration. | Current decision |
| Browser-side caption capture | Uses the active YouTube session and avoids backend scraping or cookie handling. | Boundary decided; method experimental |
| TextTrack synchronization | Delegates cue activation to the media clock while allowing a custom Persian overlay. | Current decision; validate in Phase 3 |
| Polling instead of WebSockets | Simple and adequate for one local client and incremental results. | Current decision |
| No React initially | The MVP UI is too small to justify framework complexity. | Current decision |

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

- Should progressive delivery expose any validated range or only a contiguous range from the start?
- What polling cursor produces the simplest reliable incremental response?
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
