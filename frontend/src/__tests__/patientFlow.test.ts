import { describe, it, expect, vi, beforeEach } from 'vitest';
import * as fetchModule from '../utils/fetchWithAuth';
import { clinicTime, postFlow, sparkPath, trendClass } from '../utils/patientFlow';

describe('Patient Flow sparkline', () => {
  it('draws the calculated series and skips missing points', () => {
    expect(sparkPath([0, 2, 4])).toBe('M0,27 L50,15 L100,3');
    expect(sparkPath([null, 3, null, 3])).toBe('M33,3 L100,3');
    expect(sparkPath([5])).toBe('M0,3 L100,3');
    expect(sparkPath([])).toBe('');
    expect(sparkPath([null, null])).toBe('');
  });
});

describe('Patient Flow trend colour', () => {
  it('follows whether the change is good, bad or neutral', () => {
    expect(trendClass({ trend: '+50%', trend_good: true })).toContain('positive');
    expect(trendClass({ trend: '+6m', trend_good: false })).toContain('critical');
    expect(trendClass({ trend: '+0', trend_good: null })).toBe('text-white/40');
    expect(trendClass({ trend: null, trend_good: null })).toBe('text-white/40');
  });
});

describe('Clinic time', () => {
  it('shows UTC instants in UAE time', () => {
    expect(clinicTime('2026-11-23T05:00:00Z')).toBe('09:00');
    expect(clinicTime('2026-11-22T19:55:00Z')).toBe('23:55');
    expect(clinicTime(null)).toBeNull();
    expect(clinicTime('not a date')).toBeNull();
  });
});

describe('Patient Flow actions', () => {
  beforeEach(() => vi.restoreAllMocks());

  it('reports the server reason when an action is refused', async () => {
    vi.spyOn(fetchModule, 'fetchWithAuth').mockResolvedValue({
      ok: false, status: 409,
      json: async () => ({ detail: 'Delayed is calculated: a patient waiting more than 30 minutes since arrival is shown as Delayed.' }),
    } as unknown as Response);
    await expect(postFlow('/visits/V1/status', { status: 'Delayed' })).rejects.toThrow('Delayed is calculated');
  });

  it('posts to the patient flow API', async () => {
    const spy = vi.spyOn(fetchModule, 'fetchWithAuth').mockResolvedValue({
      ok: true, status: 200, json: async () => ({ status: 'SENT', receipt_ref: 'MSG-1' }),
    } as unknown as Response);
    const result = await postFlow<{ status: string }>('/suggestions/late:A:B/approve', { idempotency_key: 'k' });
    expect(result.status).toBe('SENT');
    expect(String(spy.mock.calls[0][0])).toBe('/api/v1/patient-flow/suggestions/late:A:B/approve');
  });
});
