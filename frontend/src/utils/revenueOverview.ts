/**
 * Revenue Intelligence figures from GET /api/v1/encounters/revenue-overview. Every value is calculated
 * on the server from the clinic's encounters; this file only types the response and turns the
 * server's series into chart geometry.
 */

export type RevenueCard = {
  value: number;
  trend: string | null;
  previous: number;
  /** true: the change is good for the clinic, false: bad, null: no change or no prior data. */
  good: boolean | null;
  spark: number[];
};

export type RevenueStatus = { key: string; label: string; count: number; pct: number };

export type RevenueDay = {
  date: string;
  /** Today only: the clinic hour this point covers ("09:00"). */
  hour?: string;
  charges: number;
  paid_amount: number;
  pending: number;
  denied: number;
  claims: number;
};

export type RevenueOverview = {
  period: 'today' | 'week' | 'month';
  period_label: string;
  start: string;
  end: string;
  compare_start: string;
  compare_end: string;
  currency: string;
  cards: Record<'charges' | 'paid_amount' | 'pending' | 'denied', RevenueCard>;
  total_claims: number;
  status_breakdown: RevenueStatus[];
  series: RevenueDay[];
};

export const PERIOD_KEYS: Record<string, RevenueOverview['period']> = {
  Today: 'today',
  'This Week': 'week',
  'This Month': 'month',
};

export const periodKey = (label: string): RevenueOverview['period'] => PERIOD_KEYS[label] ?? 'week';

/** Ring colour per status key; statuses outside the three groups (e.g. a held claim) get their own. */
export const STATUS_COLORS: Record<string, string> = {
  paid: 'var(--color-semantic-positive)',
  pending: 'var(--color-semantic-attention)',
  denied: 'var(--color-semantic-critical)',
};
export const OTHER_STATUS_COLOR = '#A78BFA';
export const statusColor = (key: string) => STATUS_COLORS[key] ?? OTHER_STATUS_COLOR;

/** Ring segments (stroke-dasharray / offset on a circumference of 100) in breakdown order. */
export const ringSegments = (breakdown: RevenueStatus[]) => {
  let offset = 0;
  return breakdown
    .filter((s) => s.pct > 0)
    .map((s) => {
      const seg = { key: s.key, color: statusColor(s.key), dash: `${s.pct} ${100 - s.pct}`, offset: -offset };
      offset += s.pct;
      return seg;
    });
};

/** A round axis maximum at or above `max` (1, 2, 2.5 or 5 x 10^n), so gridlines read cleanly. */
export const niceMax = (max: number): number => {
  if (!(max > 0)) return 0;
  const exp = Math.pow(10, Math.floor(Math.log10(max)));
  for (const step of [1, 2, 2.5, 5, 10]) {
    if (step * exp >= max) return step * exp;
  }
  return 10 * exp;
};

/** "80K", "1.5M", "750" for axis labels. */
export const compactAmount = (v: number): string => {
  if (v >= 1_000_000) return `${+(v / 1_000_000).toFixed(1)}M`;
  if (v >= 1_000) return `${+(v / 1_000).toFixed(1)}K`;
  return `${Math.round(v)}`;
};

/** Sparkline path for a card in a 100 x 40 box (flat line when there is nothing to compare). */
export const sparkPath = (values: number[]): string => {
  if (values.length < 2) return 'M0,20 L100,20';
  const max = Math.max(...values);
  const min = Math.min(...values);
  const span = max - min || 1;
  return values
    .map((v, i) => {
      const x = (i / (values.length - 1)) * 100;
      const y = max === min ? 20 : 36 - ((v - min) / span) * 32;
      return `${i === 0 ? 'M' : 'L'}${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(' ');
};

const WEEKDAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

export const dayLabel = (iso: string, period: RevenueOverview['period']): string => {
  const [y, m, d] = iso.split('-').map(Number);
  const date = new Date(Date.UTC(y, m - 1, d));
  if (period === 'week') return `${WEEKDAYS[date.getUTCDay()]} ${d}`;
  return `${MONTHS[m - 1]} ${d}`;
};

export type ChartPoint = { x: number; y: number; date: string; hour?: string; value: number; label: string };

/**
 * Daily charges as points in the chart's 800 x 300 box: x from 20 to 780, value 0 at y 260 and the
 * axis maximum at y 20. Labels are thinned so that at most ~8 show.
 */
export const chartGeometry = (series: RevenueDay[], period: RevenueOverview['period']) => {
  const values = series.map((p) => p.charges);
  const axisMax = niceMax(Math.max(0, ...values));
  const n = series.length;
  const every = Math.max(1, Math.ceil(n / 8));
  const points: ChartPoint[] = series.map((p, i) => ({
    x: n === 1 ? 400 : 20 + (i / (n - 1)) * 760,
    y: axisMax ? 260 - (p.charges / axisMax) * 240 : 260,
    date: p.date,
    hour: p.hour,
    value: p.charges,
    label: i % every === 0 || i === n - 1 ? (p.hour ?? dayLabel(p.date, period)) : '',
  }));
  const line = points.map((p, i) => `${i === 0 ? 'M' : 'L'}${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(' ');
  const area = points.length
    ? `${line} L${points[points.length - 1].x.toFixed(1)},260 L${points[0].x.toFixed(1)},260 Z`
    : '';
  const ticks = [1, 2 / 3, 1 / 3, 0].map((f) => ({ y: 260 - f * 240, value: axisMax * f }));
  return { points, line, area, ticks, axisMax, total: values.reduce((a, b) => a + b, 0) };
};

/** Signal card: an amount stated by the source (engine confidence HIGH) is verified; else pending review. */
export const verificationLevel = (s: { revenue_confidence?: string | null; revenue_impact_status?: string | null }) =>
  s.revenue_confidence === 'HIGH' || s.revenue_impact_status === 'High' ? 'Verified' : 'Pending Review';

/** Download name from the server's Content-Disposition header. */
export const filenameFrom = (header: string | null, fallback: string): string => {
  const m = header?.match(/filename="?([^";]+)"?/i);
  return m ? m[1] : fallback;
};
