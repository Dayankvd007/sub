// @vitest-environment jsdom
import { describe, expect, it, vi } from 'vitest';

import type { TranslatedCue } from '../src/apiTypes';
import {
  SubtitleTrack,
  TRACK_ID,
  pickActiveText,
  supportsVttCue,
  toCueSpec,
  type CueSpec,
} from '../src/subtitleTrack';

function cue(index: number, startMs: number, endMs: number, text = `فارسی ${index}`): TranslatedCue {
  return { cue_index: index, start_ms: startMs, end_ms: endMs, persian_text: text };
}

function spec(id: string, startSec: number, text = `t${id}`): CueSpec {
  return { id, startSec, endSec: startSec + 1, text };
}

describe('toCueSpec', () => {
  it('converts backend milliseconds to media-clock seconds', () => {
    expect(toCueSpec(cue(0, 0, 1200))).toEqual({
      id: '0',
      startSec: 0,
      endSec: 1.2,
      text: 'فارسی 0',
    });
  });

  it('preserves cue_index as the cue id', () => {
    expect(toCueSpec(cue(479, 900_500, 903_000)).id).toBe('479');
  });

  it('keeps sub-second precision', () => {
    const s = toCueSpec(cue(1, 1234, 5678));
    expect(s.startSec).toBeCloseTo(1.234, 6);
    expect(s.endSec).toBeCloseTo(5.678, 6);
  });
});

describe('pickActiveText', () => {
  it('returns null when nothing is active', () => {
    expect(pickActiveText([])).toBeNull();
  });

  it('returns the only active cue', () => {
    expect(pickActiveText([spec('4', 10, 'سلام')])).toBe('سلام');
  });

  it('prefers the most recently started cue when two overlap', () => {
    const text = pickActiveText([spec('1', 10, 'older'), spec('2', 12, 'newer')]);
    expect(text).toBe('newer');
  });

  it('breaks a start-time tie by the higher cue index, not DOM order', () => {
    const ordered = pickActiveText([spec('7', 5, 'seven'), spec('8', 5, 'eight')]);
    const reversed = pickActiveText([spec('8', 5, 'eight'), spec('7', 5, 'seven')]);

    expect(ordered).toBe('eight');
    expect(reversed).toBe('eight');
  });

  it('treats an empty cue as nothing to show', () => {
    expect(pickActiveText([spec('1', 5, '')])).toBeNull();
  });
});

describe('SubtitleTrack DOM lifecycle', () => {
  function video(): HTMLVideoElement {
    document.body.innerHTML = '<div id="movie_player"><video></video></div>';
    return document.querySelector('video')!;
  }

  it('creates a removable <track> element rather than addTextTrack', () => {
    const el = video();
    const addTextTrack = vi.spyOn(el, 'addTextTrack' as never);
    const track = new SubtitleTrack(() => {});

    track.attach(el);

    expect(el.querySelector(`#${TRACK_ID}`)).not.toBeNull();
    // addTextTrack tracks cannot be removed, which leaks across navigations.
    expect(addTextTrack).not.toHaveBeenCalled();
  });

  it('marks the track hidden so the browser never draws it', () => {
    const el = video();
    const track = new SubtitleTrack(() => {});

    track.attach(el);

    const element = el.querySelector(`#${TRACK_ID}`) as HTMLTrackElement;
    expect(element.kind).toBe('subtitles');
    expect(element.srclang).toBe('fa');
    if (track.attached) expect(element.track!.mode).toBe('hidden');
  });

  it('removes the track element on disposal', () => {
    const el = video();
    const track = new SubtitleTrack(() => {});
    track.attach(el);

    track.dispose();

    expect(el.querySelector(`#${TRACK_ID}`)).toBeNull();
    expect(track.attached).toBe(false);
  });

  it('clears any visible text on disposal', () => {
    const el = video();
    const onText = vi.fn();
    const track = new SubtitleTrack(onText);
    track.attach(el);

    track.dispose();

    expect(onText).toHaveBeenLastCalledWith(null);
  });

  it('leaves no track behind after repeated attach/dispose cycles', () => {
    const el = video();

    for (let i = 0; i < 5; i += 1) {
      const track = new SubtitleTrack(() => {});
      track.attach(el);
      track.dispose();
    }

    expect(el.querySelectorAll('track')).toHaveLength(0);
  });

  it('ignores cues added after disposal', () => {
    const el = video();
    const track = new SubtitleTrack(() => {});
    track.attach(el);
    track.dispose();

    expect(track.addCues([cue(0, 0, 1000)])).toBe(0);
  });

  it('is safe to dispose more than once', () => {
    const el = video();
    const track = new SubtitleTrack(() => {});
    track.attach(el);

    expect(() => {
      track.dispose();
      track.dispose();
    }).not.toThrow();
  });

  it('degrades without throwing where VTTCue is unavailable', () => {
    // jsdom has no VTTCue; the extension must not explode in any environment
    // that lacks it, and real Chrome always has it.
    const el = video();
    const track = new SubtitleTrack(() => {});
    track.attach(el);

    expect(() => track.addCues([cue(0, 0, 1000), cue(1, 1000, 2000)])).not.toThrow();
    if (!supportsVttCue()) expect(track.cueCount).toBe(0);
  });
});
