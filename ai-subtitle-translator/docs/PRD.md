# AI Subtitle Translator — PRD v0.1

Product Requirements Document for a personal English-to-Persian YouTube subtitle tool

**Status:** Draft for staged technical validation. This document is the initial source of truth, not proof that the untested assumptions work.

**Primary user:** The project owner; one user on one computer.

**Audience:** The project owner, ChatGPT, Claude, Cursor, or a software developer implementing the tool.

**Scope boundary:** Personal tool only. No commercial SaaS, public distribution, or multi-user infrastructure.

### Decision-status labels used in this PRD

- **Current decision —** the working choice for v0.1 unless an experiment disproves it.
- **Assumption —** a belief that still needs technical or user validation.
- **Experiment —** a bounded test with an observable pass/fail outcome.
- **Open question —** an unresolved choice that should not be silently treated as decided.

## 1. Project Overview

AI Subtitle Translator is a personal tool for watching English YouTube videos with natural, fluent Persian subtitles. It reuses an English caption track that already exists on the video, translates the captions through one selected online AI model, validates the translated cue structure, caches the result locally, and displays synchronized Persian text inside the YouTube player.

The product is intentionally designed for one person on one computer. Its architecture should optimize for translation quality, simplicity, recoverability, and low operating cost—not scale, collaboration, or commercial distribution.

### Intended personal workflow

1. The user opens an English YouTube video that has an English caption track.
2. A Chrome extension detects the watch page and confirms that usable English captions are available.
3. The user clicks a Persian subtitles button.
4. The extension captures structured English caption cues from the active browser session and sends them to a FastAPI service bound to localhost.
5. The backend cleans and groups the cues, submits context-aware windows to the selected AI translation API, validates every returned cue index, and stores results in SQLite.
6. The extension polls approximately every two seconds and receives newly completed Persian cues.
7. The browser uses TextTrack and VTTCue timing while a custom right-to-left overlay renders the active Persian text.
8. Completed work is cached so reopening the same video with the same caption fingerprint, model, and prompt version does not create another translation charge.
9. A Persian SRT may be generated later from the stored cues when requested.

**Current decision:** The system has three parts: a Manifest V3 Chrome extension written in TypeScript, a local Python/FastAPI/SQLite backend, and one affordable online AI translation model.

**Assumption:** The extension can capture a reliable structured caption track from YouTube’s browser environment without making the backend independently retrieve captions.

**Experiment:** Caption extraction must be tested before the complete extension or translation workflow is built.

## 2. Problem Statement

The project owner watches substantial amounts of long-form English educational content, including AI, business, marketing, branding, GEO, and podcast material. Existing options create two recurring problems.

- **Translation quality —** YouTube’s automatic translation can be literal, poorly segmented, or inconsistent. That makes complex educational content tiring to follow and can distort meaning.
- **Cost and workflow —** Third-party subtitle tools may charge expensive per-minute fees or require downloading, uploading, and processing videos outside the normal YouTube viewing flow.

The desired solution should work while the user is already watching YouTube, reuse captions that the video provides, and produce Persian that reads like natural Persian rather than a word-for-word mapping of English lines. It should remain inexpensive enough for frequent personal use, but quality is more important than minimizing every cent.

The central product challenge is not simply translating text. English captions—especially auto-generated rolling captions—may be duplicated, weakly punctuated, and split at boundaries that do not align with Persian sentence structure. The tool must preserve usable timing while allowing the translator enough context to produce fluent Persian.

**Product principle:** Prove translation quality and caption extraction reliability before investing heavily in extension UX.

## 3. Target User

The initial and only MVP user is the project owner. No secondary personas are required for v0.1.

### Primary use cases

- AI education and tutorials
- Business education and founder content
- Marketing and growth content
- Branding and strategy content
- GEO (Generative Engine Optimization) content
- Podcasts and interviews
- Long-form educational videos

### User context

- The user watches on desktop Chrome.
- The user can run a local Python service on the same computer.
- Target videos already provide English captions, either manually authored or auto-generated.
- The user is willing to pay a modest translation API cost when the Persian result is materially better.
- The user values a one-click viewing workflow and does not need team collaboration or public sharing.

## 4. Goals

- Produce Persian subtitles that are natural, readable, and faithful to the speaker’s meaning.
- Work directly inside the YouTube viewing experience on desktop Chrome.
- Reuse existing English captions instead of downloading audio or running speech-to-text.
- Keep ongoing cost low while prioritizing quality over the cheapest possible output.
- Translate in context rather than translating each cue independently.
- Preserve complete cue coverage and usable synchronization.
- Make early translated sections available while later sections continue processing.
- Cache completed videos locally and avoid duplicate translation costs.
- Recover gracefully from temporary API, network, browser, or backend interruptions.
- Keep the codebase small, understandable, and maintainable as a personal side project.

### Product-order goal

Development should proceed in risk order: first prove caption capture, then prove Persian translation quality in a CLI, then build the backend and extension around those validated capabilities.

## 5. Non-Goals

The following are explicitly outside the MVP:

- A commercial SaaS product
- Multiple users or user accounts
- Authentication, authorization, or identity management
- Payments, subscriptions, credits, or usage billing
- Cloud deployment or public backend infrastructure
- Mobile applications
- Browsers other than desktop Chrome
- Target languages other than Persian
- Speech-to-text or transcription
- Videos without existing captions
- Whisper or any other audio transcription model
- Audio or video downloading
- Local translation models
- A public Chrome Web Store release
- Team collaboration, shared libraries, or moderation
- Enterprise analytics, observability platforms, or audit logging
- Multiple translation-provider controls in the extension UI

These exclusions are deliberate. A later version may revisit some of them only after the core personal workflow is proven useful.

## 6. Core User Story

As the project owner, I want to click a Persian subtitle control while watching a captioned English YouTube video so that fluent Persian subtitles appear in sync with playback, completed sections arrive progressively, and the translation is reused when I watch the video again.

### End-to-end acceptance scenarios

| **Scenario** | **Expected outcome** |
| --- | --- |
| First-time video | Given a captioned English video and a running local backend, when the user requests Persian subtitles, a job starts, progress is visible, and translated cues begin appearing without leaving YouTube. |
| Cached video | Given a previously completed translation with the same caption fingerprint, model, and prompt version, when the video is reopened, Persian cues load locally without a new translation request. |
| Playback change | Given active Persian subtitles, when the user pauses, seeks, changes playback speed, or enters fullscreen, the visible cue stays synchronized with the player. |
| Navigation | Given YouTube single-page navigation, when the user opens another video, old state is discarded and the extension evaluates the new video independently. |
| Temporary interruption | Given a partially completed job, when the internet or translation API temporarily fails, stored completed cues remain available and processing can resume or retry without starting from zero. |
| Unavailable captions | Given a video without a usable English track, when the user requests Persian subtitles, the extension shows a clear no-captions message and does not start a translation job. |

## 7. MVP Scope

### Must Have

- A caption extraction feasibility test before full product development
- A translation-engine CLI proof of concept that accepts JSON3, VTT, or SRT and outputs Persian SRT
- A small comparison of candidate AI models using real target-video samples
- Caption cleaning, grouping, structured translation, validation, retry, and split-on-failure logic
- A local FastAPI backend bound to 127.0.0.1
- A minimal SQLite cache for videos and cues
- A Manifest V3 Chrome extension using TypeScript, Vite, and plain HTML/TypeScript controls
- Detection of YouTube watch pages and single-page navigation
- Caption capture in the browser and transfer of structured cues to the backend
- Polling for progress and newly translated cues
- TextTrack/VTTCue synchronization with a custom Persian RTL overlay
- Basic loading, success, no-captions, backend-unavailable, and translation-error states
- Basic handling of pause, resume, seeking, speed changes, fullscreen, ads, and video changes
- A basic font-size setting

### Should Have Later

- On-demand Persian SRT export
- A small editable glossary or glossary file
- Improved error details and retry controls
- Prioritization around the current playhead after a large seek
- More advanced Persian cue condensation and sentence-level redistribution

### Explicitly Postponed

- Speech-to-text and support for videos without captions
- Local translation models
- WebSockets, Server-Sent Events, or complex streaming infrastructure
- React or another UI framework unless the UI later becomes complex enough to justify it
- Multiple translation providers exposed in the extension
- Cloud hosting, public distribution, accounts, payments, collaboration, or analytics

**MVP gate:** Phase 0 and Phase 1 are not optional prototypes; they are go/no-go gates for investing in the complete extension.

## 8. Functional Requirements

| **ID** | **Capability** | **Requirement** |
| --- | --- | --- |
| FR-001 | Watch-page detection | The extension detects YouTube watch pages and the current video ID, including changes caused by single-page navigation. |
| FR-002 | Caption availability | The extension identifies whether a usable English caption track exists before offering or starting translation. |
| FR-003 | Browser-side capture | The extension captures structured cues with cue index, start time, end time, and English text from the active browser context. |
| FR-004 | Main-world experiment | The preferred first experiment intercepts YouTube timed-text activity from a script injected into the page’s main execution world. |
| FR-005 | Job creation | The extension sends video metadata, caption fingerprint, and ordered cues to POST /jobs on the local backend. |
| FR-006 | Idempotent reuse | An equivalent request reuses a valid cached job rather than creating duplicate translation work. |
| FR-007 | Caption cleanup | The backend normalizes whitespace, rolling duplicates, irrelevant markers, and malformed short cues without changing timestamps unnecessarily. |
| FR-008 | Context windows | The backend groups roughly 40–70 cues or two to three minutes of speech, with limited adjacent context. |
| FR-009 | Structured translation | The selected model returns structured JSON that preserves each expected cue index exactly once. |
| FR-010 | Validation and repair | The backend rejects malformed, missing, duplicated, or empty cue output; it retries with a targeted instruction or splits the window. |
| FR-011 | Progressive ordering | The backend processes the beginning of the video first and makes completed contiguous or otherwise safe cue ranges available promptly. |
| FR-012 | Polling | The extension requests status and newly completed cues approximately every two seconds while work is active. |
| FR-013 | Cue synchronization | Translated cues are registered as VTTCue objects in a TextTrack used for browser-managed timing and activation. |
| FR-014 | Persian overlay | The active Persian cue is rendered in a custom right-to-left overlay inside the YouTube player. |
| FR-015 | Playback state | Pause, resume, seeking, playback-speed changes, and fullscreen do not require manual resynchronization by the user. |
| FR-016 | Ad suppression | The Persian overlay is hidden while a YouTube ad is playing and resumes for the main video. |
| FR-017 | Video isolation | Navigating to another video clears old overlay, cues, polling, and job state before initializing the new video. |
| FR-018 | Local cache | Original and translated cues are stored locally and load without a new model call when the cache key is valid. |
| FR-019 | Prompt invalidation | Changing prompt_version prevents an outdated translation from being silently treated as current. |
| FR-020 | User feedback | The extension shows concise states for ready, loading, translating, partial, complete, no captions, backend unavailable, and failed. |
| FR-021 | Font size | The user can select a basic Persian subtitle font size and the preference persists locally. |
| FR-022 | Health check | The extension can verify backend availability through GET /health before or during a request. |
| FR-023 | SRT generation | When enabled in a later convenience phase, the backend generates SRT on demand from stored cues rather than storing a separate permanent file. |

## 9. Non-Functional Requirements

| **ID** | **Quality** | **Requirement** |
| --- | --- | --- |
| NFR-001 | Translation quality | Persian should be natural, faithful, and comfortable for a complete educational video; quality review uses real sample clips and user judgment. |
| NFR-002 | Reliability | No expected cue index may be silently lost or duplicated. Invalid windows fail visibly and enter a bounded retry/split path. |
| NFR-003 | Privacy | The backend is local-only; only subtitle text and necessary translation instructions are sent to the selected AI API. |
| NFR-004 | Security | API credentials remain in backend environment variables. The backend binds to 127.0.0.1 and enforces allowed browser origins. |
| NFR-005 | Maintainability | The MVP uses TypeScript, Vite, plain HTML, Python, FastAPI, and SQLite with small modules and no unnecessary framework or infrastructure. |
| NFR-006 | Reasonable speed | Translation begins at the start of the video and returns usable early sections without waiting for the complete video. |
| NFR-007 | Low operating cost | The selected model should be affordable for frequent personal use, while cost never overrides unacceptable Persian quality. |
| NFR-008 | Recoverability | Completed cues persist after process, browser, network, or API interruption; unfinished windows can be retried. |
| NFR-009 | Local compatibility | The tool runs on the project owner’s desktop Chrome and local Python environment without public deployment. |
| NFR-010 | Change tolerance | YouTube-specific extraction logic is isolated so it can be replaced when YouTube changes implementation. |

## 10. Proposed Architecture

**Current decision:** Use a three-part local architecture and simple polling. The backend receives cues; it does not use YouTube as its primary caption source.

### End-to-end flow

```text
[YouTube browser session]
          │ existing English caption requests / track data
          ▼
[Chrome extension: capture + UI]
          │ POST ordered caption cues to localhost
          ▼
[FastAPI backend]
          │ clean → group → prompt
          ▼
[Selected AI translation API]
          │ structured Persian cue JSON
          ▼
[Validation + retry/split]
          │
          ├──────────────► [SQLite cache]
          │                       │
          ◄──── GET status/new cues by polling ────┘
          │
          ▼
[TextTrack + VTTCue timing] → [Custom RTL Persian overlay]
```

### Component responsibilities

#### Chrome extension

- Detect the current YouTube video and single-page navigation.
- Capture available English captions within the authenticated browser context.
- Provide the user control, status, font size, and error messages.
- Send ordered cues to localhost, poll for results, create VTTCue objects, and render the active Persian text.
- Handle playback lifecycle, fullscreen, ads, seeking, speed changes, and cleanup on video changes.

#### Local FastAPI backend

- Validate incoming cue shape and create or reuse a translation job.
- Clean captions, build translation windows, call the selected model, validate results, and perform bounded retry or splitting.
- Persist video metadata and cue states in SQLite.
- Return incremental results through polling and generate SRT on demand when that feature is enabled.

#### Translation API

- Translate context-aware subtitle windows into fluent Persian.
- Return strict structured output keyed by original cue index.
- Follow glossary and style instructions supplied by the backend.

### Technology choices

- Extension: TypeScript, Chrome Manifest V3, Vite, plain HTML and TypeScript.
- Backend: Python, FastAPI, SQLite, environment variables for credentials.
- Communication: HTTP on localhost with simple polling.
- Synchronization: TextTrack and VTTCue for timing; custom overlay for RTL display.

**Assumption:** TextTrack cue activation remains stable enough across YouTube playback events when the translated track is maintained by the extension.

**Current decision:** React, WebSockets, Server-Sent Events, cloud services, and queues are not required for the MVP.

## 11. Translation Pipeline

Cue-by-cue translation is explicitly rejected because it removes sentence context and tends to produce literal, inconsistent Persian. The backend should own a deterministic pipeline with observable validation at each stage.

### 11.1 Input parsing and normalization

1. Parse ordered cue indexes, start_ms, end_ms, and English text from the captured format.
2. Normalize Unicode, whitespace, line breaks, HTML entities, and obvious formatting noise.
3. Detect rolling auto-caption duplication by comparing overlapping text across adjacent cues. Retain only the newly spoken portion when confidence is high; preserve the original when the cleanup rule is uncertain.
4. Remove or normalize non-speech markers such as music or applause when they add no learning value. Do not remove meaningful speaker or sound context automatically without a clear rule.
5. Merge extremely short, empty, or malformed cues only when doing so improves the translation input and does not corrupt the source timing map.
6. Retain an immutable mapping from cleaned units back to every original cue index.

### 11.2 Window construction

- Target approximately 40–70 original cues or roughly two to three minutes of speech per translation window.
- Prefer sentence or topic boundaries when punctuation makes them detectable.
- Include limited preceding and following context for interpretation, but mark context cues as non-output so the model does not duplicate them.
- Process the earliest windows first. Parallel calls may be added only if they remain simple, respect rate limits, and do not complicate recovery.
- Keep window size, context overlap, and concurrency configurable in the backend rather than hard-coding them across the codebase.

### 11.3 Prompt and glossary

The translation prompt should instruct the model to understand complete sentences, translate meaning rather than English word order, use natural Persian, preserve names and technical terms consistently, avoid adding explanations, and return one result for every required cue index.

- A small manually maintained glossary may be passed to the model when domain terms require consistent treatment.
- Automatic glossary extraction is an experiment or later enhancement, not a required MVP subsystem.
- The prompt must distinguish source cues, required output cues, and read-only context cues.
- The prompt must carry an explicit prompt_version used in cache validation.

### 11.4 Structured output contract

The preferred output is strict JSON. The exact schema may evolve, but the model must not return prose outside the structure.

```json
{
  "window_id": "w_0001",
  "cues": [
    {"cue_index": 0, "persian_text": "..."},
    {"cue_index": 1, "persian_text": "..."}
  ]
}
```

### 11.5 Validation

- Parse valid JSON and reject trailing commentary or an incompatible schema.
- Confirm the returned window_id when used.
- Confirm that every expected cue index appears exactly once.
- Reject unexpected indexes, duplicates, missing indexes, null text, or empty output for a spoken cue.
- Confirm the result is Persian rather than copied English; normalize Persian characters, whitespace, punctuation, and line breaks; then mark the window complete.

### 11.6 Retry and split strategy

1. On a structural failure, retry once with a targeted correction message describing the exact missing, duplicate, or malformed indexes.
2. On a length or readability problem, retry the affected cues with a request to condense without losing meaning.
3. On repeated failure, split the window into smaller windows while preserving context and retry each half.
4. On rate limit or transient network failure, use bounded backoff and preserve completed windows.
5. On a persistent provider or credential error, stop the job in a recoverable failed state and surface a concise error to the extension.

### 11.7 Persian segmentation and timing

English and Persian often place verbs, modifiers, and sentence boundaries differently. Forcing each English fragment to map literally onto the same line can make Persian unnatural. The MVP therefore uses a practical compromise:

- Preserve every original cue index and approximate source timing.
- Allow the model to understand a complete sentence before assigning Persian across its cue sequence.
- Optimize the displayed Persian for natural reading rather than literal line-by-line equivalence.
- Check whether a Persian cue is disproportionately long for its display duration and request condensation when needed.
- Keep the reading-length rule configurable and inspect real results before adopting a fixed Persian characters-per-second threshold.
- Defer sophisticated sentence-level retiming or cue-boundary rewriting until the basic workflow is proven.

### 11.8 Cache write

After validation, save the original cue, Persian text, timing, and translation status. A window may become visible to the extension only after its cues have passed validation. The final SRT is generated from stored cues on demand.

## 12. Caption Extraction Strategy

### Why extraction belongs in the browser

The active YouTube page already has the player, the user’s session, cookies, client-generated tokens, and access to YouTube’s own caption activity. The extension is therefore the preferred extraction surface. Sending structured cues to the backend avoids making a separate local process reproduce YouTube’s private request context.

### Preferred interception experiment

Inject a small script into the page’s main execution world and observe the network or player data path used for timed-text captions. The experiment should capture a complete English track response, convert it to an ordered cue list, and pass only caption data back to the isolated extension context.

- Test at least one manually captioned English video.
- Test at least one auto-captioned English video.
- Confirm cue index, start time, duration or end time, and text can be recovered.
- Confirm capture still works after normal play, pause, seek, and YouTube single-page navigation.
- Confirm the script does not expose cookies, credentials, or unrelated page data to the backend.

### Fallback approaches

- Read caption-track metadata from YouTube page or player data and request the chosen track from the extension context.
- Use an existing transcript library as a temporary testing or fallback path.
- Use yt-dlp for development diagnostics or fallback experiments.

All fallbacks that depend on undocumented YouTube behavior are fragile and may break when YouTube changes implementation. They should live behind a narrow extraction interface, not be spread through the extension.

### Phase 0 go/no-go rule

**Go:** Proceed when structured cues can be captured from both a manual and an auto-captioned video in repeatable tests, with timing and text complete enough for the translation pipeline.

**No-go or redesign:** Pause full development if capture requires unreliable manual steps, frequently misses cues, cannot distinguish tracks, or depends on backend retrieval that fails under normal YouTube session behavior.

**Assumption:** The preferred timed-text interception path can be made reliable enough for one personal desktop environment.

**Experiment:** Phase 0 is the first technical task and must record the exact browser and YouTube conditions tested.

## 13. Subtitle Rendering and Synchronization

### Preferred approach

Create an in-memory TextTrack for the translated track and add each validated translation as a VTTCue with the original start and end times. Keep the track hidden from native rendering, listen for active cue changes, and draw the current Persian text in a custom overlay. This lets the browser manage time and cue activation while the extension controls RTL typography and placement.

**Current decision:** Use TextTrack/VTTCue plus a custom overlay as the preferred MVP synchronization approach.

**Assumption:** This approach remains stable in the YouTube player across supported playback operations. It is a working choice, not an irreversible architecture decision.

### Rendering behavior

- Use direction: rtl and Unicode-aware Persian text rendering.
- Center the subtitle block near the bottom of the player while avoiding YouTube controls.
- Use a high-contrast text treatment suitable for changing video backgrounds.
- Wrap long text into a small number of readable lines and avoid overflowing the player.
- Persist a basic font-size preference in extension storage.
- Render only validated cues; do not display incomplete model output.

### Playback lifecycle

- **Pause and resume —** the active cue remains tied to media time, not wall-clock timers.
- **Seeking —** TextTrack activates the cue at the new currentTime; the overlay updates immediately.
- **Playback speed —** cue timing follows media time, so no separate speed multiplier should be needed.
- **Fullscreen —** the overlay remains attached to the player/fullscreen container and rescales appropriately.
- **SPA navigation —** remove old tracks, observers, overlay nodes, and polling before initializing the next video.
- **Ads —** detect YouTube ad state, hide the Persian overlay, and restore it when main content resumes.
- **Progressive arrival —** new VTTCue objects can be appended after their corresponding translation window is validated.

## 14. API Design

The backend API should remain small. Endpoint details may change during implementation, but the MVP should preserve the responsibilities below.

| **Method** | **Path** | **Input** | **Output / behavior** |
| --- | --- | --- | --- |
| POST | /jobs | video_id, title, caption_fingerprint, ordered cues, requested model/prompt version if configured | Create or reuse a job; return job_id, status, cache_hit, total_cues, completed_cues. |
| GET | /jobs/{job_id} | Optional cursor such as after_cue_index or last_seen_update | Return status, progress, newly completed cues, and a concise recoverable or terminal error. |
| GET | /jobs/{job_id}/srt | No body | Generate and download Persian SRT from stored complete cues; enabled in the later convenience phase. |
| GET | /health | No body | Return local service availability and minimal version information without exposing credentials. |

### POST /jobs behavior

- Validate ordered indexes, timestamps, non-empty caption data, and a supported source language.
- Build a cache key from the video ID, caption fingerprint, translation model, and prompt version.
- Return a completed cached job immediately when the key matches.
- Create a new queued/processing job when no valid cache exists.
- Do not require authentication because the service is local-only, but enforce origin checks and localhost binding.

### Job states

- queued — accepted but not yet processing
- processing — translation is active and no validated result may yet exist
- partial — one or more validated cue ranges are available
- completed — every expected cue is validated and stored
- failed — processing stopped with a recoverable or terminal error description

### Polling contract

The extension may poll approximately every two seconds. Responses should include only the state and newly completed cues needed by the client. The backend does not need WebSockets, Server-Sent Events, a broker, or a streaming framework for one local user.

## 15. Data Model and Cache

SQLite is sufficient. The schema should support one local user, validated progressive results, recovery, and cache invalidation without enterprise logging tables.

### Videos table

| **Field** | **Type** | **Purpose** |
| --- | --- | --- |
| id | INTEGER primary key | Internal record key. |
| video_id | TEXT | YouTube video identifier. |
| title | TEXT | Best-effort title for local recognition. |
| status | TEXT | queued, processing, partial, completed, or failed. |
| total_cues | INTEGER | Expected number of source cues. |
| translation_model | TEXT | Actual model identifier used for this translation. |
| prompt_version | TEXT | Version of translation instructions and output contract. |
| caption_fingerprint | TEXT | Hash of the ordered source cue content/timing; prevents stale reuse when captions change. |
| created_at | DATETIME/TEXT | Creation timestamp. |
| updated_at | DATETIME/TEXT | Last meaningful update timestamp. |

### Cues table

| **Field** | **Type** | **Purpose** |
| --- | --- | --- |
| video_record_id | INTEGER | Foreign key to videos.id. |
| video_id | TEXT | External video ID retained for simple inspection/export. |
| cue_index | INTEGER | Original ordered cue index. |
| start_ms | INTEGER | Original start time in milliseconds. |
| end_ms | INTEGER | Original end time in milliseconds. |
| english_text | TEXT | Cleaned or canonical source cue text; raw input may be retained only if needed for debugging. |
| persian_text | TEXT nullable | Validated Persian output. |
| translation_status | TEXT | pending, processing, completed, or failed. |

### Constraints and cache policy

- Use a unique constraint for (video_record_id, cue_index).
- Use a cache identity that includes video_id, caption_fingerprint, translation_model, and prompt_version.
- A cache entry is reusable only when the job is completed and the identity matches.
- A changed prompt_version or source fingerprint creates a new current translation rather than silently returning outdated Persian.
- Partially completed cue rows remain available after interruption and can be resumed.
- Do not create a permanent SRT table or file record. Generate SRT from stored cues on demand.
- Do not add enterprise event, audit, billing, or analytics tables to the MVP.

## 16. Error Handling

| **Condition** | **Required behavior** | **Recovery** |
| --- | --- | --- |
| No English captions | Show that the video is unsupported for the MVP; do not create a job. | User chooses another captioned video. |
| Caption capture failure | Show capture failed and preserve diagnostic category without exposing session data. | Retry capture or use a tested fallback extractor. |
| Backend unavailable | Show how to start/check the local service; keep extension state safe. | Poll health after user restarts the backend. |
| Invalid AI output | Reject the window; do not display unvalidated text. | Targeted retry, then split the window. |
| Missing or duplicate cue IDs | Mark validation failure with the exact index set. | Correction retry; split if repeated. |
| API rate limit | Pause the affected window with bounded backoff; keep completed cues. | Resume automatically or let the user retry later. |
| Internet interruption | Stop new API calls and retain partial results in SQLite. | Resume unfinished windows when connectivity returns. |
| Partially completed job | Return completed cues and partial status rather than restarting. | Continue pending windows from stored state. |
| Navigation to another video | Cancel client polling and remove old overlay/track state. | Initialize independently for the new video. |
| Persian cue too long | Flag the affected cue/window and avoid unreadable overflow. | Retry with targeted condensation; split/re-segment later if needed. |
| Outdated prompt cache | Do not present the old entry as current. | Create/reuse a job for the current prompt_version. |
| Provider/model unavailable | Return a clear terminal or configurable model error. | Select a tested replacement through the model experiment; do not silently change quality. |

### Error design principles

- Never display unvalidated translation as if it were complete.
- Differentiate user-actionable errors from automatic retry states.
- Keep detailed provider output in local development logs only when needed; user-visible messages should be concise.
- Bound retries so one bad window cannot loop indefinitely or create uncontrolled cost.
- Preserve completed work before retrying or stopping.

## 17. Privacy and Security

- Store translation API keys only in backend environment variables. Never bundle keys in the extension.
- Bind FastAPI only to 127.0.0.1 by default; do not listen on all interfaces.
- Allow requests only from the expected extension origin and local development origins when explicitly configured.
- Do not expose the backend through a public tunnel or cloud endpoint in the MVP.
- Use no third-party database. Persist data only in local SQLite.
- Send only subtitle text, limited context, glossary entries, and translation instructions to the selected AI API.
- Do not send cookies, YouTube session tokens, browsing history, or unrelated page content to the backend or AI provider.
- The injected main-world script should pass only sanitized caption payloads to the extension’s isolated context.
- Validate request size and cue shape before processing to avoid accidental local resource exhaustion.
- Avoid logging API keys, authorization headers, full browser request headers, cookies, or sensitive page data.

**Current decision:** The tool has no authentication because it is local-only, but local-only binding and origin validation are mandatory.

**Assumption:** The selected AI provider’s data handling is acceptable for non-sensitive public-video subtitle text. Provider terms should be checked when a model is selected.

## 18. Success Criteria

The project is successful when the core personal workflow is technically reliable and the Persian is comfortable enough for real viewing. The following criteria are measurable without inventing an undefined translation-quality score:

- The extension experiment captures structured captions from at least one manually captioned English YouTube video and one auto-captioned English YouTube video.
- A representative English caption file from the user’s target content can be converted by the CLI into a complete Persian SRT.
- Every expected cue index is present exactly once after translation validation.
- The user judges the Persian natural, faithful, and comfortable enough to watch a complete educational video without routinely reverting to English captions.
- The extension displays correct Persian cues after pause, resume, seek, playback-speed change, and fullscreen.
- YouTube single-page navigation does not leave subtitles from the previous video attached to the new video.
- Persian subtitles are hidden during ads and resume for the main video.
- A completed cached video loads without a new translation API call or cost when the cache identity matches.
- A temporarily interrupted job retains completed cues and can continue without retranslating the entire video.
- Invalid model output is rejected and repaired or surfaced; missing cue IDs are never silently ignored.
- The backend remains reachable only on localhost in the default configuration.

### Model-selection acceptance

The chosen model should be selected using blind or side-by-side review of the same representative windows from actual target videos. Review naturalness, meaning fidelity, technical-term consistency, segmentation, malformed output frequency, latency, and estimated cost. The result should record why the selected model is acceptable; it should not hard-code a permanent model name into the PRD.

## 19. Development Phases

Development must follow the sequence below. Each phase should exit with a working deliverable before the next phase becomes the main focus.

### Phase 0 — Caption Extraction Feasibility

Test caption interception before building the product.

- Build a small Manifest V3 experiment that runs on YouTube watch pages.
- Attempt timed-text capture or interception from the main execution world.
- Normalize captured data into cue index, start_ms, end_ms, and English text.
- Test one manually captioned and one auto-captioned video.
- Document which YouTube/browser mechanism worked and where it is fragile.

**Deliverable:** Captured structured caption data from at least one manually captioned video and one auto-captioned video.

**Exit gate:** Proceed only if capture is repeatable enough for the project owner’s desktop environment.

### Phase 1 — Translation Engine CLI

Input: JSON3, VTT, or SRT captions. Output: Persian SRT.

- Prepare representative caption samples from actual target videos.
- Compare a suitable Gemini Flash model, a suitable OpenAI small model, and, if useful, a suitable Claude model as a higher-quality reference.
- Design cleaning and rolling-duplicate handling.
- Implement 40–70 cue or two-to-three-minute windows with context.
- Design the Persian translation prompt and optional manual glossary.
- Require structured output and validate cue coverage.
- Implement targeted retry and split-on-failure.
- Review naturalness, fidelity, segmentation, structure reliability, latency, and cost.

**Deliverable:** A CLI that converts a representative input file into a complete, reviewable Persian SRT.

**Exit gate:** The project owner considers the Persian comfortable enough for a long educational video.

### Phase 2 — Local Backend

- Create the FastAPI service and localhost-only configuration.
- Implement /health and minimal job endpoints.
- Add SQLite videos/cues storage, cache identity, and job recovery.
- Move the validated translation pipeline from the CLI behind the API.
- Implement progressive job state and polling responses.

**Deliverable:** A local API that accepts cues, progressively translates them, persists results, and reuses a valid cache.

### Phase 3 — Basic Chrome Extension

- Detect video changes and caption availability.
- Use the validated caption-capture method.
- Add a Persian subtitle button and basic status UI.
- Send cues to the backend and receive a complete translated result.
- Create TextTrack/VTTCue objects and render a basic RTL overlay.

**Deliverable:** A first end-to-end version that displays a complete translation in the YouTube player.

### Phase 4 — Progressive and Robust Playback

- Poll for newly completed cues and append them safely.
- Handle SPA navigation, video changes, ads, seeking, speed changes, fullscreen, and cleanup.
- Recover from temporary backend, internet, and provider interruptions.
- Improve long-cue condensation and user-facing error states.

**Deliverable:** A reliable personal viewing workflow for full videos, including partial results and recovery.

### Phase 5 — Personal Convenience Features

- Add on-demand SRT export.
- Persist a basic font-size preference.
- Improve error explanations and retry controls.
- Add optional manual glossary controls if the model comparison shows value.

**Deliverable:** Small convenience improvements justified by actual use; no commercial product expansion.

### Recommended implementation order inside each phase

1. Write the smallest reproducible test.
2. Record observed behavior and failure cases.
3. Implement the narrow working path.
4. Add validation and recovery for failures already observed.
5. Only then add convenience or UX polish.

## 20. Risks and Assumptions

| **Risk** | **Likelihood** | **Impact** | **Mitigation** | **Validation method** |
| --- | --- | --- | --- | --- |
| YouTube changes caption delivery | High | High | Isolate extraction behind one interface; keep fallbacks narrow. | Repeat the Phase 0 fixture tests after breakage or YouTube changes. |
| Caption interception fails | Medium | Critical | Test before product work; evaluate page data, transcript library, or yt-dlp fallbacks. | Capture manual and auto-caption tracks in Phase 0. |
| Auto captions are poorly punctuated | High | High | Use context windows, duplicate cleanup, and sentence-aware prompting. | Review real auto-caption samples in Phase 1. |
| Persian is too long for timings | High | High | Length checks and targeted condensation; later sentence-level redistribution. | Watch full sample videos and inspect flagged cues. |
| Model returns malformed structure | Medium | High | Strict schema, index validation, correction retry, split-on-failure. | Run repeated windows and inject failure fixtures. |
| SPA navigation breaks extension state | Medium | High | Central lifecycle cleanup and video-ID state machine. | Navigate repeatedly between videos in extension tests. |
| API model changes or is retired | Medium | Medium | Keep provider adapter small; store actual model ID; rerun model comparison. | Swap to a second tested candidate in a controlled test. |
| Generated implementation uses outdated Chrome/YouTube assumptions | High | High | Verify current Manifest V3 APIs and observed page behavior; avoid copying untested snippets. | Run code in the current Chrome environment at every phase. |

### Key assumptions

- Most target videos have a usable English caption track.
- A browser-side extractor can obtain complete structured cues in the owner’s desktop environment.
- One affordable online model can provide Persian quality acceptable for long educational viewing.
- Original YouTube timing can remain usable after practical Persian redistribution and condensation.
- Simple polling is sufficient for one local user.
- A local Python service and SQLite database are acceptable parts of the daily workflow.

## 21. Open Questions

- Which current model provides the best Persian for the project owner’s actual videos at an acceptable cost?
- Are the target videos primarily manually captioned or auto-captioned?
- Can the preferred timed-text interception method be made reliable across the owner’s normal YouTube usage?
- How much Persian condensation is acceptable before meaning or tone is lost?
- Should a glossary be manually maintained, automatically extracted, or omitted from the MVP?
- Is SRT export needed immediately, or only after the in-player workflow is stable?
- Should the first extension wait for the complete translation or display progressive partial results? The architecture supports progressive delivery, but the simplest first end-to-end build may render the completed result before adding partial updates.
- Should translation windows run strictly sequentially or with limited concurrency after provider rate limits and ordering behavior are measured?
- What caption-fingerprint method best detects changed YouTube tracks without unnecessary retranslations?
- Which visual treatment is most readable across the project owner’s usual YouTube player sizes and content?

These questions should be resolved by the relevant phase experiment. They should not delay Phase 0 unless they affect caption extraction.

## 22. Future Improvements

The following are plausible later additions, but all are outside the current MVP:

- Speech-to-text for videos without captions
- A local translation model
- Better sentence-level Persian re-segmentation and timing redistribution
- An editable glossary and domain-term manager
- Translation-style presets such as concise, conversational, or technical
- Dual-language English and Persian display
- Smart translation prioritization around the current playhead after seeking
- Support for additional desktop browsers

A future improvement should be added only when actual use reveals a clear problem or repeated benefit. It should not expand the MVP by default.

## 23. Final Recommended MVP

The smallest version worth building is a personal Chrome extension that captures existing English YouTube captions, sends them to a local Python translation service, translates them into natural Persian using one selected AI API, validates and caches the result, and displays synchronized right-to-left subtitles using the browser’s TextTrack timing.

Build it only after two gates pass: caption capture works on both a manually captioned and an auto-captioned video, and the standalone translation engine produces Persian that the project owner is comfortable using for a complete educational video.

**Final boundary:** No SaaS infrastructure, speech-to-text, public distribution, multi-user features, or complex streaming is required to prove the value of this personal tool.
