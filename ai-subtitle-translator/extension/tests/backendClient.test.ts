import { describe, expect, it, vi } from 'vitest';

import { validateCues, validateJobStatus, userMessageFor } from '../src/apiTypes';
import { BackendClient, type Transport } from '../src/backendClient';
import type { BackendFetchResult } from '../src/messages';

function transportReturning(result: BackendFetchResult): Transport {
  return vi.fn().mockResolvedValue(result);
}

const OK_JOB = {
  job_id: 7,
  video_id: 'vid1',
  status: 'queued',
  cache_hit: false,
  total_cues: 3,
  speech_cues: null,
  completed_cues: 0,
  cues: [],
  created_at: 'now',
};

const OK_STATUS = {
  job_id: 7,
  video_id: 'vid1',
  status: 'processing',
  done: false,
  progress: { total_cues: 3, speech_cues: 3, completed_cues: 1, failed_cues: 0, percent: 33.3 },
  cues: [{ cue_index: 0, start_ms: 0, end_ms: 1000, persian_text: 'سلام' }],
  next_cursor: 0,
  failed_indices: [],
  error: null,
};

describe('request shaping', () => {
  it('sends the health check with a short timeout', async () => {
    const transport = transportReturning({ ok: true, status: 200, data: { status: 'ok' } });
    await new BackendClient(transport).health();

    expect(transport).toHaveBeenCalledWith(
      expect.objectContaining({ method: 'GET', path: '/health', timeoutMs: 3000 }),
    );
  });

  it('posts job creation to /jobs with the payload as the body', async () => {
    const transport = transportReturning({ ok: true, status: 201, data: OK_JOB });
    const payload = {
      video_id: 'vid1',
      cues: [{ cue_index: 0, start_ms: 0, end_ms: 900, english_text: 'Hello.' }],
    };

    await new BackendClient(transport).createJob(payload);

    expect(transport).toHaveBeenCalledWith(
      expect.objectContaining({ method: 'POST', path: '/jobs', body: payload }),
    );
  });

  it('omits the cursor entirely for a reconciliation read', async () => {
    const transport = transportReturning({ ok: true, status: 200, data: OK_STATUS });
    await new BackendClient(transport).getJob(7, null);

    expect(transport).toHaveBeenCalledWith(expect.objectContaining({ path: '/jobs/7' }));
  });

  it('sends the cursor when polling incrementally', async () => {
    const transport = transportReturning({ ok: true, status: 200, data: OK_STATUS });
    await new BackendClient(transport).getJob(7, 42);

    expect(transport).toHaveBeenCalledWith(
      expect.objectContaining({ path: '/jobs/7?after_cue_index=42' }),
    );
  });
});

describe('error handling', () => {
  it('maps a backend error code to an actionable sentence', async () => {
    const client = new BackendClient(
      transportReturning({
        ok: false,
        status: 413,
        errorCode: 'too_many_cues',
        message: '9000 cues exceeds the configured limit of 5000.',
      }),
    );

    const result = await client.createJob({ video_id: 'v', cues: [] });

    expect(result.ok).toBe(false);
    if (result.ok) return;
    expect(result.errorCode).toBe('too_many_cues');
    expect(result.message).toContain('more captions');
    // The backend's own wording stays in the console, not on screen.
    expect(result.message).not.toContain('5000');
  });

  it('never surfaces raw backend text for an unknown code', async () => {
    const client = new BackendClient(
      transportReturning({
        ok: false,
        status: 500,
        errorCode: 'brand_new_code',
        message: 'Traceback: /home/user/secret/path.py line 42',
      }),
    );

    const result = await client.health();

    expect(result.ok).toBe(false);
    if (result.ok) return;
    expect(result.message).not.toContain('Traceback');
    expect(result.message).not.toContain('/home/user');
  });

  it('reports a timeout distinctly from a refused connection', async () => {
    const timedOut = await new BackendClient(
      transportReturning({ ok: false, status: null, errorCode: 'timeout', message: 'aborted' }),
    ).health();
    const refused = await new BackendClient(
      transportReturning({ ok: false, status: null, errorCode: 'network_error', message: 'ECONNREFUSED' }),
    ).health();

    expect(timedOut.ok).toBe(false);
    expect(refused.ok).toBe(false);
    if (timedOut.ok || refused.ok) return;
    expect(timedOut.message).not.toBe(refused.message);
  });
});

describe('response validation', () => {
  it('rejects a structurally wrong success response', async () => {
    const client = new BackendClient(
      transportReturning({ ok: true, status: 200, data: { nonsense: true } }),
    );

    const result = await client.getJob(7, null);

    expect(result.ok).toBe(false);
    if (result.ok) return;
    expect(result.errorCode).toBe('bad_response');
  });

  it('accepts a valid job status and preserves cue identity', async () => {
    const client = new BackendClient(transportReturning({ ok: true, status: 200, data: OK_STATUS }));

    const result = await client.getJob(7, null);

    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(result.data.cues[0]).toEqual({
      cue_index: 0,
      start_ms: 0,
      end_ms: 1000,
      persian_text: 'سلام',
    });
    expect(result.data.progress.percent).toBeCloseTo(33.3);
  });
});

describe('validateCues', () => {
  it('drops cues that cannot become a safe VTTCue', () => {
    const { cues, dropped } = validateCues([
      { cue_index: 0, start_ms: 0, end_ms: 1000, persian_text: 'ok' },
      { cue_index: 1, start_ms: 500, end_ms: 100, persian_text: 'end before start' },
      { cue_index: 2, start_ms: -5, end_ms: 100, persian_text: 'negative start' },
      { cue_index: 3.5, start_ms: 0, end_ms: 100, persian_text: 'fractional index' },
      { cue_index: 4, start_ms: 0, end_ms: 100, persian_text: '' },
      { cue_index: 5, start_ms: 0, end_ms: 100 },
      'not an object',
      null,
    ]);

    expect(cues.map((c) => c.cue_index)).toEqual([0]);
    expect(dropped).toBe(7);
  });

  it('survives a non-array entirely', () => {
    expect(validateCues(undefined)).toEqual({ cues: [], dropped: 0 });
    expect(validateCues('nope')).toEqual({ cues: [], dropped: 0 });
  });

  it('keeps hostile text as data — sanitisation is the renderer\'s job', () => {
    const { cues } = validateCues([
      { cue_index: 0, start_ms: 0, end_ms: 100, persian_text: '<img src=x onerror=alert(1)>' },
    ]);

    // Validation must not silently mangle text; the overlay writes it with
    // textContent, which is what actually makes it inert.
    expect(cues).toHaveLength(1);
    expect(cues[0]?.persian_text).toBe('<img src=x onerror=alert(1)>');
  });
});

describe('validateJobStatus', () => {
  it('defaults a malformed progress block instead of throwing', () => {
    const status = validateJobStatus({ ...OK_STATUS, progress: 'broken' });

    expect(status).not.toBeNull();
    expect(status!.progress.percent).toBe(0);
  });

  it('filters non-integer entries out of failed_indices', () => {
    const status = validateJobStatus({ ...OK_STATUS, failed_indices: [1, 'x', 2.5, 3, null] });

    expect(status!.failed_indices).toEqual([1, 3]);
  });

  it('requires the fields the controller branches on', () => {
    expect(validateJobStatus({ ...OK_STATUS, done: 'yes' })).toBeNull();
    expect(validateJobStatus({ ...OK_STATUS, status: 42 })).toBeNull();
    expect(validateJobStatus(null)).toBeNull();
  });
});

describe('userMessageFor', () => {
  it('has a sentence for every code the backend can return', () => {
    for (const code of [
      'invalid_cues',
      'too_many_cues',
      'payload_too_large',
      'job_not_found',
      'job_in_progress',
      'jobs_unavailable',
      'provider_error',
      'resume_limit_exceeded',
      'internal_error',
    ]) {
      expect(userMessageFor(code).length, code).toBeGreaterThan(10);
    }
  });
});
