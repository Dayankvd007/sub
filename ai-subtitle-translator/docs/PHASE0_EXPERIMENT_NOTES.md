# Phase 0 Experiment Notes — Caption Extraction Feasibility

Source of truth for this experiment: `docs/PRD.md` §12, §19 (Phase 0);
`docs/technical-architecture.md` §3, §12 (Phase 0). This document records
what was built, what evidence exists so far, and the honest current
pass/fail state.

## What was built

A minimal Manifest V3 Chrome extension in `extension/`:

- `src/content.ts` — detects YouTube watch pages and the active video ID,
  including single-page-app navigation (`yt-navigate-finish` event, with a
  1s URL-poll fallback). Publishes status to `chrome.storage.local`.
- `src/mainWorldExtractor.ts` — the primary extraction experiment. Runs in
  the page's main JS world via `chrome.scripting.executeScript({ world:
  'MAIN' })`. Reads `window.ytInitialPlayerResponse` (falling back to
  `#movie_player.getPlayerResponse()`), locates
  `captions.playerCaptionsTracklistRenderer.captionTracks`, selects an
  English track (manual preferred over `asr`), fetches that track's
  `baseUrl&fmt=json3` payload with the page's own `fetch`, and returns only
  the parsed track metadata + payload — nothing else crosses out of the
  page context.
- `src/normalize.ts` — converts raw `json3` events into the required cue
  contract (`cue_index`, `start_ms`, `end_ms`, `english_text`). Conservative
  by design: validates timing, drops empty/position-only events, records
  malformed events as `issues` instead of silently swallowing them, and
  deliberately does **not** deduplicate rolling auto-caption text (that
  cleanup belongs to Phase 1 per the architecture doc).
- `src/background.ts` + `src/popup.ts` — orchestration and a small UI
  (Capture Captions / Download JSON Fixture / inline error + cue preview)
  so the whole mechanism is directly observable and debuggable, per the
  task's requirement 8.

This is the "candidate strategy #1" (track metadata + page-context
retrieval) from `docs/technical-architecture.md` §3, chosen as the primary
experiment because it needs no user interaction with YouTube's CC button
and returns a complete track in one call, rather than depending on
observing/intercepting whatever timed-text request YouTube itself happens
to fire.

## What could be verified from this environment, and what could not

**Environment constraint:** this session's sandbox has an explicit network
policy block on `youtube.com` (outbound `CONNECT` to `www.youtube.com:443`
is rejected by the proxy). That means captures against real YouTube pages
could not be performed from here, by design of the sandbox, not because of
a flaw in the extraction method itself.

What **was** verified here:

- The extension builds cleanly (`npm run build`) into a loadable
  `dist/` folder — manifest, service worker, content script, and popup all
  present with no bundler/type errors (`npx tsc --noEmit` is clean).
- `normalize.ts` was unit-tested (`npm test`, 11 passing assertions) against
  two **synthetic** fixtures hand-authored to mirror real YouTube `json3`
  responses:
  - `tests/fixtures/synthetic-manual-json3.json` — clean, non-overlapping
    cues, one whitespace-only event, one position-only (no `segs`) event.
  - `tests/fixtures/synthetic-auto-json3.json` — rolling-caption text
    duplication across consecutive events (as real `asr` tracks produce),
    plus a zero-duration event, a negative-duration event, and an event
    missing `tStartMs`, to confirm malformed input is flagged as an
    `issue` rather than silently dropped or crashing the pipeline.
  - These prove the **normalization logic** is correct against
    representative input shapes. They are explicitly labeled `_disclaimer`
    fields inside the files themselves as synthetic, not live captures, and
    do not by themselves satisfy the PRD's two-real-video exit criterion.

What was **not** verified here (requires a real Chrome + real YouTube,
outside this sandbox):

- That `window.ytInitialPlayerResponse` / `#movie_player.getPlayerResponse()`
  actually contain `captionTracks` on real current YouTube pages.
- That the `fmt=json3` fetch succeeds under a real browser session (cookies,
  referer, current YouTube anti-abuse behavior).
- Repeatability across reload, seek, and SPA navigation on real videos.
- Behavior on one real manually captioned video and one real auto-captioned
  video, as the PRD's exit gate requires.

## Assumptions still open

- `ytInitialPlayerResponse`'s `captions.playerCaptionsTracklistRenderer.captionTracks`
  shape is current and stable enough for personal use; it is undocumented
  and YouTube can change it without notice (architecture doc §3, NFR-010).
- The `json3` format (`&fmt=json3`) remains fetchable without additional
  auth beyond the existing page session.
- One extraction method (track metadata + direct fetch) covers both manual
  and auto-generated English tracks; no fallback (network interception,
  transcript panel scraping, yt-dlp) has been built or tested, since the
  primary method hasn't yet been confirmed to need one.
- Chrome 111+ is available (required for `world: 'MAIN'` in
  `chrome.scripting.executeScript`).

## Owner validation run (2026-07-22)

The two real-video captures described under "Next step" below could not be
run from the build sandbox (no network access to `youtube.com`). They were
therefore run by the **project owner** in their own desktop Chrome.

**Owner-reported outcome:** the extension loaded and caption extraction
**succeeded** — the Phase 0 experiment was reported as tested successfully.
On that basis the verdict below is changed from "not yet decidable" to
**GO**.

### Honesty note on the evidence recorded here

This document is written by the build assistant, which did **not** directly
observe the owner's Chrome run and was not given the raw captured fixtures
or their metrics. To avoid recording invented data, the following specifics
are intentionally **left for the owner to fill in / commit** rather than
guessed:

- Exact test environment (Chrome version, OS).
- The two video IDs tested (one manual, one auto), their track types, and
  which `player_response_source` was used for each.
- Per-video cue counts, first/last timestamps, and any
  `normalization_issues`.
- Whether any range looked missing versus the visible YouTube transcript
  panel.

To complete the record, the owner can drop the downloaded fixtures into
`extension/tests/fixtures/` as `real-manual-<videoId>.json` and
`real-auto-<videoId>.json` and paste the above metrics into this section.
The Phase 1 test harness is written to automatically pick up any
`real-*.json` fixtures placed there, so committing them also strengthens
Phase 1's regression tests retroactively.

## Phase 0 verdict

**Status: GO (owner-validated 2026-07-22).**

The extraction mechanism is implemented, builds cleanly, is unit-tested
against representative data, and is instrumented for debuggability (popup
shows the exact failure stage: `find-player-response`,
`find-caption-tracks`, `select-english-track`, `fetch-timedtext`,
`parse-json3`, `validate-json3-shape`, or `complete`). The owner ran the
required real-video capture in their own Chrome and reported success, which
satisfies `docs/PRD.md` §12's go/no-go rule to the extent of an
owner-reported pass. Phase 1 (translation engine) is therefore authorized.

**Validated approach (confirmed for downstream phases):** main-world
injection via `chrome.scripting.executeScript({ world: 'MAIN' })` reading
the player response and fetching the selected English track's `json3`
timed-text payload, normalized into the cue contract by `normalize.ts`.
This is the extraction method Phase 3 should reuse.

### Recommended follow-up (does not block Phase 1)

1. Commit the two real fixtures into `extension/tests/fixtures/` as
   `real-manual-<videoId>.json` / `real-auto-<videoId>.json`.
2. Fill the "Owner validation run" metrics above from those fixtures.
3. If any auto-caption rolling-duplication was observed, note it — Phase 1's
   rolling-duplicate cleanup is designed against exactly that pattern and
   real samples make its tests stronger.

### Original next-step instructions (retained for history)

1. `cd extension && npm install && npm run build`, load `dist/` unpacked in
   Chrome (see `extension/README.md`).
2. Run **Capture Captions** on one manually captioned English video and one
   auto-captioned English video; repeat each across reload/seek/SPA-nav per
   the README checklist.
3. Save the two downloaded fixtures into `extension/tests/fixtures/` as
   `real-manual-<videoId>.json` and `real-auto-<videoId>.json`.
4. Report back (or update this document directly) with: which
   `player_response_source` was used, cue counts, first/last timestamps,
   any `normalization_issues`, and whether anything looked missing versus
   the visible YouTube transcript panel.
