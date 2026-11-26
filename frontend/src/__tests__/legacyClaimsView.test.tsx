import React from 'react';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const fetchWithAuth = vi.fn();
vi.mock('@/utils/fetchWithAuth', () => ({ fetchWithAuth: (...args: unknown[]) => fetchWithAuth(...args) }));

import { LegacyClaimsView } from '@/components/CommandCenter/LegacyClaimsView';

const zero = { payer_ar: 0, patient_ar: 0, credit: 0, unapplied: 0, unbilled: 0, closed_watch: 0 };

const transition = (id: string, payer: string, label: string) => ({
  id, label, scope: { deployment: 'development', region: 'UAE', org: 'ORG', clinic: 'C1', facility: 'C1', payee: 'C1', payer, currency: 'AED' },
  cutover: '2026-11-01T00:00:00Z', routes: { new: 'dest', old: 'old', fallback: 'fb', archive: 'arc' },
  max_probe_age_days: 14, origin: 'clinic', created_at: '2026-11-01T00:00:00Z',
  current_assessment_id: `LCA-${id}`, latest_status: 'SAMPLE_REVIEWABLE', latest_as_of: '2026-11-05T18:00:00Z',
});

const T = { A: transition('LCT-A', 'TPA-A', 'Run-off A'), B: transition('LCT-B', 'TPA-B', 'Run-off B'), C: transition('LCT-C', 'TPA-C', 'Run-off C') };

const assessment = (t: typeof T.A, payerAr: number) => ({
  assessment_id: t.current_assessment_id, transition: t, as_of: '2026-11-05T18:00:00Z', revision: 1,
  published: true, is_current: true, computed_at: '2026-11-05T18:00:00Z', status: 'SAMPLE_REVIEWABLE',
  policy_version: 'p', mapping_version: 'm', review_quality: 'RESEARCH_ONLY', record_count: 32,
  source_age_hours: 10, last_received_at: null, issues: [], evidence_count: 1,
  work_items: [{ id: 1, kind: 'follow_up', code: 'WORK_DENIAL', item: `den-${t.id}`, message: `Work denial of ${t.label}`,
    issues: [], history: [], owner: 'bill-team', state: 'OPEN', accepted_by: null, accepted_at: null, note: null, resolved_at: null }],
  summary: {
    currency: 'AED', lines: { legacy: 1, new: 1 },
    control: { ...zero, payer_ar: payerAr }, opening: { ...zero, payer_ar: payerAr }, closing: { ...zero, payer_ar: payerAr },
    cash: { advised: 90000, bank_observed: 90000, bank_confirmed: 90000, bank_unmatched: 0, bank_coverage: 'complete',
      ledger_posted: 90000, ledger_coverage: 'complete', provider_adjustments: 0, unapplied_bank: 0, noncash_advices: 0 },
    denials: [], routes: [], sources: [],
  },
  authority: 'Review only.',
});

const review = (inventory: string, subscription: string) => ({
  inventory_id: inventory, subscription, as_of: '2026-11-05T18:00:00Z', received_at: '2026-11-05T18:00:00Z',
  status: 'HUMAN_EXIT_REVIEW', issues: [], scopes: [], dependencies: [], signoffs: [],
  savings: { currency: 'AED', avoided: 1, incremental: 0, net: 1 }, authority: 'Sign-off only.',
});

const INVENTORIES: Record<string, { inventory_id: string; subscription: string; as_of: string; received_at: string }[]> = {
  'LCT-A': [{ inventory_id: 'LCX-A', subscription: 'subscription-A', as_of: '2026-11-05T18:00:00Z', received_at: '2026-11-05T18:00:00Z' }],
  'LCT-B': [{ inventory_id: 'LCX-B', subscription: 'subscription-B', as_of: '2026-11-05T18:00:00Z', received_at: '2026-11-05T18:00:00Z' }],
  'LCT-C': [],
};

let delays: Record<string, number> = {};
let posts: string[] = [];

const ok = (body: unknown) => ({ ok: true, status: 200, json: async () => body });

const api = async (url: string, init?: { method?: string }) => {
  if (init?.method === 'POST') {
    posts.push(url);
    return ok({});
  }
  const path = url.replace('/api/v1/legacy-claims', '');
  const id = (path.match(/LCT-[A-Z]/) ?? [''])[0];
  if (delays[id]) await new Promise((r) => setTimeout(r, delays[id]));
  if (path === '/status') return ok({ enabled: true, can_view: true, can_deliver: true, can_configure: true, stage: 'V3' });
  if (path === '/transitions') return ok([T.A, T.B, T.C]);
  if (path.endsWith('/assessment')) return ok(assessment(T[id.slice(-1) as 'A' | 'B' | 'C'], id === 'LCT-A' ? 30000 : id === 'LCT-B' ? 70000 : 10000));
  if (path.endsWith('/assessments')) return ok([]);
  if (path.endsWith('/sources')) return ok([{ binding_id: id === 'LCT-A' ? 11 : 22, role: 'bank', valid_from: '', valid_until: '', streams: [] }]);
  if (path.startsWith('/exit-inventories?transition_id=')) return ok(INVENTORIES[decodeURIComponent(path.split('=')[1])]);
  const inv = path.match(/\/exit-inventories\/(LCX-[A-Z])\/review/);
  if (inv) return ok(review(inv[1], inv[1] === 'LCX-A' ? 'subscription-A' : 'subscription-B'));
  return { ok: false, status: 404, json: async () => ({ detail: 'not found' }) };
};

const select = () => screen.getByLabelText('Transition') as HTMLSelectElement;

describe('Legacy Claims screen keeps every panel on the selected transition (review 07.5)', () => {
  beforeEach(() => {
    delays = {};
    posts = [];
    fetchWithAuth.mockReset();
    fetchWithAuth.mockImplementation(api);
  });
  afterEach(() => vi.useRealTimers());

  it('shows B\'s assessment and B\'s subscription review after switching, and Reassess stays on B', async () => {
    render(<LegacyClaimsView />);
    await screen.findByText(/subscription-A/);
    expect(screen.getByText('Work denial of Run-off A')).toBeTruthy();

    await act(async () => { fireEvent.change(select(), { target: { value: 'LCT-B' } }); });
    await screen.findByText(/subscription-B/);
    expect(screen.queryByText(/subscription-A/)).toBeNull();
    expect(screen.getByText('Work denial of Run-off B')).toBeTruthy();

    await act(async () => { fireEvent.click(screen.getByText('Reassess')); });
    await waitFor(() => expect(posts).toEqual(['/api/v1/legacy-claims/transitions/LCT-B/assessments']));
    await screen.findByText(/subscription-B/);
    expect(select().value).toBe('LCT-B');
    expect(screen.queryByText(/subscription-A/)).toBeNull();
    expect(screen.getByText('Work denial of Run-off B')).toBeTruthy();
  });

  it('discards a slower answer for a transition that is no longer selected', async () => {
    render(<LegacyClaimsView />);
    await screen.findByText(/subscription-A/);
    delays = { 'LCT-B': 120 };
    await act(async () => { fireEvent.change(select(), { target: { value: 'LCT-B' } }); });
    await act(async () => { fireEvent.change(select(), { target: { value: 'LCT-A' } }); });
    await act(async () => { await new Promise((r) => setTimeout(r, 250)); });
    expect(select().value).toBe('LCT-A');
    expect(screen.getByText(/subscription-A/)).toBeTruthy();
    expect(screen.queryByText(/subscription-B/)).toBeNull();
    expect(screen.queryByText('Work denial of Run-off B')).toBeNull();
  });

  it('explains an empty exit panel when no inventory names the selected scope', async () => {
    render(<LegacyClaimsView />);
    await screen.findByText(/subscription-A/);
    await act(async () => { fireEvent.change(select(), { target: { value: 'LCT-C' } }); });
    await screen.findByText(/No subscription inventory names this transition's scope \(payer TPA-C/);
    expect(screen.queryByText(/subscription-A/)).toBeNull();
  });
});
