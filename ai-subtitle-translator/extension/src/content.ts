/**
 * Isolated-world content script.
 *
 * Phase 0 responsibility (unchanged): detect YouTube watch pages and the
 * active video ID, including YouTube's single-page-app navigation, and publish
 * that to chrome.storage.local so the debug popup can read it.
 *
 * Phase 3 responsibility (added): own exactly one VideoSession for the current
 * video, and destroy it before the next one is created. This file is the only
 * place a session is constructed or disposed, so "one session per video" is a
 * property of a single ~20-line function rather than a convention.
 *
 * Caption extraction still does not happen here — the service worker injects
 * the Phase 0 main-world extractor, and only when the user clicks.
 */

import { log } from './debug';
import { CAPTURE_FOR_TAB, type CaptureForTabResult } from './messages';
import { VideoSession } from './session';

interface WatchStatus {
  isWatchPage: boolean;
  videoId: string | null;
  url: string;
  updatedAt: number;
}

function parseVideoId(href: string): string | null {
  let url: URL;
  try {
    url = new URL(href);
  } catch {
    return null;
  }
  const isYouTubeHost = url.hostname === 'www.youtube.com' || url.hostname === 'youtube.com';
  if (!isYouTubeHost || url.pathname !== '/watch') {
    return null;
  }
  return url.searchParams.get('v');
}

function currentStatus(): WatchStatus {
  const videoId = parseVideoId(location.href);
  return {
    isWatchPage: videoId !== null,
    videoId,
    url: location.href,
    updatedAt: Date.now(),
  };
}

// --- Phase 3: session ownership -------------------------------------------

let session: VideoSession | null = null;

/** Ask the service worker to run the Phase 0 extractor against *this* tab. */
function captureForThisTab(): Promise<CaptureForTabResult> {
  return new Promise((resolve) => {
    log('capture-message-sent');
    try {
      chrome.runtime.sendMessage({ type: CAPTURE_FOR_TAB }, (response: CaptureForTabResult) => {
        if (chrome.runtime.lastError) {
          log('workflow-error', {
            stage: 'capture-message',
            error: chrome.runtime.lastError.message ?? 'unknown messaging error',
          });
          resolve({
            ok: false,
            kind: 'extraction-failed',
            error: chrome.runtime.lastError.message ?? 'Background worker unreachable.',
          });
          return;
        }
        if (!response) {
          // A handler that returns nothing leaves the callback with undefined;
          // resolving explicitly stops the workflow hanging forever.
          log('workflow-error', { stage: 'capture-message', error: 'empty response' });
          resolve({
            ok: false,
            kind: 'extraction-failed',
            error: 'The background worker returned no result.',
          });
          return;
        }
        log('capture-message-received', {
          ok: response.ok,
          cues: response.ok ? response.cues.length : 0,
          kind: response.ok ? null : response.kind,
        });
        resolve(response);
      });
    } catch (err) {
      log('workflow-error', {
        stage: 'capture-message',
        error: err instanceof Error ? err.message : String(err),
      });
      resolve({
        ok: false,
        kind: 'extraction-failed',
        error: err instanceof Error ? err.message : String(err),
      });
    }
  });
}

function readVideoTitle(): string | null {
  const heading = document.querySelector('h1.ytd-watch-metadata, h1.title');
  const text = heading?.textContent?.trim();
  if (text) return text;
  return document.title.replace(/\s*-\s*YouTube\s*$/, '').trim() || null;
}

/**
 * The single point where sessions begin and end. Disposal is synchronous and
 * happens *before* the replacement exists, so two sessions can never overlap.
 */
function switchTo(videoId: string | null): void {
  session?.dispose();
  session = null;

  if (videoId === null) return;

  session = new VideoSession(videoId, {
    capture: captureForThisTab,
    readTitle: readVideoTitle,
  });
  void session.init();
}

// --- lifecycle wiring ------------------------------------------------------

let lastVideoId: string | null = null;

function publishStatus(reason: string): void {
  const status = currentStatus();

  if (status.videoId !== lastVideoId) {
    console.log(
      `[ai-subtitle-translator] video changed (${reason}):`,
      lastVideoId,
      '->',
      status.videoId,
    );
    lastVideoId = status.videoId;
    // Video identity changed: tear down the old session before anything new
    // can attach to the new video.
    switchTo(status.videoId);
  }

  chrome.storage.local.set({ watchStatus: status }).catch(() => {
    // The extension context can be invalidated mid-navigation (e.g. reload);
    // this is expected and not worth surfacing as an error.
  });
}

publishStatus('initial-load');

// YouTube's SPA shell dispatches this custom event on every client-side
// navigation between videos. This is the primary detection mechanism.
document.addEventListener('yt-navigate-finish', () => publishStatus('yt-navigate-finish'));

// Defensive fallback in case YouTube renames/removes that event: poll the
// URL at a low frequency so video-ID detection degrades gracefully instead
// of silently going stale.
let lastKnownUrl = location.href;
setInterval(() => {
  if (location.href !== lastKnownUrl) {
    lastKnownUrl = location.href;
    publishStatus('url-poll-fallback');
  }
}, 1000);

// Tab teardown: stop polling and remove our DOM rather than relying on the
// page going away cleanly.
window.addEventListener('pagehide', () => switchTo(null));
