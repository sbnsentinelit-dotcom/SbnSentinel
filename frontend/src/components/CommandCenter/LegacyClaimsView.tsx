import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
  AlertCircle, Archive, CheckCircle2, ClipboardCheck, FileUp, Info, Landmark, Plus, RefreshCw, Route, Settings2,
  ShieldCheck, Wallet,
} from 'lucide-react';
import { fetchWithAuth } from '@/utils/fetchWithAuth';
import { AssessmentPanel, Drawer, EvidencePanel, WorkPanel } from './LegacyClaimsPanels';
import {
  CsvUpload, ExitPanel, FilePicker, InventoryPanel, NewTransitionPanel, Signoffs, SourcesPanel, button as actionButton, missingHint,
} from './LegacyClaimsSetup';
import { formatMoney } from '@/utils/currency';
import {
  APPEAL_LABELS, Assessment, BUCKET_LABELS, AssessmentRow, ExitReview, InventoryRow, ROLE_LABELS, ROUTE_LABELS, Source, Transition,
  OUTCOME_LABELS, Pulse, ageLabel, balanceRows, disputedRows, errorText, isUnverified, samePulse, fromMinor, label, measured, openWork, parseExport, previousAcceptance, shortDate,
  statusLabel, statusTone,
} from '@/utils/legacyClaims';

const API = '/api/v1/legacy-claims';

const card = 'bg-gradient-to-br from-[var(--color-surface-raised)] to-[var(--color-surface)] border border-white/10 rounded-[24px] p-6 shadow-[0_20px_50px_rgba(46,16,85,0.3)] text-white';

const Badge = ({ status }: { status: string | null | undefined }) => {
  const tone = statusTone(status);
  const cls = tone === 'good' ? 'text-emerald-400 bg-emerald-500/20' : tone === 'bad' ? 'text-red-400 bg-red-500/20' : 'text-white/60 bg-white/10';
  return <span className={`text-[11px] font-bold px-2 py-1 rounded-[6px] ${cls}`}>{statusLabel(status)}</span>;
};

const Empty = ({ text }: { text: string }) => <p className="text-xs text-white/50 py-4">{text}</p>;

export const LegacyClaimsView: React.FC = () => {
  const [perm, setPerm] = useState<{ can_deliver: boolean; can_configure: boolean; stage: string; me?: string | null } | null>(null);
  type Panel = { type: 'evidence'; figure: string; assessmentId?: string | null } | { type: 'work'; id: number } | { type: 'assessment'; id: string }
    | { type: 'new-transition' } | { type: 'sources' } | { type: 'inventory' } | { type: 'details' } | { type: 'exit' };
  const [panel, setPanel] = useState<Panel | null>(null);
  const [checkedAt, setCheckedAt] = useState<string | null>(null);
  const lastPulse = useRef<Pulse | null>(null);
  const [transitions, setTransitions] = useState<Transition[]>([]);
  const [selected, setSelected] = useState<string>('');
  const [assessment, setAssessment] = useState<Assessment | null>(null);
  const [history, setHistory] = useState<AssessmentRow[]>([]);
  const [sources, setSources] = useState<Source[]>([]);
  const [exitReview, setExitReview] = useState<ExitReview | null>(null);
  const [inventories, setInventories] = useState<InventoryRow[]>([]);
  const [inventoryId, setInventoryId] = useState<string>('');
  const inventoryIdRef = useRef('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [uploadSource, setUploadSource] = useState<number | ''>('');
  const [uploadFile, setUploadFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  // What the reviewer has chosen, as of now: callbacks and late responses read these refs, never a
  // value captured when a request started. Each panel family has its own request counter, so a
  // response is applied only if it is the latest of its kind AND still belongs to the chosen
  // transition (and inventory). Background work (upload, accept, reassess) never changes the selection.
  const selectedRef = useRef('');
  const transitionSeq = useRef(0);
  const inventorySeq = useRef(0);

  const getJson = async (url: string) => {
    const res = await fetchWithAuth(url);
    const body = await res.json().catch(() => null);
    if (!res.ok) throw new Error(errorText(body, `Request failed (${res.status})`));
    return body;
  };

  const clearReview = () => {
    inventorySeq.current += 1;          // any review still in flight is now superseded
    inventoryIdRef.current = '';
    setInventoryId('');
    setExitReview(null);
  };

  /** Load one inventory's review for transition `id`; applied only if both are still chosen. */
  const loadReview = async (id: string, inventory: string) => {
    const seq = ++inventorySeq.current;
    inventoryIdRef.current = inventory;
    setInventoryId(inventory);
    setExitReview(null);
    try {
      const review = await getJson(`${API}/exit-inventories/${inventory}/review`);
      if (seq !== inventorySeq.current || selectedRef.current !== id || inventoryIdRef.current !== inventory) return;
      setExitReview(review);
    } catch (e: any) {
      if (seq === inventorySeq.current && selectedRef.current === id) setError(e.message);
    }
  };

  /** Re-read the panels of transition `id`. Does nothing unless `id` is the chosen transition, and
   * drops its answer if the reviewer moved on or a newer refresh started meanwhile. */
  const refresh = useCallback(async (id: string) => {
    if (!id || id !== selectedRef.current) return;
    const seq = ++transitionSeq.current;
    const current = () => seq === transitionSeq.current && selectedRef.current === id;
    try {
      // The pulse is read BEFORE the panels, so it is never newer than what the screen shows: a change
      // that lands while the panels load is seen by the next live check.
      const p: Pulse = await getJson(`${API}/transitions/${id}/pulse`);
      const [a, h, s, inv] = await Promise.all([
        getJson(`${API}/transitions/${id}/assessment`),
        getJson(`${API}/transitions/${id}/assessments`),
        getJson(`${API}/transitions/${id}/sources`),
        getJson(`${API}/exit-inventories?transition_id=${encodeURIComponent(id)}`),
      ]);
      if (!current()) return;
      lastPulse.current = p;
      setCheckedAt(new Date().toISOString());
      setAssessment(a);
      setHistory(h);
      setSources(s);
      setInventories(inv);
      // Keep the reviewer's inventory if it still qualifies; one qualifying inventory is reviewed
      // directly; with several the reviewer chooses.
      const keep = (inv as InventoryRow[]).find((x) => x.inventory_id === inventoryIdRef.current);
      const pick = keep ?? (inv.length === 1 ? inv[0] : null);
      if (pick) await loadReview(id, pick.inventory_id);
      else clearReview();
    } catch (e: any) {
      if (!current()) return;
      setError(e.message);
      setAssessment(null);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  /** The reviewer chose transition `id` (or none): clear every panel, then load the new one. */
  const showTransition = useCallback(async (id: string) => {
    selectedRef.current = id;
    transitionSeq.current += 1;          // answers for the previous choice are now superseded
    lastPulse.current = null;
    setPanel(null);
    setSelected(id);
    setError(null);
    setAssessment(null);
    setHistory([]);
    setSources([]);
    setInventories([]);
    clearReview();
    setUploadSource('');
    setUploadFile(null);
    await refresh(id);
  }, [refresh]);

  const loadAll = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const status = await getJson(`${API}/status`);
      setPerm(status);
      if (!status.can_view) {
        setError('Your role cannot view legacy claims transitions.');
        return;
      }
      const list: Transition[] = await getJson(`${API}/transitions`);
      setTransitions(list);
      if (list.some((t) => t.id === selectedRef.current)) await refresh(selectedRef.current);
      else await showTransition(list[0]?.id ?? '');
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, [refresh, showTransition]);

  useEffect(() => { loadAll(); }, [loadAll]);

  // Live: every 30 seconds ask the server for the transition's pulse (current assessment, last delivery,
  // last work change). The panels are re-read only when something changed.
  useEffect(() => {
    let stopped = false;
    const check = async () => {
      const id = selectedRef.current;
      if (!id || document.hidden) return;
      try {
        const p: Pulse = await getJson(`${API}/transitions/${id}/pulse`);
        if (stopped || selectedRef.current !== id) return;
        setCheckedAt(new Date().toISOString());
        // Compare with the pulse the shown panels were read under; refresh() records the new one.
        if (!samePulse(lastPulse.current, p)) await refresh(id);
      } catch {
        // the next check tries again
      }
    };
    const timer = setInterval(check, 30000);
    return () => { stopped = true; clearInterval(timer); };
  }, [selected, refresh]);

  /** After an action in a panel: re-read the screen now instead of waiting for the next check. */
  const changedNow = () => { refresh(selectedRef.current); };

  const open = (figure: string, assessmentId?: string | null) => setPanel({ type: 'evidence', figure, assessmentId });
  const clickable = (fn: () => void) => ({
    role: 'button' as const, tabIndex: 0, onClick: fn,
    onKeyDown: (e: React.KeyboardEvent) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); fn(); } },
  });

  const selectTransition = (id: string) => showTransition(id);

  const chooseInventory = async (inventory: string) => {
    const id = selectedRef.current;
    if (!inventory) {
      clearReview();
      return;
    }
    await loadReview(id, inventory);
  };

  /** Publish an assessment of the evidence received so far for transition `id`. */
  const publish = async (id: string) => {
    const res = await fetchWithAuth(`${API}/transitions/${id}/assessments`, { method: 'POST' });
    const body = await res.json().catch(() => null);
    if (!res.ok) throw new Error(errorText(body, 'The assessment could not be published.'));
  };

  const reassess = async () => {
    const id = selectedRef.current;
    setError(null);
    try {
      if (id && perm?.can_deliver) await publish(id);
    } catch (e: any) {
      if (selectedRef.current === id) setError(e.message);
    }
    await loadAll();
  };

  const flash = (msg: string) => {
    setNotice(msg);
    setTimeout(() => setNotice(null), 5000);
  };

  const accept = async (itemId: number) => {
    const id = selectedRef.current;
    setBusy(true);
    try {
      const res = await fetchWithAuth(`${API}/work-items/${itemId}/accept`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({}),
      });
      const body = await res.json().catch(() => null);
      if (!res.ok) throw new Error(errorText(body, 'Could not accept the item.'));
      if (selectedRef.current === id) flash('Ownership accepted and recorded in the audit log.');
    } catch (e: any) {
      if (selectedRef.current === id) setError(e.message);
    } finally {
      setBusy(false);
    }
    await refresh(id);
  };

  const deliver = async () => {
    if (!uploadFile || uploadSource === '') return;
    // Capture the transition, source, its stream head and the file before any await.
    const id = selectedRef.current;
    const binding = uploadSource;
    const file = uploadFile;
    const previous = sources.find((s) => s.binding_id === binding)?.streams.find((st) => st.resource === 'export')?.head ?? null;
    setBusy(true);
    setError(null);
    try {
      const parsed = parseExport(await file.text());
      const res = await fetchWithAuth(`${API}/transitions/${id}/pages`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ binding_id: binding, resource: 'export', previous, ...parsed }),
      });
      const body = await res.json().catch(() => null);
      if (!res.ok) throw new Error(errorText(body, 'The file was refused.'));
      if (body.status === 'STAGED') await publish(id);
      if (selectedRef.current === id) {
        flash(body.status === 'ALREADY_STAGED'
          ? 'This file was already received; nothing changed.'
          : `Received ${body.records_added} new record(s)${body.seal_stored ? ' with a completeness statement' : ''}.`);
        setUploadFile(null);
      }
    } catch (e: any) {
      if (selectedRef.current === id) setError(e.message);
    } finally {
      setBusy(false);
    }
    await refresh(id);          // updates the panels only if that transition is still chosen
  };

  const s = assessment?.summary;
  const cur = s?.currency ?? assessment?.transition.scope.currency ?? null;
  const money = (minor: number | null | undefined) => formatMoney(fromMinor(minor), cur);
  const work = assessment ? openWork(assessment.work_items) : [];
  const fmt = (minor: number) => money(minor);
  const bank = (minor: number | null | undefined) =>
    measured(minor, s?.cash.bank_coverage ?? 'missing', 'Bank evidence missing', fmt);
  const ledger = (minor: number | null | undefined) =>
    measured(minor, s?.cash.ledger_coverage ?? 'missing', 'Ledger evidence missing', fmt);

  return (
    <div className="animate-in fade-in duration-500 max-w-[1600px] mx-auto space-y-8 relative">
      <div className="flex flex-col 2xl:flex-row 2xl:items-end justify-between gap-4">
        <div className="2xl:max-w-[560px] min-w-0">
          <h2 className="text-3xl font-extrabold text-white mb-1">Legacy Claims</h2>
          <p className="text-sm text-white/70 font-medium">
            Can the clinic end its old billing subscription while payers still owe it money? Every figure below is assessed from the evidence each source delivered.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-3 min-w-0 2xl:justify-end">
          {transitions.length > 0 && (
            <select
              aria-label="Transition"
              value={selected}
              onChange={(e) => selectTransition(e.target.value)}
              className="max-w-full min-w-0 bg-white/5 border border-white/10 text-white font-bold text-xs px-3 py-2.5 rounded-[16px] premium-shadow cursor-pointer focus:outline-none hover:bg-white/10"
            >
              {transitions.map((t) => (
                <option key={t.id} value={t.id} className="bg-[var(--color-surface)] text-white">
                  {(t.label || t.id)} · {t.scope.payer}
                </option>
              ))}
            </select>
          )}
          {perm?.can_configure && (
            <button onClick={() => setPanel({ type: 'new-transition' })} title="Open a new payer transition off the old billing platform" className="flex items-center gap-2 whitespace-nowrap text-white font-bold text-xs px-4 py-2.5 rounded-[16px] premium-shadow bg-white/5 border border-white/10 hover:bg-white/10"><Plus className="w-4 h-4" /> New transition</button>
          )}
          {selected && (
            <button onClick={() => setPanel({ type: 'sources' })} title="Evidence sources and the team responsible for each kind of work" className="flex items-center gap-2 whitespace-nowrap text-white font-bold text-xs px-4 py-2.5 rounded-[16px] premium-shadow bg-white/5 border border-white/10 hover:bg-white/10"><Settings2 className="w-4 h-4" /> Sources &amp; teams</button>
          )}
          <button
            onClick={() => reassess()}
            disabled={loading}
            title="Publish an assessment of the evidence received so far"
            className="flex items-center gap-2 whitespace-nowrap text-white font-bold text-xs px-4 py-2.5 rounded-[16px] premium-shadow bg-[#111827] hover:bg-[#1F2937] disabled:opacity-50"
          >
            <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} /> Reassess
          </button>
        </div>
      </div>

      {notice && (
        <div className="flex items-center gap-2 text-xs font-bold text-emerald-300 bg-emerald-500/10 border border-emerald-500/30 rounded-[12px] px-4 py-3">
          <CheckCircle2 className="w-4 h-4" /> {notice}
        </div>
      )}
      {error && (
        <div className="flex items-center gap-2 text-xs font-bold text-red-300 bg-red-500/10 border border-red-500/30 rounded-[12px] px-4 py-3">
          <AlertCircle className="w-4 h-4" /> {error}
        </div>
      )}

      {!loading && transitions.length === 0 && !error && (
        <div className={card}>
          <Empty text="No legacy claims transition has been opened for your clinic yet." />
          {perm?.can_configure && <button onClick={() => setPanel({ type: 'new-transition' })} className="text-xs font-bold px-4 py-2 rounded-[12px] bg-[#111827] hover:bg-[#1F2937]">Open a transition</button>}
        </div>
      )}

      {assessment && s && (
        <>
          <div className="flex flex-wrap items-center gap-3 text-[11px] text-white/70">
            <Badge status={assessment.status} />
            <span className="whitespace-nowrap">Evidence as of <b className="text-white">{shortDate(assessment.as_of)}</b> ({ageLabel(assessment.source_age_hours)} ago)</span>
            <span className="whitespace-nowrap">· Cutover {shortDate(assessment.transition.cutover)}</span>
            {checkedAt && <span className="flex items-center gap-1 whitespace-nowrap text-emerald-300"><span className="w-1.5 h-1.5 rounded-full bg-emerald-400" /> Live · checked {shortDate(checkedAt)}</span>}
            <button onClick={() => setPanel({ type: 'details' })} className="flex items-center gap-1 whitespace-nowrap px-2 py-0.5 rounded-[6px] bg-white/10 hover:bg-white/20 text-white/80"><Info className="w-3 h-3" /> Assessment details</button>
          </div>
          <p className="text-[11px] text-white/50 -mt-5">
            {assessment.authority}
            {!assessment.published && ' Not yet published: this is a preview of the evidence received so far; work items start when it is published.'}
          </p>

          <div className="grid grid-cols-1 md:grid-cols-4 gap-6">
            {[
              { figure: 'balance:opening:payer_ar', title: 'Opening payer receivable', value: money(s.opening.payer_ar), sub: `${s.lines.legacy} legacy claim line(s)`, icon: Archive, bg: 'bg-purple-500/20', color: 'text-white' },
              { figure: 'balance:closing:payer_ar', title: 'Closing payer receivable', value: isUnverified(s, 'closing', 'payer_ar') ? 'Unverified' : money(s.closing.payer_ar), sub: isUnverified(s, 'closing', 'payer_ar') ? `Undisputed subtotal ${money(s.closing.payer_ar)} · ${s.unverified?.closing?.payer_ar?.length ?? 0} disputed row(s)` : `Credits ${isUnverified(s, 'closing', 'credit') ? 'unverified' : money(s.closing.credit)}`, icon: Wallet, bg: 'bg-orange-500/20', color: 'text-orange-400' },
              { figure: 'cash:confirmed', title: 'Bank-confirmed payments', value: s.cash.bank_confirmed === null ? 'Bank evidence missing' : money(s.cash.bank_confirmed), sub: `of ${money(s.cash.advised)} advised · ${ledger(s.cash.ledger_posted)} posted${s.cash.bank_coverage === 'incomplete' ? ' · bank coverage not proven' : ''}`, icon: Landmark, bg: 'bg-emerald-500/20', color: 'text-emerald-400' },
              { figure: '', title: 'Open work items', value: String(work.length), sub: `${assessment.issues.length} hold reason(s)`, icon: ClipboardCheck, bg: 'bg-red-500/20', color: 'text-red-400' },
            ].map((c) => (
              <div key={c.title} aria-label={c.title} {...clickable(() => (c.figure ? open(c.figure) : document.getElementById('legacy-work')?.scrollIntoView({ behavior: 'smooth' })))} className={`${card} flex flex-col justify-between cursor-pointer hover:border-white/30 transition-colors`}>
                <div className={`p-3 ${c.bg} rounded-[16px] w-fit`}><c.icon className={`w-5 h-5 ${c.color}`} /></div>
                <div className="mt-6">
                  <p className="text-[11px] text-white/70 uppercase font-extrabold tracking-widest mb-1">{c.title}</p>
                  <p className="text-3xl font-black text-white">{c.value}</p>
                  <p className="text-[11px] text-white/60 mt-1">{c.sub}</p>
                  <p className="text-[11px] text-white/40 mt-2 underline">{c.figure ? 'View the records' : 'Go to the work'}</p>
                </div>
              </div>
            ))}
          </div>

          <div className="grid grid-cols-1 lg:grid-cols-2 gap-8">
            <div className={card}>
              <h3 className="text-base font-bold mb-4 flex items-center gap-2"><AlertCircle className="w-4 h-4 text-red-400" /> Hold reasons</h3>
              {assessment.issues.length === 0 ? <Empty text="No hold reasons. The evidence is complete and consistent for review." /> : (
                <ul className="space-y-3 max-h-[320px] overflow-y-auto custom-scrollbar pr-2">
                  {assessment.issues.map((i) => (
                    <li key={`${i.code}-${i.item}`} {...clickable(() => open(`issue:${i.code}:${i.item}`))} className="border border-white/10 rounded-[12px] p-3 cursor-pointer hover:bg-white/5">
                      <p className="text-xs font-bold text-white">{i.message}</p>
                      <p className="text-[11px] text-white/50 mt-1">{label(ROLE_LABELS, i.item)}</p>
                    </li>
                  ))}
                </ul>
              )}
            </div>

            <div className={card} id="legacy-work">
              <h3 className="text-base font-bold mb-4 flex items-center gap-2"><ClipboardCheck className="w-4 h-4 text-orange-400" /> Continuing work</h3>
              {assessment.work_items.length === 0 ? <Empty text="No continuing work from this assessment." /> : (
                <ul className="space-y-3">
                  {assessment.work_items.map((w) => (
                    <li key={w.id} className="border border-white/10 rounded-[12px] p-3 flex items-start justify-between gap-3 hover:bg-white/5">
                      <div {...clickable(() => setPanel({ type: 'work', id: w.id }))} aria-label={`Open ${w.message}`} className="cursor-pointer flex-1">
                        <p className="text-xs font-bold text-white">
                          {w.priority === 'high' && w.state !== 'RESOLVED' && <span className="mr-2 text-[10px] font-bold px-1.5 py-0.5 rounded-[5px] bg-red-500/25 text-red-300">Priority</span>}
                          {w.message}
                        </p>
                        <p className="text-[11px] text-white/60 mt-1">
                          {w.item} · Owner: {w.owner ?? 'not assigned'}{w.assigned_to ? ` · Assigned to ${w.assigned_to}` : ''}
                          {w.state === 'ACCEPTED' && ` · Accepted by ${w.accepted_by} on ${shortDate(w.accepted_at)}`}
                          {w.state === 'RESOLVED' && ` · Resolved ${shortDate(w.resolved_at)}`}
                          {w.state === 'CLOSED' && ` · ${OUTCOME_LABELS[w.outcome ?? ''] ?? w.outcome} by ${w.outcome_by} on ${shortDate(w.outcome_at)}`}
                          {(() => {
                            const prev = previousAcceptance(w);
                            return prev ? ` · Previously accepted by ${prev.actor} on ${shortDate(prev.at)}` : null;
                          })()}
                        </p>
                        {w.issues?.length > 0 && (
                          <ul className="mt-1 space-y-0.5">
                            {w.issues.map((i) => (
                              <li key={`${i.code}-${i.item}`} className="text-[11px] text-white/50">• {i.message} ({label(ROLE_LABELS, i.item)})</li>
                            ))}
                          </ul>
                        )}
                      </div>
                      {w.state === 'OPEN' && perm?.can_deliver ? (
                        <button disabled={busy} onClick={() => accept(w.id)} className="shrink-0 text-[11px] font-bold px-3 py-1.5 rounded-[8px] bg-white/10 hover:bg-white/20 disabled:opacity-50">Accept ownership</button>
                      ) : (
                        <span className="shrink-0 text-[11px] font-bold px-2 py-1 rounded-[6px] bg-white/10 text-white/70">{w.state}</span>
                      )}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </div>

          <div className="grid grid-cols-1 lg:grid-cols-2 gap-8">
            <div className={card}>
              <h3 className="text-base font-bold mb-4 flex items-center gap-2"><Archive className="w-4 h-4" /> Opening and closing position</h3>
              <table className="w-full text-xs">
                <thead><tr className="text-white/50 text-left"><th className="py-2">Bucket</th><th>Signed control</th><th>Old platform</th><th>Closing</th></tr></thead>
                <tbody>
                  {balanceRows(s).map((r) => (
                    <tr key={r.bucket} className="border-t border-white/10">
                      <td className="py-2 font-bold">{r.label}</td>
                      <td {...clickable(() => open(`balance:control:${r.bucket}`))} className="cursor-pointer hover:underline">{money(r.control)}{r.unverified.control && ' (unverified)'}</td>
                      <td {...clickable(() => open(`balance:opening:${r.bucket}`))} className={`cursor-pointer hover:underline ${r.variance !== 0 ? 'text-red-400 font-bold' : ''}`}>{money(r.opening)}{r.unverified.opening && ' (unverified)'}</td>
                      <td {...clickable(() => open(`balance:closing:${r.bucket}`))} className={`cursor-pointer hover:underline ${r.unverified.closing ? 'text-red-400 font-bold' : ''}`}>{money(r.closing)}{r.unverified.closing && ' (unverified)'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {disputedRows(s).length > 0 && (
                <div className="mt-3 text-[11px] text-white/60">
                  <p className="font-bold text-red-300">Unverified positions (left out of the totals above):</p>
                  <ul className="mt-1 space-y-0.5">
                    {disputedRows(s).map((r) => (
                      <li key={`${r.set}-${r.key}`}>• {r.set} · {label(BUCKET_LABELS, r.bucket)} · {r.line} · {r.key}: {money(r.minor)}</li>
                    ))}
                  </ul>
                </div>
              )}
            </div>

            <div className={card}>
              <h3 className="text-base font-bold mb-4 flex items-center gap-2"><Landmark className="w-4 h-4" /> Payment, bank and ledger</h3>
              <table className="w-full text-xs">
                <tbody>
                  {[
                    ['Payer advised (cash)', money(s.cash.advised), 'cash:advised'],
                    ['Bank deposits received', bank(s.cash.bank_observed), 'cash:observed'],
                    ['Bank-confirmed', bank(s.cash.bank_confirmed), 'cash:confirmed'],
                    ['Bank money not matched to one advice', bank(s.cash.bank_unmatched), 'cash:unmatched'],
                    ['Posted in the clinic ledger', ledger(s.cash.ledger_posted), 'cash:ledger_posted'],
                    ['Provider-level recoveries', money(s.cash.provider_adjustments), 'cash:provider_adjustments'],
                    ['Bank money with no advice', bank(s.cash.unapplied_bank), 'cash:unapplied'],
                  ].map(([name, v, figure]) => (
                    <tr key={name} {...clickable(() => open(figure))} className="border-t border-white/10 first:border-0 cursor-pointer hover:bg-white/5">
                      <td className="py-2 text-white/70">{name}</td><td className="text-right font-bold">{v}</td>
                    </tr>
                  ))}
                  <tr className="border-t border-white/10"><td className="py-2 text-white/70">Non-cash advices</td><td className="text-right font-bold">{s.cash.noncash_advices}</td></tr>
                </tbody>
              </table>
            </div>
          </div>

          <div className="grid grid-cols-1 lg:grid-cols-2 gap-8">
            <div className={card}>
              <h3 className="text-base font-bold mb-4 flex items-center gap-2"><Route className="w-4 h-4" /> Route tests</h3>
              <ul className="space-y-2">
                {s.routes.map((r) => (
                  <li key={r.stage} {...clickable(() => open(`route:${r.stage}`))} className="flex items-center justify-between text-xs border-t border-white/10 first:border-0 pt-2 cursor-pointer hover:bg-white/5">
                    <span>{label(ROUTE_LABELS, r.stage)}</span>
                    <span className={`font-bold ${r.proved ? 'text-emerald-400' : 'text-red-400'}`}>{r.proved ? 'Proven' : 'Not proven'}</span>
                  </li>
                ))}
              </ul>
            </div>

            <div className={card}>
              <h3 className="text-base font-bold mb-4 flex items-center gap-2"><ShieldCheck className="w-4 h-4" /> Legacy denials</h3>
              {s.denials.length === 0 ? <Empty text="No denials were delivered for this transition." /> : (
                <table className="w-full text-xs">
                  <thead><tr className="text-white/50 text-left"><th className="py-2">Claim line</th><th>Due</th><th>Appeal</th><th>Owner</th></tr></thead>
                  <tbody>
                    {s.denials.map((d) => (
                      <tr key={d.key} {...clickable(() => open(`denial:${d.key}`))} className="border-t border-white/10 cursor-pointer hover:bg-white/5">
                        <td className="py-2 font-bold">{d.line}</td>
                        <td className={!d.due || !d.rule_verified ? 'text-red-400 font-bold' : ''}>{d.due ? shortDate(d.due) : 'Unverified'}</td>
                        <td>{d.appeal_stage ? label(APPEAL_LABELS, d.appeal_stage) : 'Not started'}</td>
                        <td>{d.owner ?? '—'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          </div>

          <div className={card}>
            <h3 className="text-base font-bold mb-4">Evidence sources</h3>
            <table className="w-full text-xs">
              <thead><tr className="text-white/50 text-left"><th className="py-2">Source</th><th>Records</th><th>Latest event</th><th>Completeness statement</th><th>Coverage</th></tr></thead>
              <tbody>
                {s.sources.map((src) => (
                  <tr key={src.role} {...clickable(() => open(`source:${src.role}`))} className="border-t border-white/10 cursor-pointer hover:bg-white/5">
                    <td className="py-2 font-bold">{label(ROLE_LABELS, src.role)}</td>
                    <td>{src.rows}</td>
                    <td>{shortDate(src.last_effective)}</td>
                    <td>{src.sealed_through ? `${src.sealed_rows} rows through ${shortDate(src.sealed_through)}` : 'None received'}</td>
                    <td className={`font-bold ${src.coverage_proved ? 'text-emerald-400' : 'text-red-400'}`}>{src.coverage_proved ? 'Proven' : 'Unproven'}</td>
                  </tr>
                ))}
              </tbody>
            </table>

            {perm?.can_deliver && sources.length > 0 && (
              <div className="mt-6 pt-4 border-t border-white/10 flex flex-col md:flex-row md:items-center gap-3">
                <p className="text-xs font-bold flex items-center gap-2"><FileUp className="w-4 h-4" /> Deliver an export file</p>
                <select
                  aria-label="Source of the file"
                  value={uploadSource}
                  onChange={(e) => setUploadSource(e.target.value ? Number(e.target.value) : '')}
                  className="bg-white/5 border border-white/10 text-white text-xs px-3 py-2 rounded-[12px]"
                >
                  <option value="" className="bg-[var(--color-surface)]">Choose the source</option>
                  {sources.map((src) => (
                    <option key={src.binding_id} value={src.binding_id} className="bg-[var(--color-surface)]">{label(ROLE_LABELS, src.role)}</option>
                  ))}
                </select>
                <FilePicker ariaLabel="Export file (JSON)" accept="application/json,.json" file={uploadFile} onFile={setUploadFile} />
                <button disabled={busy || !uploadFile || uploadSource === ''} onClick={deliver} className={actionButton}>
                  Deliver
                </button>
                <span className="text-[11px] text-white/50">{missingHint([[uploadSource !== '', 'the source'], [!!uploadFile, 'a JSON file']])}</span>
              </div>
            )}
            {perm?.can_deliver && sources.length > 0 && (
              <CsvUpload transitionId={selected} sources={sources} onUploaded={async (id, msg) => {
                await publish(id);                       // the transition the file went to
                if (selectedRef.current === id) flash(msg);
                await refresh(id);                       // re-reads only if it is still the chosen one
              }} />
            )}
          </div>

          <div className="grid grid-cols-1 lg:grid-cols-2 gap-8">
            <div className={card}>
              <div className="flex flex-wrap items-center justify-between gap-2 mb-4">
                <h3 className="text-base font-bold">Subscription exit review</h3>
                {perm?.can_deliver && <button onClick={() => setPanel({ type: 'inventory' })} className="whitespace-nowrap text-[11px] font-bold px-3 py-1.5 rounded-[8px] bg-white/10 border border-white/15 hover:bg-white/20">New inventory</button>}
              </div>
              {inventories.length > 1 && (
                <select
                  aria-label="Subscription inventory"
                  value={inventoryId}
                  onChange={(e) => chooseInventory(e.target.value)}
                  className="mb-3 w-full max-w-full bg-white/5 border border-white/10 text-white text-xs px-3 py-2 rounded-[12px]"
                >
                  <option value="" className="bg-[var(--color-surface)]">Choose the subscription inventory</option>
                  {inventories.map((x) => (
                    <option key={x.inventory_id} value={x.inventory_id} className="bg-[var(--color-surface)]">{x.subscription} · as of {shortDate(x.as_of)}</option>
                  ))}
                </select>
              )}
              {inventories.length === 0 ? (
                <Empty text={`No subscription inventory names this transition's scope (payer ${assessment.transition.scope.payer}, facility ${assessment.transition.scope.facility}).`} />
              ) : !exitReview ? <Empty text="Choose which subscription inventory to review for this transition." /> : (
                <div className="space-y-3 text-xs">
                  <div {...clickable(() => setPanel({ type: 'exit' }))} aria-label="Open the exit review" className="flex items-center gap-3 cursor-pointer rounded-[10px] -m-1 p-1 hover:bg-white/5">
                    <Badge status={exitReview.status} />
                    <span className="text-white/70">{exitReview.subscription} · as of {shortDate(exitReview.as_of)}</span>
                    <span className="ml-auto text-[11px] text-white/40 underline whitespace-nowrap">View details</span>
                  </div>
                  <p className="text-white/70">
                    Net saving over the run-off: <b className="text-white">{formatMoney(fromMinor(exitReview.savings.net), exitReview.savings.currency)}</b>
                    {' '}({formatMoney(fromMinor(exitReview.savings.avoided), exitReview.savings.currency)} avoided, {formatMoney(fromMinor(exitReview.savings.incremental), exitReview.savings.currency)} added)
                  </p>
                  <p className="text-white/70">Scopes: {exitReview.scopes.map((sc) => `${sc.scope.payer} (${statusLabel(sc.status)})`).join(', ')}</p>
                  <p className="text-white/70">Sign-offs received: {exitReview.signoffs.map((k) => k.replace(/_/g, ' ')).join(', ') || 'none'}</p>
                  <Signoffs review={exitReview} canSign={!!perm?.can_deliver} onSigned={(r) => { if (inventoryIdRef.current === r.inventory_id) setExitReview(r); }} />
                  {exitReview.issues.map((i) => <p key={`${i.code}-${i.item}`} className="text-red-300">{i.message}</p>)}
                  <p className="text-[11px] text-white/50">{exitReview.authority}</p>
                </div>
              )}
            </div>

            <div className={card}>
              <h3 className="text-base font-bold mb-4">Assessment history</h3>
              <table className="w-full text-xs">
                <thead><tr className="text-white/50 text-left"><th className="py-2 pr-3 whitespace-nowrap">Computed</th><th className="pr-3 whitespace-nowrap">Evidence as of</th><th className="pr-3">Result</th><th className="pr-3 text-center whitespace-nowrap">Hold reasons</th><th className="text-center">Records</th></tr></thead>
                <tbody>
                  {history.map((h) => (
                    <tr key={h.assessment_id} {...clickable(() => setPanel({ type: 'assessment', id: h.assessment_id }))} className="border-t border-white/10 cursor-pointer hover:bg-white/5">
                      <td className="py-2 pr-3">{shortDate(h.computed_at)}</td>
                      <td className="pr-3">{shortDate(h.as_of)}{h.is_current ? ' (current)' : ''}</td>
                      <td className="pr-3 whitespace-nowrap"><Badge status={h.status} /></td>
                      <td className="pr-3 text-center">{h.issues}</td>
                      <td className="text-center">{h.records}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </>
      )}

      {panel?.type === 'evidence' && (
        <EvidencePanel transitionId={selected} figure={panel.figure} assessmentId={panel.assessmentId} money={money} onClose={() => setPanel(null)} />
      )}
      {panel?.type === 'work' && (
        <WorkPanel itemId={panel.id} transitionId={selected} canDeliver={!!perm?.can_deliver} canReleaseOthers={!!perm?.can_configure}
          me={perm?.me ?? null} money={money} onChanged={changedNow} onClose={() => setPanel(null)} />
      )}
      {panel?.type === 'new-transition' && (
        <NewTransitionPanel currency={assessment?.transition.scope.currency ?? 'AED'} onClose={() => setPanel(null)}
          onCreated={async (id) => { setPanel(null); await loadAll(); await showTransition(id); }} />
      )}
      {panel?.type === 'sources' && transitions.find((t) => t.id === selected) && (
        <SourcesPanel transition={transitions.find((t) => t.id === selected) as Transition} canConfigure={!!perm?.can_configure}
          onChanged={() => { loadAll(); }} onClose={() => setPanel(null)} />
      )}
      {panel?.type === 'inventory' && (
        <InventoryPanel transitions={transitions} cutoffs={[...new Set(history.map((h) => h.as_of))]} currency={assessment?.transition.scope.currency ?? 'AED'} onClose={() => setPanel(null)}
          onCreated={() => { setPanel(null); refresh(selectedRef.current); }} />
      )}
      {panel?.type === 'details' && assessment && (
        <Drawer title="Assessment details" icon={<Info className="w-5 h-5" />} onClose={() => setPanel(null)}>
          <table className="w-full text-xs">
            <tbody>
              {[
                ['Result', statusLabel(assessment.status)],
                ['Assessment', `${assessment.assessment_id ?? 'not yet published (preview)'}${assessment.revision ? ` · revision ${assessment.revision}` : ''}`],
                ['Evidence as of', shortDate(assessment.as_of)],
                ['Computed', shortDate(assessment.computed_at)],
                ['Last evidence received', shortDate(assessment.last_received_at)],
                ['Records assessed', String(assessment.record_count)],
                ['Evidence references', String(assessment.evidence_count)],
                ['Rules (policy) version', assessment.policy_version],
                ['Mapping version', assessment.mapping_version],
                ['Review quality', assessment.review_quality],
                ['Stage', perm?.stage ?? '—'],
                ['Cutover', shortDate(assessment.transition.cutover)],
                ['Payer scope', `${assessment.transition.scope.payer} · ${assessment.transition.scope.facility} · ${assessment.transition.scope.currency}`],
              ].map(([k, v]) => (
                <tr key={k} className="border-t border-white/10 first:border-0"><td className="py-2 pr-3 text-white/60 whitespace-nowrap">{k}</td><td className="break-words">{v}</td></tr>
              ))}
            </tbody>
          </table>
          <p className="text-[11px] text-white/50">{assessment.authority}</p>
        </Drawer>
      )}
      {panel?.type === 'exit' && exitReview && (
        <ExitPanel review={exitReview} canSign={!!perm?.can_deliver} onClose={() => setPanel(null)}
          onSigned={(r) => { if (inventoryIdRef.current === r.inventory_id) setExitReview(r); }}
          onOpenScope={(id) => { setPanel(null); if (id !== selectedRef.current) showTransition(id); }} />
      )}
      {panel?.type === 'assessment' && (
        <AssessmentPanel transitionId={selected} assessmentId={panel.id} money={money}
          onEvidence={(figure, id) => setPanel({ type: 'evidence', figure, assessmentId: id })} onClose={() => setPanel(null)} />
      )}
    </div>
  );
};
