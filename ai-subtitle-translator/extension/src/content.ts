/**
 * Isolated-world content script.
 * Responsibility: detect YouTube watch pages and the active video ID,
 * including changes caused by YouTube's single-page-app navigation, and
 * publish that status to chrome.storage.local so the popup can display it
 * without needing the page to be freshly reloaded.
 *
 * This script deliberately does NOT touch captions. Caption extraction is
 * a separate, on-demand step performed by the main-world extractor when the
 * user clicks "Capture Captions" in the popup.
 */

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

let lastVideoId: string | null = null;

function publishStatus(reason: string): void {
  const status = currentStatus();
  if (status.videoId !== lastVideoId) {
    console.log(`[Phase0 caption experiment] video changed (${reason}):`, lastVideoId, '->', status.videoId);
    lastVideoId = status.videoId;
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
