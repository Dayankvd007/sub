/**
 * The in-player subtitle control (roadmap P3-01).
 *
 * Primary placement is YouTube's own right-hand control bar, so the button
 * inherits native styling and rides along into fullscreen with the player. If
 * that container cannot be found — YouTube renames things without notice — a
 * deliberately minimal fallback is inserted near the video title so the
 * workflow stays reachable. The fallback is one button in one wrapper; it is
 * not allowed to grow into a second UI system.
 *
 * Duplicate prevention is layered: a fixed element id is checked before every
 * insert, and the owning VideoSession removes the node on disposal.
 */

import { describeControl, type ControlContext, type ControlState } from './controlState';

export const CONTROL_ID = 'ai-subtitle-toggle';
export const FALLBACK_CONTAINER_ID = 'ai-subtitle-fallback-host';

const PRIMARY_SELECTOR = '.ytp-right-controls';

/** Checked in order; the first that exists hosts the fallback control. */
const FALLBACK_SELECTORS = [
  'ytd-watch-metadata #title',
  '#above-the-fold #title',
  '#info-contents',
];

export type ControlPlacement = 'primary' | 'fallback' | 'none';

export class SubtitleControl {
  private button: HTMLButtonElement | null = null;
  private fallbackHost: HTMLElement | null = null;
  private observer: MutationObserver | null = null;
  private disposed = false;
  private placement: ControlPlacement = 'none';
  private state: ControlState = 'ready';
  private context: ControlContext = {};

  constructor(private readonly onActivate: () => void) {}

  get currentPlacement(): ControlPlacement {
    return this.placement;
  }

  /**
   * Insert the control, waiting for YouTube's control bar to appear before
   * falling back. Safe to call repeatedly: an existing control is reused.
   */
  attach(doc: Document = document, timeoutMs = 8000): void {
    if (this.disposed) return;

    if (this.tryInsert(doc)) return;

    // The player chrome is built asynchronously, so watch for it rather than
    // guessing a delay. Falls back once the wait is clearly hopeless.
    const observer = new MutationObserver(() => {
      if (this.disposed) return;
      if (this.tryInsert(doc)) {
        observer.disconnect();
        this.observer = null;
      }
    });
    observer.observe(doc.body ?? doc.documentElement, { childList: true, subtree: true });
    this.observer = observer;

    setTimeout(() => {
      if (this.disposed || this.placement !== 'none') return;
      this.observer?.disconnect();
      this.observer = null;
      this.insertFallback(doc);
    }, timeoutMs);
  }

  setState(state: ControlState, context: ControlContext = {}): void {
    this.state = state;
    this.context = context;
    this.render();
  }

  dispose(): void {
    this.disposed = true;
    this.observer?.disconnect();
    this.observer = null;
    this.button?.remove();
    this.button = null;
    // Only the wrapper we created ourselves is removed; YouTube's own nodes
    // are never touched.
    this.fallbackHost?.remove();
    this.fallbackHost = null;
    this.placement = 'none';
  }

  // --- internals ---------------------------------------------------------

  private tryInsert(doc: Document): boolean {
    const container = doc.querySelector(PRIMARY_SELECTOR);
    if (!container) return false;
    this.mount(doc, container, 'primary', true);
    return true;
  }

  private insertFallback(doc: Document): void {
    const host = FALLBACK_SELECTORS.map((sel) => doc.querySelector(sel)).find(Boolean);
    if (!host) return; // Nothing to attach to; stay silent rather than invent UI.

    const wrapper = doc.createElement('div');
    wrapper.id = FALLBACK_CONTAINER_ID;
    wrapper.style.cssText = 'display:inline-flex;align-items:center;margin-inline-start:8px;';
    host.appendChild(wrapper);
    this.fallbackHost = wrapper;
    this.mount(doc, wrapper, 'fallback', false);
  }

  private mount(
    doc: Document,
    container: Element,
    placement: ControlPlacement,
    nativeStyling: boolean,
  ): void {
    // Layer one of duplicate prevention: adopt an existing node instead of
    // adding a second one.
    const existing = doc.getElementById(CONTROL_ID);
    if (existing) existing.remove();

    const button = doc.createElement('button');
    button.id = CONTROL_ID;
    button.type = 'button';
    if (nativeStyling) button.classList.add('ytp-button');
    button.classList.add('ai-subtitle-toggle');
    button.addEventListener('click', this.handleClick);

    if (placement === 'primary') {
      // Left of the settings gear, matching where caption controls live.
      container.insertBefore(button, container.firstChild);
    } else {
      container.appendChild(button);
    }

    this.button = button;
    this.placement = placement;
    this.render();
  }

  private handleClick = (): void => {
    if (this.disposed) return;
    this.onActivate();
  };

  private render(): void {
    if (!this.button) return;
    const view = describeControl(this.state, this.context);
    // textContent, never innerHTML — this string is ours today but the rule is
    // absolute across the extension.
    this.button.textContent = view.label;
    this.button.title = view.title;
    this.button.setAttribute('aria-label', view.title);
    this.button.disabled = view.disabled;
    this.button.setAttribute('aria-pressed', String(view.active));
    this.button.dataset.state = this.state;
    this.button.classList.toggle('ai-subtitle-toggle--active', view.active);
  }
}
