/**
 * Typed mirrors of the backend contract, plus runtime validation.
 *
 * The architecture doc requires treating every backend response as untrusted
 * input, exactly as page messages are. These guards are the boundary: nothing
 * downstream renders a value that has not passed through here. A single bad
 * cue is dropped and counted rather than poisoning the whole response.
 *
 * Field names mirror `backend/src/subtitle_translator/api/schemas.py`.
 */

export interface HealthResponse {
  status: string;
  version: string;
  prompt_version: string;
  provider: string;
  model: string;
  provider_configured: boolean;
}

export interface TranslatedCue {
  cue_index: number;
  start_ms: number;
  end_ms: number;
  persian_text: string;
}

export interface JobCreateResponse {
  job_id: number;
  video_id: string;
  status: string;
  cache_hit: boolean;
  total_cues: number;
  speech_cues: number | null;
  completed_cues: number;
  cues: TranslatedCue[];
}

export interface JobProgress {
  total_cues: number;
  speech_cues: number | null;
  completed_cues: number;
  failed_cues: number;
  percent: number;
}

export interface JobStatusResponse {
  job_id: number;
  video_id: string;
  status: string;
  done: boolean;
  progress: JobProgress;
  cues: TranslatedCue[];
  next_cursor: number | null;
  failed_indices: number[];
  error: { error_code: string; message: string } | null;
}

/** Request body for POST /jobs. The schema is `extra="forbid"`, so this is exact. */
export interface JobCreateRequest {
  video_id: string;
  title?: string;
  cues: Array<{
    cue_index: number;
    start_ms: number;
    end_ms: number;
    english_text: string;
  }>;
}

// --- validation ------------------------------------------------------------

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null;
}

function num(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value);
}

function nullableNum(value: unknown): value is number | null {
  return value === null || num(value);
}

export interface CueValidation {
  cues: TranslatedCue[];
  /** Cues rejected by validation — surfaced for debugging, never rendered. */
  dropped: number;
}

/**
 * Keep only cues that are safe to turn into a VTTCue.
 *
 * Non-integer indexes, impossible timing, and empty text are dropped rather
 * than repaired: inventing a timestamp would put wrong text on screen, which
 * is worse than showing nothing.
 */
export function validateCues(value: unknown): CueValidation {
  if (!Array.isArray(value)) return { cues: [], dropped: 0 };

  const cues: TranslatedCue[] = [];
  let dropped = 0;

  for (const raw of value) {
    if (
      isObject(raw) &&
      num(raw.cue_index) &&
      Number.isInteger(raw.cue_index) &&
      num(raw.start_ms) &&
      num(raw.end_ms) &&
      raw.start_ms >= 0 &&
      raw.end_ms > raw.start_ms &&
      typeof raw.persian_text === 'string' &&
      raw.persian_text.length > 0
    ) {
      cues.push({
        cue_index: raw.cue_index,
        start_ms: raw.start_ms,
        end_ms: raw.end_ms,
        persian_text: raw.persian_text,
      });
    } else {
      dropped += 1;
    }
  }

  return { cues, dropped };
}

export function validateHealth(value: unknown): HealthResponse | null {
  if (!isObject(value)) return null;
  if (typeof value.status !== 'string') return null;
  return {
    status: value.status,
    version: typeof value.version === 'string' ? value.version : '',
    prompt_version: typeof value.prompt_version === 'string' ? value.prompt_version : '',
    provider: typeof value.provider === 'string' ? value.provider : '',
    model: typeof value.model === 'string' ? value.model : '',
    provider_configured: value.provider_configured === true,
  };
}

export function validateJobCreate(value: unknown): JobCreateResponse | null {
  if (!isObject(value)) return null;
  if (!num(value.job_id) || typeof value.video_id !== 'string') return null;
  if (typeof value.status !== 'string') return null;

  return {
    job_id: value.job_id,
    video_id: value.video_id,
    status: value.status,
    cache_hit: value.cache_hit === true,
    total_cues: num(value.total_cues) ? value.total_cues : 0,
    speech_cues: nullableNum(value.speech_cues) ? value.speech_cues : null,
    completed_cues: num(value.completed_cues) ? value.completed_cues : 0,
    cues: validateCues(value.cues).cues,
  };
}

export function validateJobStatus(value: unknown): JobStatusResponse | null {
  if (!isObject(value)) return null;
  if (!num(value.job_id) || typeof value.video_id !== 'string') return null;
  if (typeof value.status !== 'string' || typeof value.done !== 'boolean') return null;

  const rawProgress = isObject(value.progress) ? value.progress : {};
  const progress: JobProgress = {
    total_cues: num(rawProgress.total_cues) ? rawProgress.total_cues : 0,
    speech_cues: nullableNum(rawProgress.speech_cues) ? rawProgress.speech_cues : null,
    completed_cues: num(rawProgress.completed_cues) ? rawProgress.completed_cues : 0,
    failed_cues: num(rawProgress.failed_cues) ? rawProgress.failed_cues : 0,
    percent: num(rawProgress.percent) ? rawProgress.percent : 0,
  };

  const error =
    isObject(value.error) && typeof value.error.error_code === 'string'
      ? {
          error_code: value.error.error_code,
          message: typeof value.error.message === 'string' ? value.error.message : '',
        }
      : null;

  return {
    job_id: value.job_id,
    video_id: value.video_id,
    status: value.status,
    done: value.done,
    progress,
    cues: validateCues(value.cues).cues,
    next_cursor: nullableNum(value.next_cursor) ? value.next_cursor : null,
    failed_indices: Array.isArray(value.failed_indices)
      ? value.failed_indices.filter((i): i is number => num(i) && Number.isInteger(i))
      : [],
    error,
  };
}

/**
 * Backend error_code -> a sentence the user can act on.
 *
 * The backend's own `message` is never shown: it is for the developer console.
 * An unknown code falls back to a generic line rather than leaking raw text.
 */
const ERROR_MESSAGES: Record<string, string> = {
  invalid_cues: 'The captions captured from this video were rejected as invalid.',
  too_many_cues: 'This video has more captions than the local translator accepts.',
  payload_too_large: 'The captions from this video are too large to send.',
  job_not_found: 'That translation job no longer exists. Try again.',
  job_in_progress: 'A translation for this video is already running.',
  jobs_unavailable: 'The local translator has persistence disabled, so jobs cannot run.',
  provider_error: 'The translation provider could not be reached.',
  resume_limit_exceeded: 'Translation was interrupted too many times to continue.',
  partial_translation: 'Some lines could not be translated.',
  internal_error: 'The local translator hit an internal error.',
  timeout: 'The local translator did not respond in time.',
  network_error: 'Could not reach the local translator.',
  bad_response: 'The local translator sent a response this extension could not read.',
  messaging_error: 'The extension could not reach its own background worker.',
};

export function userMessageFor(errorCode: string): string {
  return ERROR_MESSAGES[errorCode] ?? 'The local translator reported an unexpected problem.';
}
