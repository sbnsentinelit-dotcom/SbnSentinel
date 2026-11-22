/**
 * Time left until a deadline, counted live in the browser, e.g. "3 days 2 hours left",
 * "5 hours 10 minutes left" or "Deadline passed 1 day 2 hours ago". Matches the backend wording.
 */
export function timeLeftText(deadlineIso: string | null | undefined, nowMs: number = Date.now()): string | null {
  if (!deadlineIso) return null;
  const deadline = Date.parse(deadlineIso);
  if (Number.isNaN(deadline)) return null;
  const diff = deadline - nowMs;
  const totalMinutes = Math.floor(Math.abs(diff) / 60000);
  const days = Math.floor(totalMinutes / 1440);
  const hours = Math.floor((totalMinutes % 1440) / 60);
  const minutes = totalMinutes % 60;
  const plural = (n: number, unit: string) => `${n} ${unit}${n === 1 ? '' : 's'}`;
  const parts: string[] = [];
  if (days) parts.push(plural(days, 'day'));
  if (hours) parts.push(plural(hours, 'hour'));
  if (!days && (minutes || parts.length === 0)) parts.push(plural(minutes, 'minute'));
  const span = parts.slice(0, 2).join(' ');
  return diff < 0 ? `Deadline passed ${span} ago` : `${span} left`;
}

/** Badge colour by urgency: under a day red, under the 7-day warning window orange, otherwise yellow. */
export function timeLeftTone(deadlineIso: string | null | undefined, nowMs: number = Date.now()): 'passed' | 'critical' | 'warning' | 'calm' {
  const deadline = deadlineIso ? Date.parse(deadlineIso) : NaN;
  if (Number.isNaN(deadline)) return 'calm';
  const hoursLeft = (deadline - nowMs) / 3600000;
  if (hoursLeft < 0) return 'passed';
  if (hoursLeft < 24) return 'critical';
  if (hoursLeft < 24 * 7) return 'warning';
  return 'calm';
}
