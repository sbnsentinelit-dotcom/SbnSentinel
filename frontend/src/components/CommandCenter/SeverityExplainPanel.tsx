'use client';
import React, { useEffect, useState } from 'react';
import { createPortal } from 'react-dom';
import { X, ShieldAlert } from 'lucide-react';

/** What a severity evaluator returns; the panel renders it and decides nothing itself. */
export interface SeverityResult {
  severity?: string;
  label?: string;
  tone?: string;
  at_risk?: string | null;
  reason?: string | null;
  next_action?: string | null;
  assumption?: string | null;
  rule_id?: string | null;
  rule_version?: string | null;
  policy_id?: string | null;
  policy_version?: string | null;
  is_synthetic?: boolean;
  evidence_refs?: string[];
  evaluated_at?: string | null;
  as_of_local?: string | null;
  previous_alert?: { label: string; as_of_local: string; reason?: string } | null;
  timeline?: Array<{ label: string; from?: string; until?: string }>;
  is_preview?: boolean;
}

const TONE_CLASS: Record<string, string> = {
  critical: 'bg-red-500/20 text-red-400 border-red-500/30',
  high: 'bg-orange-500/20 text-orange-400 border-orange-500/30',
  moderate: 'bg-yellow-500/20 text-yellow-400 border-yellow-500/30',
  low: 'bg-emerald-500/20 text-emerald-400 border-emerald-500/30',
  ok: 'bg-teal-500/20 text-teal-300 border-teal-500/30',
  verify: 'bg-violet-500/20 text-violet-300 border-violet-500/30',
  unknown: 'bg-slate-500/20 text-slate-300 border-slate-500/30',
};

/** Badge colour from the evaluator's tone (or its severity for older results). */
export const toneClass = (tone?: string, severity?: string) => {
  const key = tone || ({ Critical: 'critical', High: 'high', Moderate: 'moderate', Low: 'low' } as Record<string, string>)[severity || ''] || 'unknown';
  return TONE_CLASS[key] || TONE_CLASS.unknown;
};

const formatEvaluatedAt = (iso?: string | null) => {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString('en-GB', { timeZone: 'Asia/Dubai', day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' }) + ' UAE';
};

interface Props {
  title: string;
  result: SeverityResult;
  onClose: () => void;
  /** Demo-only controls shown under the explanation. */
  children?: React.ReactNode;
}

export const SeverityExplainPanel: React.FC<Props> = ({ title, result, onClose, children }) => {
  // Rendered on document.body so a transformed parent (screen animations) cannot clip it.
  const [mounted, setMounted] = useState(false);
  useEffect(() => { setMounted(true); }, []);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  const rows: Array<[string, React.ReactNode]> = [
    ['What is at risk', result.at_risk],
    ['Why this level', result.reason],
    ['What can still be done', result.next_action],
    ['Assumption', result.assumption],
  ];

  if (!mounted) return null;
  return createPortal(
    <div className="fixed inset-0 z-[100] flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm" onClick={onClose}
      role="dialog" aria-modal="true" aria-label={title}>
      <div className="w-full max-w-lg max-h-[90vh] overflow-y-auto bg-gradient-to-br from-[#1E1E2E] to-[#12121A] border border-white/10 rounded-2xl shadow-2xl text-white"
        onClick={(e) => e.stopPropagation()}>
        <div className="sticky top-0 flex items-start justify-between gap-3 p-5 border-b border-white/10 bg-[#1A1A28]">
          <div className="min-w-0">
            <div className="text-[11px] text-white/50 font-bold uppercase tracking-widest flex items-center gap-2">
              <ShieldAlert className="w-4 h-4" /> {title}
            </div>
            <div className={`mt-2 inline-flex items-center px-3 py-1.5 rounded-lg border text-sm font-bold ${toneClass(result.tone, result.severity)}`}>
              {result.label || result.severity || 'Unavailable'}
            </div>
            {result.is_preview && <div className="mt-2 text-xs text-amber-300 font-semibold">Preview only: not applied to the live result.</div>}
          </div>
          <button onClick={onClose} aria-label="Close" className="p-2 rounded-lg hover:bg-white/10 text-white/70 hover:text-white">
            <X className="w-5 h-5" />
          </button>
        </div>
        <div className="p-5 space-y-4">
          {rows.filter(([, v]) => v).map(([k, v]) => (
            <div key={k} className="bg-white/5 rounded-xl border border-white/10 p-3">
              <div className="text-[11px] text-white/50 uppercase tracking-widest font-bold mb-1">{k}</div>
              <div className="text-sm text-white/90">{v}</div>
            </div>
          ))}
          {result.previous_alert && (
            <div className="bg-white/5 rounded-xl border border-white/10 p-3">
              <div className="text-[11px] text-white/50 uppercase tracking-widest font-bold mb-1">Earlier alert (history)</div>
              <div className="text-sm text-white/90"><span className="font-bold">{result.previous_alert.label}</span> as of {result.previous_alert.as_of_local}</div>
            </div>
          )}
          {result.timeline && result.timeline.length > 0 && (
            <div className="bg-white/5 rounded-xl border border-white/10 p-3 text-xs">
              <div className="text-white/50 uppercase tracking-widest font-bold mb-2">Level timeline (from this claim&apos;s data)</div>
              <ul className="space-y-1">
                {result.timeline.map((t) => (
                  <li key={t.label} className={`flex flex-wrap justify-between gap-2 ${t.label === result.label ? 'text-white font-bold' : 'text-white/70'}`}>
                    <span>{t.label}</span>
                    <span className="text-white/60">{t.from ? `from ${t.from}` : ''}{t.from && t.until ? ' ' : ''}{t.until ? `until ${t.until}` : ''}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 text-xs">
            <div className="bg-white/5 rounded-xl border border-white/10 p-3">
              <div className="text-white/50 uppercase tracking-widest font-bold mb-1">Rule</div>
              <div className="text-white/90 break-words">{result.rule_id ? `${result.rule_id} ${result.rule_version || ''}` : 'Not recorded'}</div>
              {result.policy_id && <div className="text-white/60 mt-1 break-words">Policy {result.policy_id} {result.policy_version || ''}</div>}
            </div>
            <div className="bg-white/5 rounded-xl border border-white/10 p-3">
              <div className="text-white/50 uppercase tracking-widest font-bold mb-1">Evaluated</div>
              <div className="text-white/90">{result.as_of_local || formatEvaluatedAt(result.evaluated_at) || 'Not recorded'}</div>
            </div>
          </div>
          <div className="bg-white/5 rounded-xl border border-white/10 p-3 text-xs">
            <div className="text-white/50 uppercase tracking-widest font-bold mb-1">Evidence</div>
            {result.evidence_refs && result.evidence_refs.length > 0
              ? <div className="flex flex-wrap gap-1.5">{result.evidence_refs.map((r) => <span key={r} className="px-2 py-0.5 rounded-md bg-white/10 text-white/80 font-mono break-all">{r}</span>)}</div>
              : <div className="text-white/70">No evidence references recorded.</div>}
          </div>
          {children}
        </div>
      </div>
    </div>,
    document.body,
  );
};

export default SeverityExplainPanel;
