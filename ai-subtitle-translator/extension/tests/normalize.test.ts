import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import { normalizeJson3ToCues } from '../src/normalize';

const here = dirname(fileURLToPath(import.meta.url));

function loadFixture(name: string): unknown {
  return JSON.parse(readFileSync(join(here, 'fixtures', name), 'utf8'));
}

describe('normalizeJson3ToCues — synthetic manual-caption fixture', () => {
  const payload = loadFixture('synthetic-manual-json3.json') as { events: unknown[] };
  const { cues, issues } = normalizeJson3ToCues(payload);

  it('produces one cue per spoken event, dropping the position-only and whitespace-only events', () => {
    expect(cues).toHaveLength(5);
    expect(issues).toHaveLength(0);
  });

  it('assigns dense, zero-based, ordered cue indexes', () => {
    cues.forEach((cue, i) => expect(cue.cue_index).toBe(i));
  });

  it('produces valid, strictly increasing, non-overlapping timing', () => {
    for (let i = 0; i < cues.length; i += 1) {
      const cue = cues[i]!;
      expect(cue.end_ms).toBeGreaterThan(cue.start_ms);
      if (i > 0) {
        expect(cue.start_ms).toBeGreaterThanOrEqual(cues[i - 1]!.start_ms);
      }
    }
  });

  it('trims whitespace-only text down to an empty, filtered-out cue', () => {
    expect(cues.some((c) => c.english_text.trim().length === 0)).toBe(false);
  });

  it('preserves readable English text', () => {
    expect(cues[0]!.english_text).toBe("Welcome back to the channel,");
    expect(cues[cues.length - 1]!.english_text).toBe("Let's dive right in.");
  });
});

describe('normalizeJson3ToCues — synthetic auto-caption (asr) fixture', () => {
  const payload = loadFixture('synthetic-auto-json3.json') as { events: unknown[] };
  const { cues, issues } = normalizeJson3ToCues(payload);

  it('keeps rolling-duplicate text as-is (Phase 1 owns dedup, not Phase 0)', () => {
    expect(cues.map((c) => c.english_text)).toEqual([
      'so today',
      "so today we're going",
      "we're going to talk about",
      'to talk about caption extraction',
      'and normalization of cues',
    ]);
  });

  it('flags malformed events as issues instead of silently dropping them unexplained', () => {
    expect(issues).toHaveLength(3);
    expect(issues.map((i) => i.reason).sort()).toEqual(['invalid-timing', 'invalid-timing', 'missing-start']);
  });

  it('collapses embedded newlines in rolling captions into single spaces', () => {
    expect(cues.some((c) => c.english_text.includes('\n'))).toBe(false);
  });

  it('re-indexes densely from zero even though some raw events were dropped', () => {
    cues.forEach((cue, i) => expect(cue.cue_index).toBe(i));
  });
});

describe('normalizeJson3ToCues — edge cases', () => {
  it('returns no cues and no issues for an empty events array', () => {
    const { cues, issues } = normalizeJson3ToCues({ events: [] });
    expect(cues).toEqual([]);
    expect(issues).toEqual([]);
  });

  it('handles a payload with no events key at all', () => {
    const { cues, issues } = normalizeJson3ToCues({});
    expect(cues).toEqual([]);
    expect(issues).toEqual([]);
  });
});
