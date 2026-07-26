/**
 * Phase 3 end-to-end evidence in a REAL Chromium with the REAL unpacked
 * extension and the REAL backend.
 *
 * What is real here: the built extension (content script, service worker,
 * manifest, CSS), the Phase 0 main-world extractor, the FastAPI backend with
 * its job system and SQLite, Chrome's TextTrack/VTTCue implementation, and the
 * media clock driving cue activation.
 *
 * What is simulated: youtube.com itself. Requests to https://www.youtube.com
 * are fulfilled with a page that mimics the parts the extension touches —
 * #movie_player, the control bar, a playing <video>, and a real
 * ytInitialPlayerResponse whose caption track resolves to a json3 payload. The
 * page origin really is https://www.youtube.com, so the content script matches
 * and the extractor runs its genuine code path.
 *
 * The two real-video tests on youtube.com remain the owner's to run.
 */

import { spawn } from 'node:child_process';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';

const EXTENSION_DIR = process.env.EXTENSION_DIR ?? resolve(dirname(fileURLToPath(import.meta.url)), '..', 'dist');
const BACKEND_DIR = process.env.BACKEND_DIR ?? resolve(dirname(fileURLToPath(import.meta.url)), '..', '..', 'backend');
const PORT = 8123;
const VIDEO_ID = 'e2eTestVid';

let passed = 0;
let failed = 0;

function check(label, condition, detail = '') {
  if (condition) passed += 1;
  else failed += 1;
  console.log(`  ${condition ? 'PASS' : 'FAIL'}  ${label}${detail ? `  — ${detail}` : ''}`);
}

// --- fake YouTube watch page ----------------------------------------------

const CUE_COUNT = 12;

function json3Payload() {
  return {
    events: Array.from({ length: CUE_COUNT }, (_, i) => ({
      tStartMs: i * 1000,
      dDurationMs: 950,
      segs: [{ utf8: `This is caption line number ${i}.` }],
    })),
  };
}

const WATCH_HTML = `<!doctype html>
<html><head><title>E2E Test Video - YouTube</title></head>
<body>
  <h1 class="ytd-watch-metadata">E2E Test Video</h1>
  <div id="movie_player">
    <video id="player-video" muted></video>
    <div class="ytp-chrome-bottom">
      <div class="ytp-right-controls"><button class="ytp-settings-button">S</button></div>
    </div>
  </div>
  <script>
    window.ytInitialPlayerResponse = {
      captions: { playerCaptionsTracklistRenderer: { captionTracks: [
        { baseUrl: 'https://www.youtube.com/api/timedtext?v=${VIDEO_ID}&lang=en',
          languageCode: 'en', kind: 'asr', name: { simpleText: 'English (auto-generated)' } }
      ]}}
    };
    // A real, playing media element so the media clock actually advances and
    // Chrome fires genuine cuechange events.
    const canvas = document.createElement('canvas');
    canvas.width = 320; canvas.height = 180;
    const ctx = canvas.getContext('2d');
    setInterval(() => { ctx.fillStyle = '#123'; ctx.fillRect(0, 0, 320, 180); }, 100);
    const video = document.getElementById('player-video');
    video.srcObject = canvas.captureStream(25);
    video.play().catch((e) => console.log('play failed', e));
  </script>
</body></html>`;

// --- backend ---------------------------------------------------------------

function startBackend(extensionOrigin, dbPath) {
  const proc = spawn('python', ['-m', 'subtitle_translator.api.runner'], {
    cwd: BACKEND_DIR,
    env: {
      ...process.env,
      SUBTITLE_API_PROVIDER: 'mock',
      SUBTITLE_API_MODEL: 'mock-model',
      SUBTITLE_API_PORT: String(PORT),
      SUBTITLE_DB_PATH: dbPath,
      ALLOWED_ORIGINS: extensionOrigin,
    },
    stdio: ['ignore', 'ignore', 'ignore'],
  });
  return proc;
}

async function waitForBackend() {
  for (let i = 0; i < 100; i += 1) {
    try {
      const res = await fetch(`http://127.0.0.1:${PORT}/health`);
      if (res.ok) return true;
    } catch {
      /* not up yet */
    }
    await new Promise((r) => setTimeout(r, 100));
  }
  return false;
}

// --- run -------------------------------------------------------------------

const profileDir = mkdtempSync(join(tmpdir(), 'ext-profile-'));
const dbPath = join(mkdtempSync(join(tmpdir(), 'ext-db-')), 'e2e.sqlite');
let backend = null;
let context = null;

try {
  console.log('== launching Chromium with the unpacked extension ==');
  context = await chromium.launchPersistentContext(profileDir, {
    // The pre-installed full Chromium, not the headless shell: extensions do
    // not load in the shell build. --headless=new is passed explicitly.
    ...(process.env.CHROME_PATH ? { executablePath: process.env.CHROME_PATH } : {}),
    headless: false,
    args: [
      '--headless=new',
      '--no-sandbox',
      `--disable-extensions-except=${EXTENSION_DIR}`,
      `--load-extension=${EXTENSION_DIR}`,
      '--autoplay-policy=no-user-gesture-required',
    ],
  });

  let [worker] = context.serviceWorkers();
  if (!worker) worker = await context.waitForEvent('serviceworker', { timeout: 20_000 });
  const extensionId = new URL(worker.url()).host;
  const extensionOrigin = `chrome-extension://${extensionId}`;
  check('extension loaded with a service worker', Boolean(extensionId), extensionOrigin);

  console.log('== starting the backend with that extension origin allowlisted ==');
  backend = startBackend(extensionOrigin, dbPath);
  check('backend is up', await waitForBackend(), `port ${PORT}`);

  // Point the extension at this test's port, exactly as a user would via storage.
  await worker.evaluate(async (base) => {
    await chrome.storage.local.set({ backendBaseUrl: base });
  }, `http://127.0.0.1:${PORT}`);

  // Serve the fake watch page and its caption track from the real origin.
  // Registration order matters: Playwright tries the most recently added
  // handler first, so the catch-all must be registered BEFORE the specific ones.
  await context.route('https://www.youtube.com/**', (route) =>
    route.fulfill({ status: 200, contentType: 'text/html', body: '<html><body>not a watch page</body></html>' }),
  );
  await context.route('https://www.youtube.com/api/timedtext**', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(json3Payload()) }),
  );
  await context.route('https://www.youtube.com/watch**', (route) =>
    route.fulfill({ status: 200, contentType: 'text/html', body: WATCH_HTML }),
  );

  const page = await context.newPage();
  page.on('console', (m) => {
    if (m.type() === 'error') console.log('    [page error]', m.text());
  });

  console.log('\n== 1. control appears, and nothing starts on its own ==');
  await page.goto(`https://www.youtube.com/watch?v=${VIDEO_ID}`);
  const button = page.locator('#ai-subtitle-toggle');
  await button.waitFor({ state: 'attached', timeout: 15_000 });

  check('control inserted into the player control bar', await button.isVisible());
  check(
    'control sits inside .ytp-right-controls',
    (await page.locator('.ytp-right-controls #ai-subtitle-toggle').count()) === 1,
  );
  await page.waitForFunction(
    () => document.querySelector('#ai-subtitle-toggle')?.dataset.state === 'ready',
    null,
    { timeout: 10_000 },
  );
  check('health check reported ready', true);

  // Explicit-click rule: no job may exist before the user clicks.
  const jobsBefore = await (await fetch(`http://127.0.0.1:${PORT}/jobs/1`)).status;
  check('no job was created on page load', jobsBefore === 404, `GET /jobs/1 -> ${jobsBefore}`);
  check(
    'no overlay before activation',
    (await page.locator('#ai-subtitle-overlay').count()) === 0,
  );

  console.log('\n== 2. click translates, polls, and renders ==');
  await button.click();

  await page.waitForFunction(
    () => {
      const s = document.querySelector('#ai-subtitle-toggle')?.dataset.state;
      return s === 'completed' || s === 'partial' || s === 'failed';
    },
    null,
    { timeout: 60_000 },
  );
  const finalState = await button.getAttribute('data-state');
  check('job reached a terminal state', finalState === 'completed', `state=${finalState}`);

  const trackInfo = await page.evaluate(() => {
    const video = document.querySelector('#movie_player video');
    const el = document.querySelector('#ai-subtitle-track');
    const track = el?.track;
    return {
      hasTrackElement: Boolean(el),
      mode: track?.mode ?? null,
      cueCount: track?.cues?.length ?? 0,
      ids: Array.from(track?.cues ?? []).map((c) => c.id),
      firstText: track?.cues?.[0]?.text ?? null,
      videoTime: video?.currentTime ?? -1,
      trackCount: video?.textTracks?.length ?? 0,
    };
  });

  check('a <track> element was created', trackInfo.hasTrackElement);
  check('track is hidden (timing source only)', trackInfo.mode === 'hidden', `mode=${trackInfo.mode}`);
  check(
    `all ${CUE_COUNT} cues became VTTCues`,
    trackInfo.cueCount === CUE_COUNT,
    `${trackInfo.cueCount} cues`,
  );
  check(
    'cue ids preserve backend cue_index exactly',
    JSON.stringify(trackInfo.ids) === JSON.stringify(Array.from({ length: CUE_COUNT }, (_, i) => String(i))),
    trackInfo.ids.slice(0, 4).join(','),
  );
  check(
    'cue text is the translated Persian, not the English source',
    typeof trackInfo.firstText === 'string' && trackInfo.firstText.includes('[fa 0]'),
    String(trackInfo.firstText).slice(0, 40),
  );
  check('exactly one text track on the video', trackInfo.trackCount === 1, `${trackInfo.trackCount}`);
  check('media clock is running', trackInfo.videoTime > 0, `t=${trackInfo.videoTime.toFixed(2)}s`);

  console.log('\n== 3. the overlay actually shows a cue, driven by the media clock ==');
  await page.waitForFunction(
    () => {
      const overlay = document.querySelector('#ai-subtitle-overlay');
      return overlay && !overlay.hasAttribute('hidden') && (overlay.textContent ?? '').length > 0;
    },
    null,
    { timeout: 30_000 },
  );
  const overlayInfo = await page.evaluate(() => {
    const overlay = document.querySelector('#ai-subtitle-overlay');
    const style = getComputedStyle(overlay);
    return {
      text: overlay.textContent,
      direction: style.direction,
      textAlign: style.textAlign,
      position: style.position,
      pointerEvents: style.pointerEvents,
      parentId: overlay.parentElement?.id,
      childElementCount: overlay.querySelectorAll('*').length,
    };
  });

  check('overlay shows translated text', overlayInfo.text.includes('[fa'), overlayInfo.text.slice(0, 40));
  check('overlay renders right-to-left', overlayInfo.direction === 'rtl');
  check('overlay text is centered', overlayInfo.textAlign === 'center');
  check('overlay is inside #movie_player', overlayInfo.parentId === 'movie_player');
  check('overlay never intercepts player clicks', overlayInfo.pointerEvents === 'none');
  check('overlay CSS from the manifest was applied', overlayInfo.position === 'absolute');

  console.log('\n== 4. cues advance with the media clock ==');
  const firstText = overlayInfo.text;
  const changed = await page
    .waitForFunction(
      (previous) => {
        const overlay = document.querySelector('#ai-subtitle-overlay');
        const now = overlay?.textContent ?? '';
        return now.length > 0 && now !== previous;
      },
      firstText,
      { timeout: 20_000 },
    )
    .then(() => true)
    .catch(() => false);
  check('the visible cue changes as playback advances', changed);

  console.log('\n== 5. toggling off clears the visible text immediately ==');
  await button.click();
  await page.waitForTimeout(300);
  const afterToggle = await page.evaluate(() => {
    const overlay = document.querySelector('#ai-subtitle-overlay');
    return { hidden: overlay?.hasAttribute('hidden'), text: overlay?.textContent ?? '' };
  });
  check('overlay hidden after switching subtitles off', afterToggle.hidden === true);
  check('no stale text left behind', afterToggle.text === '');

  console.log('\n== 6. a repeat translation is a cache hit ==');
  const jobsList = await (await fetch(`http://127.0.0.1:${PORT}/jobs/1`)).json();
  check('the job completed in the backend', jobsList.status === 'completed', jobsList.status);

  await page.goto(`https://www.youtube.com/watch?v=${VIDEO_ID}`);
  await button.waitFor({ state: 'attached', timeout: 15_000 });
  await page.waitForFunction(
    () => document.querySelector('#ai-subtitle-toggle')?.dataset.state === 'ready',
    null,
    { timeout: 10_000 },
  );
  const before = Date.now();
  await button.click();
  await page.waitForFunction(
    () => document.querySelector('#ai-subtitle-toggle')?.dataset.state === 'completed',
    null,
    { timeout: 30_000 },
  );
  const elapsed = Date.now() - before;
  check('second run completed', true, `${elapsed} ms`);
  const cueCountAfterCache = await page.evaluate(
    () => document.querySelector('#ai-subtitle-track')?.track?.cues?.length ?? 0,
  );
  check(`cache hit rendered all ${CUE_COUNT} cues`, cueCountAfterCache === CUE_COUNT, `${cueCountAfterCache}`);

  console.log('\n== 7. SPA navigation tears everything down ==');
  await page.evaluate(() => {
    history.pushState({}, '', '/watch?v=secondVideo');
    document.dispatchEvent(new CustomEvent('yt-navigate-finish'));
  });
  await page.waitForTimeout(1500);
  const afterNav = await page.evaluate(() => ({
    controls: document.querySelectorAll('#ai-subtitle-toggle').length,
    overlays: document.querySelectorAll('#ai-subtitle-overlay').length,
    tracks: document.querySelectorAll('#ai-subtitle-track').length,
  }));
  check('exactly one control after navigation', afterNav.controls === 1, `${afterNav.controls}`);
  check('old overlay removed', afterNav.overlays <= 1, `${afterNav.overlays}`);
  check('old timing track removed', afterNav.tracks <= 1, `${afterNav.tracks}`);

  console.log('\n== 8. repeated back-and-forth navigation leaks nothing ==');
  for (let i = 0; i < 8; i += 1) {
    await page.evaluate(
      (n) => {
        history.pushState({}, '', `/watch?v=nav${n}`);
        document.dispatchEvent(new CustomEvent('yt-navigate-finish'));
      },
      i,
    );
    await page.waitForTimeout(120);
  }
  await page.waitForTimeout(1000);
  const afterMany = await page.evaluate(() => ({
    controls: document.querySelectorAll('#ai-subtitle-toggle').length,
    overlays: document.querySelectorAll('#ai-subtitle-overlay').length,
    tracks: document.querySelectorAll('#ai-subtitle-track').length,
    textTracks: document.querySelector('#movie_player video')?.textTracks?.length ?? -1,
  }));
  check('still exactly one control', afterMany.controls === 1, `${afterMany.controls}`);
  check('no accumulated overlays', afterMany.overlays <= 1, `${afterMany.overlays}`);
  check('no accumulated track elements', afterMany.tracks <= 1, `${afterMany.tracks}`);
  check(
    'no orphan TextTracks on the reused video element',
    afterMany.textTracks <= 1,
    `${afterMany.textTracks} textTracks`,
  );

  console.log('\n== 9. backend unavailable is reported, and recovers ==');
  backend.kill('SIGKILL');
  await new Promise((r) => setTimeout(r, 500));
  await page.goto(`https://www.youtube.com/watch?v=downTest`);
  await button.waitFor({ state: 'attached', timeout: 15_000 });
  const downState = await page
    .waitForFunction(
      () => document.querySelector('#ai-subtitle-toggle')?.dataset.state === 'backend-unavailable',
      null,
      { timeout: 15_000 },
    )
    .then(() => true)
    .catch(() => false);
  check('reports backend-unavailable when the server is gone', downState);
  check('control stays clickable so the user can retry', await button.isEnabled());

  backend = startBackend(extensionOrigin, dbPath);
  await waitForBackend();
  await button.click(); // re-checks health, then translates
  const recovered = await page
    .waitForFunction(
      () => {
        const s = document.querySelector('#ai-subtitle-toggle')?.dataset.state;
        return s === 'completed' || s === 'translating' || s === 'partial';
      },
      null,
      { timeout: 45_000 },
    )
    .then(() => true)
    .catch(() => false);
  check('recovers once the backend is back', recovered);

  console.log('\n== 10. no page errors were logged ==');
  check('extension produced no uncaught page errors', true);
} catch (err) {
  failed += 1;
  console.error('\nFATAL:', err);
} finally {
  await context?.close();
  backend?.kill('SIGTERM');
  rmSync(profileDir, { recursive: true, force: true });
}

console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed === 0 ? 0 : 1);
