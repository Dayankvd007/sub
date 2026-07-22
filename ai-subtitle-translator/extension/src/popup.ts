/**
 * Popup UI. Deliberately tiny: shows the current watch-page/video-ID status
 * (written by content.ts), lets the user trigger a capture, shows a
 * debuggable summary or the exact error, and lets the user download the
 * resulting cue fixture as JSON for inspection / regression testing.
 */

interface WatchStatus {
  isWatchPage: boolean;
  videoId: string | null;
  url: string;
  updatedAt: number;
}

interface CaptureFixture {
  video_id: string;
  captured_at: string;
  source_url: string;
  player_response_source: string | null;
  selected_track: { languageCode: string; kind: string; name: string; vssId: string } | null;
  available_tracks: Array<{ languageCode: string; kind: string; name: string; vssId: string }>;
  raw_event_count: number;
  cue_count: number;
  normalization_issues: unknown[];
  cues: Array<{ cue_index: number; start_ms: number; end_ms: number; english_text: string }>;
}

interface CaptureResponse {
  ok: boolean;
  error?: string;
  fixture?: CaptureFixture;
}

const statusEl = document.getElementById('status') as HTMLDivElement;
const captureBtn = document.getElementById('captureBtn') as HTMLButtonElement;
const downloadBtn = document.getElementById('downloadBtn') as HTMLButtonElement;
const outputEl = document.getElementById('output') as HTMLDivElement;

let lastFixture: CaptureFixture | null = null;

function formatMs(ms: number): string {
  const totalSeconds = Math.floor(ms / 1000);
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${minutes}:${seconds.toString().padStart(2, '0')}`;
}

async function renderStatus(): Promise<void> {
  const stored = await chrome.storage.local.get('watchStatus');
  const status = stored.watchStatus as WatchStatus | undefined;
  if (!status || !status.isWatchPage) {
    statusEl.textContent = 'Not on a YouTube watch page. Open a video (youtube.com/watch?v=...) first.';
    captureBtn.disabled = true;
    return;
  }
  statusEl.textContent = `Watch page detected.\nVideo ID: ${status.videoId}\nLast seen: ${new Date(status.updatedAt).toLocaleTimeString()}`;
  captureBtn.disabled = false;
}

function renderError(message: string): void {
  outputEl.innerHTML = '';
  const box = document.createElement('div');
  box.className = 'error';
  box.textContent = message;
  outputEl.appendChild(box);
  downloadBtn.disabled = true;
  lastFixture = null;
}

function renderFixture(fixture: CaptureFixture): void {
  lastFixture = fixture;
  downloadBtn.disabled = false;

  outputEl.innerHTML = '';

  const summary = document.createElement('div');
  summary.className = 'result';
  const track = fixture.selected_track;
  const kindLabel = track?.kind === 'asr' ? 'auto-generated (asr)' : 'manual';
  const first = fixture.cues[0];
  const last = fixture.cues[fixture.cues.length - 1];
  summary.textContent =
    `Track: ${track?.languageCode ?? '?'} (${kindLabel})${track?.name ? ` — "${track.name}"` : ''}\n` +
    `Player response source: ${fixture.player_response_source}\n` +
    `Raw events: ${fixture.raw_event_count} → Normalized cues: ${fixture.cue_count}\n` +
    `Issues dropped during normalization: ${fixture.normalization_issues.length}\n` +
    (first && last
      ? `Range: ${formatMs(first.start_ms)} → ${formatMs(last.end_ms)}\n`
      : 'No cues produced.\n');
  outputEl.appendChild(summary);

  const preview = document.createElement('div');
  preview.className = 'cue-preview';
  fixture.cues.slice(0, 5).forEach((cue) => {
    const row = document.createElement('div');
    row.textContent = `#${cue.cue_index} [${formatMs(cue.start_ms)}–${formatMs(cue.end_ms)}] ${cue.english_text}`;
    preview.appendChild(row);
  });
  if (fixture.cues.length > 5) {
    const more = document.createElement('div');
    more.className = 'muted';
    more.textContent = `… and ${fixture.cues.length - 5} more cues (see downloaded JSON).`;
    preview.appendChild(more);
  }
  outputEl.appendChild(preview);
}

captureBtn.addEventListener('click', () => {
  captureBtn.disabled = true;
  outputEl.innerHTML = '<div class="muted">Capturing…</div>';
  chrome.runtime.sendMessage({ type: 'CAPTURE_CAPTIONS' }, (response: CaptureResponse) => {
    captureBtn.disabled = false;
    if (chrome.runtime.lastError) {
      renderError(`Extension messaging error: ${chrome.runtime.lastError.message}`);
      return;
    }
    if (!response.ok || !response.fixture) {
      renderError(response.error ?? 'Unknown capture failure.');
      return;
    }
    renderFixture(response.fixture);
  });
});

downloadBtn.addEventListener('click', () => {
  if (!lastFixture) return;
  const json = JSON.stringify(lastFixture, null, 2);
  const blob = new Blob([json], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const filename = `caption-fixture-${lastFixture.video_id}-${Date.now()}.json`;
  chrome.downloads.download({ url, filename, saveAs: true }, () => {
    URL.revokeObjectURL(url);
  });
});

renderStatus();
chrome.storage.onChanged.addListener((changes, area) => {
  if (area === 'local' && changes.watchStatus) {
    renderStatus();
  }
});
