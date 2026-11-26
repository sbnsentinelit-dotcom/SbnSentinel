/**
 * Legacy Claims Bridge screen helpers. Every figure, status, issue and message comes from
 * GET /api/v1/legacy-claims/...; this file only types the responses, converts the server's integer
 * minor units (fils / cents) to amounts, and names the fixed evidence roles and route tests.
 */

export type Scope = {
  deployment: string; region: string; org: string; clinic: string;
  facility: string; payee: string; payer: string; currency: string;
};

export type Transition = {
  id: string;
  label: string | null;
  scope: Scope;
  cutover: string;
  routes: { new: string; old: string; fallback: string; archive: string };
  max_probe_age_days: number;
  origin: string;
  created_at: string;
  latest_status: string | null;
  latest_as_of: string | null;
};

export type Coded = { code: string; item: string; message: string };

export type WorkItem = Coded & {
  id: number;
  owner: string | null;
  state: 'OPEN' | 'ACCEPTED' | 'RESOLVED';
  accepted_by: string | null;
  accepted_at: string | null;
  note: string | null;
  resolved_at: string | null;
};

export type Buckets = Record<'payer_ar' | 'patient_ar' | 'credit' | 'unapplied' | 'unbilled' | 'closed_watch', number>;

export type Summary = {
  currency: string;
  lines: { legacy: number; new: number };
  control: Buckets;
  opening: Buckets;
  closing: Buckets;
  cash: {
    advised: number; bank_confirmed: number; ledger_posted: number;
    provider_adjustments: number; unapplied_bank: number; noncash_advices: number;
  };
  denials: {
    key: string; line: string; noticed_at: string | null; due: string | null;
    rule_verified: boolean; appeal_stage: string | null; owner: string | null;
  }[];
  routes: { stage: string; proved: boolean }[];
  sources: {
    role: string; rows: number; last_effective: string | null;
    sealed_through: string | null; sealed_rows: number | null; coverage_proved: boolean;
  }[];
};

export type Assessment = {
  assessment_id: string;
  transition: Transition;
  as_of: string;
  computed_at: string;
  status: 'SAMPLE_REVIEWABLE' | 'HOLD';
  policy_version: string;
  mapping_version: string;
  review_quality: string;
  record_count: number;
  source_age_hours: number;
  last_received_at: string | null;
  issues: Coded[];
  work_items: WorkItem[];
  evidence_count: number;
  summary: Summary;
  authority: string;
};

export type AssessmentRow = {
  assessment_id: string; as_of: string; computed_at: string; status: string;
  issues: number; records: number; computed_by: string;
};

export type Source = {
  binding_id: number; role: string; valid_from: string; valid_until: string;
  streams: { resource: string; head: string; updated_at: string }[];
};

export type ExitReview = {
  inventory_id: string;
  subscription: string;
  as_of: string;
  received_at: string;
  status: 'HUMAN_EXIT_REVIEW' | 'HOLD';
  issues: Coded[];
  scopes: { scope: Scope; transition_id: string | null; label: string | null; status: string | null }[];
  dependencies: { key: string; kind: string; proof_ref: string | null; accepted_by: string | null; valid_through: string }[];
  signoffs: string[];
  savings: { currency: string; avoided: number; incremental: number; net: number };
  authority: string;
};

/** Server amounts are integer minor units: 100000 fils = AED 1,000.00. */
export const fromMinor = (minor: number | null | undefined): number | null =>
  typeof minor === 'number' ? minor / 100 : null;

export const STATUS_LABELS: Record<string, string> = {
  SAMPLE_REVIEWABLE: 'Reviewable',
  HOLD: 'On hold',
  HUMAN_EXIT_REVIEW: 'Ready for sign-off review',
};

export const statusLabel = (status: string | null | undefined) =>
  status ? STATUS_LABELS[status] ?? status : 'Not assessed';

export const statusTone = (status: string | null | undefined): 'good' | 'bad' | 'none' =>
  status === 'SAMPLE_REVIEWABLE' || status === 'HUMAN_EXIT_REVIEW' ? 'good' : status === 'HOLD' ? 'bad' : 'none';

export const ROLE_LABELS: Record<string, string> = {
  control: 'Signed control reports',
  legacy: 'Old billing export',
  destination: 'Replacement billing',
  payer: 'Payer responses',
  bank: 'Bank statements',
  ledger: 'Clinic ledger',
  operations: 'Clinic operations',
  archive: 'Records archive',
};

export const ROUTE_LABELS: Record<string, string> = {
  old_retrieval: 'Recover an old response before the switch',
  old_claim_new_route: 'Old claim answered on the new route',
  new_claim_new_route: 'New claim answered on the new route',
  archive_restore: 'Restore an original claim from the archive',
  fallback_recovery: 'Recover through the fallback route',
  denial_route: 'Work a legacy denial through a surviving route',
};

export const BUCKET_LABELS: Record<string, string> = {
  payer_ar: 'Payer receivable',
  patient_ar: 'Patient receivable',
  credit: 'Credits',
  unapplied: 'Unapplied cash',
  unbilled: 'Unbilled',
  closed_watch: 'Settled (reversal watch)',
};

export const APPEAL_LABELS: Record<string, string> = {
  sent: 'Appeal sent',
  received: 'Payer received appeal',
  decided: 'Payer decided',
};

export const label = (map: Record<string, string>, key: string) => map[key] ?? key.replace(/_/g, ' ');

/** Buckets with any amount in the control, opening or closing position, in a fixed order. */
export const balanceRows = (s: Summary) =>
  (Object.keys(BUCKET_LABELS) as (keyof Buckets)[])
    .filter((b) => s.control[b] !== 0 || s.opening[b] !== 0 || s.closing[b] !== 0)
    .map((b) => ({
      bucket: b,
      label: BUCKET_LABELS[b],
      control: s.control[b],
      opening: s.opening[b],
      closing: s.closing[b],
      variance: s.opening[b] - s.control[b],
    }));

export const openWork = (items: WorkItem[]) => items.filter((w) => w.state !== 'RESOLVED');

/** "2 h", "3 d" since the evidence instant. */
export const ageLabel = (hours: number) => (hours < 48 ? `${Math.max(0, Math.round(hours))} h` : `${Math.round(hours / 24)} d`);

export const shortDate = (iso: string | null | undefined): string => {
  if (!iso) return '—';
  const d = new Date(iso);
  if (isNaN(d.getTime())) return '—';
  return d.toLocaleString(undefined, { day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' });
};

/** Reads an uploaded export: either {records, seal} or the bridge's full page shape. */
export const parseExport = (text: string): { records: unknown[]; seal?: unknown; token?: string } => {
  const data = JSON.parse(text);
  if (!data || typeof data !== 'object' || !Array.isArray(data.records)) {
    throw new Error('The file must be JSON with a "records" list.');
  }
  return { records: data.records, seal: data.seal ?? undefined, token: typeof data.token === 'string' ? data.token : undefined };
};

/** Message from an API error body: FastAPI detail string, list or {message}. */
export const errorText = (body: any, fallback: string): string => {
  const d = body?.detail;
  if (!d) return fallback;
  if (typeof d === 'string') return d;
  if (Array.isArray(d)) return d.map((e: any) => e?.msg ?? String(e)).join('; ');
  if (typeof d === 'object') {
    const records = Array.isArray(d.records) ? ` ${d.records.map((r: any) => `${r.key}: ${r.reason}`).join('; ')}` : '';
    return `${d.message ?? fallback}${records}`;
  }
  return fallback;
};
