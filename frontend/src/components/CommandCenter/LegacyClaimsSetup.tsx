import React, { useEffect, useId, useRef, useState } from 'react';
import { FileSpreadsheet, LogOut, Paperclip, Settings2, Signature, SquarePlus } from 'lucide-react';
import { fetchWithAuth } from '@/utils/fetchWithAuth';
import { Drawer } from './LegacyClaimsPanels';
import {
  ExitReview, ROLE_LABELS, Source, Transition, errorText, fromMinor, label, shortDate, statusLabel, statusTone,
} from '@/utils/legacyClaims';
import { formatMoney } from '@/utils/currency';

const API = '/api/v1/legacy-claims';
const input = 'bg-white/5 border border-white/10 text-white text-xs px-3 py-2 rounded-[10px] w-full';
/** Action buttons stay visible on the dark cards; disabled ones are clearly greyed, not hidden. */
export const button = 'text-xs font-bold px-4 py-2 rounded-[12px] whitespace-nowrap bg-white/10 border border-white/20 text-white hover:bg-white/20 disabled:bg-white/5 disabled:border-white/10 disabled:text-white/35 disabled:cursor-not-allowed';
const small = 'text-[11px] font-bold px-3 py-1.5 rounded-[8px] whitespace-nowrap bg-white/10 border border-white/15 hover:bg-white/20 disabled:text-white/35 disabled:cursor-not-allowed';

/** A file chooser in the screen's style: the native input stays (for keyboards and screen readers) but hidden. */
export const FilePicker: React.FC<{ ariaLabel: string; accept: string; file: File | null; onFile: (f: File | null) => void }> = ({ ariaLabel, accept, file, onFile }) => {
  const id = useId();
  return (
    <span className="flex items-center gap-2 min-w-0">
      <input id={id} aria-label={ariaLabel} type="file" accept={accept} className="sr-only"
        onChange={(e) => { onFile(e.target.files?.[0] ?? null); e.target.value = ''; }} />
      <label htmlFor={id} className={`${small} cursor-pointer flex items-center gap-1`}><Paperclip className="w-3 h-3" /> Choose file</label>
      <span className={`text-[11px] truncate max-w-[220px] ${file ? 'text-white' : 'text-white/40'}`}>{file ? file.name : 'No file chosen'}</span>
    </span>
  );
};

/** What is still needed before a disabled action can run. */
export const missingHint = (items: [boolean, string][]) => {
  const missing = items.filter(([ok]) => !ok).map(([, text]) => text);
  return missing.length ? `Choose ${missing.join(' and ')} to continue.` : null;
};

const send = async (url: string, method: string, body: unknown) => {
  const res = await fetchWithAuth(url, { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  const data = await res.json().catch(() => null);
  if (!res.ok) throw new Error(errorText(data, `Request failed (${res.status})`));
  return data;
};

/** datetime-local value (the browser's local time) -> ISO instant with Z. */
const toInstant = (local: string) => (local ? new Date(local).toISOString().replace(/\.\d{3}Z$/, 'Z') : null);
/** ISO instant -> datetime-local value in the browser's local time. */
const toLocal = (iso: string | null | undefined) => {
  if (!iso) return '';
  const d = new Date(iso);
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
};

export const HOLD_TASKS: Record<string, string> = {
  RECONCILE_BANK_EVIDENCE: 'Bank evidence', RECONCILE_LEDGER: 'Clinic ledger', RECONCILE_BALANCES: 'Balances',
  RESOLVE_PAYMENT_ADVICE: 'Payment advices', CONFIRM_LINE_OWNERSHIP: 'Line ownership', VERIFY_DENIAL_DEADLINES: 'Denials',
  PROVE_ROUTES: 'Route tests', RESOLVE_SOURCE_EVIDENCE: 'Source delivery',
};

const Field = ({ name, children }: { name: string; children: React.ReactNode }) => (
  <label className="block text-[11px] text-white/60 space-y-1"><span>{name}</span>{children}</label>
);

/** Administrators open a transition: which payer scope moves off the old platform, when, and by which routes. */
export const NewTransitionPanel: React.FC<{ currency: string; onCreated: (id: string) => void; onClose: () => void }> = ({ currency, onCreated, onClose }) => {
  const [f, setF] = useState({
    label: '', facility: '', payee: '', payer: '', currency, cutover: '', new_route: '', old_route: '',
    fallback_route: '', archive_route: '', max_probe_age_days: '14',
  });
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const set = (k: keyof typeof f) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => setF({ ...f, [k]: e.target.value });
  const create = async () => {
    setBusy(true);
    setError(null);
    try {
      const t = await send(`${API}/transitions`, 'POST', {
        ...f, label: f.label || null, cutover: toInstant(f.cutover), max_probe_age_days: Number(f.max_probe_age_days),
      });
      onCreated(t.id);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };
  const ids: [keyof typeof f, string][] = [['facility', 'Facility'], ['payee', 'Payee'], ['payer', 'Payer'],
    ['new_route', 'New payer route'], ['old_route', 'Old payer route (history)'], ['fallback_route', 'Fallback route'],
    ['archive_route', 'Records archive']];
  return (
    <Drawer title="New legacy claims transition" icon={<SquarePlus className="w-5 h-5" />} onClose={onClose}>
      <p className="text-xs text-white/60">One payer scope moving off the old billing platform. Identifiers use letters, digits, dot, dash or underscore.</p>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        <Field name="Name"><input aria-label="Name" value={f.label} onChange={set('label')} className={input} /></Field>
        <Field name="Cutover (your local time)"><input aria-label="Cutover" type="datetime-local" value={f.cutover} onChange={set('cutover')} className={input} /></Field>
        {ids.map(([k, n]) => <Field key={k} name={n}><input aria-label={n} value={f[k]} onChange={set(k)} className={input} /></Field>)}
        <Field name="Currency">
          <select aria-label="Currency" value={f.currency} onChange={set('currency')} className={input}>
            <option value="AED" className="bg-[var(--color-surface)]">AED</option>
            <option value="USD" className="bg-[var(--color-surface)]">USD</option>
          </select>
        </Field>
        <Field name="Route test valid for (days)"><input aria-label="Route test valid for (days)" type="number" min={1} value={f.max_probe_age_days} onChange={set('max_probe_age_days')} className={input} /></Field>
      </div>
      {error && <p className="text-xs text-red-300">{error}</p>}
      <button disabled={busy || !f.cutover || !f.payer} onClick={create} className={button}>Open transition</button>
    </Drawer>
  );
};

type RoleRow = { role: string; sources: { binding_id: number; valid_from: string; valid_until: string }[]; csv_columns: string[] };

/** Administrators bind the evidence sources and set the responsible team per kind of investigation. */
export const SourcesPanel: React.FC<{ transition: Transition; canConfigure: boolean; onChanged: () => void; onClose: () => void }> = ({ transition, canConfigure, onChanged, onClose }) => {
  const [roles, setRoles] = useState<RoleRow[]>([]);
  const [owners, setOwners] = useState<Record<string, string>>(transition.hold_owners ?? {});
  const [from, setFrom] = useState(toLocal(new Date(new Date(transition.cutover).getTime() - 30 * 864e5).toISOString()));
  const [until, setUntil] = useState(toLocal(new Date(new Date(transition.cutover).getTime() + 365 * 864e5).toISOString()));
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(null);
  const load = () => fetchWithAuth(`${API}/transitions/${transition.id}/roles`).then(async (r) => {
    const body = await r.json().catch(() => null);
    if (r.ok) setRoles(body); else setError(errorText(body, 'Could not read the sources.'));
  });
  useEffect(() => { load(); }, [transition.id]); // eslint-disable-line react-hooks/exhaustive-deps
  const bind = async (role: string) => {
    setError(null);
    try {
      await send(`${API}/transitions/${transition.id}/sources`, 'POST', { role, valid_from: toInstant(from), valid_until: toInstant(until) });
      await load();
      onChanged();
    } catch (e: any) { setError(e.message); }
  };
  const saveOwners = async () => {
    setError(null);
    setSaved(null);
    try {
      await send(`${API}/transitions/${transition.id}/owners`, 'PUT', { hold_owners: owners });
      setSaved('Saved. The next assessment routes new investigations to these teams.');
      onChanged();
    } catch (e: any) { setError(e.message); }
  };
  return (
    <Drawer title="Sources and responsible teams" icon={<Settings2 className="w-5 h-5" />} onClose={onClose}>
      <div className="text-xs space-y-2">
        <p className="font-bold">Evidence sources</p>
        {canConfigure && (
          <div className="grid grid-cols-2 gap-2">
            <Field name="New sources valid from"><input aria-label="Valid from" type="datetime-local" value={from} onChange={(e) => setFrom(e.target.value)} className={input} /></Field>
            <Field name="until"><input aria-label="Valid until" type="datetime-local" value={until} onChange={(e) => setUntil(e.target.value)} className={input} /></Field>
          </div>
        )}
        <ul className="space-y-2">
          {roles.map((r) => (
            <li key={r.role} className="border border-white/10 rounded-[12px] p-3 flex items-center justify-between gap-3">
              <div>
                <p className="font-bold">{label(ROLE_LABELS, r.role)}</p>
                <p className="text-[11px] text-white/50">
                  {r.sources.length === 0 ? 'No source bound' : r.sources.map((s) => `Bound ${shortDate(s.valid_from)} – ${shortDate(s.valid_until)}`).join(' · ')}
                </p>
              </div>
              {canConfigure && <button onClick={() => bind(r.role)} className={small}>{r.sources.length ? 'Bind another' : 'Bind source'}</button>}
            </li>
          ))}
        </ul>
      </div>
      <div className="text-xs space-y-2">
        <p className="font-bold">Responsible team for each kind of investigation</p>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
          {Object.keys(HOLD_TASKS).map((code) => (
            <Field key={code} name={HOLD_TASKS[code]}>
              <input aria-label={`Team for ${HOLD_TASKS[code]}`} disabled={!canConfigure} value={owners[code] ?? ''} onChange={(e) => setOwners({ ...owners, [code]: e.target.value })} className={input} />
            </Field>
          ))}
        </div>
        {canConfigure && <button onClick={saveOwners} className={button}>Save teams</button>}
        {saved && <p className="text-emerald-300">{saved}</p>}
      </div>
      {error && <p className="text-xs text-red-300">{error}</p>}
    </Drawer>
  );
};

type Preview = {
  role: string; rows: number; by_kind: Record<string, number>; problems: { row: number | null; reason: string }[];
  new_records: number; already_received: number; latest_event: string | null; this_source_complete_through: string | null;
  sources_complete_through: Record<string, string>; latest_statement: string | null; can_upload: boolean;
};

/** Clinic staff deliver a source's CSV export: template, preview, completeness statement, upload. */
export const CsvUpload: React.FC<{
  transitionId: string; sources: Source[]; onUploaded: (transitionId: string, message: string) => Promise<void>;
}> = ({ transitionId, sources, onUploaded }) => {
  const [binding, setBinding] = useState<number | ''>('');
  const [file, setFile] = useState<File | null>(null);
  const [content, setContent] = useState<string>('');
  const [preview, setPreview] = useState<Preview | null>(null);
  const [complete, setComplete] = useState(false);
  const [through, setThrough] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const role = sources.find((s) => s.binding_id === binding)?.role ?? null;

  // The transition this upload belongs to; a preview or upload answer for another one is never shown.
  const current = useRef(transitionId);
  useEffect(() => {
    current.current = transitionId;
    setBinding('');
    setFile(null);
    setContent('');
    setError(null);
  }, [transitionId]);
  useEffect(() => { setPreview(null); setComplete(false); }, [binding, file, transitionId]);

  const template = async () => {
    if (!role) return;
    const res = await fetchWithAuth(`${API}/templates/${role}`);
    if (!res.ok) { setError('Could not download the template.'); return; }
    const url = URL.createObjectURL(await res.blob());
    const a = document.createElement('a');
    a.href = url;
    a.download = `legacy-${role}-template.csv`;
    a.click();
    URL.revokeObjectURL(url);
  };
  const runPreview = async () => {
    if (!file || binding === '') return;
    const tid = transitionId;
    setBusy(true);
    setError(null);
    try {
      const text = await file.text();
      const p: Preview = await send(`${API}/transitions/${tid}/uploads/preview`, 'POST', { binding_id: binding, content: text });
      if (current.current !== tid) return;
      setContent(text);
      setPreview(p);
      setThrough(toLocal(p.latest_statement ?? p.latest_event));
    } catch (e: any) { if (current.current === tid) setError(e.message); } finally { setBusy(false); }
  };
  const upload = async () => {
    if (!preview?.can_upload || binding === '' || !file) return;
    const tid = transitionId;
    const name = file.name;
    setBusy(true);
    setError(null);
    try {
      const r = await send(`${API}/transitions/${tid}/uploads`, 'POST', {
        binding_id: binding, content, filename: name, complete_through: complete ? toInstant(through) : null,
      });
      if (current.current === tid) {
        setFile(null);
        setPreview(null);
      }
      await onUploaded(tid, `Stored ${r.records_added} new record(s) from ${name}${r.complete_through ? `; complete through ${shortDate(r.complete_through)} in your name` : ''}.`);
    } catch (e: any) { if (current.current === tid) setError(e.message); } finally { setBusy(false); }
  };
  return (
    <div className="mt-4 pt-4 border-t border-white/10 space-y-3 text-xs">
      <p className="font-bold flex items-center gap-2"><FileSpreadsheet className="w-4 h-4" /> Upload a CSV export</p>
      <div className="flex flex-col md:flex-row md:items-center gap-3">
        <select aria-label="Source of the CSV" value={binding} onChange={(e) => setBinding(e.target.value ? Number(e.target.value) : '')} className="bg-white/5 border border-white/10 text-white text-xs px-3 py-2 rounded-[12px]">
          <option value="" className="bg-[var(--color-surface)]">Choose the source</option>
          {sources.map((s) => <option key={s.binding_id} value={s.binding_id} className="bg-[var(--color-surface)]">{label(ROLE_LABELS, s.role)}</option>)}
        </select>
        <button disabled={!role} onClick={template} className={small}>Download template</button>
        <FilePicker ariaLabel="CSV export" accept=".csv,text/csv" file={file} onFile={setFile} />
        <button disabled={busy || !file || binding === ''} onClick={runPreview} className={button}>Preview</button>
      </div>
      <p className="text-[11px] text-white/50">
        {missingHint([[binding !== '', 'the source'], [!!file, 'a CSV file']]) ?? 'Ready to preview.'}{' '}
        Amounts like 900.00; times like 2026-11-24 10:00 (clinic time) or with a zone. Nothing is stored until you upload.
      </p>
      {preview && (
        <div className="border border-white/10 rounded-[12px] p-3 space-y-2">
          <p><b>{preview.rows}</b> record(s): {Object.entries(preview.by_kind).map(([k, n]) => `${n} ${k.replace(/_/g, ' ')}`).join(', ') || 'none'} · <b>{preview.new_records}</b> new, {preview.already_received} already received</p>
          {preview.problems.length > 0 && (
            <ul className="text-red-300 space-y-0.5">{preview.problems.map((p, i) => <li key={i}>{p.row ? `Row ${p.row}: ` : ''}{p.reason}</li>)}</ul>
          )}
          <p className="text-white/60">
            This source is complete through {preview.this_source_complete_through ? shortDate(preview.this_source_complete_through) : 'no date yet'}.
            {preview.latest_statement && ` The latest completeness statement of any source is ${shortDate(preview.latest_statement)}; every source must state the same date for its figures to count.`}
          </p>
          {preview.can_upload && (
            <>
              <label className="flex items-center gap-2">
                <input aria-label="Complete export" type="checkbox" checked={complete} onChange={(e) => setComplete(e.target.checked)} />
                <span>This file and the earlier deliveries are the complete export of this source through</span>
                <input aria-label="Complete through" type="datetime-local" value={through} onChange={(e) => setThrough(e.target.value)} className="bg-white/5 border border-white/10 text-white text-xs px-2 py-1 rounded-[8px]" />
              </label>
              <p className="text-[11px] text-white/50">The statement is recorded in your name with the file.</p>
              <button disabled={busy || (complete && !through)} onClick={upload} className={button}>Upload</button>
            </>
          )}
        </div>
      )}
      {error && <p className="text-red-300">{error}</p>}
    </div>
  );
};

const SIGNOFF_LABELS: Record<string, string> = { finance: 'Finance', revenue_cycle: 'Revenue cycle', records: 'Records', contract_owner: 'Contract owner' };

/** Named sign-offs for the roles still missing. */
export const Signoffs: React.FC<{ review: ExitReview; canSign: boolean; onSigned: (review: ExitReview) => void }> = ({ review, canSign, onSigned }) => {
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const sign = async (role: string) => {
    setBusy(true);
    setError(null);
    try {
      onSigned(await send(`${API}/exit-inventories/${review.inventory_id}/signoffs`, 'POST', { role }));
    } catch (e: any) { setError(e.message); } finally { setBusy(false); }
  };
  return (
    <div className="space-y-1">
      {(review.signoff_details ?? []).map((d) => (
        <p key={d.role} className="text-white/70">✓ {SIGNOFF_LABELS[d.role] ?? d.role}{d.signed_by ? ` · ${d.signed_by} · ${shortDate(d.signed_at)}` : ' · in the inventory'}</p>
      ))}
      {(review.missing_signoffs ?? []).map((role) => (
        <div key={role} className="flex items-center justify-between gap-2">
          <span className="text-orange-300">Missing: {SIGNOFF_LABELS[role] ?? role}</span>
          {canSign && <button disabled={busy} onClick={() => sign(role)} className={small}><Signature className="w-3 h-3 inline mr-1" />Sign off as {SIGNOFF_LABELS[role] ?? role}</button>}
        </div>
      ))}
      {error && <p className="text-red-300">{error}</p>}
    </div>
  );
};

/** The contract owner records which payer scopes and dependencies the old subscription still covers. */
export const InventoryPanel: React.FC<{
  transitions: Transition[]; cutoffs: string[]; currency: string; onCreated: () => void; onClose: () => void;
}> = ({ transitions, cutoffs, currency, onCreated, onClose }) => {
  const kinds = ['access', 'bundled_clinical', 'contract', 'records', 'fallback'];
  const [f, setF] = useState({ subscription: '', receipt: '', as_of: cutoffs[0] ?? '', avoided: '', added: '', basis: '' });
  const [scopes, setScopes] = useState<string[]>(transitions[0] ? [transitions[0].id] : []);
  const [deps, setDeps] = useState(kinds.map((k) => ({ key: k.replace('_', '-'), kind: k, proof_ref: '', accepted_by: '', valid_through: '' })));
  const [error, setError] = useState<string | null>(null);
  const toMinor = (v: string) => Math.round(Number(v) * 100);
  const create = async () => {
    setError(null);
    try {
      const asOf = f.as_of;
      await send(`${API}/exit-inventories`, 'POST', {
        subscription: f.subscription, inventory_receipt: f.receipt, certified_at: asOf, as_of: asOf,
        required_scopes: transitions.filter((t) => scopes.includes(t.id)).map((t) => t.scope),
        dependencies: deps.map((d) => ({ ...d, proof_ref: d.proof_ref || null, accepted_by: d.accepted_by || null, valid_through: toInstant(d.valid_through) ?? asOf })),
        signoffs: {}, currency, avoided_minor: toMinor(f.avoided || '0'), incremental_minor: toMinor(f.added || '0'), cost_basis_ref: f.basis || null,
      });
      onCreated();
    } catch (e: any) { setError(e.message); }
  };
  return (
    <Drawer title="New subscription inventory" icon={<FileSpreadsheet className="w-5 h-5" />} onClose={onClose}>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        <Field name="Subscription"><input aria-label="Subscription" value={f.subscription} onChange={(e) => setF({ ...f, subscription: e.target.value })} className={input} /></Field>
        <Field name="Inventory reference"><input aria-label="Inventory reference" value={f.receipt} onChange={(e) => setF({ ...f, receipt: e.target.value })} className={input} /></Field>
        <Field name="Certified as of (an evidence cutoff)">
          <select aria-label="Certified as of" value={f.as_of} onChange={(e) => setF({ ...f, as_of: e.target.value })} className={input}>
            {cutoffs.length === 0 && <option value="" className="bg-[var(--color-surface)]">No sealed evidence yet</option>}
            {cutoffs.map((c) => <option key={c} value={c} className="bg-[var(--color-surface)]">{shortDate(c)}</option>)}
          </select>
        </Field>
        <Field name={`Fees avoided (${currency})`}><input aria-label="Fees avoided" value={f.avoided} onChange={(e) => setF({ ...f, avoided: e.target.value })} className={input} /></Field>
        <Field name={`Costs added (${currency})`}><input aria-label="Costs added" value={f.added} onChange={(e) => setF({ ...f, added: e.target.value })} className={input} /></Field>
        <Field name="Cost basis reference"><input aria-label="Cost basis reference" value={f.basis} onChange={(e) => setF({ ...f, basis: e.target.value })} className={input} /></Field>
      </div>
      <div className="text-xs space-y-1">
        <p className="font-bold">Payer scopes the subscription still serves</p>
        {transitions.map((t) => (
          <label key={t.id} className="flex items-center gap-2"><input type="checkbox" checked={scopes.includes(t.id)} onChange={(e) => setScopes(e.target.checked ? [...scopes, t.id] : scopes.filter((x) => x !== t.id))} />{t.label || t.id} · {t.scope.payer}</label>
        ))}
      </div>
      <div className="text-xs space-y-2">
        <p className="font-bold">Dependencies (proof and the owner who accepted it)</p>
        {deps.map((d, i) => (
          <div key={d.kind} className="grid grid-cols-1 md:grid-cols-4 gap-2 items-end">
            <span className="text-white/70">{d.kind.replace('_', ' ')}</span>
            <input aria-label={`Proof for ${d.kind}`} placeholder="proof reference" value={d.proof_ref} onChange={(e) => setDeps(deps.map((x, j) => (j === i ? { ...x, proof_ref: e.target.value } : x)))} className={input} />
            <input aria-label={`Accepted by for ${d.kind}`} placeholder="accepted by" value={d.accepted_by} onChange={(e) => setDeps(deps.map((x, j) => (j === i ? { ...x, accepted_by: e.target.value } : x)))} className={input} />
            <input aria-label={`Valid through for ${d.kind}`} type="datetime-local" value={d.valid_through} onChange={(e) => setDeps(deps.map((x, j) => (j === i ? { ...x, valid_through: e.target.value } : x)))} className={input} />
          </div>
        ))}
      </div>
      {error && <p className="text-xs text-red-300">{error}</p>}
      <button disabled={!f.subscription || !f.receipt || !f.as_of || scopes.length === 0} onClick={create} className={button}>Save inventory</button>
      <p className="text-[11px] text-white/50">Sign-offs are given afterwards, by each role, in the exit review.</p>
    </Drawer>
  );
};

const DEPENDENCY_LABELS: Record<string, string> = {
  access: 'System access', bundled_clinical: 'Bundled clinical features', contract: 'Contract terms', records: 'Records retention', fallback: 'Fallback route',
};

/** Everything behind a subscription exit review: scopes, dependencies, savings, sign-offs and failed rules. */
export const ExitPanel: React.FC<{
  review: ExitReview; canSign: boolean; onSigned: (review: ExitReview) => void; onOpenScope: (transitionId: string) => void; onClose: () => void;
}> = ({ review, canSign, onSigned, onOpenScope, onClose }) => {
  const money = (minor: number) => formatMoney(fromMinor(minor), review.savings.currency);
  const tone = statusTone(review.status);
  return (
    <Drawer title={`Exit review · ${review.subscription}`} icon={<LogOut className="w-5 h-5" />} onClose={onClose}>
      <div className="flex flex-wrap items-center gap-2 text-xs text-white/70">
        <span className={`text-[11px] font-bold px-2 py-1 rounded-[6px] ${tone === 'good' ? 'text-emerald-400 bg-emerald-500/20' : tone === 'bad' ? 'text-red-400 bg-red-500/20' : 'text-white/60 bg-white/10'}`}>{statusLabel(review.status)}</span>
        <span>Inventory certified as of {shortDate(review.as_of)} · received {shortDate(review.received_at)}</span>
      </div>
      <p className="text-[11px] text-white/50">{review.authority}</p>

      {review.issues.length > 0 && (
        <div className="text-xs space-y-1">
          <p className="font-bold">Why it is on hold</p>
          {review.issues.map((i) => <p key={`${i.code}-${i.item}`} className="text-red-300">• {i.message} ({i.item})</p>)}
        </div>
      )}

      <div className="text-xs space-y-1">
        <p className="font-bold">Payer scopes the subscription still serves</p>
        {review.scopes.map((sc) => (
          <div key={`${sc.scope.payer}-${sc.scope.facility}`} className="flex items-center justify-between gap-2 border border-white/10 rounded-[10px] p-2">
            <span>{sc.label ?? sc.scope.payer} · {sc.scope.payer} · {sc.scope.facility}</span>
            <span className="flex items-center gap-2">
              <span className="text-white/70">{statusLabel(sc.status)}</span>
              {sc.transition_id && <button onClick={() => onOpenScope(sc.transition_id as string)} className={small}>Open</button>}
            </span>
          </div>
        ))}
      </div>

      <div className="text-xs">
        <p className="font-bold mb-1">Dependencies on the old platform</p>
        <table className="w-full">
          <thead><tr className="text-white/50 text-left"><th className="py-1 pr-2 whitespace-nowrap">Dependency</th><th className="pr-2">Proof</th><th className="pr-2 whitespace-nowrap">Accepted by</th><th className="whitespace-nowrap">Valid through</th></tr></thead>
          <tbody>
            {review.dependencies.map((d) => (
              <tr key={d.key} className="border-t border-white/10">
                <td className="py-1 pr-2">{DEPENDENCY_LABELS[d.kind] ?? d.kind}</td>
                <td className={`pr-2 ${d.proof_ref ? '' : 'text-red-300'}`}>{d.proof_ref ?? 'missing'}</td>
                <td className={`pr-2 ${d.accepted_by ? '' : 'text-red-300'}`}>{d.accepted_by ?? 'missing'}</td>
                <td className={new Date(d.valid_through) < new Date(review.as_of) ? 'text-red-300' : ''}>{shortDate(d.valid_through)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="text-xs space-y-1">
        <p className="font-bold">Savings over the run-off</p>
        <p className="text-white/70">Fees avoided {money(review.savings.avoided)} · costs added {money(review.savings.incremental)} · net <b className="text-white">{money(review.savings.net)}</b></p>
      </div>

      <div className="text-xs space-y-1">
        <p className="font-bold">Sign-offs</p>
        <Signoffs review={review} canSign={canSign} onSigned={onSigned} />
      </div>
    </Drawer>
  );
};
