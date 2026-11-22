/**
 * Builds the stat-card trend ("12.5%" vs yesterday) and sparkline path from daily counts,
 * oldest first with today last. The path uses the card's 120 x 40 drawing box.
 */
export function trendFromDaily(counts: number[]): { trend: string; trendUp: boolean; sparkData: string } {
  const values = counts.length > 0 ? counts : [0];
  const today = values.at(-1) ?? 0;
  const yesterday = values.length > 1 ? values.at(-2) ?? 0 : 0;

  let trend: string;
  if (yesterday === 0) {
    trend = today === 0 ? '0%' : 'new';
  } else {
    const pct = ((today - yesterday) / yesterday) * 100;
    trend = `${Math.abs(Math.round(pct * 10) / 10)}%`;
  }
  const trendUp = today >= yesterday;

  const max = Math.max(...values);
  const step = values.length > 1 ? 120 / (values.length - 1) : 120;
  const y = (v: number) => (max > 0 ? Math.round((38 - (v / max) * 33) * 10) / 10 : 38);
  const sparkData = values
    .map((v, i) => `${i === 0 ? 'M' : 'L'}${Math.round(i * step)},${y(v)}`)
    .join(' ');
  return { trend, trendUp, sparkData };
}
