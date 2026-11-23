/**
 * Offset-aware date and time formatter displaying clinic-local labeled timestamps (SDS-002 §3, SDS-010 §3).
 */
export function formatDateTimeWithZone(ts: string | Date | undefined | null, targetZone: string = 'GST'): string {
  if (!ts) return 'Unavailable';
  try {
    let d: Date;
    if (typeof ts === 'string') {
      const trimmed = ts.trim();
      // Ensure ISO string without timezone is interpreted as UTC to avoid local timezone skew across browsers
      const hasTimezone = trimmed.endsWith('Z') || trimmed.includes('+') || (trimmed.length > 10 && trimmed.lastIndexOf('-') > 9);
      const normalized = hasTimezone ? trimmed : `${trimmed}Z`;
      d = new Date(normalized);
    } else {
      d = ts;
    }
    if (isNaN(d.getTime())) return String(ts);

    const options: Intl.DateTimeFormatOptions = {
      timeZone: targetZone === 'GST' ? 'Asia/Dubai' : 'UTC',
      day: '2-digit',
      month: 'short',
      year: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      hour12: false
    };
    return `${new Intl.DateTimeFormat('en-GB', options).format(d)} ${targetZone}`;
  } catch {
    return String(ts);
  }
}
