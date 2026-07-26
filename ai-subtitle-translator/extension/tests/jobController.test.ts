import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { ApiResult } from '../src/backendClient';
import type { JobCreateResponse, JobStatusResponse, TranslatedCue } from '../src/apiTypes';
import type { ControlContext, ControlState } from '../src/controlState';
import { JobController, MAX_CONSECUTIVE_FAILURES, POLL_INTERVAL_MS } from '../src/jobController';

const VIDEO_ID = 'vid1';

function cue(index: number): TranslatedCue {
  return {
    cue_index: index,
    start_ms: index * 1000,
    end_ms: (index + 1) * 1000,
    persian_text: `فارسی ${index}`,
  };
}

function created(overrides: Partial<JobCreateResponse> = {}): ApiResult<JobCreateResponse> {
  return {
    ok: true,
    data: {
      job_id: 7,
      video_id: VIDEO_ID,
      status: 'queued',
      cache_hit: false,
      total_cues: 6,
      speech_cues: null,
      completed_cues: 0,
      cues: [],
      ...overrides,
    },
  };
}

function status(overrides: Partial<JobStatusResponse> = {}): ApiResult<JobStatusResponse> {
  return {
    ok: true,
    data: {
      job_id: 7,
      video_id: VIDEO_ID,
      status: 'processing',
      done: false,
      progress: { total_cues: 6, speech_cues: 6, completed_cues: 0, failed_cues: 0, percent: 0 },
      cues: [],
      next_cursor: null,
      failed_indices: [],
      error: null,
      ...overrides,
    },
  };
}

function transportError(errorCode = 'network_error'): ApiResult<never> {
  return { ok: false, errorCode, message: 'nope', status: null };
}

/** Test harness: a scripted client plus recorded outputs. */
function harness(script: {
  create?: ApiResult<JobCreateResponse>;
  polls?: Array<ApiResult<JobStatusResponse>>;
}) {
  const polls = [...(script.polls ?? [])];
  const getJobCalls: Array<number | null> = [];

  const client = {
    createJob: vi.fn().mockResolvedValue(script.create ?? created()),
    getJob: vi.fn(async (_id: number, cursor: number | null) => {
      getJobCalls.push(cursor);
      return polls.shift() ?? status({ done: true, status: 'completed' });
    }),
  };

  const receivedCues: TranslatedCue[] = [];
  const states: Array<{ state: ControlState; context: ControlContext }> = [];
  let active = true;

  const controller = new JobController({
    client: client as never,
    videoId: VIDEO_ID,
    onCues: (cues) => receivedCues.push(...cues),
    onState: (state, context) => states.push({ state, context }),
    isActive: () => active,
  });

  return {
    controller,
    client,
    receivedCues,
    states,
    getJobCalls,
    deactivate: () => {
      active = false;
    },
    /** Throws rather than returning undefined, so assertions read cleanly. */
    lastState: (): { state: ControlState; context: ControlContext } => {
      const last = states[states.length - 1];
      if (!last) throw new Error('the controller reported no state at all');
      return last;
    },
    payload: { video_id: VIDEO_ID, cues: [] },
  };
}

/** Let queued promises settle between fake-timer steps. */
async function flush(): Promise<void> {
  for (let i = 0; i < 8; i += 1) await Promise.resolve();
}

async function tick(ms: number): Promise<void> {
  await vi.advanceTimersByTimeAsync(ms);
  await flush();
}

beforeEach(() => vi.useFakeTimers());
afterEach(() => vi.useRealTimers());

describe('cache hit', () => {
  it('renders immediately and never polls', async () => {
    const h = harness({
      create: created({ cache_hit: true, status: 'completed', cues: [cue(0), cue(1)] }),
    });

    await h.controller.start(h.payload);
    await tick(10_000);

    expect(h.receivedCues.map((c) => c.cue_index)).toEqual([0, 1]);
    expect(h.client.getJob).not.toHaveBeenCalled();
    expect(h.lastState().state).toBe('completed');
  });
});

describe('polling lifecycle', () => {
  it('polls about every two seconds while the job is running', async () => {
    const h = harness({ polls: [status(), status(), status()] });
    await h.controller.start(h.payload);

    expect(h.client.getJob).not.toHaveBeenCalled();
    await tick(POLL_INTERVAL_MS);
    expect(h.client.getJob).toHaveBeenCalledTimes(1);
    await tick(POLL_INTERVAL_MS);
    expect(h.client.getJob).toHaveBeenCalledTimes(2);
  });

  it('advances the cursor and reports progress', async () => {
    const h = harness({
      polls: [
        status({
          cues: [cue(0), cue(1)],
          next_cursor: 1,
          progress: { total_cues: 6, speech_cues: 6, completed_cues: 2, failed_cues: 0, percent: 33 },
        }),
        status({ cues: [cue(2)], next_cursor: 2 }),
      ],
    });

    await h.controller.start(h.payload);
    await tick(POLL_INTERVAL_MS);
    expect(h.getJobCalls).toEqual([null]);
    expect(h.states.some((s) => s.state === 'translating' && s.context.percent === 33)).toBe(true);

    await tick(POLL_INTERVAL_MS);
    expect(h.getJobCalls).toEqual([null, 1]);
  });

  it('keeps the previous cursor when the backend returns null', async () => {
    const h = harness({
      polls: [status({ cues: [cue(0)], next_cursor: 0 }), status({ next_cursor: null }), status()],
    });

    await h.controller.start(h.payload);
    await tick(POLL_INTERVAL_MS * 3);

    expect(h.getJobCalls.slice(0, 3)).toEqual([null, 0, 0]);
  });
});

describe('duplicate prevention', () => {
  it('never delivers a cue twice even when the backend resends everything', async () => {
    const h = harness({
      polls: [
        status({ cues: [cue(0), cue(1)] }),
        status({ cues: [cue(0), cue(1), cue(2)] }), // full resend
        status({ cues: [cue(2), cue(3)], done: true, status: 'completed' }),
      ],
    });

    await h.controller.start(h.payload);
    await tick(POLL_INTERVAL_MS * 4);

    const indexes = h.receivedCues.map((c) => c.cue_index);
    expect(indexes).toEqual([...new Set(indexes)]);
    expect([...indexes].sort((a, b) => a - b)).toEqual([0, 1, 2, 3]);
  });

  it('delivers fresh cues in cue order regardless of response order', async () => {
    const h = harness({
      polls: [status({ cues: [cue(3), cue(1), cue(2)], done: true, status: 'completed' })],
    });

    await h.controller.start(h.payload);
    await tick(POLL_INTERVAL_MS * 2);

    expect(h.receivedCues.map((c) => c.cue_index)).toEqual([1, 2, 3]);
  });
});

describe('terminal handling', () => {
  it('makes exactly one cursor-less reconciliation read after completion', async () => {
    const h = harness({
      polls: [
        status({ cues: [cue(1)], next_cursor: 1 }),
        status({ cues: [cue(2)], next_cursor: 2, done: true, status: 'completed' }),
        // Reconciliation: cue 0 completed late, behind the cursor.
        status({ cues: [cue(0), cue(1), cue(2)], done: true, status: 'completed' }),
      ],
    });

    await h.controller.start(h.payload);
    await tick(POLL_INTERVAL_MS * 3);

    expect(h.getJobCalls).toEqual([null, 1, null]);
    expect(h.receivedCues.map((c) => c.cue_index).sort((a, b) => a - b)).toEqual([0, 1, 2]);
    expect(h.lastState().state).toBe('completed');
  });

  it('stops polling once terminal', async () => {
    const h = harness({ polls: [status({ done: true, status: 'completed' })] });

    await h.controller.start(h.payload);
    await tick(POLL_INTERVAL_MS * 2);
    const callsAtFinish = h.client.getJob.mock.calls.length;
    await tick(POLL_INTERVAL_MS * 10);

    expect(h.client.getJob.mock.calls.length).toBe(callsAtFinish);
  });

  it('keeps translated cues and reports the count on a partial job', async () => {
    const h = harness({
      polls: [
        status({
          cues: [cue(0), cue(1)],
          done: true,
          status: 'partial',
          failed_indices: [2, 3],
          progress: { total_cues: 4, speech_cues: 4, completed_cues: 2, failed_cues: 2, percent: 100 },
        }),
        status({
          cues: [cue(0), cue(1)],
          done: true,
          status: 'partial',
          failed_indices: [2, 3],
          progress: { total_cues: 4, speech_cues: 4, completed_cues: 2, failed_cues: 2, percent: 100 },
        }),
      ],
    });

    await h.controller.start(h.payload);
    await tick(POLL_INTERVAL_MS * 2);

    expect(h.lastState().state).toBe('partial');
    expect(h.lastState().context.failedCount).toBe(2);
    expect(h.receivedCues).toHaveLength(2);
  });

  it('still delivers cues translated before a failure', async () => {
    const h = harness({
      polls: [
        status({ cues: [cue(0), cue(1)] }),
        status({ done: true, status: 'failed', error: { error_code: 'provider_error', message: 'x' } }),
        status({ cues: [cue(0), cue(1)], done: true, status: 'failed' }),
      ],
    });

    await h.controller.start(h.payload);
    await tick(POLL_INTERVAL_MS * 3);

    expect(h.lastState().state).toBe('failed');
    expect(h.receivedCues).toHaveLength(2);
  });
});

describe('transport failures', () => {
  it('backs off and reports retrying without losing delivered cues', async () => {
    const h = harness({
      polls: [status({ cues: [cue(0)] }), transportError(), transportError(), status()],
    });

    await h.controller.start(h.payload);
    await tick(POLL_INTERVAL_MS); // first poll succeeds
    await tick(POLL_INTERVAL_MS); // failure 1
    expect(h.lastState().state).toBe('retrying');

    await tick(2_000); // backoff 1
    expect(h.lastState().state).toBe('retrying');
    expect(h.receivedCues).toHaveLength(1);
  });

  it('calls the backend unavailable after repeated failures but keeps trying', async () => {
    const h = harness({ polls: Array(MAX_CONSECUTIVE_FAILURES + 2).fill(transportError()) });

    await h.controller.start(h.payload);
    await tick(POLL_INTERVAL_MS);
    for (let i = 0; i < MAX_CONSECUTIVE_FAILURES; i += 1) await tick(8_000);

    expect(h.lastState().state).toBe('backend-unavailable');
    const callsSoFar = h.client.getJob.mock.calls.length;
    await tick(8_000);
    // Self-healing: it must recover on its own when the backend comes back.
    expect(h.client.getJob.mock.calls.length).toBeGreaterThan(callsSoFar);
  });

  it('recovers to translating once a poll succeeds again', async () => {
    const h = harness({
      polls: [transportError(), status({ cues: [cue(0)], progress: { total_cues: 2, speech_cues: 2, completed_cues: 1, failed_cues: 0, percent: 50 } })],
    });

    await h.controller.start(h.payload);
    await tick(POLL_INTERVAL_MS);
    expect(h.lastState().state).toBe('retrying');

    await tick(2_000);
    expect(h.lastState().state).toBe('translating');
    expect(h.lastState().context.percent).toBe(50);
  });

  it('fails terminally when the job cannot be created at all', async () => {
    const h = harness({ create: transportError('too_many_cues') });

    await h.controller.start(h.payload);
    await tick(POLL_INTERVAL_MS * 3);

    expect(h.lastState().state).toBe('failed');
    expect(h.client.getJob).not.toHaveBeenCalled();
  });
});

describe('session and identity guards', () => {
  it('delivers nothing once the session is deactivated', async () => {
    const h = harness({ polls: [status({ cues: [cue(0)] })] });
    await h.controller.start(h.payload);

    h.deactivate();
    await tick(POLL_INTERVAL_MS * 3);

    expect(h.receivedCues).toHaveLength(0);
  });

  it('stops scheduling after stop()', async () => {
    const h = harness({ polls: [status(), status(), status()] });
    await h.controller.start(h.payload);

    h.controller.stop();
    await tick(POLL_INTERVAL_MS * 5);

    expect(h.client.getJob).not.toHaveBeenCalled();
  });

  it('ignores a create response for a different video', async () => {
    const h = harness({ create: created({ video_id: 'a-different-video', cues: [cue(0)] }) });

    await h.controller.start(h.payload);
    await tick(POLL_INTERVAL_MS * 2);

    expect(h.receivedCues).toHaveLength(0);
    expect(h.client.getJob).not.toHaveBeenCalled();
  });

  it('ignores a poll response for a different video', async () => {
    const h = harness({
      polls: [status({ video_id: 'other', cues: [cue(9)] }), status({ cues: [cue(0)] })],
    });

    await h.controller.start(h.payload);
    await tick(POLL_INTERVAL_MS);
    expect(h.receivedCues).toHaveLength(0);

    await tick(POLL_INTERVAL_MS);
    expect(h.receivedCues.map((c) => c.cue_index)).toEqual([0]);
  });

  it('ignores a response carrying a different job id', async () => {
    const h = harness({ polls: [status({ job_id: 999, cues: [cue(5)] }), status({ cues: [cue(0)] })] });

    await h.controller.start(h.payload);
    await tick(POLL_INTERVAL_MS * 2);

    expect(h.receivedCues.map((c) => c.cue_index)).toEqual([0]);
  });

  it('is safe to stop repeatedly', async () => {
    const h = harness({ polls: [status()] });
    await h.controller.start(h.payload);

    expect(() => {
      h.controller.stop();
      h.controller.stop();
      h.controller.stop();
    }).not.toThrow();
  });
});
