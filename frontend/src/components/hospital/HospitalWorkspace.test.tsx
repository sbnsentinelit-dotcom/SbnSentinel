import React from 'react';
import { render, screen, fireEvent, waitFor, cleanup } from '@testing-library/react';
import { afterEach, describe, it, expect, vi } from 'vitest';
import { HospitalWorkspace } from './HospitalWorkspace';
import { DepartmentSummary } from './DepartmentSummary';
import { HospitalScopeSelector } from './HospitalScopeSelector';
import { fetchWithAuth } from '@/utils/fetchWithAuth';

vi.mock('@/utils/fetchWithAuth', () => ({ fetchWithAuth: vi.fn() }));
const mockedFetch = vi.mocked(fetchWithAuth);
const scope = { org_id: 'org', clinic_id: 'a', allowed_department_ids: ['A', 'B'], all_departments: false,
  capabilities: ['report.read', 'report.export', 'review.read'], grant_revision: 1 };
const facility = { clinic_id: 'a', name: 'Hospital A', scope,
  departments: [{ id: 'A', name: 'Cardiology' }, { id: 'B', name: 'Radiology' }] };
function snapshot(label: string) {
  return { snapshot_id: label, scope, currency: 'AED', knowledge_cutoff: '2026-01-01T00:00:00Z',
    restricted: true, completeness_status: 'PARTIAL', coverage_exceptions: [],
    metrics: { line_count: 1, distinct_claim_count: 1, known_billed_amount: label,
      known_paid_amount: null, unknown_payment_lines: 1 }, rows: [] };
}
function response(data: unknown) { return { json: async () => data } as Response; }
afterEach(() => { cleanup(); vi.clearAllMocks(); });

describe('HSP01 hospital workspace', () => {
  it('only offers server-authorized directories and narrows selection', () => {
    const change = vi.fn();
    render(<HospitalScopeSelector facilities={[facility]} selection={{ clinicId: 'a', departmentId: '', revision: 1 }} onChange={change} />);
    fireEvent.change(screen.getByLabelText('Hospital department'), { target: { value: 'A' } });
    expect(change).toHaveBeenCalledWith({ clinicId: 'a', departmentId: 'A', revision: 1 });
    expect(screen.queryByText('Hospital B')).toBeNull();
  });

  it('preserves exact decimal text and unknown payment', () => {
    render(<DepartmentSummary snapshot={snapshot('9007199254740993.01')} />);
    expect(screen.getByText('AED 9007199254740993.01')).toBeTruthy();
    expect(screen.getByText('Unavailable')).toBeTruthy();
    expect(screen.getByText(/Restricted to permitted lines/)).toBeTruthy();
  });

  it('shows authorized department names and trims padding without rounding money', () => {
    const data = { ...snapshot('100.10000000'), rows: [{ line_id: 'line', department_id: 'A',
      billed_amount: '0.12345678', paid_amount: '0.00000000', status: 'submitted' }] };
    render(<DepartmentSummary snapshot={data} departments={facility.departments} />);
    expect(screen.getByText('AED 100.10')).toBeTruthy();
    expect(screen.getByText('AED 0.12345678')).toBeTruthy();
    expect(screen.getByText('AED 0.00')).toBeTruthy();
    expect(screen.getByText('Cardiology')).toBeTruthy();
    expect(screen.queryByText('A')).toBeNull();
  });

  it('keeps review and assignment available without report permission', async () => {
    const reviewerScope = { ...scope, capabilities: ['review.read', 'assignment.write'] };
    mockedFetch.mockImplementation(async url => {
      if (String(url).endsWith('/directory')) return response({ facilities: [{ ...facility, scope: reviewerScope }] });
      if (String(url).includes('/queue')) return response({ scope: reviewerScope, unavailable_reason: '', items: [
        { recommendation_id: 'review-only', status: 'ACTIVE', department_id: 'A', assignee: null, team: null, row_version: 1 },
      ] });
      throw new Error('Report must not be requested');
    });
    render(<HospitalWorkspace />);
    expect(await screen.findByText(/review-only/)).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Assign' })).toBeTruthy();
    expect(mockedFetch.mock.calls.some(([url]) => String(url).endsWith('/snapshots'))).toBe(false);
  });

  it('keeps a successful queue when financial snapshot fails', async () => {
    mockedFetch.mockImplementation(async url => {
      if (String(url).endsWith('/directory')) return response({ facilities: [facility] });
      if (String(url).includes('/queue')) return response({ scope, unavailable_reason: '', items: [
        { recommendation_id: 'retained-review', status: 'ACTIVE', department_id: 'A', assignee: null, team: null, row_version: 1 },
      ] });
      throw new Error('Snapshot failure');
    });
    render(<HospitalWorkspace />);
    expect(await screen.findByText(/retained-review/)).toBeTruthy();
    expect(screen.getByText(/Financial report unavailable/)).toBeTruthy();
  });

  it('shows the queue while the independent report request is still pending', async () => {
    mockedFetch.mockImplementation(async url => {
      if (String(url).endsWith('/directory')) return response({ facilities: [facility] });
      if (String(url).includes('/queue')) return response({ scope, unavailable_reason: '', items: [
        { recommendation_id: 'while-report-pending', status: 'ACTIVE', department_id: 'A', assignee: null, team: null, row_version: 1 },
      ] });
      return new Promise<Response>(() => {});
    });
    render(<HospitalWorkspace />);
    expect(await screen.findByText(/while-report-pending/)).toBeTruthy();
    expect(screen.getByText('Loading financial report...')).toBeTruthy();
  });

  it('clears old-scope results and discards a late response', async () => {
    let finishA: ((value: Response) => void) | undefined;
    mockedFetch.mockImplementation(async (url, options) => {
      const path = String(url);
      if (path.endsWith('/directory')) return response({ facilities: [facility] });
      if (path.includes('/queue')) return response({ items: [], unavailable_reason: '', scope });
      const body = JSON.parse(String(options?.body));
      if (body.department_id === 'A') return new Promise<Response>(resolve => { finishA = resolve; });
      return response(snapshot(body.department_id === 'B' ? '222.00' : '111.00'));
    });
    render(<HospitalWorkspace />);
    await screen.findByText('AED 111.00');
    fireEvent.change(screen.getByLabelText('Hospital department'), { target: { value: 'A' } });
    expect(screen.queryByText('AED 111.00')).toBeNull();
    await waitFor(() => expect(finishA).toBeDefined());
    fireEvent.change(screen.getByLabelText('Hospital department'), { target: { value: 'B' } });
    await screen.findByText('AED 222.00');
    finishA!(response(snapshot('999.00')));
    await waitFor(() => expect(screen.queryByText('AED 999.00')).toBeNull());
    expect(screen.getByText('AED 222.00')).toBeTruthy();
  });

  it('shows explicit unavailable state after an access failure', async () => {
    mockedFetch.mockRejectedValue(new Error('Forbidden'));
    render(<HospitalWorkspace />);
    expect(await screen.findByRole('alert')).toHaveProperty('textContent', 'Hospital directory unavailable.');
    expect(screen.queryByText(/Known billed amount/)).toBeNull();
  });

  it('keeps authorized reports visible when the review queue is unavailable', async () => {
    mockedFetch.mockImplementation(async url => {
      if (String(url).endsWith('/directory')) return response({ facilities: [facility] });
      if (String(url).includes('/queue')) throw new Error('Review capability unavailable');
      return response(snapshot('333.00'));
    });
    render(<HospitalWorkspace />);
    expect(await screen.findByText('AED 333.00')).toBeTruthy();
    expect(screen.getByText('Review queue unavailable under current permissions.')).toBeTruthy();
  });
});
