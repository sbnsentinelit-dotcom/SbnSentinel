import { describe, expect, it } from 'vitest';
import {
  Summary, WorkItem, ageLabel, balanceRows, errorText, fromMinor, label, measured, openWork, parseExport,
  previousAcceptance, ROLE_LABELS, statusLabel, statusTone,
} from '@/utils/legacyClaims';

const zero = { payer_ar: 0, patient_ar: 0, credit: 0, unapplied: 0, unbilled: 0, closed_watch: 0 };

describe('Legacy Claims screen helpers (figures come from the server assessment)', () => {
  it('converts server minor units to amounts and keeps missing values missing', () => {
    expect(fromMinor(100000)).toBe(1000);
    expect(fromMinor(-500)).toBe(-5);
    expect(fromMinor(0)).toBe(0);
    expect(fromMinor(undefined)).toBeNull();
  });

  it('lists only buckets with an amount and shows the opening variance against the signed control', () => {
    const s = {
      control: { ...zero, payer_ar: 100000, credit: -500 },
      opening: { ...zero, payer_ar: 99900, credit: -500 },
      closing: { ...zero, payer_ar: 30000, credit: -500 },
    } as unknown as Summary;
    const rows = balanceRows(s);
    expect(rows.map((r) => r.bucket)).toEqual(['payer_ar', 'credit']);
    expect(rows[0]).toMatchObject({ label: 'Payer receivable', control: 100000, opening: 99900, closing: 30000, variance: -100 });
    expect(rows[1].variance).toBe(0);
  });

  it('labels statuses and evidence roles', () => {
    expect(statusLabel('SAMPLE_REVIEWABLE')).toBe('Reviewable');
    expect(statusLabel('HOLD')).toBe('On hold');
    expect(statusLabel(null)).toBe('Not assessed');
    expect(statusTone('HUMAN_EXIT_REVIEW')).toBe('good');
    expect(statusTone('HOLD')).toBe('bad');
    expect(label(ROLE_LABELS, 'bank')).toBe('Bank statements');
    expect(label(ROLE_LABELS, 'offset_18')).toBe('offset 18');
  });

  it('counts open and accepted work but not resolved items', () => {
    const items = [{ state: 'OPEN' }, { state: 'ACCEPTED' }, { state: 'RESOLVED' }] as WorkItem[];
    expect(openWork(items)).toHaveLength(2);
  });

  it('reads an uploaded export and refuses a file without records', () => {
    expect(parseExport('{"records":[{"key":"bank-1"}],"seal":{"receipt":"s"},"token":"t-1"}')).toEqual({
      records: [{ key: 'bank-1' }], seal: { receipt: 's' }, token: 't-1',
    });
    expect(() => parseExport('{"rows":[]}')).toThrow('records');
  });

  it('shows the server reason for a refused page', () => {
    expect(errorText({ detail: 'Unknown source for this transition.' }, 'x')).toBe('Unknown source for this transition.');
    expect(errorText({ detail: { message: 'The page was refused; nothing was stored.', records: [{ key: 'bank-1', reason: 'observation time is in the future' }] } }, 'x'))
      .toBe('The page was refused; nothing was stored. bank-1: observation time is in the future');
    expect(errorText({ detail: [{ msg: 'Field required' }] }, 'x')).toBe('Field required');
    expect(errorText(null, 'fallback')).toBe('fallback');
  });

  it('shows missing bank evidence as unknown and qualifies an unproven source (review 07.1)', () => {
    const fmt = (m: number) => `AED ${m / 100}`;
    expect(measured(null, 'missing', 'Bank evidence missing', fmt)).toBe('Bank evidence missing');
    expect(measured(0, 'complete', 'Bank evidence missing', fmt)).toBe('AED 0');
    expect(measured(90000, 'incomplete', 'Bank evidence missing', fmt)).toBe('AED 900 (coverage not proven)');
  });

  it('keeps an earlier acceptance visible after resolution and reopening (review 07.3)', () => {
    const w = {
      state: 'OPEN',
      history: [
        { event: 'OPENED', actor: 'publisher', at: '2026-11-05T18:00:00Z' },
        { event: 'ACCEPTED', actor: 'finance@clinic.test', at: '2026-11-05T19:00:00Z' },
        { event: 'RESOLVED', actor: 'publisher', at: '2026-11-06T09:00:00Z' },
        { event: 'REOPENED', actor: 'publisher', at: '2026-11-06T12:00:00Z' },
      ],
    } as unknown as WorkItem;
    expect(previousAcceptance(w)?.actor).toBe('finance@clinic.test');
    expect(previousAcceptance({ ...w, state: 'ACCEPTED' } as WorkItem)).toBeNull();
  });

  it('shows evidence age in hours, then days', () => {
    expect(ageLabel(17.1)).toBe('17 h');
    expect(ageLabel(72)).toBe('3 d');
  });
});
