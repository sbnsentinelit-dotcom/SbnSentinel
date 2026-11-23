import { describe, it, expect } from 'vitest';
import { byRiskPriority, isActionable, isPendingDecision } from '../utils/signalSummary';
import { chooseActionType, isAutopilotEligible } from '../utils/autopilot';
import type { SignalEvent } from '../store/slices/signalSlice';

const sig = (id: string, risk_level: string, deadline?: string, priority_score = 0): SignalEvent => ({
  id, source: 's', type: 'Claim', message: id, timestamp: '2026-11-22T10:00:00Z', risk_level, priority_score,
  risk_evaluation: deadline ? { level: risk_level, deadline } : null,
} as SignalEvent);

describe('Priority queue order', () => {
  it('puts Critical first, then High, then Moderate, then everything else', () => {
    const ordered = [sig('m', 'Moderate'), sig('i', 'Information'), sig('h', 'High'), sig('c', 'Critical')]
      .sort(byRiskPriority).map((s) => s.id);
    expect(ordered).toEqual(['c', 'h', 'm', 'i']);
  });

  it('orders the same level by the nearest deadline, then by priority score', () => {
    const ordered = [
      sig('late', 'Critical', '2026-11-25T10:00:00Z'),
      sig('none', 'Critical', undefined, 99),
      sig('soon', 'Critical', '2026-11-23T10:00:00Z'),
    ].sort(byRiskPriority).map((s) => s.id);
    expect(ordered).toEqual(['soon', 'late', 'none']);
  });
});

describe('Pending decisions (Intelligence summary cards and Autopilot)', () => {
  const open = { ...sig('open', 'Critical'), recommended_action: 'Resubmit claim' } as SignalEvent;
  const approved = { ...open, id: 'approved', decision_status: 'APPROVED' } as SignalEvent;
  const decided = { ...open, id: 'decided', decision: 'APPROVED' } as SignalEvent;

  it('keeps decided signals in the queue but counts only open ones as pending', () => {
    expect([open, approved, decided].filter(isActionable).map((s) => s.id)).toEqual(['open', 'approved', 'decided']);
    expect([open, approved, decided].filter(isPendingDecision).map((s) => s.id)).toEqual(['open']);
  });
});

describe('Autopilot', () => {
  it('acts only on Critical signals whose evaluation has all evidence', () => {
    expect(isAutopilotEligible({ risk_level: 'Critical', risk_evaluation: { autopilot_eligible: true } })).toBe(true);
    expect(isAutopilotEligible({ risk_level: 'Critical', risk_evaluation: { autopilot_eligible: false } })).toBe(false);
    expect(isAutopilotEligible({ risk_level: 'High', risk_evaluation: { autopilot_eligible: true } })).toBe(false);
    expect(isAutopilotEligible({ risk_level: 'Critical', risk_evaluation: null })).toBe(false);
  });

  it('prefers resubmitting the claim when that action is permitted', () => {
    expect(chooseActionType(['CREATE_FOLLOWUP_TASK', 'RESUBMIT_CLAIM'])).toBe('RESUBMIT_CLAIM');
    expect(chooseActionType(['CREATE_FOLLOWUP_TASK'])).toBe('CREATE_FOLLOWUP_TASK');
    expect(chooseActionType([])).toBeUndefined();
  });
});
