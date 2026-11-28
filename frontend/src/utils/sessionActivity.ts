/**
 * MS-001: the server ends a session after SESSION_IDLE_TIMEOUT_MINUTES without activity, and it only
 * sees requests. Typing a long form or reading a page sends none, so while the person is actually
 * using the screen (keys, clicks, touch, scroll) this tells the server once a minute. A person who
 * does nothing is still signed out on time.
 */
const ACTIVITY_EVENTS = ['keydown', 'mousedown', 'touchstart', 'scroll', 'wheel'] as const;
export const HEARTBEAT_EVERY_MS = 60_000;

export function startSessionHeartbeat(ping?: () => Promise<unknown>): () => void {
  if (typeof window === 'undefined') return () => {};
  let lastActivity = 0;
  let lastPing = Date.now();
  const onActivity = () => { lastActivity = Date.now(); };
  ACTIVITY_EVENTS.forEach(evt => window.addEventListener(evt, onActivity, { passive: true, capture: true }));

  const send = ping || (async () => {
    const { fetchWithAuth } = await import('./fetchWithAuth');
    await fetchWithAuth(`${process.env.NEXT_PUBLIC_BACKEND_URL || ''}/api/v1/auth/me`);
  });

  const timer = window.setInterval(() => {
    const now = Date.now();
    const activeSinceLastPing = lastActivity > lastPing;
    if (!localStorage.getItem('token') || !activeSinceLastPing || now - lastPing < HEARTBEAT_EVERY_MS) return;
    lastPing = now;
    send().catch(() => { /* a 401 is handled by fetchWithAuth (sign-in screen with the reason) */ });
  }, 15_000);

  return () => {
    window.clearInterval(timer);
    ACTIVITY_EVENTS.forEach(evt => window.removeEventListener(evt, onActivity, { capture: true }));
  };
}

const REASONS: Record<string, string> = {
  idle_timeout: 'You were signed out after a period without activity. Please sign in again.',
  expired: 'Your session expired. Please sign in again.',
  password_changed: 'Your password was changed. Please sign in again.',
  password_reset: 'Your password was reset. Please sign in with the new password.',
  role_changed: 'An administrator changed your access. Please sign in again.',
  access_removed: 'Your access was removed. Contact your administrator.',
  org_suspended: 'Your organization is suspended. Contact your platform administrator.',
  revoked: 'This session was signed out from another session. Please sign in again.',
};

/** The sign-in screen's message for why the server ended a session ("Session ended (idle_timeout)"). */
export function signOutMessage(detail: string | null | undefined): string | null {
  if (!detail) return null;
  const match = /Session ended \(([a-z_]+)\)/.exec(detail);
  if (!match) return null;
  if (match[1] === 'logout') return null;
  return REASONS[match[1]] || 'Your session has ended. Please sign in again.';
}

export const SIGNOUT_REASON_KEY = 'signout_reason';
