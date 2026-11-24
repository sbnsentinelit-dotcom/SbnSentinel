import { describe, expect, it } from 'vitest';
import {
  RevenueDay, chartGeometry, compactAmount, dayLabel, filenameFrom, niceMax, periodKey, ringSegments, sparkPath,
  statusColor, verificationLevel,
} from '@/utils/revenueOverview';

const day = (date: string, charges: number): RevenueDay => ({ date, charges, paid_amount: 0, pending: 0, denied: 0, claims: 1 });

describe('Revenue Intelligence figures (from the server overview)', () => {
  it('maps the filter labels to server periods', () => {
    expect(periodKey('Today')).toBe('today');
    expect(periodKey('This Week')).toBe('week');
    expect(periodKey('This Month')).toBe('month');
  });

  it('draws the ring from the breakdown in order, segments summing to 100', () => {
    const segs = ringSegments([
      { key: 'paid', label: 'Paid', count: 2, pct: 33 },
      { key: 'pending', label: 'Pending', count: 2, pct: 33 },
      { key: 'denied', label: 'Denied', count: 1, pct: 17 },
      { key: 'Held - Auth Denied', label: 'Held - Auth Denied', count: 1, pct: 17 },
    ]);
    expect(segs.map((s) => s.offset)).toEqual([-0, -33, -66, -83]);
    expect(segs.map((s) => s.dash)).toEqual(['33 67', '33 67', '17 83', '17 83']);
    expect(segs[3].color).toBe(statusColor('Held - Auth Denied'));
    expect(ringSegments([{ key: 'paid', label: 'Paid', count: 0, pct: 0 }])).toEqual([]);
  });

  it('builds the chart from the daily charges: axis, points and labels from the data', () => {
    const g = chartGeometry([day('2026-11-23', 300), day('2026-11-24', 230), day('2026-11-25', 150)], 'week');
    expect(g.axisMax).toBe(500);
    expect(g.total).toBe(680);
    expect(g.points.map((p) => [p.x, Math.round(p.y)])).toEqual([[20, 116], [400, 150], [780, 188]]);
    expect(g.points.map((p) => p.label)).toEqual(['Mon 23', 'Tue 24', 'Wed 25']);
    expect(g.ticks.map((t) => Math.round(t.value))).toEqual([500, 333, 167, 0]);
    expect(g.line.startsWith('M20.0,116.0')).toBe(true);
  });

  it('shows a single day in the middle and an empty period on the baseline', () => {
    expect(chartGeometry([day('2026-11-25', 150)], 'today').points[0]).toMatchObject({ x: 400, y: 80, label: 'Nov 25' });
    const empty = chartGeometry([day('2026-11-24', 0), day('2026-11-25', 0)], 'week');
    expect(empty.total).toBe(0);
    expect(empty.points.every((p) => p.y === 260)).toBe(true);
  });

  it('labels Today by clinic hour', () => {
    const hours = ['08:00', '09:00', '10:00'].map((hour, i) => ({ ...day('2026-11-25', i * 100), hour }));
    const g = chartGeometry(hours, 'today');
    expect(g.points.map((p) => p.label)).toEqual(['08:00', '09:00', '10:00']);
    expect(g.points.map((p) => p.x)).toEqual([20, 400, 780]);
  });

  it('thins month labels to about eight', () => {
    const month = Array.from({ length: 25 }, (_, i) => day(`2026-11-${String(i + 1).padStart(2, '0')}`, i));
    const labels = chartGeometry(month, 'month').points.filter((p) => p.label);
    expect(labels.length).toBeLessThanOrEqual(9);
    expect(labels[labels.length - 1].label).toBe('Nov 25');
  });

  it('formats axis amounts and day labels', () => {
    expect(niceMax(680)).toBe(1000);
    expect(niceMax(162313)).toBe(200000);
    expect(niceMax(0)).toBe(0);
    expect(compactAmount(80000)).toBe('80K');
    expect(compactAmount(1500000)).toBe('1.5M');
    expect(dayLabel('2026-11-23', 'week')).toBe('Mon 23');
  });

  it('draws card sparklines from the period values', () => {
    expect(sparkPath([1])).toBe('M0,20 L100,20');
    expect(sparkPath([0, 10])).toBe('M0.0,36.0 L100.0,4.0');
    expect(sparkPath([5, 5, 5])).toBe('M0.0,20.0 L50.0,20.0 L100.0,20.0');
  });

  it('reads the export file name from the server and the verification level from the engine confidence', () => {
    expect(filenameFrom('attachment; filename="sbn_sentinel_revenue_today_x_denied.csv"', 'f.csv'))
      .toBe('sbn_sentinel_revenue_today_x_denied.csv');
    expect(filenameFrom(null, 'f.csv')).toBe('f.csv');
    expect(verificationLevel({ revenue_confidence: 'HIGH' })).toBe('Verified');
    expect(verificationLevel({ revenue_confidence: 'UNAVAILABLE' })).toBe('Pending Review');
  });
});
