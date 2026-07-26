# Phase 3 end-to-end harness

`phase3-e2e.mjs` drives the **built extension** in a **real Chromium** against a
**real backend**, and checks the whole Phase 3 path: control insertion, health
state, explicit-click start, caption capture through the Phase 0 main-world
extractor, `POST /jobs`, cursor polling, VTTCue creation, media-clock-driven cue
activation, the RTL overlay, cache hits, SPA teardown, and backend-down
recovery.

It is **not** part of `npm test`: it needs a browser and a Python backend, and
Playwright is deliberately not a dependency of this package. Run it on demand.

## What is real, and what is not

Real: the built extension (content script, service worker, manifest, CSS), the
unchanged Phase 0 extractor, the FastAPI backend with jobs and SQLite, and
Chrome's own `TextTrack` / `VTTCue` and media clock.

Simulated: youtube.com. Requests to `https://www.youtube.com` are fulfilled
locally with a page that mimics only what the extension touches —
`#movie_player`, `.ytp-right-controls`, a playing `<video>`, and a real
`ytInitialPlayerResponse` whose caption track resolves to a json3 payload. The
page origin genuinely is `https://www.youtube.com`, so the content script
matches and the extractor runs its real code path.

**This does not replace the P3-07 gate.** That gate requires the owner's own
Chrome on one real manual-caption and one real auto-captioned YouTube video —
the same standard Phase 0 was held to. This harness catches regressions between
those runs.

## Run it

```sh
cd extension
npm run build                  # the harness loads dist/, so build first

npm install --no-save playwright
npx playwright install chromium   # skip if a Chromium is already available

node e2e/phase3-e2e.mjs
```

The backend must be installed with its API extra (`pip install -e ".[api,dev]"`
in `backend/`). The harness starts and stops the server itself on port 8123 with
the mock provider, so it needs no API key and costs nothing.

Environment overrides: `CHROME_PATH` (use a specific Chromium binary),
`EXTENSION_DIR`, `BACKEND_DIR`.

Exit code is 0 only if every check passes.
