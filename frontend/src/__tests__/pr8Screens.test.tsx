/**
 * PR #8 MS-001 remediation, screen side: the heartbeat reports activity (never a read), "Sync Live
 * Data" runs the clinic's sync and shows the server's answer, Synced Encounters comes from the recorded
 * sync run, the organization can be edited, the profile cannot be edited before it has loaded, and the
 * controls outside V1 stay in place but disabled.
 */
import React from 'react';
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';
import { describe, test, expect, beforeEach, vi } from 'vitest';
import { startSessionHeartbeat } from '../utils/sessionActivity';
import { SettingsView } from '../components/CommandCenter/SettingsView';
import { SuperAdminPanel } from '../components/CommandCenter/SuperAdminPanel';
import { AuditLogsView } from '../components/CommandCenter/AuditLogsView';
import { UserProfileView } from '../components/CommandCenter/UserProfileView';

Object.defineProperty(window, 'matchMedia', {
  writable: true,
  value: vi.fn().mockImplementation((query: string) => ({ matches: false, media: query, onchange: null,
    addListener: vi.fn(), removeListener: vi.fn(), addEventListener: vi.fn(), removeEventListener: vi.fn(),
    dispatchEvent: vi.fn() })),
});
vi.mock('next/navigation', () => ({ useRouter: () => ({ push: vi.fn(), replace: vi.fn() }) }));

type Call = { url: string; method: string; body: any };
let calls: Call[] = [];

function json(data: any, status = 200) {
  return Promise.resolve(new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } }));
}

function install(handler: (url: string, method: string, body: any) => Promise<Response> | undefined) {
  global.fetch = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === 'string' ? input : input.toString();
    const method = (init?.method || 'GET').toUpperCase();
    const body = init?.body ? JSON.parse(String(init.body)) : null;
    calls.push({ url, method, body });
    const listLike = url.includes('/clinics') || url.includes('/health') || url.includes('/integrations');
    return handler(url, method, body) || json(listLike ? [] : {});
  }) as unknown as typeof fetch;
}

beforeEach(() => {
  calls = [];
  localStorage.setItem('token', 'tkn');
  localStorage.setItem('userRole', 'Organization Administrator');
});

describe('PR #8 screens', () => {
  test('the heartbeat reports activity to the activity endpoint, not a read', async () => {
    install(() => json({ recorded: true }));
    vi.useFakeTimers();
    try {
      const stop = startSessionHeartbeat();
      await vi.advanceTimersByTimeAsync(1_000);
      window.dispatchEvent(new MouseEvent('mousedown'));
      await vi.advanceTimersByTimeAsync(61_000);
      stop();
    } finally {
      vi.useRealTimers();
    }
    await waitFor(() => expect(calls.filter(c => c.url.endsWith('/api/v1/auth/activity')).length).toBe(1));
    const sent = calls.filter(c => c.url.endsWith('/api/v1/auth/activity'));
    expect(sent.length).toBe(1);
    expect(sent[0].method).toBe('POST');
    expect(calls.some(c => c.url.endsWith('/api/v1/auth/me'))).toBe(false);
  });

  test('Sync Live Data on a clinic without a connection shows the server answer and "Not synced"', async () => {
    install((url, method) => {
      if (url.endsWith('/api/v1/clinics')) return json([{ id: 'C-1', name: 'Clinic One', status: 'Active' }]);
      if (url.includes('/api/v1/reality/sync-status')) {
        return json({ clinic_id: 'C-1', configured: false, connections: [], last_run: null, last_success: null });
      }
      if (url.endsWith('/api/v1/reality/sync-clinic') && method === 'POST') {
        return json({ detail: 'No connection is configured for this clinic; nothing was synced.' }, 409);
      }
      return undefined;
    });
    render(<SettingsView activeMenuProp="integrations" />);
    await waitFor(() => expect(calls.some(c => c.url.includes('/reality/sync-status?clinic_id=C-1'))).toBe(true));
    expect(await screen.findByText('Not synced')).toBeTruthy();
    expect(screen.queryByText('Active Link')).toBeNull();          // no "Active Link" without a verified connection
    expect(screen.getAllByText('Not configured').length).toBeGreaterThanOrEqual(1);
    await act(async () => { fireEvent.click(screen.getByText('Sync Live Data')); });
    expect(await screen.findByText(/No connection is configured for this clinic; nothing was synced\./)).toBeTruthy();
    const sync = calls.find(c => c.url.endsWith('/api/v1/reality/sync-clinic'));
    expect(sync?.body).toEqual({ clinic_id: 'C-1' });
    expect(calls.some(c => c.url.includes('/encounters/seed-dubai'))).toBe(false);   // never the demo seed
    expect(screen.getByText('Not synced')).toBeTruthy();
  });

  test('Synced Encounters shows the last successful recorded sync, even after a failed one', async () => {
    const success = { run_id: 'R-1', status: 'succeeded', finished_at: '2026-11-28T09:00:00Z', imported_count: 12, error: null };
    install((url, method) => {
      if (url.endsWith('/api/v1/clinics')) return json([{ id: 'C-1', name: 'Clinic One', status: 'Active' }]);
      if (url.includes('/api/v1/reality/sync-status')) {
        return json({ clinic_id: 'C-1', configured: true, connections: [{ verification: 'verified' }],
          last_run: success, last_success: success });
      }
      if (url.endsWith('/api/v1/reality/sync-clinic') && method === 'POST') {
        const failed = { run_id: 'R-2', status: 'failed', finished_at: '2026-11-28T10:00:00Z', imported_count: null, error: 'Provider timed out' };
        return json({ runs: [failed], clinic_id: 'C-1', configured: true, connections: [{ verification: 'verified' }],
          last_run: failed, last_success: success });
      }
      return undefined;
    });
    render(<SettingsView activeMenuProp="integrations" />);
    expect(await screen.findByText('12 Records')).toBeTruthy();
    expect(screen.getByText('Active Link')).toBeTruthy();
    await act(async () => { fireEvent.click(screen.getByText('Sync Live Data')); });
    expect(await screen.findByText(/Sync failed: Provider timed out/)).toBeTruthy();
    expect(screen.getByText('12 Records')).toBeTruthy();
  });

  test('the Platform Administrator edits an organization name and time zone', async () => {
    install((url, method, body) => {
      if (url.endsWith('/api/v1/organizations') && method === 'GET') {
        return json([{ id: 'ORG-1', name: 'Org One', status: 'Active', users: 1, clinics: 1, time_zone: 'UTC' }]);
      }
      if (url.endsWith('/api/v1/organizations/ORG-1') && method === 'PATCH') {
        return json({ id: 'ORG-1', name: body.name, time_zone: body.time_zone, status: 'Active' });
      }
      if (url.endsWith('/api/v1/super-admin/stats')) return json({ total_clinics: 1 });
      if (url.endsWith('/api/v1/pasme/health')) return json({ maintenance_mode: false, metrics: {}, modules: {} });
      return json([]);
    });
    render(<SuperAdminPanel user={{ email: 'superadmin@sbnsentinel.com' }} onLogout={() => {}} />);
    const tab = await screen.findByRole('button', { name: /Clinics \(Tenants\)/ }, { timeout: 4000 });
    await act(async () => { fireEvent.click(tab); });
    await act(async () => { fireEvent.click(await screen.findByRole('button', { name: 'Edit Org One' })); });
    fireEvent.change(screen.getByLabelText('Organization Name'), { target: { value: 'Org One Renamed' } });
    fireEvent.change(screen.getByLabelText('Time Zone'), { target: { value: 'Asia/Dubai' } });
    await act(async () => { fireEvent.click(screen.getByText('Save Organization')); });
    const patch = calls.find(c => c.method === 'PATCH');
    expect(patch?.url).toMatch(/\/api\/v1\/organizations\/ORG-1$/);
    expect(patch?.body).toEqual({ name: 'Org One Renamed', time_zone: 'Asia/Dubai' });
    expect(await screen.findByText('Org One Renamed')).toBeTruthy();
  });

  test('Pending Approvals stays on screen and says it is not part of V1', async () => {
    install(url => url.endsWith('/api/v1/pasme/health') ? json({ maintenance_mode: false, metrics: {}, modules: {} }) : json([]));
    render(<SuperAdminPanel user={{ email: 'superadmin@sbnsentinel.com' }} onLogout={() => {}} />);
    const tab = await screen.findByRole('button', { name: /Pending Approvals/ }, { timeout: 4000 });
    await act(async () => { fireEvent.click(tab); });
    expect(await screen.findByText(/Not in V1: clinic self-registration and its approval are not part of this release/)).toBeTruthy();
    expect(screen.getByText('Pending Clinic Registrations')).toBeTruthy();
  });

  test('Export Audit Log stays in place, disabled, marked Not in V1', async () => {
    install(() => json([]));
    render(<AuditLogsView />);
    const button = await screen.findByRole('button', { name: /Export Audit Log \(Not in V1\)/ });
    expect((button as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(button);
    expect(calls.some(c => /export/i.test(c.url))).toBe(false);
  });

  test('the profile cannot be edited before the server copy has loaded', async () => {
    let release: (v: Response) => void = () => {};
    install(url => {
      if (url.endsWith('/api/v1/auth/me')) return new Promise<Response>(r => { release = r; });
      if (url.endsWith('/api/v1/auth/sessions')) return json({ idle_timeout_minutes: 30, sessions: [] });
      return undefined;
    });
    render(<UserProfileView />);
    const last = screen.getByLabelText('Last Name') as HTMLInputElement;
    expect(last.disabled).toBe(true);
    expect((screen.getByText('Save Changes').closest('button') as HTMLButtonElement).disabled).toBe(true);
    await act(async () => {
      release(new Response(JSON.stringify({ id: 7, email: 'a@clinic.example.com', full_name: 'Amina Khan', phone: '',
        role: 'Clinic Administrator', is_active: true }), { status: 200, headers: { 'Content-Type': 'application/json' } }));
    });
    await waitFor(() => expect((screen.getByLabelText('Last Name') as HTMLInputElement).disabled).toBe(false));
    expect((screen.getByLabelText('Last Name') as HTMLInputElement).value).toBe('Khan');
  });
});
