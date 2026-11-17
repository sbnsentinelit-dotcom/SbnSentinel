'use client';
import { useState } from 'react';
export interface HospitalQueueItem {
  recommendation_id: string; status: string; department_id: string | null;
  assignee: string | null; team: string | null; row_version: number;
}
export function ReviewQueue({ items, unavailableReason, canAssign = false, onAssign }: {
  items: HospitalQueueItem[]; unavailableReason?: string; canAssign?: boolean;
  onAssign?: (item: HospitalQueueItem, assignee: string, reason: string) => Promise<void>;
}) {
  const [selected, setSelected] = useState('');
  const [assignee, setAssignee] = useState('');
  const [reason, setReason] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  async function save(item: HospitalQueueItem) {
    if (!onAssign || !reason.trim()) return;
    setBusy(true); setError('');
    try { await onAssign(item, assignee, reason); setSelected(''); }
    catch { setError('Assignment could not be saved. Refresh the queue and check permissions.'); }
    finally { setBusy(false); }
  }
  return <section aria-label="Hospital review queue" className="space-y-3">
    <h2 className="text-lg font-semibold">Review queue</h2>
    <p>Assignment does not grant approval authority.</p>
    {!items.length && <p>No assigned recommendations in the permitted scope.</p>}
    {unavailableReason && <p>{unavailableReason}</p>}
    <ul>{items.map(item => <li key={item.recommendation_id} className="py-2">
      <span>{item.recommendation_id} · {item.status}</span>
      <p>{item.assignee ?? 'Unassigned'}{item.team ? ` · ${item.team}` : ''}</p>
      {canAssign && onAssign && <button type="button" onClick={() => { setSelected(item.recommendation_id); setAssignee(item.assignee ?? ''); setReason(''); }}>Assign</button>}
      {selected === item.recommendation_id && <div>
        <label>Assignee <input aria-label="Assignment assignee" value={assignee} onChange={event => setAssignee(event.target.value)} /></label>
        <label>Reason <input aria-label="Assignment reason" value={reason} onChange={event => setReason(event.target.value)} /></label>
        <button type="button" disabled={busy || !reason.trim()} onClick={() => void save(item)}>Save assignment</button>
      </div>}
    </li>)}</ul>
    {error && <p role="alert">{error}</p>}
  </section>;
}
