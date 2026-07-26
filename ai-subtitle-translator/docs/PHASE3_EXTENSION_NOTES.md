# Phase 3 — Chrome Extension Integration: implementation record

**Status:** P3-01 … P3-06 implemented and tested. **P3-07 exit gate is NOT
passed** — it requires the owner's own Chrome on one real manually captioned and
one real auto-captioned YouTube video. Phase 3 is therefore **In Progress**, and
Phase 4 must not begin.

**Goal:** connect the Phase 0 caption extractor to the completed Phase 2 backend
and display synchronized Persian subtitles inside YouTube.

## What was built

```
click FA in the player
  -> service worker injects the (unchanged) Phase 0 extractor for sender.tab.id
  -> English track -> json3 -> normalized ordered cues
  -> POST /jobs                      job_id returned immediately
  -> GET /jobs/{id}?after_cue_index  every ~2s, chained setTimeout
  -> validated cues -> VTTCue on a hidden <track>
  -> media clock cuechange -> custom RTL overlay inside #movie_player
```

New modules, all in `extension/src/`: `messages.ts` (worker contract),
`apiTypes.ts` (typed backend mirror + runtime validation), `backendClient.ts`,
`controlState.ts`, `subtitleControl.ts`, `jobController.ts`, `subtitleTrack.ts`,
`overlay.ts`, `session.ts`, plus `overlay.css`.

Modified: `content.ts` (session ownership added to the unchanged Phase 0
detection), `background.ts` (two new message handlers, existing popup path
untouched), `manifest.json`, and the build scripts.

**Phase 0 is byte-identical:** `mainWorldExtractor.ts`, `normalize.ts`,
`popup.ts`, and `popup.html` are unchanged, verified by `git diff`.

## The decisions that shaped it

**Backend calls go through the service worker.** An MV3 content script's
`fetch` is bound by the *page's* CORS context, so calling the backend from a
YouTube page would arrive with `Origin: https://www.youtube.com` — forcing the
backend's allowlist to include a public site. The worker holds the extension
origin and host permissions instead. It stays stateless: it injects the
extractor or forwards one request, and never holds job state or a timer,
because MV3 terminates idle workers and a polling loop there would silently
stop.

**One VideoSession owns everything.** The control, timing track, overlay, job
controller, and every timer belong to exactly one session and die with it.
`dispose()` is synchronous, so a session is fully torn down before its
successor exists. This makes "one session per video" a property of a single
20-line function in `content.ts` rather than a convention spread across files.

**Three guards, not one.** A `disposed` flag covers in-flight work; an
`AbortController` token covers cancellation; and an identity check compares the
`video_id` inside every response against the session's own. The third is the
one that actually prevents an old video's cues landing in a new video's track,
and the one most often omitted.

**A removable `<track>` element, not `addTextTrack()`.** Tracks created by
`addTextTrack()` cannot be removed — there is no `removeTextTrack` API. YouTube
reuses the same `<video>` element across SPA navigations, so that approach
accumulates one orphan track per video watched. Removing the element removes
the track with it; the E2E harness asserts zero orphan tracks after eight
navigations.

**The track is hidden and used only as a clock.** Cue activation follows media
time, so play, pause, seek, and playback-rate changes need no code of ours —
and no rate multiplier must ever be applied. Presentation is entirely the
overlay's job.

**The overlay lives inside `#movie_player`.** That is the element YouTube makes
fullscreen, so fullscreen and theater mode need no handling at all. Layout is
percentage-based, so resizing needs no listener. `pointer-events: none` keeps
the full-width overlay from swallowing clicks meant for play/pause.

**Dedup is not the cursor's job.** `after_cue_index` is an optimisation; a
`Set` of delivered cue indexes gates every cue unconditionally. On a terminal
status the client makes one final cursor-less read and reconciles, which is the
backend's documented contract for cues that completed out of order behind a
failed window.

**Nothing starts on page load.** Only a health check runs automatically, and it
only decides the button label. Capture, job creation, and polling all wait for
an explicit click, so no video is translated — and no provider spend incurred —
without the user asking.

## A build bug worth recording

Adding `messages.ts`, shared between `content.ts` and `background.ts`, made the
multi-entry Vite build extract it into a shared chunk and emit `import`
statements in the entry files. **An MV3 content script is a classic script**, so
that import would have stopped the content script loading at all — with a
SyntaxError and no subtitles, in a way no unit test would catch.

`scripts/build.mjs` now runs one single-entry IIFE build per entry, so each
output is self-contained with zero module statements. The build asserts nothing
by itself, so the check is recorded here and in the E2E harness, which loads the
built output rather than the sources.

## Evidence

**Unit and DOM tests: 123 pass** (11 pre-existing Phase 0 normalization + 112
new), offline, no browser or backend required. Coverage: the eight control
states and their transitions; typed client request shaping, error-code mapping,
and rejection of malformed or hostile responses; polling cadence, cursor
handling, duplicate prevention under full resends, terminal reconciliation,
backoff and recovery; cue mapping, sub-second precision, and deterministic
overlap ordering; overlay injection safety (a `<script>` and an `<img onerror>`
payload both render as literal text); and session teardown, including responses
that land *after* disposal and captures that belong to a different video.

**Real-browser E2E: 38 checks pass** (`extension/e2e/phase3-e2e.mjs`). Real
Chromium loading the real built extension, the real backend with jobs and
SQLite, the genuine Phase 0 extractor, and Chrome's own `TextTrack`/`VTTCue`
driven by a live media clock. Confirmed: the control appears in
`.ytp-right-controls`; **no job exists before the click** (`GET /jobs/1` → 404);
after clicking, all 12 cues become VTTCues whose ids preserve `cue_index`
exactly; the track is `hidden` and there is exactly one text track; the overlay
shows translated text, right-to-left, centered, inside `#movie_player`, with
`pointer-events: none`; **the visible cue changes as playback advances**;
toggling off clears the text immediately; a repeat run is a cache hit
(**83 ms**); and after eight rapid SPA navigations there is exactly one control
and **zero** leaked overlays, track elements, or orphan TextTracks. Killing the
backend produces `backend-unavailable`, and restarting it recovers on the next
click.

What that harness does **not** cover: youtube.com itself. It fulfils requests to
the real origin with a page that mimics only what the extension touches, so the
content script matches and the extractor runs its real code path — but YouTube's
actual DOM, its ad flow, and its real caption payloads are not exercised.

## What P3-07 still needs (owner)

The gate is unchanged from the roadmap and must run in the owner's own Chrome:

1. **One manually captioned English video** — translate end to end; Persian
   appears, correctly timed and complete.
2. **One auto-captioned English video** — same, with rolling duplicates already
   removed by the backend.
3. For both, exercise **pause, resume, seeking, 0.75×/1×/1.5×/2× playback,
   fullscreen entry and exit, and YouTube in-app navigation between videos**,
   confirming no stale or mistimed text.

Recommended while testing: watch the DevTools console on the YouTube tab for
`[ai-subtitle-translator]` lines, and the extension's service-worker console for
backend errors. Report the two videos, the observed cue counts, and anything
that looked wrong.

## Deliberately not built (Phase 4)

Ad detection, subtitle style preferences and font-size persistence, long-cue
condensation, advanced resize handling, progressive-delivery policy tuning, and
recovery-UX polish. Also excluded throughout: React, WebSockets/SSE, any cloud
feature, accounts, service-worker job processing, client-side credentials, and
backend caption scraping.
