// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { ApiResult } from '../src/backendClient';
import type { HealthResponse, JobCreateResponse, JobStatusResponse } from '../src/apiTypes';
import type { CaptureForTabResult } from '../src/messages';
import { OVERLAY_ID } from '../src/overlay';
import { CONTROL_ID } from '../src/subtitleControl';
import { TRACK_ID } from '../src/subtitleTrack';
import { VideoSession } from '../src/session';

const VIDEO_ID = 'vid1';

function playerMarkup(): void {
  document.body.innerHTML = `
    <h1 class="ytd-watch-metadata">A test video</h1>
    <div id="movie_player">
      <video></video>
      <div class="ytp-chrome-bottom">
        <div class="ytp-right-controls"></div>
      </div>
    </div>`;
}

const HEALTH_OK: ApiResult<HealthResponse> = {
  ok: true,
  data: {
    status: 'ok',
    version: '0.3.0',
    prompt_version: 'v1',
    provider: 'openrouter',
    model: 'm',
    provider_configured: true,
  },
};

const HEALTH_DOWN: ApiResult<never> = {
  ok: false,
  errorCode: 'network_error',
  message: 'down',
  status: null,
};

function captureOk(videoId = VIDEO_ID): CaptureForTabResult {
  return {
    ok: true,
    videoId,
    cues: [
      { cue_index: 0, start_ms: 0, end_ms: 1000, english_text: 'Hello.' },
      { cue_index: 1, start_ms: 1000, end_ms: 2000, english_text: 'World.' },
    ],
    trackKind: 'asr',
    droppedEvents: 0,
  };
}

function createdJob(overrides: Partial<JobCreateResponse> = {}): ApiResult<JobCreateResponse> {
  return {
    ok: true,
    data: {
      job_id: 7,
      video_id: VIDEO_ID,
      status: 'queued',
      cache_hit: false,
      total_cues: 2,
      speech_cues: null,
      completed_cues: 0,
      cues: [],
      ...overrides,
    },
  };
}

function jobStatus(overrides: Partial<JobStatusResponse> = {}): ApiResult<JobStatusResponse> {
  return {
    ok: true,
    data: {
      job_id: 7,
      video_id: VIDEO_ID,
      status: 'processing',
      done: false,
      progress: { total_cues: 2, speech_cues: 2, completed_cues: 0, failed_cues: 0, percent: 0 },
      cues: [],
      next_cursor: null,
      failed_indices: [],
      error: null,
      ...overrides,
    },
  };
}

function makeSession(options: {
  health?: ApiResult<HealthResponse>;
  capture?: CaptureForTabResult;
  create?: ApiResult<JobCreateResponse>;
  polls?: Array<ApiResult<JobStatusResponse>>;
  videoId?: string;
} = {}) {
  const polls = [...(options.polls ?? [])];
  const client = {
    health: vi.fn().mockResolvedValue(options.health ?? HEALTH_OK),
    createJob: vi.fn().mockResolvedValue(options.create ?? createdJob()),
    getJob: vi.fn(async () => polls.shift() ?? jobStatus({ done: true, status: 'completed' })),
  };
  const capture = vi.fn().mockResolvedValue(options.capture ?? captureOk());

  const session = new VideoSession(options.videoId ?? VIDEO_ID, {
    client: client as never,
    capture,
    doc: document,
    readTitle: () => 'A test video',
  });

  return { session, client, capture };
}

function control(): HTMLButtonElement | null {
  return document.getElementById(CONTROL_ID) as HTMLButtonElement | null;
}

async function flush(): Promise<void> {
  for (let i = 0; i < 12; i += 1) await Promise.resolve();
}

beforeEach(() => {
  playerMarkup();
  vi.useRealTimers();
});

afterEach(() => {
  document.body.innerHTML = '';
});

describe('start behaviour', () => {
  it('inserts the control and checks health without touching captions', async () => {
    const { session, client, capture } = makeSession();

    await session.init();

    expect(control()).not.toBeNull();
    expect(client.health).toHaveBeenCalledTimes(1);
    // Nothing may happen on page load: no capture, no job, no provider spend.
    expect(capture).not.toHaveBeenCalled();
    expect(client.createJob).not.toHaveBeenCalled();
    expect(session.currentState).toBe('ready');
  });

  it('reports an unavailable backend without disabling the control', async () => {
    const { session } = makeSession({ health: HEALTH_DOWN });

    await session.init();

    expect(session.currentState).toBe('backend-unavailable');
    expect(control()!.disabled).toBe(false);
  });

  it('creates a job only after an explicit click', async () => {
    const { session, client, capture } = makeSession();
    await session.init();

    control()!.click();
    await flush();

    expect(capture).toHaveBeenCalledTimes(1);
    expect(client.createJob).toHaveBeenCalledTimes(1);
  });

  it('sends the exact backend contract: no fingerprint, cue_index preserved', async () => {
    const { session, client } = makeSession();
    await session.init();

    control()!.click();
    await flush();

    const payload = client.createJob.mock.calls[0]![0];
    expect(Object.keys(payload).sort()).toEqual(['cues', 'title', 'video_id']);
    expect(payload).not.toHaveProperty('caption_fingerprint');
    expect(payload.video_id).toBe(VIDEO_ID);
    expect(payload.cues.map((c: { cue_index: number }) => c.cue_index)).toEqual([0, 1]);
  });

  it('retries the health check when the backend was down at init', async () => {
    const client = {
      health: vi.fn().mockResolvedValueOnce(HEALTH_DOWN).mockResolvedValue(HEALTH_OK),
      createJob: vi.fn().mockResolvedValue(createdJob()),
      getJob: vi.fn().mockResolvedValue(jobStatus({ done: true, status: 'completed' })),
    };
    const session = new VideoSession(VIDEO_ID, {
      client: client as never,
      capture: vi.fn().mockResolvedValue(captureOk()),
      doc: document,
    });
    await session.init();
    expect(session.currentState).toBe('backend-unavailable');

    control()!.click();
    await flush();

    expect(client.health).toHaveBeenCalledTimes(2);
    expect(client.createJob).toHaveBeenCalled();
  });
});

describe('capture outcomes', () => {
  it('shows a no-captions state and never creates a job', async () => {
    const { session, client } = makeSession({
      capture: { ok: false, kind: 'no-captions', error: 'no english track' },
    });
    await session.init();

    control()!.click();
    await flush();

    expect(session.currentState).toBe('no-captions');
    expect(control()!.disabled).toBe(true);
    expect(client.createJob).not.toHaveBeenCalled();
  });

  it('drops a capture that arrived for a different video', async () => {
    const { session, client } = makeSession({ capture: captureOk('a-different-video') });
    await session.init();

    control()!.click();
    await flush();

    expect(client.createJob).not.toHaveBeenCalled();
  });
});

describe('rendering', () => {
  it('mounts the overlay and timing track inside the player on start', async () => {
    const { session } = makeSession();
    await session.init();

    control()!.click();
    await flush();

    expect(document.getElementById(OVERLAY_ID)).not.toBeNull();
    expect(document.getElementById(OVERLAY_ID)!.parentElement!.id).toBe('movie_player');
    expect(document.querySelector(`#${TRACK_ID}`)).not.toBeNull();
  });

  it('renders a cache hit immediately without polling', async () => {
    const { session, client } = makeSession({
      create: createdJob({
        cache_hit: true,
        status: 'completed',
        cues: [{ cue_index: 0, start_ms: 0, end_ms: 1000, persian_text: 'سلام' }],
      }),
    });
    await session.init();

    control()!.click();
    await flush();

    expect(session.currentState).toBe('completed');
    expect(client.getJob).not.toHaveBeenCalled();
  });
});

describe('display toggle', () => {
  it('clears visible text the moment subtitles are switched off', async () => {
    const { session } = makeSession({
      create: createdJob({ cache_hit: true, status: 'completed' }),
    });
    await session.init();
    control()!.click();
    await flush();
    expect(session.isShowing).toBe(true);

    control()!.click();
    await flush();

    expect(session.isShowing).toBe(false);
    expect(document.getElementById(OVERLAY_ID)!.hasAttribute('hidden')).toBe(true);
  });

  it('does not create a second job when toggling', async () => {
    const { session, client } = makeSession({
      create: createdJob({ cache_hit: true, status: 'completed' }),
    });
    await session.init();

    control()!.click();
    await flush();
    control()!.click();
    await flush();
    control()!.click();
    await flush();

    expect(client.createJob).toHaveBeenCalledTimes(1);
  });
});

describe('disposal', () => {
  it('removes every node it created', async () => {
    const { session } = makeSession();
    await session.init();
    control()!.click();
    await flush();

    session.dispose();

    expect(control()).toBeNull();
    expect(document.getElementById(OVERLAY_ID)).toBeNull();
    expect(document.querySelector(`#${TRACK_ID}`)).toBeNull();
    // YouTube's own DOM is untouched.
    expect(document.getElementById('movie_player')).not.toBeNull();
    expect(document.querySelector('video')).not.toBeNull();
    expect(document.querySelector('.ytp-right-controls')).not.toBeNull();
  });

  it('stops polling', async () => {
    vi.useFakeTimers();
    const { session, client } = makeSession({ polls: [jobStatus(), jobStatus(), jobStatus()] });
    await session.init();
    control()!.click();
    await vi.advanceTimersByTimeAsync(0);

    session.dispose();
    const callsAtDispose = client.getJob.mock.calls.length;
    await vi.advanceTimersByTimeAsync(30_000);

    expect(client.getJob.mock.calls.length).toBe(callsAtDispose);
  });

  it('ignores a click after disposal', async () => {
    const { session, client } = makeSession();
    await session.init();
    const button = control()!;

    session.dispose();
    button.click();
    await flush();

    expect(client.createJob).not.toHaveBeenCalled();
  });

  it('is safe to dispose repeatedly', async () => {
    const { session } = makeSession();
    await session.init();

    expect(() => {
      session.dispose();
      session.dispose();
    }).not.toThrow();
    expect(session.isDisposed).toBe(true);
  });

  it('drops a health response that lands after disposal', async () => {
    let resolveHealth: (v: ApiResult<HealthResponse>) => void = () => {};
    const client = {
      health: vi.fn().mockReturnValue(new Promise((r) => (resolveHealth = r))),
      createJob: vi.fn(),
      getJob: vi.fn(),
    };
    const session = new VideoSession(VIDEO_ID, {
      client: client as never,
      capture: vi.fn().mockResolvedValue(captureOk()),
      doc: document,
    });

    const init = session.init();
    session.dispose();
    resolveHealth(HEALTH_DOWN);
    await init;

    // A late response must not resurrect a disposed session's UI.
    expect(control()).toBeNull();
  });

  it('drops a capture that lands after disposal', async () => {
    let resolveCapture: (v: CaptureForTabResult) => void = () => {};
    const client = {
      health: vi.fn().mockResolvedValue(HEALTH_OK),
      createJob: vi.fn().mockResolvedValue(createdJob()),
      getJob: vi.fn(),
    };
    const session = new VideoSession(VIDEO_ID, {
      client: client as never,
      capture: vi.fn().mockReturnValue(new Promise((r) => (resolveCapture = r))),
      doc: document,
    });
    await session.init();

    control()!.click();
    await flush();
    session.dispose();
    resolveCapture(captureOk());
    await flush();

    expect(client.createJob).not.toHaveBeenCalled();
    expect(document.getElementById(OVERLAY_ID)).toBeNull();
  });
});

describe('sequential sessions (SPA navigation)', () => {
  it('leaves exactly one control, overlay, and track after repeated switching', async () => {
    let live: VideoSession | null = null;

    for (let i = 0; i < 6; i += 1) {
      live?.dispose();
      const { session } = makeSession({ videoId: `video-${i}` });
      live = session;
      await session.init();
    }

    expect(document.querySelectorAll(`#${CONTROL_ID}`)).toHaveLength(1);
    expect(document.querySelectorAll(`#${OVERLAY_ID}`).length).toBeLessThanOrEqual(1);
    expect(document.querySelectorAll(`#${TRACK_ID}`).length).toBeLessThanOrEqual(1);

    live?.dispose();
    expect(document.querySelectorAll(`#${CONTROL_ID}`)).toHaveLength(0);
  });

  it('never lets an old video\'s job drive the new session', async () => {
    const { session: first, client: firstClient } = makeSession({
      videoId: 'video-a',
      capture: captureOk('video-a'),
      create: createdJob({ video_id: 'video-a' }),
    });
    await first.init();
    control()!.click();
    await flush();

    first.dispose();
    const { session: second } = makeSession({ videoId: 'video-b' });
    await second.init();

    // The first session's job responses are for video-a and are now inert.
    expect(second.currentState).toBe('ready');
    expect(second.isShowing).toBe(false);
    expect(firstClient.createJob).toHaveBeenCalledTimes(1);
  });
});
