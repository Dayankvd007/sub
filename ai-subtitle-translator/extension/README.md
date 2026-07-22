# Phase 0 — Caption Extraction Experiment

This is **only** the Phase 0 feasibility experiment described in
`../docs/PRD.md` §12 and `../docs/technical-architecture.md` §3. It proves
(or disproves) that a Chrome MV3 extension can capture structured English
YouTube caption cues. It does **not** translate anything, does not talk to
any backend, and does not render subtitles into the player.

## What it does

1. A content script (`src/content.ts`) detects YouTube watch pages and the
   active video ID, including YouTube's single-page-app navigation.
2. Clicking **Capture Captions** in the popup runs a function inside the
   page's own main JavaScript world (`src/mainWorldExtractor.ts`, via
   `chrome.scripting.executeScript({ world: 'MAIN' })`). It reads the
   player response YouTube already loaded, finds the English caption track
   (preferring a manual track over an auto-generated "asr" one), and fetches
   that track's `json3` timed-text payload using the page's own `fetch`.
3. Only the parsed track metadata + timed-text payload is returned to the
   extension — no cookies, headers, or unrelated page data ever cross that
   boundary.
4. `src/normalize.ts` converts the raw payload into the cue contract:
   `cue_index`, `start_ms`, `end_ms`, `english_text`. This step is
   deliberately conservative — it validates and orders, but does not
   deduplicate rolling auto-captions (that's Phase 1's job).
5. The result is stored in `chrome.storage.local` and shown in the popup.
   Use **Download JSON Fixture** to save it for inspection.

## Build

```sh
npm install
npm run build
```

Output goes to `dist/`.

## Load into Chrome

1. Go to `chrome://extensions`.
2. Enable **Developer mode** (top right).
3. Click **Load unpacked** and select the `dist/` folder produced above.
4. Open a YouTube watch page, e.g. `https://www.youtube.com/watch?v=...`.
5. Click the extension icon, confirm the popup shows "Watch page detected"
   with the correct video ID, then click **Capture Captions**.
6. Check the popup summary (track kind, cue count, first/last timestamp,
   normalization issues) and open the DevTools console on the YouTube tab
   for `[Phase0 caption experiment]` log lines if something looks off.
7. Click **Download JSON Fixture** to save the result.

## Running the two required Phase 0 tests

Per the PRD's exit gate, run this against:

- **One manually captioned English video** — save the downloaded fixture as
  something like `tests/fixtures/real-manual-<videoId>.json`.
- **One auto-captioned English video** — save it as
  `tests/fixtures/real-auto-<videoId>.json`.

For each, also test: initial load, a page reload, seeking, and navigating
to it via YouTube's in-app link (SPA navigation) from another video — the
popup's "Watch page detected" status should update correctly each time
without a manual extension reload.

Before committing a real fixture, double-check it only contains caption
text/timing and track metadata — no cookies, tokens, or request headers
(the extractor doesn't collect those, but eyeball it anyway).

## Unit tests

```sh
npm test
```

`tests/normalize.test.ts` exercises `normalize.ts` against two **synthetic**
fixtures (`tests/fixtures/synthetic-*.json`) that mirror the real YouTube
`json3` shape — including rolling-caption duplication and malformed events —
without needing network access. These are not a substitute for the two real
video tests above; see `../docs/PHASE0_EXPERIMENT_NOTES.md`.

## Known limitations of this experiment

- Relies on `window.ytInitialPlayerResponse` or `#movie_player.getPlayerResponse()`,
  both undocumented YouTube internals that can change without notice.
- Only tested for `en`/`en-*` language codes.
- Does not yet handle YouTube ad playback, age-restricted/embed-restricted
  videos, or videos with no captions at all beyond returning a clear error.
