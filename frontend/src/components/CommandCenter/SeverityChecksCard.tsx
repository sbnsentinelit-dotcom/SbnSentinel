'use client';
import React, { useCallback, useEffect, useState } from 'react';
import { Gauge, RefreshCw } from 'lucide-react';
import { fetchWithAuth } from '@/utils/fetchWithAuth';
import { SeverityExplainPanel, SeverityResult, toneClass } from './SeverityExplainPanel';

const BACKEND = process.env.NEXT_PUBLIC_BACKEND_URL || '';

interface RosterEntry { id: number; worker: string; role?: string | null; qualified: boolean; assigned: boolean; shift: string }

interface ChecksResponse {
  as_of_local: string;
  review_capacity: SeverityResult & { ready_reviews?: number; projected_miss?: number | null; internal_cutoff?: string };
  review_capacity_preview: (SeverityResult & { is_preview?: boolean }) | null;
  filing_risk: (SeverityResult & { subject?: { claim?: string; payer?: string } }) | null;
  filing_totals: Array<{ label: string; count: number; tone: string }>;
  claims_evaluated: number;
  roster: RosterEntry[];
}

/**
 * Severity checks on the clinic's live data: review capacity (open claims and the clinic's review
 * roster) and payer filing risk (each open claim evaluated on its own, then totalled).
 * Every level, reason and action comes from the backend; this card only renders it.
 */
export const SeverityChecksCard: React.FC = () => {
  const [preview, setPreview] = useState(false);
  const [data, setData] = useState<ChecksResponse | null>(null);
  const [failed, setFailed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [open, setOpen] = useState<'review' | 'preview' | 'filing' | null>(null);

  const load = useCallback(async () => {
    try {
      const res = await fetchWithAuth(`${BACKEND}/api/v1/severity/checks?preview_backup=${preview}`);
      if (!res.ok) throw new Error(String(res.status));
      setData(await res.json());
      setFailed(false);
    } catch {
      setFailed(true);
    }
  }, [preview]);

  useEffect(() => {
    load();
    const t = setInterval(load, 30000);
    return () => clearInterval(t);
  }, [load]);

  const assign = async (entry: RosterEntry, assigned: boolean) => {
    setBusy(true);
    try {
      await fetchWithAuth(`${BACKEND}/api/v1/severity/roster/${entry.id}`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ assigned }),
      });
      setPreview(false);
      await load();
    } finally {
      setBusy(false);
    }
  };

  const review = data?.review_capacity;
  const filing = data?.filing_risk;
  const backups = (data?.roster || []).filter((r) => r.qualified && !r.assigned);
  const assignedBackups = (data?.roster || []).filter((r) => r.assigned && /backup/i.test(r.role || ''));
  const shown = open === 'review' ? review : open === 'preview' ? data?.review_capacity_preview : open === 'filing' ? filing : null;

  return (
    <div className="bg-gradient-to-br from-[#1E1E2E] to-[#12121A] rounded-3xl p-6 border border-white/10 shadow-[0_20px_50px_rgba(0,0,0,0.3)] text-white">
      <div className="flex flex-wrap items-center justify-between gap-3 mb-4">
        <h3 className="text-lg font-bold flex items-center gap-2"><Gauge className="w-5 h-5 text-indigo-300" /> Severity checks</h3>
        <div className="flex items-center gap-2 text-xs text-white/60">
          {data && <span>As of {data.as_of_local}</span>}
          <button onClick={load} aria-label="Refresh severity checks" className="p-1.5 rounded-lg hover:bg-white/10"><RefreshCw className="w-4 h-4" /></button>
        </div>
      </div>
      {failed && !data && <div className="text-sm text-white/60">Severity checks: Unavailable</div>}
      {data && review && (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div className="bg-white/5 rounded-2xl border border-white/10 p-4 min-w-0">
            <div className="text-[11px] text-white/50 uppercase tracking-widest font-bold mb-2">
              Review capacity · {review.ready_reviews ?? 0} ready · internal cutoff {review.internal_cutoff}
            </div>
            <button onClick={() => setOpen('review')} title="Why this level?"
              className={`inline-flex items-center px-3 py-1.5 rounded-lg border text-sm font-bold text-left hover:brightness-125 ${toneClass(review.tone, review.severity)}`}>
              {review.label}
            </button>
            {review.assumption && <div className="text-xs text-white/60 mt-2">{review.assumption}</div>}
            {data.roster.length > 0 && (
              <div className="text-xs text-white/50 mt-1">
                Roster: {data.roster.map((r) => `${r.worker} ${r.shift}${r.assigned ? '' : ' (not assigned)'}`).join(' · ')}
              </div>
            )}
            {data.review_capacity_preview && (
              <button onClick={() => setOpen('preview')} className="mt-2 block text-left text-xs text-amber-300 hover:underline">
                Preview with backup: {data.review_capacity_preview.label} (not applied)
              </button>
            )}
            <div className="flex flex-wrap gap-2 mt-3">
              {backups.length > 0 && (
                <button onClick={() => setPreview((p) => !p)} disabled={busy}
                  className="text-xs font-bold px-3 py-1.5 rounded-lg border border-white/15 hover:bg-white/10">
                  {preview ? 'Hide backup preview' : 'Preview backup'}
                </button>
              )}
              {backups.map((b) => (
                <button key={b.id} onClick={() => assign(b, true)} disabled={busy}
                  className="text-xs font-bold px-3 py-1.5 rounded-lg bg-indigo-500/80 hover:bg-indigo-500">
                  Assign {b.worker}
                </button>
              ))}
              {assignedBackups.map((b) => (
                <button key={b.id} onClick={() => assign(b, false)} disabled={busy}
                  className="text-xs font-bold px-3 py-1.5 rounded-lg border border-white/15 hover:bg-white/10">
                  Release {b.worker}
                </button>
              ))}
            </div>
          </div>
          <div className="bg-white/5 rounded-2xl border border-white/10 p-4 min-w-0">
            <div className="text-[11px] text-white/50 uppercase tracking-widest font-bold mb-2">
              Payer filing risk · {data.claims_evaluated} open claims, each evaluated on its own
            </div>
            {data.filing_totals.length > 0 ? (
              <div className="flex flex-wrap gap-1.5 mb-3">
                {data.filing_totals.map((t) => (
                  <span key={t.label} className={`px-2 py-1 rounded-md border text-[11px] font-bold ${toneClass(t.tone)}`}>{t.count} · {t.label}</span>
                ))}
              </div>
            ) : <div className="text-xs text-white/60 mb-2">No open claims awaiting a decision.</div>}
            {filing && (
              <>
                <div className="text-[11px] text-white/50 font-semibold mb-1">Most urgent: claim {filing.subject?.claim} · {filing.subject?.payer}</div>
                <button onClick={() => setOpen('filing')} title="Why this level?"
                  className={`inline-flex items-center px-3 py-1.5 rounded-lg border text-sm font-bold text-left hover:brightness-125 ${toneClass(filing.tone, filing.severity)}`}>
                  {filing.label}
                </button>
                {filing.assumption && <div className="text-xs text-white/60 mt-2">{filing.assumption}</div>}
              </>
            )}
          </div>
        </div>
      )}
      {shown && (
        <SeverityExplainPanel title={open === 'filing' ? 'Payer filing risk' : 'Review capacity'} result={shown} onClose={() => setOpen(null)} />
      )}
    </div>
  );
};

export default SeverityChecksCard;
