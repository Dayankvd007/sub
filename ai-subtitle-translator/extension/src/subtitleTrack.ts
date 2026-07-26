/**
 * The timing layer (roadmap P3-05).
 *
 * A hidden TextTrack is the *only* clock: cue activation follows the video
 * element's media time, so play, pause, seeking, and playback-rate changes all
 * work with no code of ours involved — and no rate multiplier must ever be
 * applied. The track is `hidden` rather than `showing` so the browser never
 * draws it; presentation belongs to the RTL overlay.
 *
 * The track is created from a real <track> element rather than
 * `video.addTextTrack()`, because a track added that way **cannot be removed** —
 * there is no removeTextTrack API. YouTube reuses the same <video> element
 * across SPA navigations, so addTextTrack would accumulate an orphan track per
 * video watched. Removing the element removes the track with it.
 */

import type { TranslatedCue } from './apiTypes';

export const TRACK_LABEL = 'Persian (AI)';
export const TRACK_ID = 'ai-subtitle-track';

export interface CueSpec {
  /** The backend cue_index as a string — identity survives into the DOM. */
  id: string;
  startSec: number;
  endSec: number;
  text: string;
}

/** Backend milliseconds -> media-clock seconds, preserving cue identity. */
export function toCueSpec(cue: TranslatedCue): CueSpec {
  return {
    id: String(cue.cue_index),
    startSec: cue.start_ms / 1000,
    endSec: cue.end_ms / 1000,
    text: cue.persian_text,
  };
}

/**
 * Choose which of several simultaneously active cues to display.
 *
 * Auto-captions can overlap, and DOM order must never decide by accident: the
 * most recently started cue wins, ties broken by the higher cue index.
 */
export function pickActiveText(active: readonly CueSpec[]): string | null {
  let best: CueSpec | null = null;

  for (const candidate of active) {
    if (best === null || candidate.startSec > best.startSec) {
      best = candidate;
      continue;
    }
    if (candidate.startSec === best.startSec) {
      const a = Number(candidate.id);
      const b = Number(best.id);
      if (Number.isFinite(a) && Number.isFinite(b) && a > b) best = candidate;
    }
  }

  if (best === null) return null;
  return best.text.length > 0 ? best.text : null;
}

/** True when the environment can actually build cues (jsdom cannot). */
export function supportsVttCue(): boolean {
  return typeof VTTCue !== 'undefined';
}

export class SubtitleTrack {
  private trackElement: HTMLTrackElement | null = null;
  private textTrack: TextTrack | null = null;
  private readonly added = new Set<number>();
  private disposed = false;

  constructor(private readonly onActiveText: (text: string | null) => void) {}

  get cueCount(): number {
    return this.added.size;
  }

  get attached(): boolean {
    return this.textTrack !== null;
  }

  /** Create the hidden track on this video element. Safe to call once per session. */
  attach(video: HTMLVideoElement): boolean {
    if (this.disposed || this.textTrack) return this.textTrack !== null;

    const element = video.ownerDocument.createElement('track');
    element.id = TRACK_ID;
    element.kind = 'subtitles';
    element.label = TRACK_LABEL;
    element.srclang = 'fa';
    element.default = false;
    video.appendChild(element);
    // Recorded before the capability check so disposal always removes it.
    this.trackElement = element;

    const track = element.track ?? null;
    if (!track) {
      // No TextTrack implementation (jsdom). Real Chrome always supplies one;
      // keeping the element means dispose() stays symmetric either way.
      return false;
    }

    // hidden: activation and cuechange fire, but the browser draws nothing.
    track.mode = 'hidden';
    track.addEventListener('cuechange', this.handleCueChange);
    this.textTrack = track;
    return true;
  }

  /**
   * Add newly validated cues. Adding to a live track takes effect immediately,
   * including for regions the playhead has already passed.
   */
  addCues(cues: readonly TranslatedCue[]): number {
    if (this.disposed || !this.textTrack || !supportsVttCue()) return 0;

    let added = 0;
    for (const cue of cues) {
      // Second line of defence behind JobController's dedup set: a cue index
      // is added to the track at most once, ever.
      if (this.added.has(cue.cue_index)) continue;

      const spec = toCueSpec(cue);
      if (!(spec.endSec > spec.startSec)) continue;

      try {
        const vtt = new VTTCue(spec.startSec, spec.endSec, spec.text);
        vtt.id = spec.id;
        this.textTrack.addCue(vtt);
        this.added.add(cue.cue_index);
        added += 1;
      } catch {
        // One malformed cue must not abort the rest of the batch.
      }
    }
    return added;
  }

  /** Clear the visible line without tearing the track down. */
  clearActiveText(): void {
    this.onActiveText(null);
  }

  /** Remove the track, its cues, and its listener completely. */
  dispose(): void {
    this.disposed = true;

    if (this.textTrack) {
      this.textTrack.removeEventListener('cuechange', this.handleCueChange);
      try {
        // Belt-and-braces in case anything still holds a track reference.
        const existing = Array.from(this.textTrack.cues ?? []);
        for (const cue of existing) this.textTrack.removeCue(cue);
        this.textTrack.mode = 'disabled';
      } catch {
        // Track already detached; nothing to undo.
      }
    }

    this.trackElement?.remove();
    this.trackElement = null;
    this.textTrack = null;
    this.added.clear();
    this.onActiveText(null);
  }

  private handleCueChange = (): void => {
    if (this.disposed || !this.textTrack) return;

    const active: CueSpec[] = [];
    for (const cue of Array.from(this.textTrack.activeCues ?? [])) {
      const vtt = cue as VTTCue;
      active.push({
        id: vtt.id,
        startSec: vtt.startTime,
        endSec: vtt.endTime,
        text: typeof vtt.text === 'string' ? vtt.text : '',
      });
    }

    this.onActiveText(pickActiveText(active));
  };
}
