/**
 * MS-001 screen behaviour: Sign Out ends the server session, the profile saves to the server and
 * survives a reload, the password change sends the current password, and an invitation link leads to
 * terms + password confirmation before activation.
 */
import React from 'react';
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';
import { describe, test, expect, beforeEach, vi } from 'vitest';
import { signOut } from '../utils/signOut';
import { UserProfileView } from '../components/CommandCenter/UserProfileView';
import { AuthScreen } from '../components/Auth/AuthScreen';
import { SuperAdminPanel } from '../components/CommandCenter/SuperAdminPanel';
import { startSessionHeartbeat, signOutMessage } from '../utils/sessionActivity';
import { fetchWithAuth } from '../utils/fetchWithAuth';
import RootPage from '../app/page';

Object.defineProperty(window, 'matchMedia', {
  writable: true,
  value: vi.fn().mockImplementation((query: string) => ({ matches: false, media: query, onchange: null,
    addListener: vi.fn(), removeListener: vi.fn(), addEventListener: vi.fn(), removeEventListener: vi.fn(),
    dispatchEvent: vi.fn() })),
});

const routerPush = vi.fn();
vi.mock('next/navigation', () => ({ useRouter: () => ({ push: routerPush, replace: vi.fn() }) }));

type Call = { url: string; method: string; body: any };
let calls: Call[] = [];
let me: any;

function respond(data: any, status = 200) {
  return Promise.resolve({ ok: status < 400, status, json: async () => data, statusText: '' } as Response);
}

const server = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
  const url = typeof input === 'string' ? input : input.toString();
  const method = (init?.method || 'GET').toUpperCase();
  const body = init?.body ? JSON.parse(String(init.body)) : null;
  calls.push({ url, method, body });
  if (url.endsWith('/api/v1/auth/logout')) return respond({ message: 'Signed out', session_id: 's1' });
  if (url.endsWith('/api/v1/auth/me') && method === 'PATCH') {
    me = { ...me, ...body };
    return respond(me);
  }
  if (url.endsWith('/api/v1/auth/me')) return respond(me);
  if (url.includes('/api/v1/clinics/')) return respond({ id: 'C-1', name: 'Clinic One' });
  if (url.endsWith('/api/v1/auth/password-policy')) return respond({ min_length: 10 });
  if (url.endsWith('/api/v1/auth/sessions')) {
    return respond({ idle_timeout_minutes: 30, sessions: [{ id: 's1', created_at: '2026-11-27T10:00:00Z',
      last_seen_at: '2026-11-27T10:05:00Z', ended_at: null, end_reason: null, user_agent: null, ip_address: null,
      active: true, current: true }] });
  }
  if (url.endsWith('/api/v1/auth/change-password')) {
    return body.current_password === 'Old2026pass' ? respond({ message: 'ok', other_sessions_ended: 2 })
      : respond({ detail: 'Current password is incorrect' }, 400);
  }
  if (url.includes('/api/v1/auth/invitation')) {
    return respond({ email: 'new@clinic.example.com', role_label: 'Staff User', organization: 'Org A',
      clinic: 'Clinic One', terms_version: 'v1' });
  }
  if (url.endsWith('/api/v1/auth/accept-invite')) return respond({ message: 'Account has been successfully activated.' });
  return respond({});
});

beforeEach(() => {
  calls = [];
  me = { id: 7, email: 'admin@clinic.example.com', full_name: 'Amina Khan', role: 'Clinic Administrator',
    phone: '', clinic_id: 'C-1', org_id: 'ORG-A', is_active: true, created_at: '2026-11-01T00:00:00Z' };
  global.fetch = server as unknown as typeof fetch;
  localStorage.setItem('token', 'tkn');
  window.history.replaceState(null, '', '/');
});

describe('MS-001', () => {
  test('Sign Out calls the server logout before clearing the browser', async () => {
    localStorage.setItem('user', '{}');
    localStorage.setItem('userRole', 'Clinic Administrator');
    const ended = await signOut();
    expect(ended).toBe(true);
    const logout = calls.find(c => c.url.endsWith('/api/v1/auth/logout'));
    expect(logout?.method).toBe('POST');
    expect(localStorage.getItem('token')).toBeNull();
    expect(localStorage.getItem('userRole')).toBeNull();
  });

  test('the profile saves to the server and shows what the server returned after a reload', async () => {
    const first = render(<UserProfileView />);
    await screen.findByText('Amina Khan', { exact: false });
    fireEvent.change(screen.getByLabelText('Last Name'), { target: { value: 'Rahman' } });
    fireEvent.change(screen.getByLabelText('Phone Number'), { target: { value: '+971 4 111 2222' } });
    await act(async () => { fireEvent.click(screen.getByText('Save Changes')); });
    await screen.findByText('Profile updated successfully');
    const patch = calls.find(c => c.url.endsWith('/api/v1/auth/me') && c.method === 'PATCH');
    expect(patch?.body).toEqual({ full_name: 'Amina Rahman', phone: '+971 4 111 2222' });
    first.unmount();
    render(<UserProfileView />);                                    // a reload reads the server again
    await waitFor(() => expect((screen.getByLabelText('Last Name') as HTMLInputElement).value).toBe('Rahman'));
    expect((screen.getByLabelText('Phone Number') as HTMLInputElement).value).toBe('+971 4 111 2222');
    expect(screen.getByText(/Two-factor sign-in is not part of this release/)).toBeTruthy();
  });

  test('the password change sends the current password and reports a wrong one', async () => {
    render(<UserProfileView />);
    await screen.findByText('Amina Khan', { exact: false });
    fireEvent.click(screen.getByText('Change Password'));
    expect(screen.getByText(/At least 10 characters/)).toBeTruthy();
    fireEvent.change(screen.getByLabelText('Current password'), { target: { value: 'wrong' } });
    fireEvent.change(screen.getByLabelText('New password'), { target: { value: 'New2026passw' } });
    fireEvent.change(screen.getByLabelText('Confirm new password'), { target: { value: 'New2026passw' } });
    await act(async () => { fireEvent.click(screen.getByText('Update Password')); });
    await screen.findByText('Current password is incorrect');
    fireEvent.change(screen.getByLabelText('Current password'), { target: { value: 'Old2026pass' } });
    await act(async () => { fireEvent.click(screen.getByText('Update Password')); });
    await screen.findByText(/2 other session\(s\) signed out/);
    const sent = calls.filter(c => c.url.endsWith('/api/v1/auth/change-password')).pop();
    expect(sent?.body).toEqual({ current_password: 'Old2026pass', new_password: 'New2026passw', confirm_password: 'New2026passw' });
  });

  test('an invitation link asks for the terms and a confirmed password before activating', async () => {
    window.history.replaceState(null, '', '/?invite_token=abc123');
    render(<AuthScreen onLogin={() => {}} />);
    await screen.findByText(/Invited as Staff User · Org A · Clinic One/);
    expect(screen.getByText('Using the secure link from your email.')).toBeTruthy();
    const pw = screen.getByPlaceholderText('••••••••');
    fireEvent.change(pw, { target: { value: 'Welcome2026' } });
    fireEvent.change(screen.getByLabelText('Confirm new password'), { target: { value: 'Welcome2026' } });
    fireEvent.click(screen.getByRole('checkbox'));
    await act(async () => { fireEvent.submit(pw.closest('form')!); });
    const accept = calls.find(c => c.url.endsWith('/api/v1/auth/accept-invite'));
    expect(accept?.body).toMatchObject({ token: 'abc123', new_password: 'Welcome2026', confirm_password: 'Welcome2026',
      accept_terms: true });
    expect(accept?.body.otp).toBeUndefined();
    await screen.findByText(/Account activated successfully/);
  });

  test('one failing platform request does not empty the tenant list', async () => {
    const platformApi = vi.fn((input: RequestInfo | URL) => {
      const url = typeof input === 'string' ? input : input.toString();
      if (url.endsWith('/api/v1/pasme/rules')) return respond({ detail: 'Internal Server Error' }, 500);
      if (url.endsWith('/api/v1/super-admin/users')) {
        return respond([{ id: 2, full_name: 'Demo Owner', email: 'owner@clinic.example.com', role: 'Organization Administrator',
          org_id: 'ORG-UAE-01', is_active: true }]);
      }
      if (url.endsWith('/api/v1/organizations')) {
        return respond([{ id: 'ORG-UAE-01', name: 'Sentinel UAE Medical Center', status: 'Active', users: 3, clinics: 1 }]);
      }
      if (url.endsWith('/api/v1/super-admin/stats')) return respond({ total_clinics: 1, active_users: 3 });
      if (url.endsWith('/api/v1/pasme/health')) return respond({ maintenance_mode: false, metrics: {}, modules: {} });
      return respond([]);
    });
    global.fetch = platformApi as unknown as typeof fetch;
    render(<SuperAdminPanel user={{ email: 'superadmin@sbnsentinel.com', full_name: 'SBN Super Admin' }} onLogout={() => {}} />);
    const tab = await screen.findByRole('button', { name: /Clinics \(Tenants\)/ }, { timeout: 4000 });
    await act(async () => { fireEvent.click(tab); });
    expect(await screen.findByText('Sentinel UAE Medical Center')).toBeTruthy();
    expect(screen.getByText('Demo Owner')).toBeTruthy();
    expect(screen.queryByText('No clinics registered yet.')).toBeNull();
  });

  test('activity on the screen keeps the session alive; doing nothing sends nothing', async () => {
    vi.useFakeTimers();
    try {
      const ping = vi.fn(() => Promise.resolve());
      const stop = startSessionHeartbeat(ping);
      await vi.advanceTimersByTimeAsync(3 * 60_000);        // idle: no request, the server may end it
      expect(ping).not.toHaveBeenCalled();
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'a' }));   // typing in a form
      await vi.advanceTimersByTimeAsync(61_000);
      expect(ping).toHaveBeenCalledTimes(1);
      await vi.advanceTimersByTimeAsync(3 * 60_000);        // no further activity, no further ping
      expect(ping).toHaveBeenCalledTimes(1);
      stop();
    } finally {
      vi.useRealTimers();
    }
  });

  test('the sign-in screen says why the session ended', async () => {
    expect(signOutMessage('Session ended (idle_timeout)')).toMatch(/without activity/);
    expect(signOutMessage('Session ended (org_suspended)')).toMatch(/suspended/);
    expect(signOutMessage('Session ended (logout)')).toBeNull();
    expect(signOutMessage('Could not validate credentials')).toBeNull();
    sessionStorage.setItem('signout_reason', signOutMessage('Session ended (idle_timeout)')!);
    render(<AuthScreen onLogin={() => {}} />);
    expect(await screen.findByText(/signed out after a period without activity/)).toBeTruthy();
    expect(sessionStorage.getItem('signout_reason')).toBeNull();     // shown once
  });

  test('a request refused because the session went idle keeps the reason for the sign-in screen', async () => {
    global.fetch = vi.fn(() => Promise.resolve(new Response(JSON.stringify({ detail: 'Session ended (idle_timeout)' }),
      { status: 401, headers: { 'Content-Type': 'application/json' } }))) as unknown as typeof fetch;
    await expect(fetchWithAuth('/api/v1/super-admin/users/invite', { method: 'POST' })).rejects.toBeTruthy();
    expect(sessionStorage.getItem('signout_reason')).toMatch(/without activity/);
    expect(localStorage.getItem('token')).toBeNull();
    sessionStorage.removeItem('signout_reason');
  });

  test('an invitation link opens activation even in a signed-in browser, and signs that user out', async () => {
    routerPush.mockReset();
    localStorage.setItem('token', 'platform-token');
    localStorage.setItem('userRole', 'System Administrator');
    window.history.replaceState(null, '', '/?invite_token=abc123');
    render(<RootPage />);
    expect(await screen.findByText('Using the secure link from your email.', {}, { timeout: 4000 })).toBeTruthy();
    expect(calls.some(c => c.url.endsWith('/api/v1/auth/logout') && c.method === 'POST')).toBe(true);
    expect(localStorage.getItem('token')).toBeNull();
    expect(routerPush).not.toHaveBeenCalled();
  });

  test('System Health shows only measured values', async () => {
    const health = (cpu: number | null) => vi.fn((input: RequestInfo | URL) => {
      const url = typeof input === 'string' ? input : input.toString();
      if (url.endsWith('/api/v1/pasme/health')) return respond({ maintenance_mode: false, metrics: { cpu_percent: cpu, memory_usage_mb: null }, modules: {} });
      return respond(url.endsWith('/stats') ? { total_clinics: 1 } : []);
    });
    global.fetch = health(37.4) as unknown as typeof fetch;
    const first = render(<SuperAdminPanel user={{ email: 'superadmin@sbnsentinel.com' }} onLogout={() => {}} />);
    expect(await screen.findByText('37%', {}, { timeout: 4000 })).toBeTruthy();
    expect(screen.getAllByText('Not measured').length).toBeGreaterThanOrEqual(2);   // storage, AI quota
    expect(screen.queryByText('45%')).toBeNull();
    expect(screen.queryByText('28%')).toBeNull();
    first.unmount();
    global.fetch = health(null) as unknown as typeof fetch;
    render(<SuperAdminPanel user={{ email: 'superadmin@sbnsentinel.com' }} onLogout={() => {}} />);
    await waitFor(() => expect(screen.getAllByText('Not measured').length).toBeGreaterThanOrEqual(3));
  });
});
