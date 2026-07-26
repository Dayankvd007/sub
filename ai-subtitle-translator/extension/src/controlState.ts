/**
 * Pure presentation logic for the subtitle control (roadmap P3-01).
 *
 * Kept free of DOM and chrome APIs so every state transition is unit-testable.
 * The eight states are the ones the roadmap requires; `retrying` is a *client*
 * state (our poll could not reach the backend), deliberately distinct from the
 * backend's own bounded provider retries, which are invisible from here and
 * simply keep the job in `processing`.
 */

export type ControlState =
  | 'ready'
  | 'no-captions'
  | 'backend-unavailable'
  | 'translating'
  | 'completed'
  | 'partial'
  | 'retrying'
  | 'failed';

export interface ControlContext {
  /** Backend progress percentage while translating. */
  percent?: number;
  /** Cue indexes the backend could not translate (partial jobs). */
  failedCount?: number;
  /** Whether Persian subtitles are currently being displayed. */
  showing?: boolean;
}

export interface ControlView {
  /** Short glyph rendered in the player control bar. */
  label: string;
  /** Tooltip / accessible name carrying the real explanation. */
  title: string;
  disabled: boolean;
  /** Drives the "on" highlight, mirroring YouTube's own toggle buttons. */
  active: boolean;
}

/** Terminal backend job statuses mapped onto control states. */
export const TERMINAL_STATES: ReadonlySet<ControlState> = new Set([
  'completed',
  'partial',
  'failed',
]);

function percentLabel(percent: number | undefined): string {
  if (typeof percent !== 'number' || !Number.isFinite(percent)) return '';
  return ` ${Math.max(0, Math.min(100, Math.round(percent)))}%`;
}

/**
 * Map a state to exactly what the button should show. One function, so the
 * button can never drift out of sync with the job.
 */
export function describeControl(state: ControlState, context: ControlContext = {}): ControlView {
  const showing = context.showing ?? false;

  switch (state) {
    case 'ready':
      return {
        label: 'FA',
        title: 'Translate subtitles to Persian',
        disabled: false,
        active: false,
      };

    case 'no-captions':
      return {
        label: 'FA',
        title: 'No English captions are available for this video',
        disabled: true,
        active: false,
      };

    case 'backend-unavailable':
      return {
        label: 'FA',
        title:
          'Local translator is not running. Start it with "subtitle-api", then click to retry.',
        disabled: false,
        active: false,
      };

    case 'translating':
      return {
        label: `FA${percentLabel(context.percent)}`,
        title: `Translating to Persian…${percentLabel(context.percent)}`,
        disabled: false,
        active: true,
      };

    case 'completed':
      return {
        label: 'FA',
        title: showing ? 'Persian subtitles on — click to hide' : 'Persian subtitles ready',
        disabled: false,
        active: showing,
      };

    case 'partial': {
      const failed = context.failedCount ?? 0;
      return {
        label: 'FA',
        title: `Persian subtitles ready — ${failed} line${failed === 1 ? '' : 's'} could not be translated`,
        disabled: false,
        active: showing,
      };
    }

    case 'retrying':
      return {
        label: 'FA',
        title: 'Lost contact with the local translator — reconnecting…',
        disabled: false,
        active: true,
      };

    case 'failed':
      return {
        label: 'FA',
        title: 'Translation failed — click to retry (already translated lines are kept)',
        disabled: false,
        active: false,
      };
  }
}

/** Backend job status -> control state. */
export function stateForJobStatus(status: string): ControlState {
  switch (status) {
    case 'queued':
    case 'processing':
      return 'translating';
    case 'completed':
      return 'completed';
    case 'partial':
      return 'partial';
    case 'failed':
      return 'failed';
    default:
      // An unrecognized status must not silently look healthy.
      return 'failed';
  }
}
