/**
 * MS-001: Sign Out ends the session on the server first (the token stops working at once and the
 * logout is audited), then clears the browser's copy. A failed call still clears the browser.
 */
export async function signOut(): Promise<boolean> {
  const token = typeof window !== 'undefined' ? localStorage.getItem('token') : null;
  let endedOnServer = false;
  if (token) {
    try {
      const res = await fetch(`${process.env.NEXT_PUBLIC_BACKEND_URL || ''}/api/v1/auth/logout`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}` },
      });
      endedOnServer = res.ok || res.status === 401;   // 401: the server had already ended it
    } catch {
      endedOnServer = false;
    }
  }
  localStorage.removeItem('token');
  localStorage.removeItem('user');
  localStorage.removeItem('userRole');
  sessionStorage.removeItem('just_logged_in');
  return endedOnServer;
}
