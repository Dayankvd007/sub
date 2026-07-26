/**
 * Content-script view of the local backend.
 *
 * Every call is proxied through the service worker (see messages.ts): an MV3
 * content script's fetch is bound by the *page's* CORS context, so a direct
 * call from a YouTube page would arrive with Origin: https://www.youtube.com.
 * Routing through the worker keeps the backend's allowlist at
 * chrome-extension://<id>.
 *
 * The transport is injectable so the whole client is testable without Chrome.
 */

import {
  userMessageFor,
  validateHealth,
  validateJobCreate,
  validateJobStatus,
  type HealthResponse,
  type JobCreateRequest,
  type JobCreateResponse,
  type JobStatusResponse,
} from './apiTypes';
import {
  BACKEND_FETCH,
  TRANSPORT_ERRORS,
  type BackendFetchMessage,
  type BackendFetchResult,
} from './messages';

export type ApiResult<T> =
  | { ok: true; data: T }
  | { ok: false; errorCode: string; message: string; status: number | null };

export type Transport = (message: BackendFetchMessage) => Promise<BackendFetchResult>;

/** Health is checked on demand and must never hold up the UI. */
export const HEALTH_TIMEOUT_MS = 3_000;
/** Creation validates and persists cues; it does not wait for a model call. */
export const CREATE_TIMEOUT_MS = 30_000;
/** A poll competes with a 2 s cadence, so it must fail fast. */
export const POLL_TIMEOUT_MS = 10_000;

const defaultTransport: Transport = (message) =>
  new Promise((resolve) => {
    try {
      chrome.runtime.sendMessage(message, (response: BackendFetchResult) => {
        if (chrome.runtime.lastError) {
          resolve({
            ok: false,
            status: null,
            errorCode: TRANSPORT_ERRORS.messaging,
            message: chrome.runtime.lastError.message ?? 'Background worker unreachable.',
          });
          return;
        }
        resolve(response);
      });
    } catch (err) {
      resolve({
        ok: false,
        status: null,
        errorCode: TRANSPORT_ERRORS.messaging,
        message: err instanceof Error ? err.message : String(err),
      });
    }
  });

function failure<T>(result: Extract<BackendFetchResult, { ok: false }>): ApiResult<T> {
  return {
    ok: false,
    errorCode: result.errorCode,
    message: userMessageFor(result.errorCode),
    status: result.status,
  };
}

function unreadable<T>(): ApiResult<T> {
  return {
    ok: false,
    errorCode: TRANSPORT_ERRORS.badResponse,
    message: userMessageFor(TRANSPORT_ERRORS.badResponse),
    status: null,
  };
}

export class BackendClient {
  constructor(private readonly transport: Transport = defaultTransport) {}

  async health(): Promise<ApiResult<HealthResponse>> {
    const result = await this.transport({
      type: BACKEND_FETCH,
      method: 'GET',
      path: '/health',
      timeoutMs: HEALTH_TIMEOUT_MS,
    });
    if (!result.ok) return failure(result);

    const health = validateHealth(result.data);
    return health ? { ok: true, data: health } : unreadable();
  }

  /**
   * Create, reuse, or resume a job. The backend is idempotent through its own
   * cache identity, so repeating this is safe and is how "retry" works.
   */
  async createJob(payload: JobCreateRequest): Promise<ApiResult<JobCreateResponse>> {
    const result = await this.transport({
      type: BACKEND_FETCH,
      method: 'POST',
      path: '/jobs',
      body: payload,
      timeoutMs: CREATE_TIMEOUT_MS,
    });
    if (!result.ok) return failure(result);

    const job = validateJobCreate(result.data);
    return job ? { ok: true, data: job } : unreadable();
  }

  /**
   * Read job progress. Passing a null cursor requests everything, which is the
   * final reconciliation read once the job reaches a terminal state.
   */
  async getJob(jobId: number, cursor: number | null): Promise<ApiResult<JobStatusResponse>> {
    const query = cursor === null ? '' : `?after_cue_index=${encodeURIComponent(String(cursor))}`;
    const result = await this.transport({
      type: BACKEND_FETCH,
      method: 'GET',
      path: `/jobs/${encodeURIComponent(String(jobId))}${query}`,
      timeoutMs: POLL_TIMEOUT_MS,
    });
    if (!result.ok) return failure(result);

    const status = validateJobStatus(result.data);
    return status ? { ok: true, data: status } : unreadable();
  }
}
