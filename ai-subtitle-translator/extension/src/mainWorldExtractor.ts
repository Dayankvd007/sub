/**
 * Runs inside the PAGE's own main execution world via
 * chrome.scripting.executeScript({ world: 'MAIN', func: extractCaptionsMainWorld }).
 *
 * IMPORTANT: this function is serialized (Function.prototype.toString) and
 * re-executed in the page context. It must be fully self-contained — no
 * references to outer module bindings, imports, or closures — and must only
 * return plain JSON-serializable data. This is also the security boundary
 * described in the architecture doc: only the sanitized return value below
 * (track metadata + the timed-text payload) ever leaves the page context.
 * Cookies, session tokens, and unrelated page data are never read or
 * returned.
 */

export interface CaptionTrackSummary {
  languageCode: string;
  kind: string;
  name: string;
  vssId: string;
}

export interface MainWorldExtractionResult {
  ok: boolean;
  stage: string;
  error?: string;
  playerResponseSource?: 'ytInitialPlayerResponse' | 'getPlayerResponse' | null;
  availableTracks?: CaptionTrackSummary[];
  selectedTrack?: CaptionTrackSummary | null;
  fetchedUrl?: string;
  rawEventCount?: number;
  raw?: unknown;
}

export async function extractCaptionsMainWorld(): Promise<MainWorldExtractionResult> {
  type RawCaptionTrack = {
    baseUrl?: string;
    languageCode?: string;
    kind?: string;
    vssId?: string;
    name?: { simpleText?: string; runs?: Array<{ text?: string }> };
  };

  function trackName(track: RawCaptionTrack): string {
    if (track.name?.simpleText) return track.name.simpleText;
    if (track.name?.runs) return track.name.runs.map((r) => r.text ?? '').join('');
    return '';
  }

  function summarize(track: RawCaptionTrack): CaptionTrackSummary {
    return {
      languageCode: track.languageCode ?? '',
      kind: track.kind ?? '',
      name: trackName(track),
      vssId: track.vssId ?? '',
    };
  }

  // Step 1: locate the player response object. YouTube exposes this two
  // different ways depending on load path; try both and record which one
  // actually worked so the experiment notes can say so with evidence.
  let playerResponse: unknown = null;
  let playerResponseSource: MainWorldExtractionResult['playerResponseSource'] = null;

  const globalAny = window as unknown as {
    ytInitialPlayerResponse?: unknown;
  };

  if (globalAny.ytInitialPlayerResponse) {
    playerResponse = globalAny.ytInitialPlayerResponse;
    playerResponseSource = 'ytInitialPlayerResponse';
  } else {
    const moviePlayer = document.getElementById('movie_player') as
      | (HTMLElement & { getPlayerResponse?: () => unknown })
      | null;
    if (moviePlayer && typeof moviePlayer.getPlayerResponse === 'function') {
      try {
        playerResponse = moviePlayer.getPlayerResponse();
        playerResponseSource = 'getPlayerResponse';
      } catch (err) {
        return {
          ok: false,
          stage: 'find-player-response',
          error: `#movie_player.getPlayerResponse() threw: ${String(err)}`,
        };
      }
    }
  }

  if (!playerResponse || typeof playerResponse !== 'object') {
    return {
      ok: false,
      stage: 'find-player-response',
      error:
        'Could not find window.ytInitialPlayerResponse or a working #movie_player.getPlayerResponse(). Is this a YouTube watch page with the player loaded?',
    };
  }

  // Step 2: pull the caption track list out of the player response.
  const captionTracks = (
    playerResponse as {
      captions?: {
        playerCaptionsTracklistRenderer?: { captionTracks?: RawCaptionTrack[] };
      };
    }
  ).captions?.playerCaptionsTracklistRenderer?.captionTracks;

  if (!captionTracks || captionTracks.length === 0) {
    return {
      ok: false,
      stage: 'find-caption-tracks',
      error: 'This video has no captionTracks in its player response (no captions available at all).',
      playerResponseSource,
      availableTracks: [],
    };
  }

  const availableTracks = captionTracks.map(summarize);

  // Step 3: select the best English track — prefer a manually authored
  // track over an auto-generated ("asr") one.
  const manualEnglish = captionTracks.find(
    (t) => (t.languageCode ?? '').toLowerCase().startsWith('en') && t.kind !== 'asr',
  );
  const autoEnglish = captionTracks.find(
    (t) => (t.languageCode ?? '').toLowerCase().startsWith('en') && t.kind === 'asr',
  );
  const chosen = manualEnglish ?? autoEnglish ?? null;

  if (!chosen || !chosen.baseUrl) {
    return {
      ok: false,
      stage: 'select-english-track',
      error: 'No English (manual or auto-generated) caption track with a usable baseUrl was found.',
      playerResponseSource,
      availableTracks,
    };
  }

  const selectedTrack = summarize(chosen);

  // Step 4: fetch the structured (json3) timed-text payload using the
  // page's own fetch, so it naturally carries whatever session context
  // YouTube itself requires. Only this parsed payload is returned — no
  // request headers, cookies, or credentials are read or forwarded.
  const separator = chosen.baseUrl.includes('?') ? '&' : '?';
  const fetchUrl = `${chosen.baseUrl}${separator}fmt=json3`;

  let response: Response;
  try {
    response = await fetch(fetchUrl, { credentials: 'include' });
  } catch (err) {
    return {
      ok: false,
      stage: 'fetch-timedtext',
      error: `fetch() threw: ${String(err)}`,
      playerResponseSource,
      availableTracks,
      selectedTrack,
      fetchedUrl: fetchUrl,
    };
  }

  if (!response.ok) {
    return {
      ok: false,
      stage: 'fetch-timedtext',
      error: `HTTP ${response.status} ${response.statusText}`,
      playerResponseSource,
      availableTracks,
      selectedTrack,
      fetchedUrl: fetchUrl,
    };
  }

  let parsed: unknown;
  try {
    parsed = await response.json();
  } catch (err) {
    return {
      ok: false,
      stage: 'parse-json3',
      error: `Response was not valid JSON: ${String(err)}`,
      playerResponseSource,
      availableTracks,
      selectedTrack,
      fetchedUrl: fetchUrl,
    };
  }

  const events = (parsed as { events?: unknown[] })?.events;
  if (!Array.isArray(events)) {
    return {
      ok: false,
      stage: 'validate-json3-shape',
      error: 'Parsed timed-text response has no events[] array.',
      playerResponseSource,
      availableTracks,
      selectedTrack,
      fetchedUrl: fetchUrl,
    };
  }

  return {
    ok: true,
    stage: 'complete',
    playerResponseSource,
    availableTracks,
    selectedTrack,
    fetchedUrl: fetchUrl,
    rawEventCount: events.length,
    raw: parsed,
  };
}
