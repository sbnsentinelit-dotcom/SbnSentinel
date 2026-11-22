/** The clinic's time zone; the greeting follows UAE time whatever the viewer's own time zone is. */
export const CLINIC_TIME_ZONE = 'Asia/Dubai';

/** Hour of day (0-23) in the given time zone. */
export function hourInTimeZone(date: Date, timeZone: string = CLINIC_TIME_ZONE): number {
  const hour = new Intl.DateTimeFormat('en-US', { timeZone, hour: 'numeric', hourCycle: 'h23' }).format(date);
  return Number(hour) % 24;
}

/**
 * Greeting for UAE time:
 * 5:00 AM - 11:59 AM Good Morning, 12:00 PM - 4:59 PM Good Afternoon, 5:00 PM - 4:59 AM Good Evening.
 */
export function uaeGreeting(date: Date = new Date()): string {
  const hour = hourInTimeZone(date);
  if (hour >= 5 && hour < 12) return 'Good Morning';
  if (hour >= 12 && hour < 17) return 'Good Afternoon';
  return 'Good Evening';
}
