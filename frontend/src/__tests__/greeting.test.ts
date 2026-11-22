import { describe, it, expect } from 'vitest';
import { uaeGreeting, hourInTimeZone } from '@/utils/greeting';

// UAE is UTC+4 all year (no daylight saving), so UAE hour = UTC hour + 4.
const atUaeTime = (hour: number, minute = 0) => new Date(Date.UTC(2026, 10, 21, (hour - 4 + 24) % 24, minute));

describe('uaeGreeting', () => {
  it('uses UAE time, not the viewer time zone', () => {
    expect(hourInTimeZone(atUaeTime(9))).toBe(9);
  });

  it('says Good Morning from 5:00 AM to 11:59 AM', () => {
    expect(uaeGreeting(atUaeTime(5))).toBe('Good Morning');
    expect(uaeGreeting(atUaeTime(11, 59))).toBe('Good Morning');
  });

  it('says Good Afternoon from 12:00 PM to 4:59 PM', () => {
    expect(uaeGreeting(atUaeTime(12))).toBe('Good Afternoon');
    expect(uaeGreeting(atUaeTime(16, 59))).toBe('Good Afternoon');
  });

  it('says Good Evening from 5:00 PM to 4:59 AM', () => {
    expect(uaeGreeting(atUaeTime(17))).toBe('Good Evening');
    expect(uaeGreeting(atUaeTime(23, 30))).toBe('Good Evening');
    expect(uaeGreeting(atUaeTime(0))).toBe('Good Evening');
    expect(uaeGreeting(atUaeTime(4, 59))).toBe('Good Evening');
  });
});
