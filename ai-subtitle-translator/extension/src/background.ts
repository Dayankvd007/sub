/**
 * MV3 service worker. Two stateless jobs, nothing more:
 *
 *  1. Run the Phase 0 main-world extractor against a tab and normalize the
 *     result. Two entry points share one implementation: the Phase 0 popup
 *     (active tab, unchanged behaviour) and the Phase 3 in-page control, which
 *     uses `sender.tab.id` so switching tabs mid-capture cannot extract from
 *     the wrong video.
 *
 *  2. Forward one HTTP request to the local backend. The worker holds the
 *     extension origin and host permissions, so it is exempt from the page's
 *     CORS context — which a content script is not.
 *
 * It deliberately owns NO job state and runs NO polling loop: MV3 terminates
 * idle workers, so timers here would stop silently. Lifecycle belongs to the
 * content script's VideoSession.
 */

import { log, redactUrl } from './debug';
import {
  BACKEND_FETCH,
  CAPTURE_FOR_TAB,
  TRANSPORT_ERRORS,
  type BackendFetchMessage,
  type BackendFetchResult,
  type CaptureForTabResult,
} from './messages';
import { extractCaptionsMainWorld, type MainWorldExtractionResult } from './mainWorldExtractor';
import { normalizeJson3ToCues, type Json3Payload } from './normalize';

interface CaptureCaptionsMessage {
  type: 'CAPTURE_CAPTIONS';
}

export interface CaptureFixture {
  video_id: string;
  captured_at: string;
  source_url: string;
  player_response_source: string | null;
  selected_track: { languageCode: string; kind: string; name: string; vssId: string } | null;
  available_tracks: Array<{ languageCode: string; kind: string; name: string; vssId: string }>;
  raw_event_count: number;
  cue_count: number;
  normalization_issues: unknown[];
  cues: Array<{ cue_index: number; start_ms: number; end_ms: number; english_text: string }>;
}

interface CaptureResponse {
  ok: boolean;
  error?: string;
  fixture?: CaptureFixture;
}

export const DEFAULT_BACKEND_BASE_URL = 'http://127.0.0.1:8000';

function isYouTubeWatchUrl(url: string | undefined): url is string {
  if (!url) return false;
  try {
    const parsed = new URL(url);
    const isYouTubeHost = parsed.hostname === 'www.youtube.com' || parsed.hostname === 'youtube.com';
    return isYouTubeHost && parsed.pathname === '/watch';
  } catch {
    return false;
  }
}

async function findActiveYouTubeTab(): Promise<chrome.tabs.Tab> {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab || typeof tab.id !== 'number' || !isYouTubeWatchUrl(tab.url)) {
    throw new Error('The active tab is not a YouTube watch page (https://www.youtube.com/watch?v=...).');
  }
  return tab;
}

/** Inject the unchanged Phase 0 extractor into one specific tab. */
async function runExtraction(tabId: number): Promise<MainWorldExtractionResult | null> {
  const injectionResults = await chrome.scripting.executeScript({
    target: { tabId },
    world: 'MAIN',
    func: extractCaptionsMainWorld,
  });
  return injectionResults[0]?.result ?? null;
}

/**
 * Phase 0 popup path. Behaviour is unchanged: resolve the active tab, capture,
 * store a downloadable fixture.
 */
async function captureCaptions(): Promise<CaptureResponse> {
  const tab = await findActiveYouTubeTab();
  const videoId = new URL(tab.url as string).searchParams.get('v');
  if (!videoId) {
    return { ok: false, error: 'Could not read a video ID ("v" query param) from the active tab URL.' };
  }

  const extraction = await runExtraction(tab.id as number);
  if (!extraction) {
    return { ok: false, error: 'The main-world extractor did not return a result (injection may have failed).' };
  }
  if (!extraction.ok) {
    return { ok: false, error: `[${extraction.stage}] ${extraction.error ?? 'Unknown extraction failure.'}` };
  }

  const { cues, issues } = normalizeJson3ToCues(extraction.raw as Json3Payload);

  const fixture: CaptureFixture = {
    video_id: videoId,
    captured_at: new Date().toISOString(),
    source_url: tab.url as string,
    player_response_source: extraction.playerResponseSource ?? null,
    selected_track: extraction.selectedTrack ?? null,
    available_tracks: extraction.availableTracks ?? [],
    raw_event_count: extraction.rawEventCount ?? 0,
    cue_count: cues.length,
    normalization_issues: issues,
    cues,
  };

  await chrome.storage.local.set({
    [`fixture:${videoId}`]: fixture,
    lastFixtureVideoId: videoId,
  });

  return { ok: true, fixture };
}

/** Stages that mean "this video has no usable English captions", not "we broke". */
const NO_CAPTION_STAGES = new Set(['find-caption-tracks', 'select-english-track']);

/**
 * Phase 3 in-page path. Keyed to the calling tab, and returns cues directly
 * rather than writing a fixture — the content script needs data, not a file.
 */
async function captureForTab(sender: chrome.runtime.MessageSender): Promise<CaptureForTabResult> {
  const tabId = sender.tab?.id;
  const url = sender.tab?.url;
  log('capture-message-received', { hasTabId: typeof tabId === 'number', isWatchPage: isYouTubeWatchUrl(url) });

  if (typeof tabId !== 'number' || !isYouTubeWatchUrl(url)) {
    return { ok: false, kind: 'not-a-watch-page', error: 'The requesting tab is not a YouTube watch page.' };
  }

  const videoId = new URL(url).searchParams.get('v');
  if (!videoId) {
    return { ok: false, kind: 'not-a-watch-page', error: 'No video ID in the requesting tab URL.' };
  }

  let extraction: MainWorldExtractionResult | null;
  try {
    log('extractor-start', { tabId, videoId });
    extraction = await runExtraction(tabId);
  } catch (err) {
    log('workflow-error', {
      stage: 'extractor-injection',
      error: err instanceof Error ? err.message : String(err),
    });
    return {
      ok: false,
      kind: 'extraction-failed',
      error: err instanceof Error ? err.message : String(err),
    };
  }

  if (!extraction) {
    log('workflow-error', { stage: 'extractor', error: 'injection returned no result' });
    return { ok: false, kind: 'extraction-failed', error: 'The main-world extractor returned nothing.' };
  }

  // Never log the fetched URL's query string: it carries YouTube session
  // parameters. Origin and path only.
  log('extractor-result', {
    ok: extraction.ok,
    stage: extraction.stage,
    trackKind: extraction.selectedTrack?.kind ?? null,
    availableTracks: extraction.availableTracks?.length ?? 0,
    rawEvents: extraction.rawEventCount ?? 0,
    url: redactUrl(extraction.fetchedUrl),
    error: extraction.ok ? undefined : extraction.error,
  });

  if (!extraction.ok) {
    return {
      ok: false,
      kind: NO_CAPTION_STAGES.has(extraction.stage) ? 'no-captions' : 'extraction-failed',
      error: `[${extraction.stage}] ${extraction.error ?? 'Unknown extraction failure.'}`,
    };
  }

  const { cues, issues } = normalizeJson3ToCues(extraction.raw as Json3Payload);
  if (cues.length === 0) {
    return { ok: false, kind: 'no-captions', error: 'The caption track produced no usable cues.' };
  }

  return {
    ok: true,
    videoId,
    cues,
    trackKind: extraction.selectedTrack?.kind ?? null,
    droppedEvents: issues.length,
  };
}

async function backendBaseUrl(): Promise<string> {
  try {
    const stored = await chrome.storage.local.get('backendBaseUrl');
    const value = stored.backendBaseUrl;
    if (typeof value === 'string' && value.trim()) {
      return value.trim().replace(/\/+$/, '');
    }
  } catch {
    // Storage is unavailable in some teardown states; the default is fine.
  }
  return DEFAULT_BACKEND_BASE_URL;
}

/** Forward exactly one request. No retries here — retry policy is the caller's. */
async function backendFetch(message: BackendFetchMessage): Promise<BackendFetchResult> {
  const base = await backendBaseUrl();
  if (message.path.startsWith('/jobs') && message.method === 'POST') {
    log('post-jobs-start', { path: message.path });
  }
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), message.timeoutMs);

  try {
    const response = await fetch(`${base}${message.path}`, {
      method: message.method,
      headers: message.body === undefined ? undefined : { 'Content-Type': 'application/json' },
      body: message.body === undefined ? undefined : JSON.stringify(message.body),
      signal: controller.signal,
      // No cookies to or from the local backend; it has no sessions.
      credentials: 'omit',
    });

    const text = await response.text();
    let data: unknown = null;
    if (text) {
      try {
        data = JSON.parse(text);
      } catch {
        return {
          ok: false,
          status: response.status,
          errorCode: TRANSPORT_ERRORS.badResponse,
          message: 'Response body was not JSON.',
        };
      }
    }

    if (!response.ok) {
      const body = data as { error_code?: unknown; message?: unknown } | null;
      return {
        ok: false,
        status: response.status,
        errorCode: typeof body?.error_code === 'string' ? body.error_code : 'http_error',
        message: typeof body?.message === 'string' ? body.message : `HTTP ${response.status}`,
      };
    }

    if (message.path.startsWith('/jobs')) {
      log('post-jobs-result', { method: message.method, path: message.path, status: response.status });
    }
    return { ok: true, status: response.status, data };
  } catch (err) {
    const aborted = err instanceof Error && err.name === 'AbortError';
    return {
      ok: false,
      status: null,
      errorCode: aborted ? TRANSPORT_ERRORS.timeout : TRANSPORT_ERRORS.network,
      message: err instanceof Error ? err.message : String(err),
    };
  } finally {
    clearTimeout(timer);
  }
}

chrome.runtime.onMessage.addListener(
  (message: CaptureCaptionsMessage | { type: string }, sender, sendResponse: (response: unknown) => void) => {
    // Phase 0 popup path — unchanged.
    if (message?.type === 'CAPTURE_CAPTIONS') {
      captureCaptions()
        .then(sendResponse)
        .catch((err: unknown) =>
          sendResponse({ ok: false, error: err instanceof Error ? err.message : String(err) }),
        );
      return true;
    }

    if (message?.type === CAPTURE_FOR_TAB) {
      captureForTab(sender)
        .then(sendResponse)
        .catch((err: unknown) =>
          sendResponse({
            ok: false,
            kind: 'extraction-failed',
            error: err instanceof Error ? err.message : String(err),
          }),
        );
      return true;
    }

    if (message?.type === BACKEND_FETCH) {
      backendFetch(message as BackendFetchMessage)
        .then(sendResponse)
        .catch((err: unknown) =>
          sendResponse({
            ok: false,
            status: null,
            errorCode: TRANSPORT_ERRORS.network,
            message: err instanceof Error ? err.message : String(err),
          }),
        );
      return true;
    }

    return undefined;
  },
);
