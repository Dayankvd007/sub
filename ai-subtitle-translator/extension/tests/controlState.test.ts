import { describe, expect, it } from 'vitest';

import {
  describeControl,
  stateForJobStatus,
  type ControlState,
} from '../src/controlState';

const ALL_STATES: ControlState[] = [
  'ready',
  'no-captions',
  'backend-unavailable',
  'translating',
  'completed',
  'partial',
  'retrying',
  'failed',
];

describe('describeControl', () => {
  it('covers every roadmap P3-01 state with a usable label and explanation', () => {
    for (const state of ALL_STATES) {
      const view = describeControl(state);
      expect(view.label, state).toBeTruthy();
      expect(view.title.length, state).toBeGreaterThan(10);
    }
  });

  it('disables the control only when there is genuinely nothing to do', () => {
    expect(describeControl('no-captions').disabled).toBe(true);
    // A missing backend is recoverable: the user starts it and clicks again.
    expect(describeControl('backend-unavailable').disabled).toBe(false);
    expect(describeControl('failed').disabled).toBe(false);
    expect(describeControl('ready').disabled).toBe(false);
  });

  it('shows translation progress in the label', () => {
    expect(describeControl('translating', { percent: 42.4 }).label).toBe('FA 42%');
    expect(describeControl('translating', { percent: 0 }).label).toBe('FA 0%');
  });

  it('clamps and tolerates nonsense progress values', () => {
    expect(describeControl('translating', { percent: 250 }).label).toBe('FA 100%');
    expect(describeControl('translating', { percent: -10 }).label).toBe('FA 0%');
    expect(describeControl('translating', { percent: NaN }).label).toBe('FA');
    expect(describeControl('translating', {}).label).toBe('FA');
  });

  it('reports how many lines a partial job could not translate', () => {
    expect(describeControl('partial', { failedCount: 3 }).title).toContain('3 lines');
    expect(describeControl('partial', { failedCount: 1 }).title).toContain('1 line');
  });

  it('reflects whether subtitles are currently displayed', () => {
    expect(describeControl('completed', { showing: true }).active).toBe(true);
    expect(describeControl('completed', { showing: false }).active).toBe(false);
    expect(describeControl('completed', { showing: true }).title).toContain('hide');
  });

  it('tells the user how to fix an unavailable backend', () => {
    expect(describeControl('backend-unavailable').title).toContain('subtitle-api');
  });
});

describe('stateForJobStatus', () => {
  it('maps every backend job status', () => {
    expect(stateForJobStatus('queued')).toBe('translating');
    expect(stateForJobStatus('processing')).toBe('translating');
    expect(stateForJobStatus('completed')).toBe('completed');
    expect(stateForJobStatus('partial')).toBe('partial');
    expect(stateForJobStatus('failed')).toBe('failed');
  });

  it('never treats an unknown status as healthy', () => {
    expect(stateForJobStatus('something-new')).toBe('failed');
    expect(stateForJobStatus('')).toBe('failed');
  });
});
