/**
 * The Persian subtitle overlay (roadmap P3-06).
 *
 * Deliberately dumb: it renders whatever text the timing layer says is active
 * and nothing else. It makes no timing decisions and holds no cue state.
 *
 * Placement is inside #movie_player, which is the element YouTube makes
 * fullscreen — so fullscreen, theater mode, and player resizing need no
 * handling here at all, and the layout rules in overlay.css are percentage
 * based rather than pixel based for the same reason.
 *
 * Text is written with textContent, never innerHTML. Subtitle text is model
 * output: it is data, never markup.
 */

export const OVERLAY_ID = 'ai-subtitle-overlay';
export const LINE_CLASS = 'ai-subtitle-line';

export class RtlOverlay {
  private container: HTMLElement | null = null;
  private line: HTMLElement | null = null;
  private disposed = false;

  get attached(): boolean {
    return this.container !== null;
  }

  /** Current visible text, for tests and debugging. */
  get text(): string {
    return this.line?.textContent ?? '';
  }

  get visible(): boolean {
    return this.container !== null && !this.container.hasAttribute('hidden');
  }

  /**
   * Mount inside the player container. Safe to call repeatedly: an overlay
   * left behind by a previous session is replaced, never duplicated.
   */
  attach(player: HTMLElement): void {
    if (this.disposed) return;

    const doc = player.ownerDocument;
    doc.getElementById(OVERLAY_ID)?.remove();

    const container = doc.createElement('div');
    container.id = OVERLAY_ID;
    container.setAttribute('hidden', '');
    // Announce updates without stealing focus or reading the whole player.
    container.setAttribute('aria-live', 'polite');
    container.setAttribute('dir', 'rtl');
    container.setAttribute('lang', 'fa');

    const line = doc.createElement('span');
    line.className = LINE_CLASS;
    container.appendChild(line);

    player.appendChild(container);
    this.container = container;
    this.line = line;
  }

  /**
   * Show one cue, or clear the overlay when there is nothing active.
   *
   * Hidden rather than empty, so no translucent box floats over the video
   * between cues.
   */
  setText(text: string | null): void {
    if (this.disposed || !this.container || !this.line) return;

    const value = typeof text === 'string' ? text.trim() : '';
    if (value.length === 0) {
      this.line.textContent = '';
      this.container.setAttribute('hidden', '');
      return;
    }

    // The single most important line in this file: assignment, not parsing.
    this.line.textContent = value;
    this.container.removeAttribute('hidden');
  }

  /** Clear immediately — used when subtitles are switched off or on seek. */
  clear(): void {
    this.setText(null);
  }

  dispose(): void {
    this.disposed = true;
    this.container?.remove();
    this.container = null;
    this.line = null;
  }
}
