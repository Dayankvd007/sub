/**
 * Diagnostic logging for the real-YouTube workflow.
 *
 * Added after the first P3-07 gate attempt, where the FA control was visible
 * but clicking it produced no observable effect anywhere — no state change, no
 * backend request, no error. There was no way to tell from the outside whether
 * the click handler fired, the message reached the service worker, or the
 * extractor failed. These logs make each hop visible.
 *
 * Deliberately `console.log`, not `console.debug`: they must show up in the
 * default Chrome console during a gate run without the user enabling Verbose.
 *
 * Never log a caption URL's query string (it carries YouTube session
 * parameters), a raw caption payload, or anything from the environment. Use
 * `redactUrl` for any URL.
 */

export const LOG_PREFIX = '[ai-subtitle]';

export type LogEvent =
  | 'control-click'
  | 'session-start'
  | 'capture-message-sent'
  | 'capture-message-received'
  | 'extractor-start'
  | 'extractor-result'
  | 'post-jobs-start'
  | 'post-jobs-result'
  | 'workflow-error';

export function log(event: LogEvent, detail?: Record<string, unknown>): void {
  if (detail === undefined) {
    console.log(`${LOG_PREFIX} ${event}`);
    return;
  }
  console.log(`${LOG_PREFIX} ${event}`, detail);
}

/**
 * Origin + path only. A timedtext URL carries session-scoped query parameters
 * that must never reach a log line.
 */
export function redactUrl(url: string | null | undefined): string {
  if (!url) return '';
  try {
    const parsed = new URL(url);
    return `${parsed.origin}${parsed.pathname}?<redacted>`;
  } catch {
    return '<unparseable-url>';
  }
}
