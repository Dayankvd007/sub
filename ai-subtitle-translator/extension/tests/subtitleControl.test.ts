// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { CONTROL_ID, FALLBACK_CONTAINER_ID, SubtitleControl } from '../src/subtitleControl';

/**
 * Every control is disposed after its test. An undisposed control keeps a live
 * MutationObserver and a document click listener, and would go on re-inserting
 * its button into the next test's DOM — which is exactly what it is supposed to
 * do in production, where the owning VideoSession always disposes it.
 */
const created: SubtitleControl[] = [];

function newControl(onActivate: () => void = () => {}): SubtitleControl {
  const control = new SubtitleControl(onActivate);
  created.push(control);
  return control;
}

function playerMarkup(): void {
  document.body.innerHTML = `
    <div id="movie_player">
      <div class="ytp-chrome-bottom">
        <div class="ytp-right-controls"><button class="ytp-settings-button"></button></div>
      </div>
    </div>`;
}

function pageWithoutPlayerControls(): void {
  document.body.innerHTML = `
    <ytd-watch-metadata><div id="title"><h1>A video</h1></div></ytd-watch-metadata>`;
}

function control(): HTMLButtonElement | null {
  return document.getElementById(CONTROL_ID) as HTMLButtonElement | null;
}

beforeEach(() => {
  document.body.innerHTML = '';
  vi.useRealTimers();
});

afterEach(() => {
  while (created.length) created.pop()?.dispose();
  document.body.innerHTML = '';
});

describe('primary placement', () => {
  it('inserts one control into YouTube\'s right-hand control bar', () => {
    playerMarkup();
    const ctl = newControl();

    ctl.attach();

    expect(ctl.currentPlacement).toBe('primary');
    const button = control();
    expect(button).not.toBeNull();
    expect(button!.parentElement!.className).toBe('ytp-right-controls');
    // Native styling, so it looks like part of the player rather than an add-on.
    expect(button!.classList.contains('ytp-button')).toBe(true);
  });

  it('sits before the settings button rather than after it', () => {
    playerMarkup();
    newControl().attach();

    const bar = document.querySelector('.ytp-right-controls')!;
    expect(bar.firstElementChild!.id).toBe(CONTROL_ID);
  });
});

describe('duplicate prevention', () => {
  it('never creates a second control however many times attach runs', () => {
    playerMarkup();
    const ctl = newControl();

    ctl.attach();
    ctl.attach();
    ctl.attach();

    expect(document.querySelectorAll(`#${CONTROL_ID}`)).toHaveLength(1);
  });

  it('adopts a control left behind by a previous session', () => {
    playerMarkup();
    const stale = document.createElement('button');
    stale.id = CONTROL_ID;
    stale.textContent = 'stale';
    document.querySelector('.ytp-right-controls')!.appendChild(stale);

    newControl().attach();

    const buttons = document.querySelectorAll(`#${CONTROL_ID}`);
    expect(buttons).toHaveLength(1);
    expect(buttons[0]?.textContent).not.toBe('stale');
  });
});

describe('deferred insertion', () => {
  it('waits for the player chrome to appear', async () => {
    document.body.innerHTML = '<div id="movie_player"></div>';
    const ctl = newControl();

    ctl.attach(document, 60_000);
    expect(control()).toBeNull();

    // YouTube builds its control bar asynchronously.
    const bar = document.createElement('div');
    bar.className = 'ytp-right-controls';
    document.getElementById('movie_player')!.appendChild(bar);

    await vi.waitFor(() => expect(control()).not.toBeNull());
    expect(ctl.currentPlacement).toBe('primary');
  });
});

describe('fallback placement', () => {
  it('falls back near the title when the control bar never appears', async () => {
    vi.useFakeTimers();
    pageWithoutPlayerControls();
    const ctl = newControl();

    ctl.attach(document, 1000);
    expect(control()).toBeNull();
    vi.advanceTimersByTime(1001);

    expect(ctl.currentPlacement).toBe('fallback');
    expect(control()).not.toBeNull();
    expect(document.getElementById(FALLBACK_CONTAINER_ID)).not.toBeNull();
    // Minimal by design: exactly one wrapper, exactly one button.
    expect(document.querySelectorAll(`#${FALLBACK_CONTAINER_ID} button`)).toHaveLength(1);
  });

  it('prefers the real control bar and never falls back once inserted', async () => {
    vi.useFakeTimers();
    playerMarkup();
    const ctl = newControl();

    ctl.attach(document, 1000);
    vi.advanceTimersByTime(5000);

    expect(ctl.currentPlacement).toBe('primary');
    expect(document.getElementById(FALLBACK_CONTAINER_ID)).toBeNull();
  });

  it('stays silent when there is nowhere safe to attach', async () => {
    vi.useFakeTimers();
    document.body.innerHTML = '<div>unrecognisable page</div>';
    const ctl = newControl();

    ctl.attach(document, 1000);
    vi.advanceTimersByTime(1001);

    // Better no control than inventing UI in an unknown layout.
    expect(control()).toBeNull();
    expect(ctl.currentPlacement).toBe('none');
  });
});

describe('state rendering', () => {
  it('exposes state through data attributes and accessible names', () => {
    playerMarkup();
    const ctl = newControl();
    ctl.attach();

    ctl.setState('translating', { percent: 40 });
    expect(control()!.dataset.state).toBe('translating');
    expect(control()!.textContent).toBe('FA 40%');
    expect(control()!.getAttribute('aria-pressed')).toBe('true');

    ctl.setState('no-captions');
    expect(control()!.disabled).toBe(true);
    expect(control()!.getAttribute('aria-label')).toContain('No English captions');
  });

  it('writes label text with textContent, never as markup', () => {
    playerMarkup();
    const ctl = newControl();
    ctl.attach();

    ctl.setState('completed', { showing: true });
    expect(control()!.querySelector('*')).toBeNull();
  });
});

describe('activation', () => {
  it('invokes the callback on click', () => {
    playerMarkup();
    const onActivate = vi.fn();
    newControl(onActivate).attach();

    control()!.click();

    expect(onActivate).toHaveBeenCalledTimes(1);
  });

  it('goes silent after disposal', () => {
    playerMarkup();
    const onActivate = vi.fn();
    const ctl = newControl(onActivate);
    ctl.attach();
    const button = control()!;

    ctl.dispose();
    button.click();

    expect(onActivate).not.toHaveBeenCalled();
  });
});

describe('disposal', () => {
  it('removes every node it created', () => {
    playerMarkup();
    const ctl = newControl();
    ctl.attach();

    ctl.dispose();

    expect(control()).toBeNull();
    expect(document.getElementById(FALLBACK_CONTAINER_ID)).toBeNull();
    // YouTube's own nodes are untouched.
    expect(document.querySelector('.ytp-right-controls')).not.toBeNull();
    expect(document.querySelector('.ytp-settings-button')).not.toBeNull();
  });

  it('removes the fallback wrapper too', () => {
    vi.useFakeTimers();
    pageWithoutPlayerControls();
    const ctl = newControl();
    ctl.attach(document, 100);
    vi.advanceTimersByTime(101);

    ctl.dispose();

    expect(document.getElementById(FALLBACK_CONTAINER_ID)).toBeNull();
    expect(document.querySelector('ytd-watch-metadata #title')).not.toBeNull();
  });

  it('does not insert anything after disposal, even if the player appears later', async () => {
    document.body.innerHTML = '<div id="movie_player"></div>';
    const ctl = newControl();
    ctl.attach(document, 60_000);

    ctl.dispose();
    const bar = document.createElement('div');
    bar.className = 'ytp-right-controls';
    document.getElementById('movie_player')!.appendChild(bar);
    await new Promise((r) => setTimeout(r, 20));

    expect(control()).toBeNull();
  });
});

describe('surviving YouTube interference (P3-07 gate-failure regression)', () => {
  it('fires even when an ancestor stops the click in the capture phase', () => {
    playerMarkup();
    const onActivate = vi.fn();
    newControl(onActivate).attach();

    // YouTube's player does exactly this for click-to-pause. A capture-phase
    // listener on an ancestor runs *before* the target phase, so a listener
    // bound to the button itself would never fire.
    document
      .getElementById('movie_player')!
      .addEventListener('click', (e) => e.stopPropagation(), true);

    control()!.click();

    expect(onActivate).toHaveBeenCalledTimes(1);
  });

  it('fires after YouTube replaces the control with a listener-less clone', () => {
    playerMarkup();
    const onActivate = vi.fn();
    newControl(onActivate).attach();

    const original = control()!;
    original.replaceWith(original.cloneNode(true));

    control()!.click();

    expect(onActivate).toHaveBeenCalledTimes(1);
  });

  it('still renders state onto a cloned node', () => {
    playerMarkup();
    const ctl = newControl();
    ctl.attach();
    const original = control()!;
    original.replaceWith(original.cloneNode(true));

    ctl.setState('translating', { percent: 30 });

    // The visible node is the clone, so that is the one that must update.
    expect(control()!.dataset.state).toBe('translating');
    expect(control()!.textContent).toBe('FA 30%');
  });

  it('re-inserts the control if YouTube removes it entirely', async () => {
    playerMarkup();
    newControl().attach();
    expect(control()).not.toBeNull();

    document.querySelector('.ytp-right-controls')!.innerHTML = '';
    expect(control()).toBeNull();

    await vi.waitFor(() => expect(control()).not.toBeNull());
  });

  it('does not act on clicks elsewhere in the player', () => {
    playerMarkup();
    const onActivate = vi.fn();
    newControl(onActivate).attach();

    (document.querySelector('.ytp-settings-button') as HTMLElement).click();

    expect(onActivate).not.toHaveBeenCalled();
  });

  it('ignores clicks once disabled', () => {
    playerMarkup();
    const onActivate = vi.fn();
    const ctl = newControl(onActivate);
    ctl.attach();
    ctl.setState('no-captions');

    control()!.click();

    expect(onActivate).not.toHaveBeenCalled();
  });
});
