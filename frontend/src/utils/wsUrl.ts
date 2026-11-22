/**
 * Base URL for backend WebSockets, e.g. "wss://www.sbnsentinel.com".
 *
 * Order: NEXT_PUBLIC_BACKEND_WS_URL, then NEXT_PUBLIC_BACKEND_URL with http(s) turned into ws(s),
 * then the page's own origin (the reverse proxy routes /api/* to the backend). It never falls back
 * to localhost, so a deployed page does not try to reach the visitor's own machine.
 */
export function getBackendWsBaseUrl(): string {
  const explicit = process.env.NEXT_PUBLIC_BACKEND_WS_URL;
  if (explicit) return explicit.replace(/\/$/, '');
  const httpBase = process.env.NEXT_PUBLIC_BACKEND_URL;
  if (httpBase) return httpBase.replace(/\/$/, '').replace(/^http/, 'ws');
  if (typeof window !== 'undefined') {
    const scheme = window.location.protocol === 'https:' ? 'wss' : 'ws';
    return `${scheme}://${window.location.host}`;
  }
  return '';
}
