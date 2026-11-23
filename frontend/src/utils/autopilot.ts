import { fetchRecommendationReview, submitHumanDecision } from '@/utils/decisionApi';
import { fetchActionLifecycle } from '@/utils/actionLifecycle';
import { createAction, executeAction } from '@/utils/actionCommands';
import { fetchWithAuth } from '@/utils/fetchWithAuth';

export const AUTOPILOT_REASON = 'Auto-approved by Autopilot (Critical, all required evidence available)';

/** Autopilot only acts on Critical signals whose evaluation says every required input and every piece
 *  of evidence is available; everything else waits for a human decision. */
export function isAutopilotEligible(s: { risk_level?: string; risk_evaluation?: { autopilot_eligible?: boolean } | null }): boolean {
  return s.risk_level === 'Critical' && Boolean(s.risk_evaluation?.autopilot_eligible);
}

/** A claim resubmission is preferred when it is permitted; otherwise the first permitted action. */
export function chooseActionType(allowed: string[] | undefined): string | undefined {
  if (!allowed || allowed.length === 0) return undefined;
  return allowed.includes('RESUBMIT_CLAIM') ? 'RESUBMIT_CLAIM' : allowed[0];
}

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
  const actionType = chooseActionType(lifecycle.creation?.allowed_action_types);
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

export type EvidenceRetrieval = {
  status: string;
  complete: boolean;
  documents: { name: string; status: 'FOUND' | 'NOT_FOUND'; source: string | null; document_ref: string | null }[];
  not_found: string[];
  searched_at?: string;
  requested_by?: string;
};

/** Collects the evidence the payer needs for a denied claim from the connected systems. Anything not
 *  found raises a bell notification in the backend. */
export async function retrieveClaimEvidence(signalId: string): Promise<EvidenceRetrieval | null> {
  try {
    const res = await fetchWithAuth(
      `${process.env.NEXT_PUBLIC_BACKEND_URL || ''}/api/v1/signals/${encodeURIComponent(signalId)}/evidence-retrieval`,
      { method: 'POST' });
    if (!res.ok) return null;
    return (await res.json()).retrieval as EvidenceRetrieval;
  } catch {
    return null;
  }
}

/**
 * Autopilot for one Critical signal. A denied claim first gets its evidence collected; it is only
 * approved and resubmitted when every document was found (otherwise a notification asks a person to
 * request the rest). Other Critical signals are approved and their permitted action executed.
 */
export async function runAutopilot(signal: { id: string; risk_evaluation?: { autopilot_action?: string | null } | null }): Promise<AutopilotResult> {
  if (signal.risk_evaluation?.autopilot_action === 'resubmit_claim') {
    const retrieval = await retrieveClaimEvidence(signal.id);
    if (!retrieval) {
      return { signalId: signal.id, ok: false, step: 'review', message: 'Evidence could not be collected.' };
    }
    if (!retrieval.complete) {
      await logAutopilot(signal.id, `Autopilot collected evidence; not found: ${retrieval.not_found.join(', ')}. Claim left for a person.`);
      return { signalId: signal.id, ok: false, step: 'review', message: `Evidence not found: ${retrieval.not_found.join(', ')}` };
    }
  }
  return runAutopilotForSignal(signal.id);
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
