// @vitest-environment jsdom
import { beforeEach, describe, expect, it } from 'vitest';

import { LINE_CLASS, OVERLAY_ID, RtlOverlay } from '../src/overlay';

function player(): HTMLElement {
  document.body.innerHTML = `
    <div id="movie_player">
      <video></video>
      <div class="ytp-chrome-bottom"></div>
    </div>`;
  return document.getElementById('movie_player')!;
}

function overlayEl(): HTMLElement | null {
  return document.getElementById(OVERLAY_ID);
}

beforeEach(() => {
  document.body.innerHTML = '';
});

describe('placement', () => {
  it('mounts inside the player container so fullscreen carries it along', () => {
    const host = player();
    new RtlOverlay().attach(host);

    expect(overlayEl()).not.toBeNull();
    expect(overlayEl()!.parentElement!.id).toBe('movie_player');
  });

  it('marks itself right-to-left and Persian for correct shaping', () => {
    new RtlOverlay().attach(player());

    expect(overlayEl()!.getAttribute('dir')).toBe('rtl');
    expect(overlayEl()!.getAttribute('lang')).toBe('fa');
  });

  it('starts hidden so nothing floats over the video before the first cue', () => {
    const overlay = new RtlOverlay();
    overlay.attach(player());

    expect(overlay.visible).toBe(false);
    expect(overlayEl()!.hasAttribute('hidden')).toBe(true);
  });

  it('replaces an overlay left behind by a previous session', () => {
    const host = player();
    const stale = document.createElement('div');
    stale.id = OVERLAY_ID;
    stale.textContent = 'stale text';
    host.appendChild(stale);

    new RtlOverlay().attach(host);

    expect(document.querySelectorAll(`#${OVERLAY_ID}`)).toHaveLength(1);
    expect(overlayEl()!.textContent).toBe('');
  });
});

describe('text safety', () => {
  it('renders markup-looking translation as literal text', () => {
    const overlay = new RtlOverlay();
    overlay.attach(player());

    overlay.setText('<img src=x onerror="alert(1)">');

    // The payload must exist as characters, not as a node.
    expect(overlayEl()!.querySelector('img')).toBeNull();
    expect(overlayEl()!.querySelectorAll('*')).toHaveLength(1); // just the line span
    expect(overlay.text).toBe('<img src=x onerror="alert(1)">');
  });

  it('does not execute or parse a script tag in a cue', () => {
    const overlay = new RtlOverlay();
    overlay.attach(player());

    overlay.setText('<script>window.__pwned = true;</script>');

    expect(overlayEl()!.querySelector('script')).toBeNull();
    expect((window as unknown as { __pwned?: boolean }).__pwned).toBeUndefined();
  });

  it('renders ordinary Persian unchanged', () => {
    const overlay = new RtlOverlay();
    overlay.attach(player());

    overlay.setText('سلام و درود بر شما');

    expect(overlay.text).toBe('سلام و درود بر شما');
    expect(overlay.visible).toBe(true);
  });

  it('keeps multi-line cues intact for CSS to wrap', () => {
    const overlay = new RtlOverlay();
    overlay.attach(player());

    overlay.setText('خط اول\nخط دوم');

    expect(overlay.text).toBe('خط اول\nخط دوم');
  });
});

describe('clearing', () => {
  it('hides the overlay when no cue is active', () => {
    const overlay = new RtlOverlay();
    overlay.attach(player());
    overlay.setText('سلام');

    overlay.setText(null);

    expect(overlay.visible).toBe(false);
    expect(overlay.text).toBe('');
  });

  it('treats whitespace-only text as nothing to show', () => {
    const overlay = new RtlOverlay();
    overlay.attach(player());
    overlay.setText('سلام');

    overlay.setText('   \n  ');

    expect(overlay.visible).toBe(false);
  });

  it('clears immediately when subtitles are switched off', () => {
    const overlay = new RtlOverlay();
    overlay.attach(player());
    overlay.setText('سلام');

    overlay.clear();

    expect(overlay.text).toBe('');
    expect(overlay.visible).toBe(false);
  });

  it('never leaves stale text between two cues', () => {
    const overlay = new RtlOverlay();
    overlay.attach(player());

    overlay.setText('اول');
    overlay.setText(null);
    overlay.setText('دوم');

    expect(overlay.text).toBe('دوم');
  });
});

describe('disposal', () => {
  it('removes the overlay and leaves the player intact', () => {
    const host = player();
    const overlay = new RtlOverlay();
    overlay.attach(host);
    overlay.setText('سلام');

    overlay.dispose();

    expect(overlayEl()).toBeNull();
    expect(document.getElementById('movie_player')).not.toBeNull();
    expect(document.querySelector('video')).not.toBeNull();
  });

  it('ignores text written after disposal', () => {
    const overlay = new RtlOverlay();
    overlay.attach(player());
    overlay.dispose();

    overlay.setText('too late');

    expect(overlayEl()).toBeNull();
    expect(overlay.text).toBe('');
  });

  it('does not re-attach after disposal', () => {
    const host = player();
    const overlay = new RtlOverlay();
    overlay.dispose();

    overlay.attach(host);

    expect(overlayEl()).toBeNull();
  });

  it('is safe to dispose repeatedly', () => {
    const overlay = new RtlOverlay();
    overlay.attach(player());

    expect(() => {
      overlay.dispose();
      overlay.dispose();
    }).not.toThrow();
  });
});

describe('structure', () => {
  it('keeps exactly one line node regardless of how often text changes', () => {
    const overlay = new RtlOverlay();
    overlay.attach(player());

    for (let i = 0; i < 50; i += 1) overlay.setText(`cue ${i}`);

    expect(overlayEl()!.querySelectorAll(`.${LINE_CLASS}`)).toHaveLength(1);
  });
});
