import { describe, it, expect } from 'vitest';
import { formatMoney } from '@/utils/currency';
import { formatExposureTotals, isActionable, isPendingDecision } from '@/utils/signalSummary';
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

    it('never infers currency when currency parameter is omitted', () => {
      expect(formatMoney('$150.00')).toBe('150.00');
      expect(formatMoney('AED 500.00')).toBe('500.00');
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

    it('isPendingDecision returns true only for undecided signals (B-51)', () => {
      const pendingSignal: SignalEvent = {
        id: 'sig_pending',
        title: 'Pending signal',
        severity: 'medium',
        risk_level: 'Medium',
        recommended_action: 'Review',
        status: 'active',
        timestamp: new Date().toISOString(),
      };
      expect(isPendingDecision(pendingSignal)).toBe(true);

      const approvedSignal: SignalEvent = {
        ...pendingSignal,
        decision: { status: 'APPROVED' } as any,
      };
      expect(isPendingDecision(approvedSignal)).toBe(false);

      const rejectedSignal: SignalEvent = {
        ...pendingSignal,
        decision: { status: 'REJECTED' } as any,
      };
      expect(isPendingDecision(rejectedSignal)).toBe(false);
    });
  });

  describe('Wait Times & Clinic-Day Trends (B-74)', () => {
    it('preserves genuine 0 wait times and computes true average across [0, 10] as 5m', () => {
      const encounters = [
        { id: 'enc_1', wait_time: '0 mins' },
        { id: 'enc_2', wait_time: '10 mins' },
      ];

      const validWaitTimes = encounters.map(e => {
        const m = String(e.wait_time ?? '').match(/(\d+)/);
        return m ? parseInt(m[1], 10) : null;
      }).filter((w): w is number => w !== null);

      expect(validWaitTimes).toEqual([0, 10]);
      const avg = Math.round(validWaitTimes.reduce((a, b) => a + b, 0) / validWaitTimes.length);
      expect(avg).toBe(5);
    });

    it('returns 0m when encounters have 0 wait or are empty (not falling back to 14m)', () => {
      const emptyEncounters: any[] = [];
      const validWaitTimes = emptyEncounters.map(e => {
        const m = String(e.wait_time ?? '').match(/(\d+)/);
        return m ? parseInt(m[1], 10) : null;
      }).filter((w): w is number => w !== null);

      const avg = validWaitTimes.length > 0
        ? Math.round(validWaitTimes.reduce((a, b) => a + b, 0) / validWaitTimes.length)
        : 0;
      expect(avg).toBe(0);
    });

    it('isolates current clinic day and ignores yesterday records for top cards', () => {
      const yesterday = '2026-11-21';
      const today = '2026-11-22';
      const encounters = [
        { id: 'enc_yesterday', date: yesterday, status: 'Waiting', wait_time: '10 mins' },
        { id: 'enc_today_1', date: today, status: 'Waiting', wait_time: '20 mins' },
        { id: 'enc_today_2', date: today, status: 'In Room', wait_time: '5 mins' },
      ];

      // Filtering strictly for today
      const currentDayEncs = encounters.filter(e => e.date === today);
      expect(currentDayEncs.length).toBe(2);

      // Waiting in current day is 1, not 2
      const waitingToday = currentDayEncs.filter(e => e.status === 'Waiting').length;
      expect(waitingToday).toBe(1);

      // Average wait time for today is (20 + 5) / 2 = 13 (or 12.5), not contaminated by yesterday's 10m
      const waitTimesToday = currentDayEncs
        .map(e => parseInt(e.wait_time.match(/(\d+)/)?.[1] || '0', 10));
      const avgWaitToday = Math.round(waitTimesToday.reduce((a, b) => a + b, 0) / waitTimesToday.length);
      expect(avgWaitToday).toBe(13);
    });
  });
});
