import type { SignalEvent } from '@/store/slices/signalSlice';

const NOT_ACTIONABLE = new Set(['None required.', 'Monitor progress.', 'NO_RECOMMENDATION']);

/** A signal stays visible in the queue if it has a recommended action and is not dismissed. */
export function isActionable(e: SignalEvent): boolean {
  return Boolean(e.recommended_action) && !NOT_ACTIONABLE.has(e.recommended_action as string)
    && e.status !== 'dismissed';
}

/** A signal is pending human review if no decision (APPROVED / REJECTED) has been recorded yet. */
export function isPendingDecision(e: SignalEvent): boolean {
  const dec = (e as any).decision_status || (e as any).decision?.status || (e as any).decision || e.status;
  const isDecided = dec === 'APPROVED' || dec === 'REJECTED' || dec === 'dismissed' || Boolean(e.decision);
  return isActionable(e) && !isDecided;
}

/**
 * Sums exposure strings such as "AED 640.00" per currency and formats them, e.g. "AED 4,970.00".
 * Values without a recognisable amount (for example "UNAVAILABLE") are left out; a value with a
 * bare "$" is counted as USD. Returns "Unavailable" when nothing can be summed (never guesses currency).
 */
export function formatExposureTotals(values: Array<string | undefined | null>): string {
  const totals = new Map<string, number>();
  for (const raw of values) {
    if (!raw) continue;
    const text = raw.trim();
    const amountMatch = text.match(/-?\d[\d,]*(\.\d+)?/);
    if (!amountMatch) continue;
    const amount = parseFloat(amountMatch[0].replace(/,/g, ''));
    if (Number.isNaN(amount)) continue;
    const codeMatch = text.match(/\b([A-Z]{3})\b/);
    const currency = codeMatch ? codeMatch[1] : text.includes('$') ? 'USD' : 'Unspecified';
    totals.set(currency, (totals.get(currency) || 0) + amount);
  }
  if (totals.size === 0) return 'Unavailable';
  return Array.from(totals.entries())
    .map(([currency, amount]) =>
      `${currency === 'Unspecified' ? '' : `${currency} `}${amount.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`)
    .join(' · ');
}
