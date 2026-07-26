/**
 * One VideoSession owns the entire lifecycle of one video (roadmap P3-07).
 *
 * Everything the extension creates for a video — the control, the timing
 * track, the overlay, the job controller, every timer — belongs to exactly one
 * session and dies with it. `dispose()` is synchronous and total, so a session
 * is fully torn down before its successor is constructed.
 *
 * Three independent guards keep an old video's data away from a new one:
 *
 *  1. `disposed` — every async resumption checks it first.
 *  2. `AbortController` — a shared cancellation token; in-flight work observes
 *     `signal.aborted`. (The HTTP request itself lives in the service worker
 *     and is bounded by its own timeout, so this is a guard, not a socket
 *     cancel.)
 *  3. Identity — the JobController compares the `video_id` inside every
 *     response against this session's own. This is the guard that actually
 *     prevents cross-video contamination, and the one usually forgotten.
 *
 * Nothing here starts on page load: capture, job creation, and polling all
 * wait for an explicit click. A health check is the only automatic request,
 * and it only decides which label the button shows.
 */

import type { ControlContext, ControlState } from './controlState';
import type { JobCreateRequest } from './apiTypes';
import type { TranslatedCue } from './apiTypes';
import type { CaptureForTabResult } from './messages';
import { BackendClient } from './backendClient';
import { log } from './debug';
import { JobController } from './jobController';
import { RtlOverlay } from './overlay';
import { SubtitleControl } from './subtitleControl';
import { SubtitleTrack } from './subtitleTrack';

export const PLAYER_SELECTOR = '#movie_player';

export interface SessionDeps {
  client?: BackendClient;
  /** Runs the Phase 0 extractor for this tab, via the service worker. */
  capture: () => Promise<CaptureForTabResult>;
  doc?: Document;
  /** Best-effort video title, used only as translation context. */
  readTitle?: () => string | null;
}

export class VideoSession {
  private readonly doc: Document;
  private readonly client: BackendClient;
  private readonly abortController = new AbortController();

  private readonly control: SubtitleControl;
  private readonly overlay = new RtlOverlay();
  private readonly track: SubtitleTrack;

  private jobs: JobController | null = null;
  private disposed = false;
  private started = false;
  private showing = false;
  private state: ControlState = 'ready';
  private context: ControlContext = {};

  constructor(
    readonly videoId: string,
    private readonly deps: SessionDeps,
  ) {
    this.doc = deps.doc ?? document;
    this.client = deps.client ?? new BackendClient();
    this.control = new SubtitleControl(() => {
      // A rejection here would otherwise be an unhandled promise: the click
      // would appear to do nothing, which is exactly the failure this guards.
      this.handleActivate().catch((err: unknown) => this.failWorkflow('activate', err));
    });
    this.track = new SubtitleTrack((text) => this.renderText(text));
  }

  get currentState(): ControlState {
    return this.state;
  }

  get isShowing(): boolean {
    return this.showing;
  }

  get isDisposed(): boolean {
    return this.disposed;
  }

  /** Insert the control and check the backend. No captions are read yet. */
  async init(): Promise<void> {
    if (this.disposed) return;
    this.control.attach(this.doc);
    this.setState('ready');
    await this.checkHealth();
  }

  /**
   * Total, synchronous teardown. Ordered so nothing can fire mid-cleanup:
   * flag first, then cancel work, then remove DOM.
   */
  dispose(): void {
    if (this.disposed) return;
    this.disposed = true;

    this.abortController.abort();
    this.jobs?.stop();
    this.jobs = null;

    this.track.dispose();
    this.overlay.dispose();
    this.control.dispose();
    this.showing = false;
  }

  // --- internals ---------------------------------------------------------

  private alive(): boolean {
    return !this.disposed && !this.abortController.signal.aborted;
  }

  private setState(state: ControlState, context: ControlContext = {}): void {
    if (this.disposed) return;
    this.state = state;
    this.context = { ...context, showing: this.showing };
    this.control.setState(state, this.context);
  }

  /** Probe the backend without writing any state. */
  private async probeHealth(): Promise<boolean> {
    const result = await this.client.health();
    return result.ok;
  }

  private async checkHealth(): Promise<void> {
    const healthy = await this.probeHealth();
    if (!this.alive()) return;

    if (!healthy) {
      this.setState('backend-unavailable');
      return;
    }
    // Only overwrite the resting state; never stomp on a running job.
    if (this.state === 'backend-unavailable' || this.state === 'ready') {
      this.setState('ready');
    }
  }

  private async handleActivate(): Promise<void> {
    if (!this.alive()) return;

    // Already translating or translated: the click toggles display only.
    if (this.started && this.state !== 'failed' && this.state !== 'backend-unavailable') {
      this.toggleDisplay();
      return;
    }

    const wasUnavailable = this.state === 'backend-unavailable';

    // Move the control *before* any await. A workflow that dies later must
    // never be indistinguishable from a click that was ignored — that
    // ambiguity is what made the first gate failure so hard to diagnose.
    this.showing = true;
    this.setState('translating', { percent: 0 });

    if (wasUnavailable) {
      const healthy = await this.probeHealth();
      if (!this.alive()) return;
      if (!healthy) {
        this.showing = false;
        this.setState('backend-unavailable');
        return;
      }
    }

    await this.startTranslation();
  }

  /** One place where an unexpected failure becomes a visible, logged state. */
  private failWorkflow(stage: string, error: unknown): void {
    log('workflow-error', {
      stage,
      error: error instanceof Error ? error.message : String(error),
    });
    if (!this.alive()) return;
    this.showing = false;
    this.setState('failed');
  }

  private toggleDisplay(): void {
    this.showing = !this.showing;
    if (!this.showing) {
      // Switching off must clear what is on screen right now, not at the next
      // cue boundary.
      this.overlay.clear();
    }
    this.setState(this.state, this.context);
  }

  /** Capture captions, then create or resume the backend job. */
  private async startTranslation(): Promise<void> {
    this.started = true;
    this.showing = true;
    this.setState('translating', { percent: 0 });
    log('session-start', { videoId: this.videoId });

    const capture = await this.deps.capture();
    if (!this.alive()) return;

    if (!capture.ok) {
      this.started = false;
      this.setState(capture.kind === 'no-captions' ? 'no-captions' : 'failed');
      log('workflow-error', { stage: 'capture', kind: capture.kind, error: capture.error });
      return;
    }

    // The extractor ran against this tab, but the user may have navigated
    // between the click and the result.
    if (capture.videoId !== this.videoId) {
      log('workflow-error', {
        stage: 'capture',
        error: 'capture returned a different video; ignoring',
      });
      return;
    }

    if (!this.mountPlayerParts()) {
      this.started = false;
      this.setState('failed');
      log('workflow-error', { stage: 'mount', error: 'no #movie_player video element found' });
      return;
    }

    const payload: JobCreateRequest = {
      video_id: this.videoId,
      cues: capture.cues,
    };
    const title = this.deps.readTitle?.();
    if (title) payload.title = title.slice(0, 500);

    this.jobs = new JobController({
      client: this.client,
      videoId: this.videoId,
      onCues: (cues) => this.acceptCues(cues),
      onState: (state, context) => this.setState(state, context),
      isActive: () => this.alive(),
    });

    log('post-jobs-start', { videoId: this.videoId, cues: payload.cues.length });
    try {
      await this.jobs.start(payload);
      log('post-jobs-result', { state: this.state, jobId: this.jobs?.currentJobId ?? null });
    } catch (err) {
      this.failWorkflow('post-jobs', err);
    }
  }

  /** Attach the overlay and timing track to the live player. */
  private mountPlayerParts(): boolean {
    const player = this.doc.querySelector(PLAYER_SELECTOR) as HTMLElement | null;
    const video = player?.querySelector('video') as HTMLVideoElement | null;
    if (!player || !video) return false;

    this.overlay.attach(player);
    this.track.attach(video);
    return true;
  }

  private acceptCues(cues: TranslatedCue[]): void {
    if (!this.alive()) return;
    this.track.addCues(cues);
  }

  /** The only path from timing to pixels. */
  private renderText(text: string | null): void {
    if (!this.alive() || !this.showing) return;
    this.overlay.setText(text);
  }
}
