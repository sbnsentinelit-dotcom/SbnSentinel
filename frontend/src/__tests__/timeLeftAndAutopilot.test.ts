import { describe, it, expect, vi, beforeEach } from 'vitest';
import * as fetchModule from '../utils/fetchWithAuth';
import { timeLeftText, timeLeftTone } from '../utils/timeLeft';
import { runAutopilot } from '../utils/autopilot';

const NOW = Date.parse('2026-11-22T10:00:00Z');
const plus = (hours: number) => new Date(NOW + hours * 3600000).toISOString();

describe('Time left badge', () => {
  it('counts down from one day to the next', () => {
    expect(timeLeftText(plus(74), NOW)).toBe('3 days 2 hours left');
    expect(timeLeftText(plus(74), NOW + 24 * 3600000)).toBe('2 days 2 hours left');
    expect(timeLeftText(plus(5 + 10 / 60), NOW)).toBe('5 hours 10 minutes left');
    expect(timeLeftText(plus(-26), NOW)).toBe('Deadline passed 1 day 2 hours ago');
    expect(timeLeftText(null, NOW)).toBeNull();
  });

  it('colours by urgency', () => {
    expect(timeLeftTone(plus(5), NOW)).toBe('critical');
    expect(timeLeftTone(plus(72), NOW)).toBe('warning');
    expect(timeLeftTone(plus(200), NOW)).toBe('calm');
    expect(timeLeftTone(plus(-1), NOW)).toBe('passed');
  });
});

describe('Autopilot with evidence retrieval', () => {
  beforeEach(() => vi.restoreAllMocks());

  it('does not approve a claim when evidence is missing', async () => {
    const calls: string[] = [];
    vi.spyOn(fetchModule, 'fetchWithAuth').mockImplementation(async (url: any) => {
      calls.push(String(url));
      return {
        ok: true,
        json: async () => ({ retrieval: { status: 'INCOMPLETE', complete: false, not_found: ['Pathology report'], documents: [] } }),
      } as unknown as Response;
    });
    const result = await runAutopilot({ id: 'SIG-1', risk_evaluation: { autopilot_action: 'resubmit_claim' } });
    expect(result.ok).toBe(false);
    expect(result.message).toBe('Evidence not found: Pathology report');
    expect(calls.some((u) => u.includes('/signals/SIG-1/evidence-retrieval'))).toBe(true);
    expect(calls.some((u) => u.includes('/decisions'))).toBe(false);
  });
});
