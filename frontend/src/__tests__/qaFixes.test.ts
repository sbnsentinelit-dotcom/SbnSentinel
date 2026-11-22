import { describe, it, expect } from 'vitest';
import { formatMoney } from '@/utils/currency';
import { formatExposureTotals, isActionable } from '@/utils/signalSummary';
import type { SignalEvent } from '@/store/slices/signalSlice';

describe('QA Verification Fixes', () => {
  describe('formatMoney (Rule 2 & 5)', () => {
    it('returns "Unavailable" for null, undefined, or empty string', () => {
      expect(formatMoney(null)).toBe('Unavailable');
      expect(formatMoney(undefined)).toBe('Unavailable');
      expect(formatMoney('')).toBe('Unavailable');
    });

    it('preserves genuine 0 as $0.00 / AED 0.00', () => {
      expect(formatMoney(0, 'USD')).toBe('$0.00');
      expect(formatMoney(0, 'AED')).toBe('AED 0.00');
      expect(formatMoney('0', 'USD')).toBe('$0.00');
      expect(formatMoney('0', 'AED')).toBe('AED 0.00');
      expect(formatMoney('0.00', 'USD')).toBe('$0.00');
      expect(formatMoney('0.00', 'AED')).toBe('AED 0.00');
    });

    it('formats numeric value without guessing when currency is omitted', () => {
      expect(formatMoney(150)).toBe('150.00');
      expect(formatMoney(0)).toBe('0.00');
      expect(formatMoney('1,250.50')).toBe('1,250.50');
    });

    it('extracts currency from string if present', () => {
      expect(formatMoney('$150.00')).toBe('$150.00');
      expect(formatMoney('AED 500.00')).toBe('AED 500.00');
    });
  });

  describe('formatExposureTotals & isActionable (B-51)', () => {
    it('returns "Unavailable" when exposure totals are empty or missing (never guesses currency)', () => {
      expect(formatExposureTotals([])).toBe('Unavailable');
      expect(formatExposureTotals([null, undefined, ''])).toBe('Unavailable');
    });

    it('formats exposures per currency accurately', () => {
      expect(formatExposureTotals(['AED 500.00', 'AED 140.00'])).toBe('AED 640.00');
    });

    it('keeps approved/decided signals in actionable queue so Decision Recorded - APPROVED badge displays', () => {
      const mockSignal: SignalEvent = {
        id: 'sig_01',
        title: 'High Exposure Alert',
        severity: 'high',
        risk_level: 'High',
        recommended_action: 'Review and approve billing',
        status: 'active',
        timestamp: new Date().toISOString(),
        decision: { status: 'APPROVED' } as any,
      };
      // isActionable must return true for approved signal so it stays in queue
      expect(isActionable(mockSignal)).toBe(true);

      // Dismissed signals are dropped
      const dismissedSignal: SignalEvent = {
        ...mockSignal,
        status: 'dismissed',
      };
      expect(isActionable(dismissedSignal)).toBe(false);
    });
  });
});
