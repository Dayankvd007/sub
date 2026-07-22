/**
 * MV3 service worker. Orchestrates one thing: on request from the popup,
 * run the main-world extractor against the active YouTube watch tab,
 * normalize the result into the cue contract, persist it as a fixture in
 * chrome.storage.local, and report back a debuggable result (success with
 * data, or the exact stage/reason it failed at).
 */

import { extractCaptionsMainWorld } from './mainWorldExtractor';
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

async function captureCaptions(): Promise<CaptureResponse> {
  const tab = await findActiveYouTubeTab();
  const videoId = new URL(tab.url as string).searchParams.get('v');
  if (!videoId) {
    return { ok: false, error: 'Could not read a video ID ("v" query param) from the active tab URL.' };
  }

  const injectionResults = await chrome.scripting.executeScript({
    target: { tabId: tab.id as number },
    world: 'MAIN',
    func: extractCaptionsMainWorld,
  });

  const extraction = injectionResults[0]?.result;
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

chrome.runtime.onMessage.addListener(
  (message: CaptureCaptionsMessage, _sender, sendResponse: (response: CaptureResponse) => void) => {
    if (message?.type !== 'CAPTURE_CAPTIONS') {
      return undefined;
    }
    captureCaptions()
      .then(sendResponse)
      .catch((err: unknown) => sendResponse({ ok: false, error: err instanceof Error ? err.message : String(err) }));
    return true; // keep the message channel open for the async response
  },
);
