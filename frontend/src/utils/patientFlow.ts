import { fetchWithAuth } from '@/utils/fetchWithAuth';

/** Live Patient Flow snapshot from GET /api/v1/patient-flow (every value is calculated on the server). */
export type FlowCard = {
  value: number | null;
  display: string;
  trend: string | null;
  trend_good: boolean | null;
  previous: number | null;
  spark: Array<number | null>;
  sample?: number;
  without_arrival?: number;
};

export type FlowRow = {
  id: string;
  visit_id: string;
  patient_name: string | null;
  patient_ref: string | null;
  mrn: string | null;
  payer_network: string | null;
  provider_name: string | null;
  department: string | null;
  priority: string | null;
  status: string;
  journey_stage: string;
  room: string | null;
  wait_minutes: number | null;
  wait_measured: boolean;
  wait_time: string | null;
  arrived_at: string | null;
  stage_timestamps: Record<string, string>;
  date: string;
  /** Waiting-room review (server): seen / waiting / left / noShow / unknown / review. */
  review_status?: string | null;
  since_ready_minutes?: number | null;
  /** "30 mins since ready" or "Readiness unconfirmed" while the patient is waiting. */
  readiness?: string | null;
  ready_alert?: boolean;
  handoff?: string | null;
  next_owner?: string | null;
};

export type FlowSummary = {
  consultations_started: number;
  average_wait_minutes: number | null;
  average_sample: number;
  started_without_arrival: number;
  still_waiting: number;
  longest_wait_minutes: number | null;
  left_before_seen: number;
  no_show: number;
  unknown: number;
  needs_review: number;
};

export type FlowRoom = {
  id: number;
  name: string;
  kind: 'consult' | 'lab';
  doctor: string | null;
  status: 'Available' | 'Occupied' | 'Cleaning';
  visit_id: string | null;
  patient_name: string | null;
  provider: string | null;
  department: string | null;
  wait_minutes: number | null;
  available_in_minutes: number | null;
  patients_in_lab: number;
};

export type FlowSuggestion = { id: string; type: string; action: string; visit_id: string; patient_name: string; text: string };

export type FlowSnapshot = {
  clinic_id: string;
  as_of: string;
  period: string;
  mode: 'occupancy' | 'period';
  timezone: string;
  feed: { last_event_at: string | null; synced: boolean };
  cards: Record<'checked_in' | 'in_consultation' | 'avg_wait' | 'delayed', FlowCard>;
  summary?: FlowSummary;
  queue: FlowRow[];
  total: number;
  rooms: FlowRoom[];
  suggestions: FlowSuggestion[];
  doctors: Array<{ name: string; specialty: string | null; available: boolean }>;
  departments: string[];
};

export type FlowActionResult = {
  action_id: string;
  action_type: string;
  status: string;
  receipt_ref: string | null;
  detail: string | null;
  recipient: string | null;
  connector: string | null;
};

/** Sparkline path for the card's 100 x 30 drawing box from the calculated series (missing points skipped). */
export function sparkPath(values: Array<number | null | undefined>): string {
  const pts = values.map((v, i) => [i, v] as const).filter(([, v]) => typeof v === 'number') as Array<readonly [number, number]>;
  if (pts.length === 0) return '';
  const max = Math.max(...pts.map(([, v]) => v), 0);
  const n = values.length > 1 ? values.length - 1 : 1;
  const y = (v: number) => (max > 0 ? Math.round((27 - (v / max) * 24) * 10) / 10 : 27);
  if (pts.length === 1) return `M0,${y(pts[0][1])} L100,${y(pts[0][1])}`;
  return pts.map(([i, v], k) => `${k === 0 ? 'M' : 'L'}${Math.round((i / n) * 100)},${y(v)}`).join(' ');
}

/** Trend text colour: green when the change is good, red when bad, grey when neutral or unavailable. */
export function trendClass(card: Pick<FlowCard, 'trend' | 'trend_good'>): string {
  if (!card.trend || card.trend_good === null) return 'text-white/40';
  return card.trend_good ? 'text-[var(--color-semantic-positive)]' : 'text-[var(--color-semantic-critical)]';
}

/** Clock time (HH:MM) of an ISO instant in the clinic's time zone (UAE = Asia/Dubai). */
export function clinicTime(iso: string | null | undefined, timeZone = 'Asia/Dubai'): string | null {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  return d.toLocaleTimeString('en-GB', { timeZone, hour: '2-digit', minute: '2-digit', hour12: false });
}

export function newIdempotencyKey(prefix: string): string {
  const rand = typeof crypto !== 'undefined' && 'randomUUID' in crypto ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`;
  return `${prefix}:${rand}`;
}

/** POST to a Patient Flow endpoint. Resolves to the JSON body, or throws with the server's reason. */
export async function postFlow<T = unknown>(path: string, body: Record<string, unknown> = {}): Promise<T> {
  const res = await fetchWithAuth(`/api/v1/patient-flow${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => null);
  if (!res.ok) throw new Error((data && (data.detail as string)) || `Request failed (${res.status})`);
  return data as T;
}
