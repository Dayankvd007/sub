/**
 * The content-script <-> service-worker message contract.
 *
 * Two operations only. The worker is a stateless broker: it injects the
 * Phase 0 extractor, or forwards one HTTP request. It never holds job state
 * and never runs a polling loop — MV3 terminates idle workers after roughly
 * 30 seconds, so a timer living there would stop without warning. All
 * lifecycle state belongs to the content script's VideoSession.
 */

import type { NormalizedCue } from './normalize';

export const CAPTURE_FOR_TAB = 'CAPTURE_FOR_TAB' as const;
export const BACKEND_FETCH = 'BACKEND_FETCH' as const;

/**
 * Capture captions for the tab the message came from.
 *
 * Deliberately carries no tab id: the worker reads `sender.tab.id`, so a user
 * switching tabs mid-capture cannot make us extract from the wrong video. (The
 * Phase 0 popup path still resolves the active tab; that behaviour is
 * unchanged.)
 */
export interface CaptureForTabMessage {
  type: typeof CAPTURE_FOR_TAB;
}

/** Why a capture failed, at the granularity the UI actually distinguishes. */
export type CaptureFailureKind = 'no-captions' | 'extraction-failed' | 'not-a-watch-page';

export type CaptureForTabResult =
  | {
      ok: true;
      videoId: string;
      cues: NormalizedCue[];
      trackKind: string | null;
      droppedEvents: number;
    }
  | { ok: false; kind: CaptureFailureKind; error: string };

export interface BackendFetchMessage {
  type: typeof BACKEND_FETCH;
  method: 'GET' | 'POST';
  /** Path only, e.g. "/jobs" or "/jobs/12?after_cue_index=40". */
  path: string;
  body?: unknown;
  timeoutMs: number;
}

export type BackendFetchResult =
  | { ok: true; status: number; data: unknown }
  | {
      ok: false;
      status: number | null;
      /** Backend `error_code` when it supplied one, else a transport code. */
      errorCode: string;
      message: string;
    };

export type ExtensionMessage = CaptureForTabMessage | BackendFetchMessage;

/** Transport-level failure codes, distinct from any backend error_code. */
export const TRANSPORT_ERRORS = {
  timeout: 'timeout',
  network: 'network_error',
  badResponse: 'bad_response',
  messaging: 'messaging_error',
} as const;
