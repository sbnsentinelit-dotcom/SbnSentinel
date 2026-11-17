'use client';
import { useEffect, useState } from 'react';
import { fetchWithAuth } from '@/utils/fetchWithAuth';
import { HospitalScopeSelector, type HospitalFacility, type HospitalSelection } from './HospitalScopeSelector';
import { DepartmentSummary, type HospitalSnapshot } from './DepartmentSummary';
import { ReviewQueue, type HospitalQueueItem } from './ReviewQueue';

function businessDate(value: Date) {
  const parts = new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Dubai', year: 'numeric', month: '2-digit', day: '2-digit' }).formatToParts(value);
  return ['year', 'month', 'day'].map(type => parts.find(part => part.type === type)?.value).join('-');
}
export function HospitalWorkspace() {
  const [facilities, setFacilities] = useState<HospitalFacility[]>([]);
  const [selection, setSelection] = useState<HospitalSelection | null>(null);
  const [currency, setCurrency] = useState('AED');
  const [start, setStart] = useState(() => businessDate(new Date(Date.now() - 30 * 86400000)));
  const [end, setEnd] = useState(() => businessDate(new Date(Date.now() + 86400000)));
  const [directoryError, setDirectoryError] = useState('');
  const [view, setView] = useState<{ key: string; snapshot?: HospitalSnapshot; queue?: HospitalQueueItem[]; reason?: string; error?: string; reportError?: string; queueError?: string; reportLoading?: boolean; queueLoading?: boolean } | null>(null);
  const [exportError, setExportError] = useState('');
  const [refresh, setRefresh] = useState(0);
  useEffect(() => {
    let alive = true;
    let loading = false;
    const controller = new AbortController();
    async function load() {
      if (loading) return;
      loading = true;
      try {
        const response = await fetchWithAuth('/api/v1/hospital/directory', { signal: controller.signal });
        const data = await response.json() as { facilities: HospitalFacility[] };
        if (!alive) return;
        setFacilities(data.facilities); setDirectoryError('');
        setSelection(current => {
          const facility = data.facilities.find(item => item.clinic_id === current?.clinicId);
          if (!facility) {
            const first = data.facilities[0];
            return first ? { clinicId: first.clinic_id, departmentId: '', revision: first.scope.grant_revision } : null;
          }
          const departmentId = facility.departments.some(item => item.id === current?.departmentId) ? current!.departmentId : '';
          if (current?.revision === facility.scope.grant_revision && departmentId === current.departmentId) return current;
          return { clinicId: facility.clinic_id, departmentId, revision: facility.scope.grant_revision };
        });
      } catch {
        if (alive) { setFacilities([]); setSelection(null); setDirectoryError('Hospital directory unavailable.'); }
      } finally { loading = false; }
    }
    void load();
    const timer = setInterval(() => void load(), 5000);
    return () => { alive = false; controller.abort(); clearInterval(timer); };
  }, []);
  const verifiedScope = facilities.find(item => item.clinic_id === selection?.clinicId)?.scope;
  const canReport = verifiedScope?.capabilities.includes('report.read') ?? false;
  const canReview = verifiedScope?.capabilities.includes('review.read') ?? false;
  const key = JSON.stringify([selection, start, end, currency, refresh, verifiedScope?.capabilities]);
  useEffect(() => {
    if (!selection) return;
    let alive = true;
    let loading = false;
    const controller = new AbortController();
    async function load() {
      if (loading || !selection) return;
      loading = true;
      try {
        const query = new URLSearchParams({ clinic_id: selection.clinicId });
        if (selection.departmentId) query.set('department_id', selection.departmentId);
        setView(previous => previous?.key === key ? previous : { key, reportLoading: canReport, queueLoading: canReview,
          reportError: canReport ? undefined : 'Financial report unavailable under current permissions or source state.',
          queueError: canReview ? undefined : 'Review queue unavailable under current permissions.' });
        const reportTask = canReport ? fetchWithAuth('/api/v1/hospital/snapshots', { method: 'POST', signal: controller.signal,
          headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ clinic_id: selection.clinicId,
            department_id: selection.departmentId || null, currency, start: `${start}T00:00:00+04:00`, end: `${end}T00:00:00+04:00` }) })
          .then(response => response.json() as Promise<HospitalSnapshot>)
          .then(snapshot => {
            if (snapshot.scope.grant_revision !== selection.revision) throw new Error('Scope changed');
            if (alive) setView(previous => ({ ...previous, key, snapshot, reportLoading: false, reportError: undefined }));
          }).catch(() => {
            if (alive) setView(previous => ({ ...previous, key, snapshot: undefined, reportLoading: false,
              reportError: 'Financial report unavailable under current permissions or source state.' }));
          }) : Promise.resolve();
        const queueTask = canReview ? fetchWithAuth(`/api/v1/hospital/queue?${query}`, { signal: controller.signal })
          .then(response => response.json() as Promise<{ items: HospitalQueueItem[]; unavailable_reason: string;
            scope: { grant_revision: number } }>)
          .then(queue => {
            if (queue.scope.grant_revision !== selection.revision) throw new Error('Scope changed');
            if (alive) setView(previous => ({ ...previous, key, queue: queue.items, reason: queue.unavailable_reason,
              queueLoading: false, queueError: undefined }));
          }).catch(() => {
            if (alive) setView(previous => ({ ...previous, key, queue: undefined, queueLoading: false,
              queueError: 'Review queue unavailable under current permissions.' }));
          }) : Promise.resolve();
        await Promise.allSettled([reportTask, queueTask]);
      } catch {
        if (alive) setView({ key, error: 'Hospital results unavailable. Check current access and source readiness.' });
      } finally { loading = false; }
    }
    void load();
    const timer = setInterval(() => void load(), 5000);
    return () => { alive = false; controller.abort(); clearInterval(timer); };
  }, [key, selection, currency, start, end, canReport, canReview]);
  const current = view?.key === key && selection ? view : null;
  async function assign(item: HospitalQueueItem, assignee: string, reason: string) {
    if (!selection) throw new Error('Select a facility');
    await fetchWithAuth(`/api/v1/hospital/assignments/${encodeURIComponent(item.recommendation_id)}`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({
        clinic_id: selection.clinicId, department_id: selection.departmentId || null,
        expected_version: item.row_version, assignee: assignee || null, reason,
      }),
    });
    setRefresh(value => value + 1);
  }
  async function download() {
    if (!current?.snapshot) return;
    try {
      setExportError('');
      const response = await fetchWithAuth(`/api/v1/hospital/snapshots/${encodeURIComponent(current.snapshot.snapshot_id)}/csv`);
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement('a'); link.href = url; link.download = 'hospital-report.csv'; link.click(); URL.revokeObjectURL(url);
    } catch { setExportError('Export unavailable under current permissions.'); }
  }
  return <main className="space-y-6 p-6">
    <h1 className="text-2xl font-semibold">Hospital workspace</h1>
    {directoryError && <p role="alert">{directoryError}</p>}
    {!facilities.length && !directoryError && <p>No authorized hospital facilities.</p>}
    <HospitalScopeSelector facilities={facilities} selection={selection} onChange={setSelection} />
    <div className="flex flex-wrap gap-4">
      <label>From <input aria-label="Hospital period start" type="date" value={start} onChange={event => setStart(event.target.value)} /></label>
      <label>Until (exclusive) <input aria-label="Hospital period end" type="date" value={end} onChange={event => setEnd(event.target.value)} /></label>
      <label>Currency <input aria-label="Hospital currency" maxLength={3} value={currency} onChange={event => setCurrency(event.target.value.toUpperCase())} /></label>
    </div>
    <p>Business timezone: Asia/Dubai</p>
    <p>Last accounted import batch: {facilities.find(item => item.clinic_id === selection?.clinicId)?.last_import_at ?? 'Unavailable'}</p>
    {selection && !current && <p role="status">Loading permitted results…</p>}
    {current?.error && <p role="alert">{current.error}</p>}
    {current?.reportLoading && <p role="status">Loading financial report...</p>}
    {current?.queueLoading && <p role="status">Loading review queue...</p>}
    {current?.reportError && <p role="alert">{current.reportError}</p>}
    {current?.queueError && <p role="alert">{current.queueError}</p>}
    {current?.snapshot && <><DepartmentSummary snapshot={current.snapshot}
      departments={facilities.find(item => item.clinic_id === selection?.clinicId)?.departments} />
      <button type="button" onClick={() => void download()} disabled={!current.snapshot.scope.capabilities.includes('report.export')}>Export this snapshot</button></>}
    {exportError && <p role="alert">{exportError}</p>}
    {current?.queue && <ReviewQueue key={key} items={current.queue} unavailableReason={current.reason}
      canAssign={verifiedScope?.capabilities.includes('assignment.write')} onAssign={assign} />}
  </main>;
}
