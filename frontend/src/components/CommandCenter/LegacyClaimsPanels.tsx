import React, { useCallback, useEffect, useState } from 'react';
import { createPortal } from 'react-dom';
import { ClipboardCheck, FileSearch, History, X } from 'lucide-react';
import { fetchWithAuth } from '@/utils/fetchWithAuth';
import {
  Assessment, Assignee, Comparison, EVENT_LABELS, EvidenceRecord, OUTCOME_LABELS, FigureEvidence, ROLE_LABELS, WorkItemDetail,
  errorText, fieldText, figureLabel, label, shortDate, statusLabel, statusTone,
} from '@/utils/legacyClaims';

const API = '/api/v1/legacy-claims';

const call = async (url: string, init?: RequestInit) => {
  const res = await fetchWithAuth(url, init);
  const body = await res.json().catch(() => null);
  if (!res.ok) throw new Error(errorText(body, `Request failed (${res.status})`));
  return body;
};
const post = (url: string, body: unknown = {}) =>
  call(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });

const Tone = ({ status }: { status: string | null | undefined }) => {
  const tone = statusTone(status);
  const cls = tone === 'good' ? 'text-emerald-400 bg-emerald-500/20' : tone === 'bad' ? 'text-red-400 bg-red-500/20' : 'text-white/60 bg-white/10';
  return <span className={`text-[11px] font-bold px-2 py-1 rounded-[6px] ${cls}`}>{statusLabel(status)}</span>;
};

/** Right-hand panel over the screen; Escape or the close button closes it. */
export const Drawer: React.FC<{ title: string; icon: React.ReactNode; onClose: () => void; children: React.ReactNode }> = ({ title, icon, onClose, children }) => {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);
  // Rendered at the end of <body>, above the dashboard header and sidebar.
  return createPortal(
    <div className="fixed inset-0 z-[1000] flex justify-end" role="dialog" aria-label={title}>
      <button aria-label="Close panel" className="absolute inset-0 bg-black/50 cursor-default" onClick={onClose} />
      <div className="relative w-full max-w-[720px] h-full overflow-y-auto custom-scrollbar bg-[var(--color-surface)] border-l border-white/10 p-6 text-white space-y-5">
        <div className="flex items-start justify-between gap-4">
          <h3 className="text-lg font-bold flex items-center gap-2">{icon} {title}</h3>
          <button aria-label="Close" onClick={onClose} className="p-2 rounded-[10px] hover:bg-white/10"><X className="w-4 h-4" /></button>
        </div>
        {children}
      </div>
    </div>,
    document.body,
  );
};

const Records = ({ records, empty, money }: { records: EvidenceRecord[]; empty: string; money?: (minor: number) => string }) =>
  records.length === 0 ? <p className="text-xs text-white/50">{empty}</p> : (
    <ul className="space-y-2">
      {records.map((r) => (
        <li key={`${r.kind}-${r.key}-${r.source}`} className="border border-white/10 rounded-[12px] p-3 text-xs space-y-1">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <span className="font-bold">{r.key} <span className="text-white/50 font-normal">· {r.kind.replace(/_/g, ' ')} · {label(ROLE_LABELS, r.source ?? '')}</span></span>
            {r.status && <span className="text-[11px] px-2 py-0.5 rounded-[6px] bg-white/10 text-white/80">{r.status}</span>}
          </div>
          <p className="text-white/70 break-words">{fieldText(r.fields, money)}</p>
          <p className="text-[11px] text-white/50">
            Event {shortDate(r.effective)} · received {shortDate(r.received_at)}{r.received_by ? ` by ${r.received_by}` : ''}
            {r.page_token ? ` · file ${r.page_token.slice(0, 24)}` : ''}{r.custody_evidence_id ? ` · custody ${r.custody_evidence_id.slice(0, 16)}…` : ''}
          </p>
        </li>
      ))}
    </ul>
  );

/** The stored records behind one figure. */
export const EvidencePanel: React.FC<{
  transitionId: string; figure: string; assessmentId?: string | null; money: (minor: number | null | undefined) => string; onClose: () => void;
}> = ({ transitionId, figure, assessmentId, money, onClose }) => {
  const [data, setData] = useState<FigureEvidence | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    setData(null);
    setError(null);
    const q = new URLSearchParams({ figure, ...(assessmentId ? { assessment_id: assessmentId } : {}) });
    call(`${API}/transitions/${transitionId}/evidence?${q}`)
      .then((d) => { if (live) setData(d); })
      .catch((e) => { if (live) setError(e.message); });
    return () => { live = false; };
  }, [transitionId, figure, assessmentId]);
  const title = figure.startsWith('issue:') ? (data?.label ?? 'Hold reason') : figureLabel(figure);
  return (
    <Drawer title={title} icon={<FileSearch className="w-5 h-5" />} onClose={onClose}>
      {error && <p className="text-xs text-red-300">{error}</p>}
      {!data && !error && <p className="text-xs text-white/50">Loading the stored evidence…</p>}
      {data && (
        <>
          <div className="text-xs text-white/70 space-y-1">
            {data.total !== null && <p>Total from these records: <b className="text-white">{money(data.total)}</b></p>}
            <p>Assessment {data.assessment_id} · evidence as of {shortDate(data.as_of)}</p>
            {data.rule && <p className="text-white/50">{data.rule}</p>}
          </div>
          {data.unavailable && <p className="text-xs text-amber-300">{data.unavailable}</p>}
          <Records records={data.records} money={(m) => money(m)} empty={data.unavailable ? 'The records behind this assessment cannot be shown.' : 'No record contributes to this figure in this assessment.'} />
          {data.seals && (
            <div className="text-xs">
              <p className="font-bold mb-1">Completeness statements</p>
              <ul className="space-y-1 text-white/70">
                {data.seals.map((s) => (
                  <li key={`${s.receipt}-${s.through}`}>{s.unique_rows} rows through {shortDate(s.through)} · received {shortDate(s.received_at)}{s.applies ? ' · used by this assessment' : ''}{s.superseded ? ' · replaced by a later statement' : ''}</li>
                ))}
              </ul>
            </div>
          )}
        </>
      )}
    </Drawer>
  );
};

/** A task: its evidence, timeline and the actions a reviewer can take. */
export const WorkPanel: React.FC<{
  itemId: number; transitionId: string; canDeliver: boolean; canReleaseOthers: boolean; me: string | null;
  money: (minor: number | null | undefined) => string; onChanged: () => void; onClose: () => void;
}> = ({ itemId, transitionId, canDeliver, canReleaseOthers, me, money, onChanged, onClose }) => {
  const [item, setItem] = useState<WorkItemDetail | null>(null);
  const [people, setPeople] = useState<Assignee[]>([]);
  const [assignee, setAssignee] = useState('');
  const [note, setNote] = useState('');
  const [outcome, setOutcome] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      setItem(await call(`${API}/work-items/${itemId}`));
    } catch (e: any) {
      setError(e.message);
    }
  }, [itemId]);

  useEffect(() => {
    load();
    if (canDeliver) call(`${API}/transitions/${transitionId}/assignees`).then(setPeople).catch(() => setPeople([]));
  }, [load, canDeliver, transitionId]);

  const act = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    setError(null);
    try {
      await fn();
      await load();
      onChanged();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  const base = `${API}/work-items/${itemId}`;
  const mayRelease = item?.state === 'ACCEPTED' && (item.accepted_by === me || canReleaseOthers);
  return (
    <Drawer title={item?.message ?? 'Work item'} icon={<ClipboardCheck className="w-5 h-5 text-orange-400" />} onClose={onClose}>
      {error && <p className="text-xs text-red-300">{error}</p>}
      {!item && !error && <p className="text-xs text-white/50">Loading…</p>}
      {item && (
        <>
          <div className="text-xs text-white/70 space-y-1">
            <p>{item.priority === 'high' && <span className="mr-2 px-2 py-0.5 rounded-[6px] bg-red-500/25 font-bold text-red-300">Priority</span>}<span className="px-2 py-0.5 rounded-[6px] bg-white/10 font-bold text-white">{item.state}</span> · {item.kind === 'hold' ? 'Investigation for hold reasons' : 'Continuing work'} · {item.item}</p>
            <p>Responsible team: <b className="text-white">{item.owner ?? 'not assigned'}</b>{item.assigned_to ? <> · Assigned to <b className="text-white">{item.assigned_to}</b></> : null}</p>
            {item.state === 'ACCEPTED' && <p>Accepted by {item.accepted_by} on {shortDate(item.accepted_at)}</p>}
            {item.state === 'RESOLVED' && <p>Resolved by later evidence on {shortDate(item.resolved_at)}</p>}
            {item.state === 'CLOSED' && (
              <p className="text-emerald-300">Outcome: <b>{OUTCOME_LABELS[item.outcome ?? ''] ?? item.outcome}</b> · {item.outcome_by} · {shortDate(item.outcome_at)}
                {item.outcome_note ? <span className="block text-white/60 break-words">{item.outcome_note}</span> : null}</p>
            )}
            {item.state !== 'CLOSED' && (item.outcome_amount ?? 0) !== 0 && (
              <p className="text-amber-300">Partial outcome so far: <b>{money(item.outcome_amount)}</b> proven; the claim line still has a balance, so the task stays open.</p>
            )}
            {(item.outcome_proof ?? []).length > 0 && (
              <p className="text-white/60 break-words">Proof{item.outcome_assessment_id ? ` (assessment ${item.outcome_assessment_id})` : ''}: {(item.outcome_proof ?? []).map((p) => `${Object.values(p.refs).join(' → ')} ${money(p.amount)}`).join('; ')}</p>
            )}
          </div>

          {item.issues.length > 0 && (
            <div className="text-xs">
              <p className="font-bold mb-1">Hold reasons in this investigation</p>
              <ul className="space-y-1 text-white/70">{item.issues.map((i) => <li key={`${i.code}-${i.item}`}>• {i.message} ({label(ROLE_LABELS, i.item)})</li>)}</ul>
            </div>
          )}

          {canDeliver && item.state !== 'RESOLVED' && item.state !== 'CLOSED' && (
            <div className="space-y-3 border border-white/10 rounded-[12px] p-3">
              <div className="flex flex-wrap gap-2">
                {item.state === 'OPEN' && (
                  <button disabled={busy} onClick={() => act(() => post(`${base}/accept`))} className="text-[11px] font-bold px-3 py-1.5 rounded-[8px] bg-white/10 hover:bg-white/20 disabled:opacity-50">Accept ownership</button>
                )}
                {mayRelease && (
                  <button disabled={busy} onClick={() => act(() => post(`${base}/release`, { note: note || null }))} className="text-[11px] font-bold px-3 py-1.5 rounded-[8px] bg-white/10 hover:bg-white/20 disabled:opacity-50">Release ownership</button>
                )}
                <button disabled={busy} onClick={() => act(() => post(`${API}/transitions/${transitionId}/assessments`))} className="text-[11px] font-bold px-3 py-1.5 rounded-[8px] bg-white/10 hover:bg-white/20 disabled:opacity-50">Re-check with latest evidence</button>
              </div>
              <div className="flex flex-col md:flex-row gap-2">
                <select aria-label="Assign to" value={assignee} onChange={(e) => setAssignee(e.target.value)} className="bg-white/5 border border-white/10 text-white text-xs px-3 py-2 rounded-[10px] md:w-1/2">
                  <option value="" className="bg-[var(--color-surface)]">Assign to a team or person</option>
                  {people.map((p) => <option key={p.value} value={p.value} className="bg-[var(--color-surface)]">{p.kind === 'team' ? `Team · ${p.label}` : p.label}</option>)}
                </select>
                <button disabled={busy || !assignee} onClick={() => act(async () => { await post(`${base}/assign`, { assignee, note: note || null }); setAssignee(''); setNote(''); })} className="text-[11px] font-bold px-3 py-1.5 rounded-[8px] bg-white/10 hover:bg-white/20 disabled:opacity-50">Assign</button>
              </div>
              <div className="flex flex-col md:flex-row gap-2">
                <textarea aria-label="Note" value={note} onChange={(e) => setNote(e.target.value)} maxLength={2000} rows={2} placeholder="Add a note for the timeline" className="flex-1 bg-white/5 border border-white/10 text-white text-xs px-3 py-2 rounded-[10px]" />
                <button disabled={busy || !note.trim()} onClick={() => act(async () => { await post(`${base}/notes`, { note }); setNote(''); })} className="text-[11px] font-bold px-3 py-1.5 rounded-[8px] bg-white/10 hover:bg-white/20 disabled:opacity-50 self-start">Add note</button>
              </div>
              {(item.allowed_outcomes ?? []).length > 0 && (
                <div className="flex flex-col md:flex-row gap-2 md:items-center">
                  <select aria-label="Outcome" value={outcome} onChange={(e) => setOutcome(e.target.value)} className="bg-white/5 border border-white/10 text-white text-xs px-3 py-2 rounded-[10px] md:w-1/2">
                    <option value="" className="bg-[var(--color-surface)]">Record an outcome</option>
                    {(item.allowed_outcomes ?? []).map((o) => <option key={o} value={o} className="bg-[var(--color-surface)]">{OUTCOME_LABELS[o] ?? o}</option>)}
                  </select>
                  <button disabled={busy || !outcome || (outcome === 'risk_accepted' && !note.trim())} onClick={() => act(async () => { await post(`${base}/outcome`, { outcome, note: note || null }); setOutcome(''); setNote(''); })} className="text-[11px] font-bold px-3 py-1.5 rounded-[8px] bg-white/10 hover:bg-white/20 disabled:opacity-50 whitespace-nowrap">Close with outcome</button>
                </div>
              )}
              <p className="text-[11px] text-white/50">
                The task closes by itself when later evidence shows the discrepancy is gone. Collected and written off need ledger evidence after the payer&apos;s decision; only the amount the reconciliation proves is applied, and the task closes when the claim line&apos;s payer balance is zero. An accepted risk needs an administrator and a reason (use the note) and covers only the discrepancies it names.
              </p>
            </div>
          )}

          <div>
            <p className="text-xs font-bold mb-2">Records behind this task</p>
            <Records records={item.evidence} money={(m) => money(m)} empty="No record in the current assessment names this task." />
          </div>

          <div>
            <p className="text-xs font-bold mb-2">Timeline</p>
            <ol className="space-y-2 text-xs">
              {item.history.map((e, n) => (
                <li key={n} className="border-l-2 border-white/20 pl-3">
                  <p className="font-bold">{EVENT_LABELS[e.event] ?? e.event} <span className="font-normal text-white/50">· {shortDate(e.at)} · {e.actor}</span></p>
                  {e.note && <p className="text-white/70 break-words">{e.note}</p>}
                </li>
              ))}
            </ol>
          </div>
        </>
      )}
    </Drawer>
  );
};

/** An earlier assessment, read only, and what changed up to the current one. */
export const AssessmentPanel: React.FC<{
  transitionId: string; assessmentId: string; money: (minor: number | null | undefined) => string;
  onEvidence: (figure: string, assessmentId: string) => void; onClose: () => void;
}> = ({ transitionId, assessmentId, money, onEvidence, onClose }) => {
  const [a, setA] = useState<Assessment | null>(null);
  const [diff, setDiff] = useState<Comparison | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    setA(null);
    setDiff(null);
    Promise.all([
      call(`${API}/transitions/${transitionId}/assessments/${assessmentId}`),
      call(`${API}/transitions/${transitionId}/assessments/${assessmentId}/compare`),
    ]).then(([x, d]) => { if (live) { setA(x); setDiff(d); } })
      .catch((e) => { if (live) setError(e.message); });
    return () => { live = false; };
  }, [transitionId, assessmentId]);
  const value = (figure: string, v: number | null) =>
    v === null ? 'unknown' : figure.startsWith('routes') || figure.startsWith('denials') || figure === 'cash:noncash_advices' ? String(v) : money(v);
  return (
    <Drawer title={a?.is_current ? 'Current assessment' : 'Earlier assessment'} icon={<History className="w-5 h-5" />} onClose={onClose}>
      {error && <p className="text-xs text-red-300">{error}</p>}
      {!a && !error && <p className="text-xs text-white/50">Loading…</p>}
      {a && (
        <>
          <div className="flex flex-wrap items-center gap-2 text-xs text-white/70">
            <Tone status={a.status} />
            <span>Evidence as of {shortDate(a.as_of)} · revision {a.revision} · {a.record_count} records · computed {shortDate(a.computed_at)}</span>
          </div>
          {!a.is_current && <p className="text-[11px] text-orange-300">Read only. Work items follow the current assessment.</p>}
          {a.rules_current === false && (
            <p className="text-[11px] text-amber-300">Computed under earlier rules ({a.policy_version}). Its figures are shown as recorded; its record-level detail cannot be rebuilt. The assessment under the current rules replaced it.</p>
          )}
          {a.supersedes_id && <p className="text-[11px] text-white/50">Replaces assessment {a.supersedes_id}.</p>}
          <div className="text-xs">
            <p className="font-bold mb-1">Hold reasons ({a.issues.length})</p>
            {a.issues.length === 0 ? <p className="text-white/50">None.</p> : (
              <ul className="space-y-1">
                {a.issues.map((i) => (
                  <li key={`${i.code}-${i.item}`}>
                    <button className="text-left hover:underline text-white/80" onClick={() => onEvidence(`issue:${i.code}:${i.item}`, a.assessment_id as string)}>• {i.message} ({label(ROLE_LABELS, i.item)})</button>
                  </li>
                ))}
              </ul>
            )}
          </div>
          {diff && diff.from.assessment_id !== diff.to.assessment_id && (
            <div className="text-xs space-y-2 border border-white/10 rounded-[12px] p-3">
              <p className="font-bold">Changes up to the current assessment ({statusLabel(diff.from.status)} → {statusLabel(diff.to.status)})</p>
              {diff.issues_removed.map((i) => <p key={`r-${i.code}-${i.item}`} className="text-emerald-300">Cleared: {i.message} ({i.item})</p>)}
              {diff.issues_added.map((i) => <p key={`a-${i.code}-${i.item}`} className="text-red-300">New: {i.message} ({i.item})</p>)}
              {diff.figures_changed.length > 0 && (
                <table className="w-full">
                  <thead><tr className="text-white/50 text-left"><th className="py-1">Figure</th><th>Then</th><th>Now</th></tr></thead>
                  <tbody>
                    {diff.figures_changed.map((f) => (
                      <tr key={f.figure} className="border-t border-white/10"><td className="py-1">{figureLabel(f.figure)}</td><td>{value(f.figure, f.from)}</td><td>{value(f.figure, f.to)}</td></tr>
                    ))}
                  </tbody>
                </table>
              )}
              {diff.issues_added.length + diff.issues_removed.length + diff.figures_changed.length === 0 && <p className="text-white/50">No change in hold reasons or figures.</p>}
            </div>
          )}
        </>
      )}
    </Drawer>
  );
};
