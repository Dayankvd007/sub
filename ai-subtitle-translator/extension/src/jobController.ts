/**
 * Job creation and polling (roadmap P3-03 / P3-04).
 *
 * Owns one backend job for one video: create it, poll it with a cursor, hand
 * validated cues upward exactly once each, and stop cleanly.
 *
 * Three rules this file exists to guarantee:
 *
 *  1. **A cue is never delivered twice.** The cursor is an optimisation; the
 *     `seen` set is the actual guarantee, and every cue passes through it.
 *  2. **A terminal job gets one final cursor-less read.** The backend returns
 *     any validated range, so a cue that completed out of order (behind a
 *     failed window) could sit below the cursor forever. One extra request
 *     closes that hole — this is the backend's documented contract.
 *  3. **Nothing from an old video reaches a new one.** Every async resumption
 *     re-checks the session guard *and* the video identity carried by the
 *     response itself.
 */

import type { ControlContext, ControlState } from './controlState';
import { stateForJobStatus } from './controlState';
import type { BackendClient } from './backendClient';
import type { JobCreateRequest, JobStatusResponse, TranslatedCue } from './apiTypes';

/** Architecture §8: poll roughly every two seconds while a job is active. */
export const POLL_INTERVAL_MS = 2_000;
/** Backoff for transport failures. The last value repeats. */
export const BACKOFF_MS = [2_000, 4_000, 8_000] as const;
/** Consecutive poll failures before we call the backend unavailable. */
export const MAX_CONSECUTIVE_FAILURES = 5;

export interface JobControllerDeps {
  client: BackendClient;
  /** The video this controller belongs to; responses must agree. */
  videoId: string;
  /** Called with cues never seen before, in cue order. */
  onCues: (cues: TranslatedCue[]) => void;
  onState: (state: ControlState, context: ControlContext) => void;
  /** False once the owning session is disposed or superseded. */
  isActive: () => boolean;
  /** Injectable for tests; defaults to setTimeout. */
  setTimer?: (fn: () => void, ms: number) => ReturnType<typeof setTimeout>;
  clearTimer?: (handle: ReturnType<typeof setTimeout>) => void;
}

export class JobController {
  private readonly seen = new Set<number>();
  private cursor: number | null = null;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private jobId: number | null = null;
  private failures = 0;
  private stopped = false;
  private finished = false;

  private readonly setTimer: NonNullable<JobControllerDeps['setTimer']>;
  private readonly clearTimer: NonNullable<JobControllerDeps['clearTimer']>;

  constructor(private readonly deps: JobControllerDeps) {
    this.setTimer = deps.setTimer ?? ((fn, ms) => setTimeout(fn, ms));
    this.clearTimer = deps.clearTimer ?? ((handle) => clearTimeout(handle));
  }

  /** Cues delivered so far — the dedup set's size, exposed for tests and UI. */
  get deliveredCount(): number {
    return this.seen.size;
  }

  get currentJobId(): number | null {
    return this.jobId;
  }

  /**
   * Create (or reuse, or resume) the job and begin polling.
   *
   * A cache hit short-circuits everything: the response already carries every
   * cue, so no poll is ever scheduled.
   */
  async start(payload: JobCreateRequest): Promise<void> {
    if (this.abandon()) return;
    this.deps.onState('translating', { percent: 0 });

    const result = await this.deps.client.createJob(payload);
    if (this.abandon()) return;

    if (!result.ok) {
      this.failTerminally(result.errorCode);
      return;
    }

    const job = result.data;
    if (job.video_id !== this.deps.videoId) {
      // A response for a different video must never drive this session.
      return;
    }

    this.jobId = job.job_id;
    this.emit(job.cues);

    if (job.cache_hit) {
      // Everything is already here; polling would be pure waste.
      this.finish('completed', { showing: true });
      return;
    }

    if (job.status === 'completed' || job.status === 'partial' || job.status === 'failed') {
      // Defensive: a terminal status straight from create still gets its
      // reconciliation read rather than being trusted as complete.
      void this.reconcile(job.status);
      return;
    }

    this.deps.onState('translating', { percent: 0, showing: true });
    this.schedule(POLL_INTERVAL_MS);
  }

  /** Stop all activity. Idempotent, synchronous, safe from any state. */
  stop(): void {
    this.stopped = true;
    if (this.timer !== null) {
      this.clearTimer(this.timer);
      this.timer = null;
    }
  }

  // --- internals ---------------------------------------------------------

  /** True when this controller must not touch anything further. */
  private abandon(): boolean {
    return this.stopped || this.finished || !this.deps.isActive();
  }

  private schedule(delayMs: number): void {
    if (this.abandon()) return;
    // Chained timeout, never setInterval: a slow response must not let a
    // second request stack on top of the first.
    this.timer = this.setTimer(() => {
      this.timer = null;
      void this.poll();
    }, delayMs);
  }

  private async poll(): Promise<void> {
    if (this.abandon() || this.jobId === null) return;

    const result = await this.deps.client.getJob(this.jobId, this.cursor);
    if (this.abandon()) return;

    if (!result.ok) {
      this.handlePollFailure();
      return;
    }

    const job = result.data;
    if (job.video_id !== this.deps.videoId || job.job_id !== this.jobId) {
      // Stale or mismatched: drop it and keep the previous cadence.
      this.schedule(POLL_INTERVAL_MS);
      return;
    }

    this.failures = 0;
    this.emit(job.cues);
    if (job.next_cursor !== null) this.cursor = job.next_cursor;

    if (job.done) {
      void this.reconcile(job.status, job);
      return;
    }

    this.deps.onState('translating', {
      percent: job.progress.percent,
      showing: true,
    });
    this.schedule(POLL_INTERVAL_MS);
  }

  private handlePollFailure(): void {
    this.failures += 1;
    const step = Math.min(this.failures - 1, BACKOFF_MS.length - 1);
    const delay = BACKOFF_MS[step] ?? BACKOFF_MS[BACKOFF_MS.length - 1] ?? POLL_INTERVAL_MS;

    if (this.failures >= MAX_CONSECUTIVE_FAILURES) {
      // Say so plainly, but keep trying at the slowest cadence so restarting
      // the backend recovers on its own.
      this.deps.onState('backend-unavailable', { showing: true });
    } else {
      this.deps.onState('retrying', { showing: true });
    }

    this.schedule(delay);
  }

  /**
   * The one final cursor-less read. Runs for every terminal status, including
   * `failed` — cues translated before the failure are still valid and still
   * worth showing.
   */
  private async reconcile(status: string, known?: JobStatusResponse): Promise<void> {
    if (this.abandon() || this.jobId === null) return;

    const result = await this.deps.client.getJob(this.jobId, null);
    if (this.abandon()) return;

    let finalStatus = status;
    let failedCount = known?.progress.failed_cues ?? 0;

    if (result.ok && result.data.video_id === this.deps.videoId) {
      this.emit(result.data.cues);
      finalStatus = result.data.status;
      failedCount = result.data.progress.failed_cues;
    }

    this.finish(stateForJobStatus(finalStatus), {
      failedCount,
      showing: true,
    });
  }

  private failTerminally(errorCode: string): void {
    // A cue-count or payload rejection is not going to fix itself; surface it
    // as failed so the control offers a retry rather than spinning.
    this.finish('failed', { showing: false });
    console.debug('[ai-subtitle-translator] job could not be created:', errorCode);
  }

  private finish(state: ControlState, context: ControlContext): void {
    this.finished = true;
    this.stop();
    this.stopped = false; // `finished` already blocks further work.
    this.deps.onState(state, context);
  }

  /** Deliver only cues never delivered before, in cue order. */
  private emit(cues: TranslatedCue[]): void {
    if (cues.length === 0) return;

    const fresh: TranslatedCue[] = [];
    for (const cue of cues) {
      if (this.seen.has(cue.cue_index)) continue;
      this.seen.add(cue.cue_index);
      fresh.push(cue);
    }

    if (fresh.length === 0) return;
    fresh.sort((a, b) => a.cue_index - b.cue_index);
    this.deps.onCues(fresh);
  }
}
