import React from 'react';
import { act, configure, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const fetchWithAuth = vi.fn();
vi.mock('@/utils/fetchWithAuth', () => ({ fetchWithAuth: (...args: unknown[]) => fetchWithAuth(...args) }));

import { LegacyClaimsView } from '@/components/CommandCenter/LegacyClaimsView';

// The screen chains several API calls; give them room on a busy CI runner.
configure({ asyncUtilTimeout: 3000 });

const zero = { payer_ar: 0, patient_ar: 0, credit: 0, unapplied: 0, unbilled: 0, closed_watch: 0 };
type Id = 'A' | 'B' | 'C' | 'D';

const transition = (id: Id) => ({
  id: `LCT-${id}`, label: `Run-off ${id}`,
  scope: { deployment: 'development', region: 'UAE', org: 'ORG', clinic: 'C1', facility: 'C1', payee: 'C1', payer: `TPA-${id}`, currency: 'AED' },
  cutover: '2026-11-01T00:00:00Z', routes: { new: 'dest', old: 'old', fallback: 'fb', archive: 'arc' },
  max_probe_age_days: 14, origin: 'clinic', created_at: '2026-11-01T00:00:00Z',
  current_assessment_id: `LCA-${id}`, latest_status: 'SAMPLE_REVIEWABLE', latest_as_of: '2026-11-05T18:00:00Z',
});

const T: Record<Id, ReturnType<typeof transition>> = { A: transition('A'), B: transition('B'), C: transition('C'), D: transition('D') };

const assessment = (id: Id) => ({
  assessment_id: `LCA-${id}`, transition: T[id], as_of: '2026-11-05T18:00:00Z', revision: 1,
  published: true, is_current: true, computed_at: '2026-11-05T18:00:00Z', status: id === 'C' ? 'HOLD' : 'SAMPLE_REVIEWABLE',
  policy_version: 'p', mapping_version: 'm', review_quality: 'RESEARCH_ONLY', record_count: 32,
  source_age_hours: 10, last_received_at: null, issues: [], evidence_count: 1,
  work_items: [{ id: 1, kind: 'follow_up', code: 'WORK_DENIAL', item: `den-${id}`, message: `Work denial of Run-off ${id}`,
    issues: [], history: [], owner: 'bill-team', state: 'OPEN', accepted_by: null, accepted_at: null, note: null, resolved_at: null }],
  summary: {
    currency: 'AED', lines: { legacy: 1, new: 1 },
    control: { ...zero, payer_ar: 100000 }, opening: { ...zero, payer_ar: 100000 },
    // C: the AED 300 payer position arrived twice in one snapshot; the server leaves it out and lists the rows.
    closing: { ...zero, payer_ar: id === 'C' ? 0 : 30000 },
    unverified: id === 'C' ? { closing: { payer_ar: [
      { key: 'closing-ar', line: 'legacy/C7/01', minor: 30000, effective: '2026-11-05T18:00:00Z' },
      { key: 'closing-ar-copy', line: 'legacy/C7/01', minor: 30000, effective: '2026-11-05T18:00:00Z' },
    ] } } : {},
    cash: { advised: 90000, bank_observed: 90000, bank_confirmed: 90000, bank_unmatched: 0, bank_coverage: 'complete',
      ledger_posted: 90000, ledger_coverage: 'complete', provider_adjustments: 0, unapplied_bank: 0, noncash_advices: 0 },
    denials: [], routes: [], sources: [],
  },
  authority: 'Review only.',
});

const inventory = (key: string) => ({ inventory_id: `LCX-${key}`, subscription: `subscription-${key}`, as_of: '2026-11-05T18:00:00Z', received_at: '2026-11-05T18:00:00Z' });
const INVENTORIES: Record<string, ReturnType<typeof inventory>[]> = {
  'LCT-A': [inventory('A')], 'LCT-B': [inventory('B')], 'LCT-C': [], 'LCT-D': [inventory('D1'), inventory('D2')],
};
const review = (key: string) => ({
  inventory_id: `LCX-${key}`, subscription: `subscription-${key}`, as_of: '2026-11-05T18:00:00Z', received_at: '2026-11-05T18:00:00Z',
  status: 'HUMAN_EXIT_REVIEW', issues: [], scopes: [{ scope: { payer: `TPA-${key}`, facility: 'C1' }, transition_id: `LCT-${key.slice(0, 1)}`, label: `Run-off ${key}`, status: 'SAMPLE_REVIEWABLE' }],
  dependencies: [{ key: 'access', kind: 'access', proof_ref: 'proof-access', accepted_by: null, valid_through: '2026-12-31T00:00:00Z' }], signoffs: [], missing_signoffs: key === 'A' ? ['contract_owner'] : [],
  savings: { currency: 'AED', avoided: 1, incremental: 0, net: 1 }, authority: 'Sign-off only.',
});

/** Per-request control: `delays[pattern]` ms and `failures` patterns, matched against "METHOD url". */
let delays: Record<string, number> = {};
let failures: string[] = [];
let posts: string[] = [];
let bodies: any[] = [];
let previewProblems: { row: number | null; reason: string }[] = [];
let pulseValue = { transition_id: 'LCT-A', current_assessment_id: 'LCA-A', last_received_at: null, last_work_event: 1, lock_version: 1 };
let workHistory: { event: string; actor: string; at: string; note: string | null }[] = [];
const ok = (body: unknown) => ({ ok: true, status: 200, json: async () => body });
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

const api = async (url: string, init?: { method?: string }) => {
  const method = init?.method ?? 'GET';
  const call = `${method} ${url}`;
  const wait = Object.entries(delays).find(([k]) => call.includes(k));
  if (wait) await sleep(wait[1]);
  if (failures.some((k) => call.includes(k))) return { ok: false, status: 500, json: async () => ({ detail: `Failed: ${url}` }) };
  const path = url.replace('/api/v1/legacy-claims', '');
  if (method === 'POST' || method === 'PUT') {
    posts.push(url);
    bodies.push(init && (init as any).body ? JSON.parse((init as any).body) : null);
    if (path.endsWith('/pages')) return ok({ status: 'STAGED', records_added: 1, seal_stored: false });
    if (path.endsWith('/uploads/preview')) return ok({ role: 'bank', rows: 2, by_kind: { deposit: 2 }, problems: previewProblems,
      new_records: 2, already_received: 0, latest_event: '2026-11-04T10:00:00Z', this_source_complete_through: null,
      sources_complete_through: { control: '2026-11-05T18:00:00Z' }, latest_statement: '2026-11-05T18:00:00Z', can_upload: previewProblems.length === 0 });
    if (path.endsWith('/uploads')) return ok({ status: 'STAGED', records_added: 2, complete_through: '2026-11-05T18:00:00Z', sealed_rows: 3 });
    if (path.endsWith('/signoffs')) return ok({ ...review('A'), missing_signoffs: [], signoffs: ['contract_owner', 'finance', 'records', 'revenue_cycle'],
      signoff_details: [{ role: 'contract_owner', decision_ref: 'x', signed_by: 'me@clinic.test', signed_at: '2026-11-06T09:00:00Z', given_on_screen: true }] });
    if (path === '/transitions') return ok({ ...T.A, id: 'LCT-N' });
    return ok({});
  }
  if (path.includes('/pulse')) return ok(pulseValue);
  if (path.includes('/evidence?')) {
    const figure = new URLSearchParams(path.split('?')[1]).get('figure');
    return ok({ figure, label: figure, assessment_id: 'LCA-A', as_of: '2026-11-05T18:00:00Z', total: 90000,
      rule: 'A deposit is confirmed only when one cash advice and one deposit agree.',
      records: [{ key: 'bank-1', kind: 'deposit', source: 'bank', effective: '2026-11-04T10:00:00Z', seen: '2026-11-04T10:00:00Z',
        received_at: '2026-11-05T18:00:00Z', received_by: 'source@clinic.test', page_token: 'bank-1', custody_evidence_id: 'evd_123456789012345678',
        evidence_ref: 'ev-bank-1', fields: { trace: 'pay-9', minor: 90000 }, status: 'confirmed: one cash advice and one deposit agree' }] });
  }
  if (path === '/work-items/1') return ok({ ...assessment('A').work_items[0], evidence: [], history: workHistory,
    allowed_outcomes: ['collected', 'written_off', 'risk_accepted'] });
  if (path.endsWith('/assignees')) return ok([{ value: 'revenue-cycle', label: 'revenue-cycle', kind: 'team' }]);
  if (path.endsWith('/compare')) return ok({
    from: { assessment_id: 'LCA-OLD', as_of: '2026-11-04T18:00:00Z', revision: 1, status: 'HOLD' },
    to: { assessment_id: 'LCA-A', as_of: '2026-11-05T18:00:00Z', revision: 1, status: 'SAMPLE_REVIEWABLE' },
    issues_added: [], issues_removed: [{ code: 'BANK_PROOF_INCOMPLETE', item: 'advice-1', message: 'The payment advice has no single matching bank deposit for its net amount.' }],
    figures_changed: [{ figure: 'cash:bank_confirmed', from: null, to: 90000 }], records: { from: 31, to: 32 } });
  if (path.endsWith('/assessments/LCA-OLD')) return ok({ ...assessment('A'), assessment_id: 'LCA-OLD', status: 'HOLD', is_current: false, work_items: [],
    issues: [{ code: 'BANK_PROOF_INCOMPLETE', item: 'advice-1', message: 'The payment advice has no single matching bank deposit for its net amount.' }] });
  const id = ((path.match(/LCT-([A-Z])/) ?? ['', 'A'])[1]) as Id;
  if (path === '/status') return ok({ enabled: true, can_view: true, can_deliver: true, can_configure: true, stage: 'V3' });
  if (path === '/transitions') return ok([T.A, T.B, T.C, T.D]);
  if (path.endsWith('/assessment')) return ok(assessment(id));
  if (path.endsWith('/assessments')) return ok(id === 'A' ? [{ assessment_id: 'LCA-OLD', as_of: '2026-11-04T18:00:00Z', revision: 1, is_current: false,
    computed_at: '2026-11-04T19:00:00Z', status: 'HOLD', issues: 1, records: 31, computed_by: 'x' }] : []);
  if (path.endsWith('/sources')) return ok([{ binding_id: id === 'A' ? 11 : 22, role: 'bank', valid_from: '', valid_until: '', streams: [] }]);
  if (path.startsWith('/exit-inventories?transition_id=')) return ok(INVENTORIES[decodeURIComponent(path.split('=')[1])]);
  const inv = path.match(/\/exit-inventories\/LCX-([A-Z0-9]+)\/review/);
  if (inv) return ok(review(inv[1]));
  return { ok: false, status: 404, json: async () => ({ detail: 'not found' }) };
};

const select = () => screen.getByLabelText('Transition') as HTMLSelectElement;
const choose = async (id: string) => { await act(async () => { fireEvent.change(select(), { target: { value: id } }); }); };
const settle = async (ms = 300) => { await act(async () => { await sleep(ms); }); };

describe('Legacy Claims screen keeps every panel on the chosen transition and inventory (reviews 07.5, R7)', () => {
  beforeEach(() => {
    delays = {};
    failures = [];
    posts = [];
    pulseValue = { transition_id: 'LCT-A', current_assessment_id: 'LCA-A', last_received_at: null, last_work_event: 1, lock_version: 1 };
    workHistory = [{ event: 'OPENED', actor: 'publisher', at: '2026-11-05T18:00:00Z', note: null }];
    fetchWithAuth.mockReset();
    fetchWithAuth.mockImplementation(api);
  });

  it('shows B\'s assessment and B\'s subscription review after switching, and Reassess stays on B', async () => {
    render(<LegacyClaimsView />);
    await screen.findByText(/subscription-A/);
    expect(screen.getByText('Work denial of Run-off A')).toBeTruthy();
    await choose('LCT-B');
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
    await choose('LCT-B');
    await choose('LCT-A');
    await settle(250);
    expect(select().value).toBe('LCT-A');
    expect(screen.getByText(/subscription-A/)).toBeTruthy();
    expect(screen.queryByText(/subscription-B/)).toBeNull();
    expect(screen.queryByText('Work denial of Run-off B')).toBeNull();
  });

  it('keeps inventory B when the review of the earlier chosen inventory A arrives last (R7 case 1)', async () => {
    render(<LegacyClaimsView />);
    await screen.findByText(/subscription-A/);
    await choose('LCT-D');
    const picker = await screen.findByLabelText('Subscription inventory') as HTMLSelectElement;
    delays = { 'LCX-D1/review': 150 };
    await act(async () => { fireEvent.change(picker, { target: { value: 'LCX-D1' } }); });
    delays = {};
    await act(async () => { fireEvent.change(picker, { target: { value: 'LCX-D2' } }); });
    await settle(300);
    expect(select().value).toBe('LCT-D');
    expect((screen.getByLabelText('Subscription inventory') as HTMLSelectElement).value).toBe('LCX-D2');
    expect(screen.getByText(/subscription-D2 · as of/, { selector: 'span' })).toBeTruthy();
    expect(screen.queryByText(/subscription-D1 · as of/, { selector: 'span' })).toBeNull();
  });

  it('clearing the inventory choice drops a review still in flight', async () => {
    render(<LegacyClaimsView />);
    await screen.findByText(/subscription-A/);
    await choose('LCT-D');
    const picker = await screen.findByLabelText('Subscription inventory') as HTMLSelectElement;
    delays = { 'LCX-D1/review': 150 };
    await act(async () => { fireEvent.change(picker, { target: { value: 'LCX-D1' } }); });
    await act(async () => { fireEvent.change(picker, { target: { value: '' } }); });
    await settle(300);
    expect((screen.getByLabelText('Subscription inventory') as HTMLSelectElement).value).toBe('');
    expect(screen.queryByText(/subscription-D1 · as of/, { selector: 'span' })).toBeNull();
    expect(screen.getByText('Choose which subscription inventory to review for this transition.')).toBeTruthy();
  });

  it('an upload finishing for A after the reviewer chose B leaves B\'s panels together (R7 case 2)', async () => {
    render(<LegacyClaimsView />);
    await screen.findByText(/subscription-A/);
    fireEvent.change(screen.getByLabelText('Source of the file'), { target: { value: '11' } });
    const file = new File([JSON.stringify({ records: [{ key: 'bank-9' }] })], 'export.json', { type: 'application/json' });
    fireEvent.change(screen.getByLabelText('Export file (JSON)'), { target: { files: [file] } });
    delays = { 'POST /api/v1/legacy-claims/transitions/LCT-A/pages': 150 };
    await act(async () => { fireEvent.click(screen.getByText('Deliver')); });
    await choose('LCT-B');
    await settle(400);
    expect(posts).toContain('/api/v1/legacy-claims/transitions/LCT-A/pages');
    expect(posts).toContain('/api/v1/legacy-claims/transitions/LCT-A/assessments');
    expect(select().value).toBe('LCT-B');
    expect(screen.getByText('Work denial of Run-off B')).toBeTruthy();
    expect(screen.queryByText('Work denial of Run-off A')).toBeNull();
    expect(screen.getByText(/subscription-B/)).toBeTruthy();
    expect(screen.queryByText(/subscription-A/)).toBeNull();
    expect(screen.queryByText(/Received 1 new record/)).toBeNull();
  });

  it('a delayed error for a transition no longer selected is not shown', async () => {
    render(<LegacyClaimsView />);
    await screen.findByText(/subscription-A/);
    delays = { 'LCT-B/assessment': 120 };
    failures = ['LCT-B/assessment'];
    await choose('LCT-B');
    await choose('LCT-A');
    await settle(250);
    expect(select().value).toBe('LCT-A');
    expect(screen.queryByText(/Failed:/)).toBeNull();
    expect(screen.getByText('Work denial of Run-off A')).toBeTruthy();
  });

  it('explains an empty exit panel when no inventory names the selected scope', async () => {
    render(<LegacyClaimsView />);
    await screen.findByText(/subscription-A/);
    await choose('LCT-C');
    await screen.findByText(/No subscription inventory names this transition's scope \(payer TPA-C/);
    expect(screen.queryByText(/subscription-A/)).toBeNull();
  });

  it('shows a disputed closing position as unverified instead of adding it twice (R4)', async () => {
    render(<LegacyClaimsView />);
    await screen.findByText(/subscription-A/);
    await choose('LCT-C');
    await screen.findByText('Unverified');
    expect(screen.getByText(/Undisputed subtotal AED 0\.00 · 2 disputed row\(s\)/)).toBeTruthy();
    expect(screen.queryByText('AED 600.00')).toBeNull();
    expect(screen.getByText(/Unverified positions/)).toBeTruthy();
    expect(screen.getByText(/closing-ar-copy: AED 300\.00/)).toBeTruthy();
  });
});

describe('Legacy Claims screen is a working screen: evidence, work and history panels, live updates', () => {
  beforeEach(() => {
    delays = {};
    failures = [];
    posts = [];
    pulseValue = { transition_id: 'LCT-A', current_assessment_id: 'LCA-A', last_received_at: null, last_work_event: 1, lock_version: 1 };
    workHistory = [{ event: 'OPENED', actor: 'publisher', at: '2026-11-05T18:00:00Z', note: null }];
    fetchWithAuth.mockReset();
    fetchWithAuth.mockImplementation(api);
  });

  it('clicking a figure opens the stored records behind it', async () => {
    render(<LegacyClaimsView />);
    await screen.findByText(/subscription-A/);
    await act(async () => { fireEvent.click(screen.getByLabelText('Bank-confirmed payments')); });
    const panel = await screen.findByRole('dialog');
    await waitFor(() => expect(panel.textContent).toContain('bank-1'));
    expect(fetchWithAuth.mock.calls.some(([u]) => String(u).includes('/transitions/LCT-A/evidence?figure=cash%3Aconfirmed'))).toBe(true);
    expect(panel.textContent).toContain('confirmed: one cash advice and one deposit agree');
    expect(panel.textContent).toContain('source@clinic.test');
    expect(panel.textContent).toContain('trace: pay-9');
    await act(async () => { fireEvent.click(screen.getByLabelText('Close')); });
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('a task opens with its actions; assign and note call the API and show in the timeline', async () => {
    render(<LegacyClaimsView />);
    await screen.findByText(/subscription-A/);
    await act(async () => { fireEvent.click(screen.getByLabelText('Open Work denial of Run-off A')); });
    await screen.findByRole('dialog');
    await screen.findByText('Team · revenue-cycle');
    fireEvent.change(screen.getByLabelText('Assign to'), { target: { value: 'revenue-cycle' } });
    workHistory = [...workHistory, { event: 'ASSIGNED', actor: 'me', at: '2026-11-06T09:00:00Z', note: 'Assigned to revenue-cycle' }];
    await act(async () => { fireEvent.click(screen.getByText('Assign')); });
    await waitFor(() => expect(posts).toContain('/api/v1/legacy-claims/work-items/1/assign'));
    fireEvent.change(screen.getByLabelText('Note'), { target: { value: 'Appeal packet in preparation' } });
    workHistory = [...workHistory, { event: 'NOTE', actor: 'me', at: '2026-11-06T09:05:00Z', note: 'Appeal packet in preparation' }];
    await act(async () => { fireEvent.click(screen.getByText('Add note')); });
    await waitFor(() => expect(posts).toContain('/api/v1/legacy-claims/work-items/1/notes'));
    const panel = screen.getByRole('dialog');
    await waitFor(() => expect(panel.textContent).toContain('Appeal packet in preparation'));
    expect(panel.textContent).toContain('Assigned to revenue-cycle');
  });

  it('new evidence arriving right after a panel action is still picked up by the next live check', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      render(<LegacyClaimsView />);
      await screen.findByText(/subscription-A/);
      await act(async () => { fireEvent.click(screen.getByLabelText('Open Work denial of Run-off A')); });
      await screen.findByText('Team · revenue-cycle');
      fireEvent.change(screen.getByLabelText('Assign to'), { target: { value: 'revenue-cycle' } });
      await act(async () => { fireEvent.click(screen.getByText('Assign')); });
      const reads = () => fetchWithAuth.mock.calls.filter(([u]) => String(u).endsWith('/transitions/LCT-A/assessment')).length;
      await waitFor(() => expect(posts).toContain('/api/v1/legacy-claims/work-items/1/assign'));
      await act(async () => { await vi.advanceTimersByTimeAsync(100); });
      const before = reads();
      pulseValue = { ...pulseValue, current_assessment_id: 'LCA-A2', lock_version: 3 };   // a source delivers meanwhile
      await act(async () => { await vi.advanceTimersByTimeAsync(30000); });
      await waitFor(() => expect(reads()).toBe(before + 1));
    } finally {
      vi.useRealTimers();
    }
  });

  it('a history row opens the earlier assessment and what changed since', async () => {
    render(<LegacyClaimsView />);
    await screen.findByText(/subscription-A/);
    const row = (await screen.findAllByText('On hold')).map((el) => el.closest('tr')).find(Boolean) as HTMLElement;
    await act(async () => { fireEvent.click(row); });
    const panel = await screen.findByRole('dialog');
    await waitFor(() => expect(panel.textContent).toContain('Earlier assessment'));
    await waitFor(() => expect(panel.textContent).toContain('Cleared: The payment advice has no single matching bank deposit'));
    expect(panel.textContent).toContain('Bank-confirmed');
  });

  it('re-reads the screen when the server pulse changes, and not otherwise', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      render(<LegacyClaimsView />);
      await screen.findByText(/subscription-A/);
      const reads = () => fetchWithAuth.mock.calls.filter(([u]) => String(u).endsWith('/transitions/LCT-A/assessment')).length;
      await waitFor(() => expect(fetchWithAuth.mock.calls.some(([u]) => String(u).endsWith('/pulse'))).toBe(true));
      const before = reads();
      await act(async () => { await vi.advanceTimersByTimeAsync(30000); });
      expect(reads()).toBe(before);
      pulseValue = { ...pulseValue, current_assessment_id: 'LCA-A2', lock_version: 2 };
      await act(async () => { await vi.advanceTimersByTimeAsync(30000); });
      await waitFor(() => expect(reads()).toBe(before + 1));
      expect(screen.getByText(/Live · checked/)).toBeTruthy();
    } finally {
      vi.useRealTimers();
    }
  });
});

describe('Legacy Claims setup, CSV upload and sign-offs', () => {
  beforeEach(() => {
    delays = {};
    failures = [];
    posts = [];
    bodies = [];
    previewProblems = [];
    fetchWithAuth.mockReset();
    fetchWithAuth.mockImplementation(api);
  });

  it('previews a CSV export, then uploads it with the completeness statement and publishes', async () => {
    render(<LegacyClaimsView />);
    await screen.findByText(/subscription-A/);
    fireEvent.change(screen.getByLabelText('Source of the CSV'), { target: { value: '11' } });
    const file = new File(['key,kind,effective,trace,amount\nbank-9,deposit,2026-11-04 14:00,pay-9,900.00\n'], 'bank-nov.csv', { type: 'text/csv' });
    fireEvent.change(screen.getByLabelText('CSV export'), { target: { files: [file] } });
    await act(async () => { fireEvent.click(screen.getByText('Preview')); });
    await waitFor(() => expect(document.body.textContent).toContain('2 new, 0 already received'));
    expect(posts).toEqual(['/api/v1/legacy-claims/transitions/LCT-A/uploads/preview']);
    fireEvent.click(screen.getByLabelText('Complete export'));
    await act(async () => { fireEvent.click(screen.getByText('Upload')); });
    await waitFor(() => expect(posts).toContain('/api/v1/legacy-claims/transitions/LCT-A/assessments'));
    const upload = bodies[posts.indexOf('/api/v1/legacy-claims/transitions/LCT-A/uploads')];
    expect(upload).toMatchObject({ binding_id: 11, filename: 'bank-nov.csv', complete_through: '2026-11-05T18:00:00Z' });
    expect(upload.content).toContain('bank-9,deposit');
    await screen.findByText(/Stored 2 new record\(s\) from bank-nov.csv/);
  });

  it('a CSV upload that finishes after the reviewer switched publishes only the transition the file went to', async () => {
    render(<LegacyClaimsView />);
    await screen.findByText(/subscription-A/);
    fireEvent.change(screen.getByLabelText('Source of the CSV'), { target: { value: '11' } });
    fireEvent.change(screen.getByLabelText('CSV export'), { target: { files: [new File(['key,kind'], 'a.csv')] } });
    await act(async () => { fireEvent.click(screen.getByText('Preview')); });
    await waitFor(() => expect(document.body.textContent).toContain('2 new, 0 already received'));
    delays = { 'POST /api/v1/legacy-claims/transitions/LCT-A/uploads': 150 };
    await act(async () => { fireEvent.click(screen.getByText('Upload')); });
    await choose('LCT-B');
    await settle(400);
    expect(posts).toContain('/api/v1/legacy-claims/transitions/LCT-A/uploads');
    expect(posts).toContain('/api/v1/legacy-claims/transitions/LCT-A/assessments');
    expect(posts).not.toContain('/api/v1/legacy-claims/transitions/LCT-B/assessments');
    expect(select().value).toBe('LCT-B');
    expect(screen.getByText('Work denial of Run-off B')).toBeTruthy();
    expect(screen.queryByText(/Stored 2 new record/)).toBeNull();
    expect((screen.getByLabelText('Source of the CSV') as HTMLSelectElement).value).toBe('');
  });

  it('shows the problems of a refused file with their rows and offers no upload', async () => {
    previewProblems = [{ row: 2, reason: 'amount must be an amount such as 900.00' }];
    render(<LegacyClaimsView />);
    await screen.findByText(/subscription-A/);
    fireEvent.change(screen.getByLabelText('Source of the CSV'), { target: { value: '11' } });
    fireEvent.change(screen.getByLabelText('CSV export'), { target: { files: [new File(['x'], 'bad.csv')] } });
    await act(async () => { fireEvent.click(screen.getByText('Preview')); });
    await screen.findByText('Row 2: amount must be an amount such as 900.00');
    expect(screen.queryByText('Upload')).toBeNull();
  });

  it('a missing sign-off is given by the signed-in reviewer and the review updates', async () => {
    render(<LegacyClaimsView />);
    await screen.findByText(/subscription-A/);
    await act(async () => { fireEvent.click(screen.getByText(/Sign off as Contract owner/)); });
    await screen.findByText(/me@clinic.test/);
    expect(posts).toContain('/api/v1/legacy-claims/exit-inventories/LCX-A/signoffs');
    expect(bodies[posts.indexOf('/api/v1/legacy-claims/exit-inventories/LCX-A/signoffs')]).toEqual({ role: 'contract_owner' });
    expect(screen.queryByText(/Missing: Contract owner/)).toBeNull();
  });

  it('an administrator opens a new transition from the form', async () => {
    render(<LegacyClaimsView />);
    await screen.findByText(/subscription-A/);
    await act(async () => { fireEvent.click(screen.getByText('New transition')); });
    for (const [name, value] of [['Name', 'Old billing run-off'], ['Facility', 'C1'], ['Payee', 'C1'], ['Payer', 'TPA-X'],
      ['New payer route', 'dest'], ['Old payer route (history)', 'hist'], ['Fallback route', 'fb'], ['Records archive', 'arc']]) {
      fireEvent.change(screen.getByLabelText(name), { target: { value } });
    }
    fireEvent.change(screen.getByLabelText('Cutover'), { target: { value: '2026-11-01T00:00' } });
    await act(async () => { fireEvent.click(screen.getByText('Open transition')); });
    await waitFor(() => expect(posts).toContain('/api/v1/legacy-claims/transitions'));
    const body = bodies[posts.indexOf('/api/v1/legacy-claims/transitions')];
    expect(body).toMatchObject({ label: 'Old billing run-off', payer: 'TPA-X', new_route: 'dest', max_probe_age_days: 14 });
    expect(body.cutover).toMatch(/^2026-1[01]-\d\dT\d\d:\d\d:\d\dZ$/);
  });
});

describe('Legacy Claims details panels', () => {
  beforeEach(() => {
    delays = {};
    failures = [];
    posts = [];
    bodies = [];
    fetchWithAuth.mockReset();
    fetchWithAuth.mockImplementation(api);
  });

  it('keeps the status line short and shows the technical details on request', async () => {
    render(<LegacyClaimsView />);
    await screen.findByText(/subscription-A/);
    expect(document.querySelector('main, div')?.textContent).not.toContain('evidence references');
    await act(async () => { fireEvent.click(screen.getByText('Assessment details')); });
    const panel = await screen.findByRole('dialog');
    expect(panel.textContent).toContain('Evidence references');
    expect(panel.textContent).toContain('Rules (policy) version');
  });

  it('the exit review opens with its scopes, dependencies, savings and sign-offs', async () => {
    render(<LegacyClaimsView />);
    await screen.findByText(/subscription-A/);
    await act(async () => { fireEvent.click(screen.getByLabelText('Open the exit review')); });
    const panel = await screen.findByRole('dialog');
    expect(panel.textContent).toContain('Exit review · subscription-A');
    expect(panel.textContent).toContain('System access');
    expect(panel.textContent).toContain('proof-access');
    expect(panel.textContent).toContain('missing');                 // accepted_by not given
    expect(panel.textContent).toContain('Run-off A');
    expect(panel.textContent).toContain('Sign off as Contract owner');
  });
});

describe('Legacy Claims separate outcomes', () => {
  beforeEach(() => {
    delays = {};
    failures = [];
    posts = [];
    bodies = [];
    workHistory = [{ event: 'OPENED', actor: 'publisher', at: '2026-11-05T18:00:00Z', note: null }];
    fetchWithAuth.mockReset();
    fetchWithAuth.mockImplementation(api);
  });

  it('closes a task with a chosen outcome; an accepted risk needs a reason first', async () => {
    render(<LegacyClaimsView />);
    await screen.findByText(/subscription-A/);
    await act(async () => { fireEvent.click(screen.getByLabelText('Open Work denial of Run-off A')); });
    await screen.findByText('Record an outcome');
    const close = () => screen.getByText('Close with outcome') as HTMLButtonElement;
    fireEvent.change(screen.getByLabelText('Outcome'), { target: { value: 'risk_accepted' } });
    expect(close().disabled).toBe(true);                       // no reason yet
    fireEvent.change(screen.getByLabelText('Note'), { target: { value: 'Payer closed; amount under threshold' } });
    expect(close().disabled).toBe(false);
    fireEvent.change(screen.getByLabelText('Outcome'), { target: { value: 'written_off' } });
    await act(async () => { fireEvent.click(close()); });
    await waitFor(() => expect(posts).toContain('/api/v1/legacy-claims/work-items/1/outcome'));
    expect(bodies[posts.indexOf('/api/v1/legacy-claims/work-items/1/outcome')]).toEqual({ outcome: 'written_off', note: 'Payer closed; amount under threshold' });
  });
});
