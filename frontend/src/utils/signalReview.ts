import { fetchWithAuth } from './fetchWithAuth';

const BACKEND = process.env.NEXT_PUBLIC_BACKEND_URL || '';

/** Records an acknowledge or dismiss for a signal in the backend under the signed-in user. */
export async function recordSignalReview(signalId: string, action: 'acknowledge' | 'dismiss'): Promise<boolean> {
  try {
    const res = await fetchWithAuth(`${BACKEND}/api/v1/signals/${encodeURIComponent(signalId)}/${action}`, {
      method: 'POST',
    });
    return res.ok;
  } catch (e) {
    console.error(`Failed to ${action} signal`, e);
    return false;
  }
}
