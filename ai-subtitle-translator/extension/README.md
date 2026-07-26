# Chrome Extension — Caption Extraction (Phase 0) + Subtitle Workflow (Phase 3)

Reads English captions from the YouTube page you are watching, sends them to
**your own local backend**, and renders the returned Persian subtitles inside
the player. Nothing is uploaded to a cloud service, and no API key ever exists
in the extension.

- **Phase 0 — caption extraction** (complete): main-world extraction of the
  English track plus normalization into the cue contract.
- **Phase 3 — subtitle workflow** (implemented; exit gate pending owner
  validation): the in-player control, the backend connection, job creation,
  polling, TextTrack timing, and the Persian RTL overlay.

## How it works

```
click the FA button in the player
  -> service worker injects the Phase 0 extractor into the page's main world
  -> English track found, json3 fetched, normalized to ordered cues
  -> POST /jobs           (through the service worker; job_id returned at once)
  -> GET  /jobs/{id}?after_cue_index=N   every ~2s
  -> validated Persian cues -> VTTCue on a hidden TextTrack
  -> media clock fires cuechange -> custom right-to-left overlay
```

Translation **only starts when you click**. On page load the extension does
nothing but check whether the backend is running, so no video is ever
translated — and no provider spend incurred — without you asking.

## Build and load

```sh
cd extension
npm install
npm run build          # -> dist/
```

1. Open `chrome://extensions`, enable **Developer mode**.
2. **Load unpacked** -> select `dist/`.
3. Copy the extension ID shown on the card.
4. Start the backend with that ID allowlisted:

```sh
cd ../backend
export OPENROUTER_API_KEY=...
export ALLOWED_ORIGINS=chrome-extension://<your-extension-id>
subtitle-api
```

5. Open a YouTube video with English captions and click **FA** in the player
   control bar.

The backend defaults to `http://127.0.0.1:8000`. To use another port, set it
once from the extension's service-worker console:

```js
chrome.storage.local.set({ backendBaseUrl: 'http://127.0.0.1:9000' });
```

`host_permissions` covers `http://127.0.0.1/*`, so any local port works without
rebuilding.

## The control and its states

The button is inserted into YouTube's own right-hand control bar. If that
container cannot be found, a minimal fallback appears near the video title so
the workflow stays reachable.

| State | Meaning |
| --- | --- |
| ready | Backend reachable; click to translate |
| no captions | This video has no usable English track |
| backend unavailable | `subtitle-api` is not running — start it and click again |
| translating N% | Job running; cues appear as they are validated |
| completed | Every line translated; click to hide or show |
| partial | Finished, but some lines could not be translated |
| retrying | Lost contact with the backend mid-job; reconnecting |
| failed | Terminal error; click to retry (already translated lines are kept) |

Clicking once translation has started toggles the subtitles on and off; it never
creates a second job. Switching them off clears the visible line immediately.

## Behaviour worth knowing

- **Re-watching is instant.** An identical video, captions, model, and prompt
  version is a cache hit: every cue arrives in the `POST /jobs` response with no
  provider call and no polling.
- **Gaps are normal.** Non-speech cues (`[Music]`) are never translated, and
  anything that failed validation is reported rather than faked. A missing cue
  index means "no subtitle here".
- **Navigating away does not waste work.** The backend job keeps running and
  populates the cache, so coming back is a cache hit.
- **YouTube's own captions are left alone.** If you have English CC switched on,
  it will overlap the Persian overlay — turn CC off in the player. The extension
  deliberately does not drive YouTube's caption settings.

## Architecture notes

- **The content script owns everything.** One `VideoSession` per video owns the
  control, track, overlay, job controller, and every timer. Disposal is
  synchronous and total, so two sessions can never overlap.
- **The service worker holds no state.** It injects the extractor and forwards
  one HTTP request at a time. MV3 terminates idle workers, so a polling loop
  there would silently stop — polling lives in the content script.
- **Backend calls are proxied through the worker.** An MV3 content script's
  `fetch` is bound by the page's CORS context, so calling the backend directly
  would send `Origin: https://www.youtube.com`. The worker holds the extension
  origin instead.
- **Three guards stop cross-video contamination:** a disposed flag, an
  `AbortController` token, and an identity check comparing the `video_id` inside
  every response with the session's own.
- **Timing uses a removable `<track>` element**, not `video.addTextTrack()`,
  whose tracks cannot be removed and would accumulate on YouTube's reused
  `<video>` element across navigations.
- **Overlay text is written with `textContent`, never `innerHTML`.** Subtitle
  text is model output: data, never markup.

## Tests

```sh
npm test        # 123 tests, offline, no browser or backend needed
npm run typecheck
```

Covers json3 normalization (Phase 0), the control state machine, the typed
backend client and its response validation, polling with cursor/dedup/
reconciliation/backoff, cue mapping and overlap ordering, overlay injection
safety, and session teardown including stale-response handling.

`e2e/` holds a Playwright harness that drives the **built** extension in a real
Chromium against a real backend — see `e2e/README.md`. It is not part of
`npm test` and needs no API key.

## Phase 0 debug tool

The toolbar popup is unchanged from Phase 0: it reports watch-page status,
captures captions on demand, and downloads the cue fixture as JSON. It is kept
as a debugging and fixture-capture aid — the product workflow is the in-player
button.

## Known limitations

- Relies on `window.ytInitialPlayerResponse` / `#movie_player.getPlayerResponse()`
  and the `&fmt=json3` endpoint — undocumented YouTube internals that can change
  without notice.
- Only `en` / `en-*` caption tracks.
- No ad handling, no subtitle style preferences, no advanced resize logic —
  those are Phase 4.
