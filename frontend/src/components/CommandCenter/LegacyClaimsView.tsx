import React, { useCallback, useEffect, useState } from 'react';
import {
  AlertCircle, Archive, CheckCircle2, ClipboardCheck, FileUp, Landmark, RefreshCw, Route, ShieldCheck, Wallet,
} from 'lucide-react';
import { fetchWithAuth } from '@/utils/fetchWithAuth';
import { formatMoney } from '@/utils/currency';
import {
  APPEAL_LABELS, Assessment, AssessmentRow, ExitReview, ROLE_LABELS, ROUTE_LABELS, Source, Transition,
  ageLabel, balanceRows, errorText, fromMinor, label, openWork, parseExport, shortDate, statusLabel, statusTone,
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
  const [perm, setPerm] = useState<{ can_deliver: boolean; stage: string } | null>(null);
  const [transitions, setTransitions] = useState<Transition[]>([]);
  const [selected, setSelected] = useState<string>('');
  const [assessment, setAssessment] = useState<Assessment | null>(null);
  const [history, setHistory] = useState<AssessmentRow[]>([]);
  const [sources, setSources] = useState<Source[]>([]);
  const [exitReview, setExitReview] = useState<ExitReview | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [uploadSource, setUploadSource] = useState<number | ''>('');
  const [uploadFile, setUploadFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);

  const getJson = async (url: string) => {
    const res = await fetchWithAuth(url);
    const body = await res.json().catch(() => null);
    if (!res.ok) throw new Error(errorText(body, `Request failed (${res.status})`));
    return body;
  };

  const loadTransition = useCallback(async (id: string) => {
    if (!id) return;
    setError(null);
    try {
      const [a, h, s] = await Promise.all([
        getJson(`${API}/transitions/${id}/assessment`),
        getJson(`${API}/transitions/${id}/assessments`),
        getJson(`${API}/transitions/${id}/sources`),
      ]);
      setAssessment(a);
      setHistory(h);
      setSources(s);
    } catch (e: any) {
      setError(e.message);
      setAssessment(null);
    }
  }, []);

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
      const id = list.some((t) => t.id === selected) ? selected : list[0]?.id ?? '';
      setSelected(id);
      if (id) await loadTransition(id);
      const inventories = await getJson(`${API}/exit-inventories`);
      setExitReview(inventories[0] ? await getJson(`${API}/exit-inventories/${inventories[0].inventory_id}/review`) : null);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loadTransition]);

  useEffect(() => { loadAll(); }, [loadAll]);

  const selectTransition = async (id: string) => {
    setSelected(id);
    await loadTransition(id);
  };

  const flash = (msg: string) => {
    setNotice(msg);
    setTimeout(() => setNotice(null), 5000);
  };

  const accept = async (id: number) => {
    setBusy(true);
    try {
      const res = await fetchWithAuth(`${API}/work-items/${id}/accept`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({}),
      });
      const body = await res.json().catch(() => null);
      if (!res.ok) throw new Error(errorText(body, 'Could not accept the item.'));
      flash('Ownership accepted and recorded in the audit log.');
      await loadTransition(selected);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  const deliver = async () => {
    if (!uploadFile || uploadSource === '') return;
    setBusy(true);
    setError(null);
    try {
      const parsed = parseExport(await uploadFile.text());
      const source = sources.find((s) => s.binding_id === uploadSource);
      const previous = source?.streams.find((st) => st.resource === 'export')?.head ?? null;
      const res = await fetchWithAuth(`${API}/transitions/${selected}/pages`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ binding_id: uploadSource, resource: 'export', previous, ...parsed }),
      });
      const body = await res.json().catch(() => null);
      if (!res.ok) throw new Error(errorText(body, 'The file was refused.'));
      flash(body.status === 'ALREADY_STAGED'
        ? 'This file was already received; nothing changed.'
        : `Received ${body.records_added} new record(s)${body.seal_stored ? ' with a completeness statement' : ''}.`);
      setUploadFile(null);
      await loadTransition(selected);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  const s = assessment?.summary;
  const cur = s?.currency ?? assessment?.transition.scope.currency ?? null;
  const money = (minor: number | null | undefined) => formatMoney(fromMinor(minor), cur);
  const work = assessment ? openWork(assessment.work_items) : [];

  return (
    <div className="animate-in fade-in duration-500 max-w-[1600px] mx-auto space-y-8 relative">
      <div className="flex flex-col md:flex-row md:items-end justify-between gap-4">
        <div>
          <h2 className="text-3xl font-extrabold text-white mb-1">Legacy Claims</h2>
          <p className="text-sm text-white/70 font-medium">
            Can the clinic end its old billing subscription while payers still owe it money? Every figure below is assessed from the evidence each source delivered.
          </p>
        </div>
        <div className="flex items-center gap-3">
          {transitions.length > 0 && (
            <select
              aria-label="Transition"
              value={selected}
              onChange={(e) => selectTransition(e.target.value)}
              className="bg-white/5 border border-white/10 text-white font-bold text-xs px-3 py-2.5 rounded-[16px] premium-shadow cursor-pointer focus:outline-none hover:bg-white/10"
            >
              {transitions.map((t) => (
                <option key={t.id} value={t.id} className="bg-[var(--color-surface)] text-white">
                  {(t.label || t.id)} · {t.scope.payer}
                </option>
              ))}
            </select>
          )}
          <button
            onClick={() => loadAll()}
            disabled={loading}
            className="flex items-center gap-2 text-white font-bold text-xs px-4 py-2.5 rounded-[16px] premium-shadow bg-[#111827] hover:bg-[#1F2937] disabled:opacity-50"
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
        <div className={card}><Empty text="No legacy claims transition has been opened for your clinic yet." /></div>
      )}

      {assessment && s && (
        <>
          <div className="flex flex-wrap items-center gap-3 text-[11px] text-white/70">
            <Badge status={assessment.status} />
            <span>Evidence as of <b className="text-white">{shortDate(assessment.as_of)}</b> ({ageLabel(assessment.source_age_hours)} ago)</span>
            <span>· Cutover {shortDate(assessment.transition.cutover)}</span>
            <span>· {assessment.record_count} records, {assessment.evidence_count} evidence references</span>
            <span>· {assessment.policy_version} / {assessment.mapping_version}</span>
            <span className="px-2 py-0.5 rounded-[6px] bg-white/10 text-white/70">{perm?.stage ?? assessment.review_quality}</span>
          </div>
          <p className="text-[11px] text-white/50 -mt-5">{assessment.authority}</p>

          <div className="grid grid-cols-1 md:grid-cols-4 gap-6">
            {[
              { title: 'Opening payer receivable', value: money(s.opening.payer_ar), sub: `${s.lines.legacy} legacy claim line(s)`, icon: Archive, bg: 'bg-purple-500/20', color: 'text-white' },
              { title: 'Closing payer receivable', value: money(s.closing.payer_ar), sub: `Credits ${money(s.closing.credit)}`, icon: Wallet, bg: 'bg-orange-500/20', color: 'text-orange-400' },
              { title: 'Bank-confirmed payments', value: money(s.cash.bank_confirmed), sub: `of ${money(s.cash.advised)} advised · ${money(s.cash.ledger_posted)} posted`, icon: Landmark, bg: 'bg-emerald-500/20', color: 'text-emerald-400' },
              { title: 'Open work items', value: String(work.length), sub: `${assessment.issues.length} hold reason(s)`, icon: ClipboardCheck, bg: 'bg-red-500/20', color: 'text-red-400' },
            ].map((c) => (
              <div key={c.title} className={`${card} flex flex-col justify-between`}>
                <div className={`p-3 ${c.bg} rounded-[16px] w-fit`}><c.icon className={`w-5 h-5 ${c.color}`} /></div>
                <div className="mt-6">
                  <p className="text-[11px] text-white/70 uppercase font-extrabold tracking-widest mb-1">{c.title}</p>
                  <p className="text-3xl font-black text-white">{c.value}</p>
                  <p className="text-[11px] text-white/60 mt-1">{c.sub}</p>
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
                    <li key={`${i.code}-${i.item}`} className="border border-white/10 rounded-[12px] p-3">
                      <p className="text-xs font-bold text-white">{i.message}</p>
                      <p className="text-[11px] text-white/50 mt-1">{label(ROLE_LABELS, i.item)}</p>
                    </li>
                  ))}
                </ul>
              )}
            </div>

            <div className={card}>
              <h3 className="text-base font-bold mb-4 flex items-center gap-2"><ClipboardCheck className="w-4 h-4 text-orange-400" /> Continuing work</h3>
              {assessment.work_items.length === 0 ? <Empty text="No continuing work from this assessment." /> : (
                <ul className="space-y-3">
                  {assessment.work_items.map((w) => (
                    <li key={w.id} className="border border-white/10 rounded-[12px] p-3 flex items-start justify-between gap-3">
                      <div>
                        <p className="text-xs font-bold text-white">{w.message}</p>
                        <p className="text-[11px] text-white/60 mt-1">
                          {w.item} · Owner: {w.owner ?? 'not assigned'}
                          {w.state === 'ACCEPTED' && ` · Accepted by ${w.accepted_by} on ${shortDate(w.accepted_at)}`}
                          {w.state === 'RESOLVED' && ` · Resolved ${shortDate(w.resolved_at)}`}
                        </p>
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
                      <td>{money(r.control)}</td>
                      <td className={r.variance !== 0 ? 'text-red-400 font-bold' : ''}>{money(r.opening)}</td>
                      <td>{money(r.closing)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            <div className={card}>
              <h3 className="text-base font-bold mb-4 flex items-center gap-2"><Landmark className="w-4 h-4" /> Payment, bank and ledger</h3>
              <table className="w-full text-xs">
                <tbody>
                  {[
                    ['Payer advised (cash)', s.cash.advised],
                    ['Bank-confirmed', s.cash.bank_confirmed],
                    ['Posted in the clinic ledger', s.cash.ledger_posted],
                    ['Provider-level recoveries', s.cash.provider_adjustments],
                    ['Bank money with no advice', s.cash.unapplied_bank],
                  ].map(([name, v]) => (
                    <tr key={name as string} className="border-t border-white/10 first:border-0">
                      <td className="py-2 text-white/70">{name}</td><td className="text-right font-bold">{money(v as number)}</td>
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
                  <li key={r.stage} className="flex items-center justify-between text-xs border-t border-white/10 first:border-0 pt-2">
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
                      <tr key={d.key} className="border-t border-white/10">
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
                  <tr key={src.role} className="border-t border-white/10">
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
                <input
                  aria-label="Export file (JSON)"
                  type="file"
                  accept="application/json,.json"
                  onChange={(e) => setUploadFile(e.target.files?.[0] ?? null)}
                  className="text-xs text-white/70"
                />
                <button
                  disabled={busy || !uploadFile || uploadSource === ''}
                  onClick={deliver}
                  className="text-xs font-bold px-4 py-2 rounded-[12px] bg-[#111827] hover:bg-[#1F2937] disabled:opacity-50"
                >
                  Deliver
                </button>
              </div>
            )}
          </div>

          <div className="grid grid-cols-1 lg:grid-cols-2 gap-8">
            <div className={card}>
              <h3 className="text-base font-bold mb-4">Subscription exit review</h3>
              {!exitReview ? <Empty text="No subscription inventory has been delivered yet." /> : (
                <div className="space-y-3 text-xs">
                  <div className="flex items-center gap-3"><Badge status={exitReview.status} /><span className="text-white/70">{exitReview.subscription} · as of {shortDate(exitReview.as_of)}</span></div>
                  <p className="text-white/70">
                    Net saving over the run-off: <b className="text-white">{formatMoney(fromMinor(exitReview.savings.net), exitReview.savings.currency)}</b>
                    {' '}({formatMoney(fromMinor(exitReview.savings.avoided), exitReview.savings.currency)} avoided, {formatMoney(fromMinor(exitReview.savings.incremental), exitReview.savings.currency)} added)
                  </p>
                  <p className="text-white/70">Scopes: {exitReview.scopes.map((sc) => `${sc.scope.payer} (${statusLabel(sc.status)})`).join(', ')}</p>
                  <p className="text-white/70">Sign-offs received: {exitReview.signoffs.map((k) => k.replace(/_/g, ' ')).join(', ') || 'none'}</p>
                  {exitReview.issues.map((i) => <p key={`${i.code}-${i.item}`} className="text-red-300">{i.message}</p>)}
                  <p className="text-[11px] text-white/50">{exitReview.authority}</p>
                </div>
              )}
            </div>

            <div className={card}>
              <h3 className="text-base font-bold mb-4">Assessment history</h3>
              <table className="w-full text-xs">
                <thead><tr className="text-white/50 text-left"><th className="py-2">Computed</th><th>Evidence as of</th><th>Result</th><th>Hold reasons</th><th>Records</th></tr></thead>
                <tbody>
                  {history.map((h) => (
                    <tr key={h.assessment_id} className="border-t border-white/10">
                      <td className="py-2">{shortDate(h.computed_at)}</td>
                      <td>{shortDate(h.as_of)}</td>
                      <td><Badge status={h.status} /></td>
                      <td>{h.issues}</td>
                      <td>{h.records}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </>
      )}
    </div>
  );
};
