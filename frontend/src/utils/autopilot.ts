import { fetchRecommendationReview, submitHumanDecision } from '@/utils/decisionApi';
import { fetchActionLifecycle } from '@/utils/actionLifecycle';
import { createAction, executeAction } from '@/utils/actionCommands';
import { fetchWithAuth } from '@/utils/fetchWithAuth';

export const AUTOPILOT_REASON = 'Auto-approved by Autopilot';

export type AutopilotResult = {
  signalId: string;
  ok: boolean;
  step: 'review' | 'decision' | 'action' | 'execute' | 'done';
  decisionId?: string;
  actionId?: string;
  message?: string;
  /** The decision stored for the signal (new, or one that already existed), if any. */
  decision?: { decision_id: string; recommendation_id: string; decision_type: string; decision_timestamp?: string | null };
};

/**
 * Handles one signal the way a user would on the review page, through the same governed APIs:
 * record an APPROVED decision (signed in user, reason "Auto-approved by Autopilot"), create the
 * first permitted action on the permitted target and execute it. Every step is stored by the
 * backend; nothing is counted only in the browser.
 */
export async function runAutopilotForSignal(signalId: string): Promise<AutopilotResult> {
  const review = await fetchRecommendationReview(signalId);
  const rec = review.recommendation;
  if (review.current_decision) {
    const d = review.current_decision;
    return { signalId, ok: false, step: 'review', message: 'Already decided.',
      decision: { decision_id: d.decision_id, recommendation_id: d.recommendation_id, decision_type: d.decision_type,
        decision_timestamp: d.decision_timestamp ?? null } };
  }
  if (!rec || review.authority?.state !== 'AUTHORIZED'
      || !review.authority.allowed_decisions.includes('APPROVED')) {
    return { signalId, ok: false, step: 'review', message: 'Not eligible for an automatic decision.' };
  }

  let decisionId: string | undefined;
  try {
    const recorded = await submitHumanDecision({
      recommendationId: rec.recommendation_id,
      decisionType: 'APPROVED',
      reason: AUTOPILOT_REASON,
    });
    decisionId = recorded?.decision_id;
  } catch (err) {
    return { signalId, ok: false, step: 'decision', message: err instanceof Error ? err.message : 'Decision failed' };
  }
  if (!decisionId) return { signalId, ok: false, step: 'decision', message: 'No decision ID returned.' };
  const decision = { decision_id: decisionId, recommendation_id: rec.recommendation_id, decision_type: 'APPROVED',
    decision_timestamp: new Date().toISOString() };

  let actionId: string | undefined;
  const lifecycle = await fetchActionLifecycle(decisionId);
  const actionType = lifecycle.creation?.allowed_action_types?.[0];
  const target = lifecycle.creation?.permitted_targets?.[0]?.target_id;
  if (actionType && target) {
    const created = await createAction({ decision_id: decisionId, action_type: actionType, target_reference: target, parameters: {} });
    if (!created.ok || !created.action_id) {
      await logAutopilot(signalId, `Autopilot approved ${rec.recommendation_id}; action could not be created: ${created.message ?? 'unknown error'}`);
      return { signalId, ok: false, step: 'action', decisionId, decision, message: created.message };
    }
    actionId = created.action_id;
    const executed = await executeAction(actionId);
    if (!executed.ok) {
      await logAutopilot(signalId, `Autopilot approved ${rec.recommendation_id} and created ${actionId}; execution failed: ${executed.message ?? 'unknown error'}`);
      return { signalId, ok: false, step: 'execute', decisionId, actionId, decision, message: executed.message };
    }
  }

  await logAutopilot(signalId, actionId
    ? `Autopilot approved ${rec.recommendation_id} (decision ${decisionId}) and completed action ${actionId}`
    : `Autopilot approved ${rec.recommendation_id} (decision ${decisionId}); no action is permitted for it`);
  return { signalId, ok: true, step: 'done', decisionId, actionId, decision };
}

async function logAutopilot(signalId: string, action: string): Promise<void> {
  try {
    // The backend records the signed in user as the actor.
    await fetchWithAuth(`${process.env.NEXT_PUBLIC_BACKEND_URL || ''}/api/v1/audit/`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action, module: 'Autopilot', resource: `Signal: ${signalId}` }),
    });
  } catch {
    // The decision and action are already stored; a failed audit note does not undo them.
  }
}
