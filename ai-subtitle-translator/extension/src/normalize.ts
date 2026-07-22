/**
 * Pure normalization of a YouTube json3 timed-text payload into the Phase 0
 * cue contract: cue_index, start_ms, end_ms, english_text.
 *
 * Deliberately conservative per the technical architecture doc: this only
 * decodes/validates/orders. Rolling-duplicate removal and linguistic
 * cleanup are Phase 1 (backend) responsibilities, not Phase 0's.
 */

export interface NormalizedCue {
  cue_index: number;
  start_ms: number;
  end_ms: number;
  english_text: string;
}

export interface NormalizeIssue {
  reason: 'missing-start' | 'invalid-timing';
  eventIndex: number;
  detail?: string;
}

export interface NormalizeResult {
  cues: NormalizedCue[];
  issues: NormalizeIssue[];
}

interface Json3Segment {
  utf8?: string;
}

interface Json3Event {
  tStartMs?: number;
  dDurationMs?: number;
  segs?: Json3Segment[];
}

export interface Json3Payload {
  events?: unknown[];
}

function decodeEntities(text: string): string {
  return text
    .replace(/&amp;/g, '&')
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'");
}

export function normalizeJson3ToCues(payload: Json3Payload): NormalizeResult {
  const cues: NormalizedCue[] = [];
  const issues: NormalizeIssue[] = [];
  const events = payload.events ?? [];

  let nextIndex = 0;
  events.forEach((rawEvent, eventIndex) => {
    const event = (rawEvent ?? {}) as Json3Event;
    if (typeof event.tStartMs !== 'number') {
      issues.push({ reason: 'missing-start', eventIndex });
      return;
    }

    // YouTube emits caption-window/position-only events with no segs;
    // these are not spoken cues and are silently skipped (not an issue).
    if (!event.segs || event.segs.length === 0) {
      return;
    }

    const text = decodeEntities(event.segs.map((s) => s.utf8 ?? '').join(''))
      .replace(/\n+/g, ' ')
      .replace(/\s+/g, ' ')
      .trim();
    if (text.length === 0) {
      return;
    }

    const startMs = event.tStartMs;
    const durationMs = typeof event.dDurationMs === 'number' ? event.dDurationMs : 0;
    const endMs = startMs + durationMs;

    if (durationMs <= 0 || endMs <= startMs) {
      issues.push({
        reason: 'invalid-timing',
        eventIndex,
        detail: `start=${startMs} duration=${durationMs}`,
      });
      return;
    }

    cues.push({ cue_index: nextIndex, start_ms: startMs, end_ms: endMs, english_text: text });
    nextIndex += 1;
  });

  return { cues, issues };
}
