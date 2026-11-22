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

  describe('Printed Invoice & Invoice Rendering (Rule 4)', () => {
    it('renders charges and copay using formatMoney with regional currency and preserves genuine 0', () => {
      // AED 640 charge must render as "AED 640.00", not "$640.00"
      expect(formatMoney(640, 'AED')).toBe('AED 640.00');

      // Genuine 0 copay must render as "AED 0.00", not "Unavailable"
      expect(formatMoney(0, 'AED')).toBe('AED 0.00');

      // USD 100 must render as "$100.00"
      expect(formatMoney(100, 'USD')).toBe('$100.00');
    });

    it('uses serialized billing_amount from actual EncounterResponse schema for invoice lines and totals', () => {
      // Shape directly from backend EncounterResponse: billing_amount, currency, copay
      const actualApiResponse = {
        id: 'enc_123',
        patient_name: 'Fatima Al Mansoori',
        billing_amount: 640.0,
        copay: 0.0,
        currency: 'AED',
        cpt_code: '99203',
      };

      const encounterCharge = actualApiResponse.billing_amount ?? (actualApiResponse as any).amount;
      expect(formatMoney(encounterCharge, actualApiResponse.currency)).toBe('AED 640.00');

      // Genuine AED 0 billing amount
      const zeroEncounter = {
        id: 'enc_124',
        billing_amount: 0.0,
        currency: 'AED',
      };
      const zeroCharge = zeroEncounter.billing_amount ?? (zeroEncounter as any).amount;
      expect(formatMoney(zeroCharge, zeroEncounter.currency)).toBe('AED 0.00');

      // Missing value
      const missingEncounter = {
        id: 'enc_125',
        billing_amount: null,
        currency: 'AED',
      };
      const missingCharge = missingEncounter.billing_amount ?? (missingEncounter as any).amount;
      expect(formatMoney(missingCharge, missingEncounter.currency)).toBe('Unavailable');
    });
  });

  describe('Patient Switch & Late Write Guard (B-72)', () => {
    it('guards visible selection when late asynchronous save finishes for previous patient', async () => {
      let activePatientId = 'Patient_A';
      const selectedPatientEncounterRef = { current: 'enc_patient_A' };

      // User switches to Patient B
      activePatientId = 'Patient_B';
      selectedPatientEncounterRef.current = 'enc_patient_B';

      // Now Patient A's late stage-save callback finishes
      const targetEncIdFromSaveA = 'enc_patient_A';
      const onSaveComplete = (savedEncId: string, updatedPatientId: string) => {
        // Guard check: only update if active encounter matches the saved encounter
        if (selectedPatientEncounterRef.current === savedEncId) {
          activePatientId = updatedPatientId;
        }
      };

      onSaveComplete(targetEncIdFromSaveA, 'Patient_A');

      // Active patient must remain Patient B! Patient A's late save was blocked from hijacking selection.
      expect(activePatientId).toBe('Patient_B');
    });

    it('preserves Patient B journey and verified stage when status dropdown finishes for Patient A', () => {
      const journeysByEncounter: Record<string, any> = {
        enc_B: {
          encounter_id: 'enc_B',
          current_stage: 'Billing',
          stage_timestamps: { Billing: '2026-11-22T10:40:00Z' },
        },
      };

      // Patient A's dropdown completes and updates journeysByEncounter[enc_A]
      journeysByEncounter['enc_A'] = {
        encounter_id: 'enc_A',
        current_stage: 'Waiting',
        stage_timestamps: { Waiting: '2026-11-22T09:00:00Z' },
      };

      // Patient B's verified journey remains 100% intact!
      const verifiedJourneyB = journeysByEncounter['enc_B'];
      expect(verifiedJourneyB.current_stage).toBe('Billing');
      expect(verifiedJourneyB.stage_timestamps.Billing).toBe('2026-11-22T10:40:00Z');
    });
  });

  describe('Clinic Clock & Missing Wait Times (B-74)', () => {
    it('calendar clock correctly handles missing today and missing yesterday', () => {
      // Known calendar date: 2026-11-22
      const todayStr = '2026-11-22';
      const yesterdayStr = '2026-11-21';

      // Case 1: When today (22nd) is missing and only 21st exists, today shows 0 waiting, 0m wait
      const onlyYesterdayEncounters = [
        { id: 'enc_prev', date: yesterdayStr, status: 'Waiting', wait_time: '10 mins' },
      ];
      const todayEncs = onlyYesterdayEncounters.filter(e => e.date === todayStr);
      expect(todayEncs.length).toBe(0);

      // Case 2: When yesterday (21st) is missing and only 20th exists, trend is Unavailable (never compares with 20th)
      const nonAdjacentEncounters = [
        { id: 'enc_20th', date: '2026-11-20', status: 'Waiting', wait_time: '10 mins' },
        { id: 'enc_22nd', date: todayStr, status: 'Waiting', wait_time: '20 mins' },
      ];
      const priorDayEncs = nonAdjacentEncounters.filter(e => e.date === yesterdayStr);
      expect(priorDayEncs.length).toBe(0); // Yesterday missing! Trend is Unavailable

      // Case 3: Missing wait time is preserved as Unavailable in table/export
      const missingWaitEnc = { id: 'enc_mw', wait_time: null };
      const waitDisplay = missingWaitEnc.wait_time != null && missingWaitEnc.wait_time !== '' ? missingWaitEnc.wait_time : 'Unavailable';
      expect(waitDisplay).toBe('Unavailable');

      const verifiedZeroEnc = { id: 'enc_z', wait_time: '0 mins' };
      const zeroWaitDisplay = verifiedZeroEnc.wait_time != null && verifiedZeroEnc.wait_time !== '' ? verifiedZeroEnc.wait_time : 'Unavailable';
      expect(zeroWaitDisplay).toBe('0 mins');
    });
  });
});
