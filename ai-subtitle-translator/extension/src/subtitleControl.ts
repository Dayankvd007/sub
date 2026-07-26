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
 * ## Why activation uses capture-phase delegation on the document
 *
 * The first P3-07 gate attempt failed with the control visible but clicks doing
 * nothing. Two YouTube behaviours reproduce that symptom exactly, and both were
 * confirmed in a real Chromium:
 *
 *   1. The player registers **capture-phase** click handlers on `#movie_player`
 *      (click-to-pause and friends) and calls `stopPropagation()`. A capture
 *      listener on an ancestor runs *before* the target phase, so a listener
 *      bound directly to our button never fires at all.
 *   2. YouTube re-renders the control bar and may **replace our node with a
 *      clone**. A clone carries the id and the styling but none of the
 *      listeners, so the visible button is inert.
 *
 * One mechanism defeats both: a single capture-phase listener on `document`,
 * which runs before any listener on `#movie_player` because `document` is
 * higher in the tree, and which matches by id rather than by node identity.
 * For the same reason `render()` always re-resolves the live node by id instead
 * of trusting a stored reference, so state updates reach a clone too.
 */

import { describeControl, type ControlContext, type ControlState } from './controlState';
import { log } from './debug';

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
  private doc: Document | null = null;
  private fallbackHost: HTMLElement | null = null;
  private observer: MutationObserver | null = null;
  private recheckQueued = false;
  private disposed = false;
  private placement: ControlPlacement = 'none';
  private state: ControlState = 'ready';
  private context: ControlContext = {};

  constructor(private readonly onActivate: () => void) {}

  get currentPlacement(): ControlPlacement {
    return this.placement;
  }

  /**
   * Insert the control and start listening for activation. Safe to call
   * repeatedly: an existing control is adopted rather than duplicated.
   */
  attach(doc: Document = document, timeoutMs = 8000): void {
    if (this.disposed) return;
    this.doc = doc;

    // Registered once, on the document, in the capture phase — see the note at
    // the top of this file.
    doc.addEventListener('click', this.handleDocumentClick, true);

    const inserted = this.tryInsert(doc);

    // Keep watching after the first insert: YouTube rebuilds the player chrome
    // on navigation and can drop our node. Work is coalesced to one check per
    // task, because this observer sees every mutation on a very busy page.
    const observer = new MutationObserver(() => this.queueRecheck(doc));
    observer.observe(doc.body ?? doc.documentElement, { childList: true, subtree: true });
    this.observer = observer;

    if (!inserted) {
      setTimeout(() => {
        if (this.disposed || this.placement !== 'none') return;
        this.insertFallback(doc);
      }, timeoutMs);
    }
  }

  setState(state: ControlState, context: ControlContext = {}): void {
    this.state = state;
    this.context = context;
    this.render();
  }

  dispose(): void {
    this.disposed = true;
    this.doc?.removeEventListener('click', this.handleDocumentClick, true);
    this.observer?.disconnect();
    this.observer = null;
    this.liveButton()?.remove();
    // Only the wrapper we created ourselves is removed; YouTube's own nodes
    // are never touched.
    this.fallbackHost?.remove();
    this.fallbackHost = null;
    this.placement = 'none';
    this.doc = null;
  }

  // --- internals ---------------------------------------------------------

  /**
   * Always resolved by id, never cached: if YouTube swapped our node for a
   * clone, the clone is the one the user can see and the one to update.
   */
  private liveButton(): HTMLButtonElement | null {
    return (this.doc?.getElementById(CONTROL_ID) as HTMLButtonElement | null) ?? null;
  }

  private handleDocumentClick = (event: Event): void => {
    if (this.disposed) return;

    const target = event.target;
    if (!(target instanceof Element)) return;
    const button = target.closest(`#${CONTROL_ID}`);
    if (!button) return;

    if ((button as HTMLButtonElement).disabled) return;

    // Our button, our event: stop YouTube from also treating it as a click on
    // the player surface (which would toggle play/pause).
    event.stopPropagation();
    event.preventDefault();

    log('control-click', { state: this.state, placement: this.placement });
    this.onActivate();
  };

  private queueRecheck(doc: Document): void {
    if (this.disposed || this.recheckQueued) return;
    this.recheckQueued = true;
    setTimeout(() => {
      this.recheckQueued = false;
      if (this.disposed) return;
      // Re-insert only if our control actually went away.
      if (!doc.getElementById(CONTROL_ID)) {
        this.tryInsert(doc);
      }
    }, 0);
  }

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
    // Duplicate prevention: adopt the slot, never add a second node.
    doc.getElementById(CONTROL_ID)?.remove();

    const button = doc.createElement('button');
    button.id = CONTROL_ID;
    button.type = 'button';
    if (nativeStyling) button.classList.add('ytp-button');
    button.classList.add('ai-subtitle-toggle');

    if (placement === 'primary') {
      // Left of the settings gear, matching where caption controls live.
      container.insertBefore(button, container.firstChild);
    } else {
      container.appendChild(button);
    }

    this.placement = placement;
    this.render();
  }

  private render(): void {
    const button = this.liveButton();
    if (!button) return;

    const view = describeControl(this.state, this.context);
    // textContent, never innerHTML — this string is ours today but the rule is
    // absolute across the extension.
    button.textContent = view.label;
    button.title = view.title;
    button.setAttribute('aria-label', view.title);
    button.disabled = view.disabled;
    button.setAttribute('aria-pressed', String(view.active));
    button.dataset.state = this.state;
    button.classList.toggle('ai-subtitle-toggle--active', view.active);
  }
}
