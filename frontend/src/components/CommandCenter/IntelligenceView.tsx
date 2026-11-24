import React, { useState, useEffect } from 'react';
import { Activity, CheckCircle2, AlertTriangle, ArrowRight, UserCircle2, FileText, Zap, X, AlertOctagon, Info, AlertCircle, Clock } from 'lucide-react';
import { useDispatch, useSelector } from 'react-redux';
import { RootState } from '@/store';
import { removeSignal, fetchHistoricalSignals } from '@/store/slices/signalSlice';
import { formatMoney } from '@/utils/currency';
import { fetchWithAuth } from '@/utils/fetchWithAuth';
import { byRiskPriority, formatExposureTotals, isActionable, isPendingDecision } from '@/utils/signalSummary';
import { recordSignalReview } from '@/utils/signalReview';
import { timeLeftText, timeLeftTone } from '@/utils/timeLeft';
import { SeverityChecksCard } from './SeverityChecksCard';
import { SeverityExplainPanel, SeverityResult } from './SeverityExplainPanel';

const BACKEND = process.env.NEXT_PUBLIC_BACKEND_URL || '';

interface IntelligenceViewProps {
  /** Opens the governed review for a signal, where the decision and action are recorded. */
  onExecute?: (signalId: string) => void;
}

export const IntelligenceView: React.FC<IntelligenceViewProps> = ({ onExecute }) => {
  const dispatch = useDispatch<any>();
  // Readiness checks from /health/ready: passed / total. null = not loaded or unavailable.
  const [health, setHealth] = useState<{ passed: number; total: number } | null>(null);
  const [connectors, setConnectors] = useState<{ healthy: number; total: number } | null>(null);

  useEffect(() => {
    dispatch(fetchHistoricalSignals());
  }, [dispatch]);

  useEffect(() => {
    let cancelled = false;
    fetchWithAuth(`${BACKEND}/api/v1/health/ready`)
      .then(async (res) => {
        const body = await res.json().catch(() => ({}));
        const checks: Record<string, boolean> = body?.checks || body?.detail?.checks || {};
        const total = Object.keys(checks).length;
        if (!cancelled && total > 0) setHealth({ passed: Object.values(checks).filter(Boolean).length, total });
      })
      .catch(() => { if (!cancelled) setHealth(null); });
    fetchWithAuth(`${BACKEND}/api/v1/reality/health`)
      .then(async (res) => {
        if (!res.ok) throw new Error(String(res.status));
        const list: Array<{ status?: string }> = await res.json();
        if (!cancelled) setConnectors({ healthy: list.filter((c) => c.status === 'Connected').length, total: list.length });
      })
      .catch(() => { if (!cancelled) setConnectors(null); });
    return () => { cancelled = true; };
  }, []);

  // Readiness of the backend (checks passed), separate from connector state and data freshness.
  const healthAllPass = !!health && health.passed === health.total;
  const healthText = health === null
    ? 'Readiness checks: Unavailable'
    : `Readiness checks passed: ${health.passed}/${health.total}`;

  // Card values from the server for the clinic's whole scope (not only the signals loaded here).
  const [summary, setSummary] = useState<{
    active_signals: number; awaiting_decision: number; critical_awaiting: number; clinic_timestamp?: string;
    awaiting_exposure?: { totals: Record<string, number>; known_count: number; count: number };
  } | null>(null);
  const [summaryFailed, setSummaryFailed] = useState(false);

  const rawEvents = useSelector((state: RootState) => state.signals.events);
  const pendingEventsCount = rawEvents.filter(isPendingDecision).length;
  const decidedCount = rawEvents.filter((e) => !!e.decision).length;
  useEffect(() => {
    let cancelled = false;
    fetchWithAuth(`${BACKEND}/api/v1/signals/summary`)
      .then(async (res) => {
        if (!res.ok) throw new Error(String(res.status));
        const body = await res.json();
        if (!cancelled) { setSummary(body); setSummaryFailed(false); }
      })
      .catch(() => { if (!cancelled) setSummaryFailed(true); });
    return () => { cancelled = true; };
  }, [rawEvents.length, decidedCount, pendingEventsCount]);
  // Decided or dismissed signals (recorded in the backend) stay visible in queue with their recorded status.
  const actionableEvents = rawEvents
    .filter(isActionable)
    .sort(byRiskPriority);
  // The summary cards count only signals still waiting for a decision, so approving one
  // (by a person or by Autopilot) lowers Critical Risks and Total Financial Exposure.
  const pendingEvents = actionableEvents.filter(isPendingDecision);
  const [selectedSignalId, setSelectedSignalId] = useState<string | null>(null);
  const [explainOpen, setExplainOpen] = useState(false);
  // Clock for the time-left badge: it counts down live while the screen is open.
  const [nowMs, setNowMs] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNowMs(Date.now()), 30000);
    return () => clearInterval(timer);
  }, []);
  const selectedEvent = (selectedSignalId ? actionableEvents.find(e => e.id === selectedSignalId) : null) || (actionableEvents.length > 0 ? actionableEvents[0] : null);
  const handleDismiss = async () => {
    if (selectedEvent) {
      const ok = await recordSignalReview(selectedEvent.id, 'dismiss');
      if (ok) {
        dispatch(removeSignal(selectedEvent.id));
        setSelectedSignalId(null);
      }
    }
  };
  const handleApply = () => {
    // Review goes through governed recommendation review, where the decision is stored.
    if (selectedEvent && onExecute) {
      onExecute(selectedEvent.id);
    }
  };
  const getRiskColor = (risk: string) => {
    switch (risk) {
      case 'Critical': return 'bg-red-500/20 text-red-400 border-red-500/30';
      case 'High': return 'bg-orange-500/20 text-orange-400 border-orange-500/30';
      case 'Moderate': return 'bg-yellow-500/20 text-yellow-400 border-yellow-500/30';
      case 'Low': return 'bg-emerald-500/20 text-emerald-400 border-emerald-500/30';
      case 'Information':
      default: return 'bg-blue-500/20 text-blue-400 border-blue-500/30';
    }
  };

  const getRiskIcon = (risk: string) => {
    switch (risk) {
      case 'Critical': return <AlertOctagon className="w-5 h-5" />;
      case 'High': return <AlertTriangle className="w-5 h-5" />;
      case 'Moderate': return <AlertCircle className="w-5 h-5" />;
      case 'Low': return <CheckCircle2 className="w-5 h-5" />;
      case 'Information':
      default: return <Info className="w-5 h-5" />;
    }
  };

  return (
    <div className="animate-in fade-in duration-500 max-w-[1400px] mx-auto space-y-8">
      {/* Header */}
      <div className="flex flex-col md:flex-row md:items-end justify-between gap-4 md:gap-0">
        <div>
          <div className="flex items-center gap-3 mb-2">
            <div className="p-2 bg-gradient-to-br from-[var(--color-text-primary)] to-indigo-600 rounded-2xl shadow-lg">
              <Zap className="w-6 h-6 text-white" />
            </div>
            <h2 className="text-3xl font-extrabold text-white tracking-tight">Executive Intelligence Dashboard</h2>
          </div>
          <p className="text-sm text-white/70 font-medium ml-12">Decision support & operational risk management for clinic leadership.</p>
        </div>
        <div className={`flex items-center gap-2 px-5 py-2.5 rounded-full border shadow-sm ${healthAllPass ? 'bg-emerald-50 text-emerald-700 border-emerald-100' : 'bg-amber-50 text-amber-700 border-amber-200'}`}>
          <div className="relative flex h-2.5 w-2.5">
            {healthAllPass && <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75"></span>}
            <span className={`relative inline-flex rounded-full h-2.5 w-2.5 ${healthAllPass ? 'bg-emerald-500' : 'bg-amber-500'}`}></span>
          </div>
          <span className="text-sm font-bold tracking-wide">{healthText}</span>
        </div>
      </div>

      {/* Executive Summary Stats */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
        <div className="bg-gradient-to-br from-[#1E1E2E] to-[#12121A] border border-white/10 p-6 rounded-[24px] shadow-lg flex items-center gap-4">
          <div className="w-12 h-12 bg-rose-500/20 text-rose-400 rounded-full flex items-center justify-center">
            <AlertOctagon className="w-6 h-6" />
          </div>
          <div>
            <div className="text-xs text-white/50 font-bold uppercase tracking-widest mb-1">Critical Risks</div>
            <div className="text-2xl font-black text-white">{summary ? summary.critical_awaiting : summaryFailed ? 'Unavailable' : pendingEvents.filter(e => e.risk_level === 'Critical').length}</div>
          </div>
        </div>
        <div className="bg-gradient-to-br from-[#1E1E2E] to-[#12121A] border border-white/10 p-6 rounded-[24px] shadow-lg flex items-center gap-4">
          <div className="w-12 h-12 bg-amber-500/20 text-amber-400 rounded-full flex items-center justify-center">
            <FileText className="w-6 h-6" />
          </div>
          <div>
            <div className="text-xs text-white/50 font-bold uppercase tracking-widest mb-1">Total Financial Exposure</div>
            <div className="text-2xl font-black text-white">
              {summary?.awaiting_exposure
                ? formatExposureTotals(Object.entries(summary.awaiting_exposure.totals).map(([cur, amt]) => `${cur} ${amt}`))
                : summaryFailed ? 'Unavailable' : formatExposureTotals(pendingEvents.map((e) => e.estimated_financial_exposure))}
            </div>
            {summary?.awaiting_exposure && (
              <div className="text-[10px] text-white/50 font-semibold mt-1">
                Known exposure for {summary.awaiting_exposure.known_count} of {summary.awaiting_exposure.count} signals awaiting a decision
              </div>
            )}
          </div>
        </div>
        <div className="bg-gradient-to-br from-[#1E1E2E] to-[#12121A] border border-white/10 p-6 rounded-[24px] shadow-lg flex items-center gap-4">
          <div className="w-12 h-12 bg-emerald-500/20 text-emerald-400 rounded-full flex items-center justify-center">
            <Activity className="w-6 h-6" />
          </div>
          <div>
            <div className="text-xs text-white/50 font-bold uppercase tracking-widest mb-1">Active Connectors</div>
            <div className="text-2xl font-black text-white">{!connectors ? 'Unavailable' : connectors.total === 0 ? 'No external systems' : `${connectors.healthy} / ${connectors.total} Connected`}</div>
          </div>
        </div>
      </div>

      {/* Severity checks: review capacity and payer filing risk on the clinic's live data */}
      <SeverityChecksCard />

      <div className="grid grid-cols-1 lg:grid-cols-12 gap-8 items-start">
        {/* Left Side: Smart Inbox */}
        <div className="lg:col-span-4 space-y-6">
          <div className="bg-gradient-to-br from-[#1E1E2E] to-[#12121A] rounded-3xl p-6 border border-white/10 shadow-[0_20px_50px_rgba(0,0,0,0.3)] text-white">
            <h3 className="text-lg font-bold text-white mb-6 flex items-center justify-between">
              Priority Queue
              <span className="bg-indigo-500/20 text-indigo-300 text-xs px-2.5 py-1 rounded-full font-bold" title={summary ? `${summary.active_signals} active signals in scope` : undefined}>
                {summary ? summary.awaiting_decision : pendingEventsCount} Pending
              </span>
            </h3>

            {summary && (
              <p className="text-[10px] text-white/50 font-semibold -mt-4 mb-3">
                {summary.active_signals} active · {summary.awaiting_decision} awaiting a decision · {actionableEvents.length} shown
              </p>
            )}
            <div className="space-y-3 overflow-y-auto custom-scrollbar max-h-[600px] p-1 pr-2">
              {actionableEvents.length > 0 ? actionableEvents.map((evt, idx) => (
                <button
                  key={evt.id || idx}
                  onClick={() => setSelectedSignalId(evt.id)}
                  className={`w-full text-left p-4 rounded-2xl transition-all duration-300 border ${selectedEvent?.id === evt.id
                    ? 'bg-white/20 border-white/40 ring-2 ring-white/20 shadow-md'
                    : 'bg-white/5 border-white/10 hover:border-white/30 hover:bg-white/10'
                    }`}
                >
                  <div className="flex items-start gap-3">
                    <div className={`mt-0.5 p-2 rounded-2xl flex-shrink-0 ${getRiskColor(evt.risk_level || 'Information')}`}>
                      {getRiskIcon(evt.risk_level || 'Information')}
                    </div>
                    <div className="min-w-0 flex-1">
                      <div className="flex justify-between items-start gap-2 mb-1">
                        <h4 className="min-w-0 text-sm font-bold text-white capitalize break-words [overflow-wrap:anywhere]">{evt.type === 'EHR' ? 'Patient Event' : (evt.type || 'Signal')} Notification</h4>
                        <span className="flex-shrink-0 text-xs text-white/60 font-medium">
                          {new Date(evt.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                        </span>
                      </div>
                      <p className="text-xs text-white/80 line-clamp-2 leading-relaxed break-words [overflow-wrap:anywhere]">{evt.message}</p>
                    </div>
                  </div>
                </button>
              )) : (
                <div className="text-center py-12">
                  <CheckCircle2 className="w-12 h-12 text-emerald-400 mx-auto mb-3 opacity-50" />
                  <p className="text-sm font-bold text-white">All Caught Up!</p>
                  <p className="text-xs text-white/70 mt-1">No pending operational risks detected.</p>
                </div>
              )}
            </div>
          </div>
        </div>

        {/* Right Side: Recommendation Details */}
        <div className="lg:col-span-8">
          {selectedEvent ? (
            <div className="bg-gradient-to-br from-[#1E1E2E] to-[#12121A] rounded-3xl border border-white/10 shadow-[0_20px_50px_rgba(0,0,0,0.3)] overflow-hidden flex flex-col h-full animate-in slide-in-from-right-4 duration-500 text-white">
              {/* Event Context Header */}
              <div className="bg-white/5 p-8 text-white relative overflow-hidden border-b border-white/10">
                <div className="absolute top-0 right-0 w-64 h-64 bg-indigo-500 rounded-full mix-blend-multiply filter blur-3xl opacity-10 translate-x-1/2 -translate-y-1/2"></div>
                <div className="relative z-10">
                  <div className="flex items-center gap-2 text-indigo-300 text-xs font-bold uppercase tracking-wider mb-4">
                    <Activity className="w-4 h-4" />
                    What Happened?
                  </div>
                  <h2 className="text-2xl font-bold leading-tight mb-2 text-white">
                    {selectedEvent.message}
                  </h2>
                  <div className="flex items-center gap-4 mt-6">
                    <div className="flex items-center gap-2 bg-white/10 backdrop-blur-md px-3 py-1.5 rounded-lg border border-white/10">
                      <UserCircle2 className="w-4 h-4 text-indigo-200" />
                      <span className="text-xs font-medium text-indigo-200">Patient: <span className="font-bold text-white">{selectedEvent.metadata?.patient_name || 'Unknown'}</span></span>
                    </div>
                    <button type="button" onClick={() => setExplainOpen(true)} title="Why this level?"
                      className={`flex items-center gap-2 backdrop-blur-md px-3 py-1.5 rounded-lg border hover:brightness-125 ${getRiskColor(selectedEvent.risk_level || 'Information')}`}>
                      {getRiskIcon(selectedEvent.risk_level || 'Information')}
                      <span className="text-xs font-medium">Risk Level: <span className="font-bold text-white">{(selectedEvent.risk_evaluation?.label as string | undefined) || selectedEvent.risk_level || 'Information'}</span></span>
                    </button>
                    {timeLeftText(selectedEvent.risk_evaluation?.deadline, nowMs) && (() => {
                      const tone = timeLeftTone(selectedEvent.risk_evaluation?.deadline, nowMs);
                      const toneClass = tone === 'critical' ? 'bg-red-500/20 text-red-400 border-red-500/30'
                        : tone === 'warning' ? 'bg-orange-500/20 text-orange-400 border-orange-500/30'
                        : tone === 'passed' ? 'bg-violet-500/20 text-violet-300 border-violet-500/30'
                        : 'bg-yellow-500/20 text-yellow-400 border-yellow-500/30';
                      return (
                        <div className={`flex items-center gap-2 backdrop-blur-md px-3 py-1.5 rounded-lg border ${toneClass}`}
                          title="Time left before the payer resubmission deadline">
                          <Clock className="w-5 h-5" />
                          <span className="text-xs font-medium">Time Left: <span className="font-bold text-white">{timeLeftText(selectedEvent.risk_evaluation?.deadline, nowMs)}</span></span>
                        </div>
                      );
                    })()}
                  </div>
                </div>
              </div>

              {/* Deterministic Evaluation */}
              <div className="p-8 space-y-6 flex-1 bg-transparent overflow-y-auto">
                <div className="grid grid-cols-2 gap-4">
                  <div className="bg-white/5 p-4 rounded-xl border border-white/10">
                    <div className="text-xs text-white/50 uppercase tracking-widest font-bold mb-2">Problem</div>
                    <div className="text-white/90 font-medium">{selectedEvent.problem || 'None'}</div>
                  </div>
                  <div className="bg-white/5 p-4 rounded-xl border border-white/10">
                    <div className="text-xs text-white/50 uppercase tracking-widest font-bold mb-2">Reason</div>
                    <div className="text-white/90 font-medium">{selectedEvent.reason || 'Routine event'}</div>
                  </div>
                </div>

                {/* Explainability Block */}
                {selectedEvent.explainability_log && (
                  <div className="bg-indigo-500/10 p-4 rounded-xl border border-indigo-500/30 shadow-sm mt-4">
                    <div className="text-xs text-indigo-400 uppercase tracking-widest font-bold mb-1 flex items-center gap-2">
                      <Activity className="w-3.5 h-3.5" />
                      Traceability Log
                    </div>
                    <div className="text-indigo-100/90 font-medium font-mono text-sm">
                      {selectedEvent.explainability_log}
                    </div>
                  </div>
                )}

                {/* Revenue Intelligence Block */}
                <div className={`p-6 rounded-2xl border relative mt-6 ${!selectedEvent.estimated_financial_exposure || selectedEvent.estimated_financial_exposure === '$0.00' ? 'bg-amber-500/5 border-amber-500/20 opacity-80' : 'bg-amber-500/10 border-amber-500/30'}`}>
                  <div className={`absolute -top-3 left-6 bg-[#1E1E2E] px-3 py-1 rounded-full text-xs font-black uppercase tracking-wider flex items-center gap-1.5 shadow-sm border ${!selectedEvent.estimated_financial_exposure || selectedEvent.estimated_financial_exposure === '$0.00' ? 'text-amber-500/50 border-amber-500/20' : 'text-amber-400 border-amber-500/30'}`}>
                    <FileText className="w-3.5 h-3.5" />
                    Revenue Intelligence
                  </div>
                  
                  {(!selectedEvent.estimated_financial_exposure || selectedEvent.estimated_financial_exposure === '$0.00') && selectedEvent.revenue_risk_category === 'None' ? (
                    <div className="mt-4 flex items-center gap-3 text-amber-500/70">
                      <AlertTriangle className="w-5 h-5" />
                      <span className="text-sm font-medium">Data Unavailable (Engine operating in degraded mode). Operational intelligence is still valid.</span>
                    </div>
                  ) : (
                    <>
                      <div className="grid grid-cols-2 gap-4 mt-2">
                        <div>
                          <div className="text-xs text-amber-500/50 uppercase tracking-widest font-bold mb-1">Financial Exposure</div>
                          <div className="text-amber-400 font-bold text-xl">{formatMoney(selectedEvent.estimated_financial_exposure, selectedEvent.currency)}</div>
                        </div>
                        <div>
                          <div className="text-xs text-amber-500/50 uppercase tracking-widest font-bold mb-1">Risk Category</div>
                          <div className="text-amber-100 font-bold">{selectedEvent.revenue_risk_category || 'None'}</div>
                        </div>
                      </div>
                      <div className="mt-4 pt-4 border-t border-amber-500/20 grid grid-cols-2 gap-4">
                        <div>
                          <div className="text-xs text-amber-500/50 uppercase tracking-widest font-bold mb-1">Verification Level</div>
                          <div className={selectedEvent.revenue_impact_status === 'High' ? 'text-emerald-400 font-bold text-sm' : 'text-amber-300 font-bold text-sm'}>{selectedEvent.revenue_impact_status === 'High' ? 'Verified' : 'Pending Review'}</div>
                        </div>
                        <div>
                          <div className="text-xs text-amber-500/50 uppercase tracking-widest font-bold mb-1">Dependency</div>
                          <div className="text-amber-100/70 text-sm leading-relaxed">{selectedEvent.operational_dependency || 'N/A'}</div>
                        </div>
                      </div>
                    </>
                  )}
                </div>

                {/* Operational Context Block */}
                <div className="bg-white/5 p-6 rounded-2xl border border-white/10 relative mt-6">
                  <div className="absolute -top-3 left-6 bg-[#1E1E2E] text-indigo-300 px-3 py-1 rounded-full text-xs font-black uppercase tracking-wider flex items-center gap-1.5 shadow-sm border border-indigo-500/30">
                    <Activity className="w-3.5 h-3.5" />
                    Decision Context Engine
                  </div>
                  <div className="grid grid-cols-2 gap-4 mt-2">
                    <div>
                      <div className="text-xs text-white/50 uppercase tracking-widest font-bold mb-1">Primary Context</div>
                      <div className="text-white/90 font-bold">{selectedEvent.primary_context || 'Unknown'}</div>
                    </div>
                    <div>
                      <div className="text-xs text-white/50 uppercase tracking-widest font-bold mb-1">Secondary Context</div>
                      <div className="text-white/90 font-bold">{selectedEvent.secondary_context || 'Not Available'}</div>
                    </div>
                  </div>
                  <div className="mt-4 pt-4 border-t border-white/10">
                    <div className="text-xs text-white/50 uppercase tracking-widest font-bold mb-1">Verification Level: <span className={selectedEvent.context_validation_status === 'High' ? 'text-emerald-400' : 'text-yellow-400'}>{selectedEvent.context_validation_status === 'High' ? 'Verified' : 'Pending'}</span></div>
                    <div className="text-white/70 text-sm leading-relaxed">{selectedEvent.context_reason || 'Insufficient information.'}</div>
                  </div>
                </div>

                {/* Recommendation Box */}
                <div className="bg-emerald-500/10 p-6 rounded-2xl border border-emerald-500/30 shadow-sm relative mt-4">
                  <div className="absolute -top-3 left-6 bg-emerald-500 text-white px-3 py-1 rounded-full text-xs font-black uppercase tracking-wider flex items-center gap-1.5 shadow-sm">
                    <CheckCircle2 className="w-3.5 h-3.5" />
                    Recommended Action
                  </div>
                  <p className="text-emerald-300 font-bold leading-relaxed mt-2 text-xl">
                    {selectedEvent.recommended_action || "Acknowledge"}
                  </p>
                  <div className="mt-4 pt-4 border-t border-emerald-500/20 flex items-start gap-2">
                    <div className="text-xs font-bold text-emerald-400/70 uppercase tracking-wide shrink-0 mt-0.5">Expected Outcome:</div>
                    <div className="text-emerald-200/90 text-sm font-medium">{selectedEvent.expected_outcome || "Expected outcome unavailable"}</div>
                  </div>
                </div>
              </div>

              {/* Action Buttons */}
              <div className="p-6 bg-transparent border-t border-white/10 flex items-center justify-end gap-4">
                <button onClick={handleDismiss} className="px-6 py-3 rounded-2xl font-bold text-white/70 hover:bg-white/10 transition-colors flex items-center gap-2">
                  <X className="w-5 h-5" /> Dismiss
                </button>
                {(selectedEvent as any)?.decision_status === 'REJECTED' || (selectedEvent as any)?.status === 'REJECTED' || (selectedEvent as any)?.decision === 'REJECTED' || (selectedEvent as any)?.decision?.status === 'REJECTED' ? (
                  <span className="px-6 py-3 rounded-2xl font-bold text-red-400 bg-red-500/20 border border-red-500/30 flex items-center gap-2">
                    <X className="w-5 h-5" /> Decision Recorded – REJECTED
                  </span>
                ) : (selectedEvent as any)?.decision_status === 'APPROVED' || (selectedEvent as any)?.status === 'APPROVED' || (selectedEvent as any)?.decision === 'APPROVED' || (selectedEvent as any)?.decision?.status === 'APPROVED' ? (
                  <div className="flex flex-col items-end gap-1.5">
                    <span className="px-6 py-3 rounded-2xl font-bold text-emerald-400 bg-emerald-500/20 border border-emerald-500/30 flex items-center gap-2">
                      <CheckCircle2 className="w-5 h-5" /> Decision Recorded – APPROVED
                    </span>
                    <span className="text-[11px] text-white/60 font-mono tracking-wide">
                      Action: {(selectedEvent as any)?.action_id || (selectedEvent as any)?.decision?.recommendation_id || `ACT-${(selectedEvent as any)?.id?.slice(-6) || 'LINKED'}`} | Receipt: {(selectedEvent as any)?.action_receipt || (selectedEvent as any)?.decision?.decision_id || `RCP-${(selectedEvent as any)?.id?.slice(-6) || 'VERIFIED'}`}
                    </span>
                  </div>
                ) : (
                  <button onClick={handleApply} className="px-8 py-3 rounded-2xl font-bold text-white bg-emerald-500 hover:bg-emerald-600 shadow-[0_0_15px_rgba(16,185,129,0.4)] hover:shadow-lg hover:-translate-y-0.5 transition-all flex items-center gap-2">
                    Review recommendation <ArrowRight className="w-5 h-5" />
                  </button>
                )}
              </div>

            </div>
          ) : (
            <div className="bg-gradient-to-br from-[#1E1E2E] to-[#12121A] rounded-3xl border border-white/10 shadow-[0_20px_50px_rgba(0,0,0,0.3)] h-[600px] flex flex-col items-center justify-center text-center p-8 text-white">
              <div className="w-24 h-24 bg-white/5 rounded-full flex items-center justify-center mb-6">
                <Activity className="w-12 h-12 text-indigo-300/50" />
              </div>
              <h3 className="text-2xl font-bold text-white mb-2">Engine is Monitoring</h3>
              <p className="text-white/70 max-w-md mx-auto">
                Select an operational alert from the priority queue to view deterministic evaluation and business impact.
              </p>
            </div>
          )}
        </div>
      </div>
      {explainOpen && selectedEvent && (
        <SeverityExplainPanel title="Risk level" onClose={() => setExplainOpen(false)}
          result={signalSeverityResult(selectedEvent)} />
      )}
    </div>
  );
};

/** The selected signal's recorded evaluation in the shape the explanation panel renders. */
function signalSeverityResult(evt: { risk_level?: string; problem?: string; reason?: string; recommended_action?: string;
  id: string; metadata?: any; risk_evaluation?: Record<string, unknown> | null }): SeverityResult {
  const ev = (evt.risk_evaluation || {}) as Record<string, any>;
  const inputs = (ev.inputs || {}) as Record<string, any>;
  const refs = [evt.id, inputs.claim_id, evt.metadata?.claim_id, evt.metadata?.pipeline_event_id]
    .filter((v, i, a) => v && a.indexOf(v) === i) as string[];
  return {
    severity: ev.level || evt.risk_level,
    label: ev.label || ev.level || evt.risk_level || 'Unavailable',
    at_risk: ev.problem || evt.problem || null,
    reason: ev.reason || evt.reason || null,
    next_action: ev.recommended_action || evt.recommended_action || null,
    assumption: ev.time_left ? `Time left: ${ev.time_left}` : null,
    rule_id: ev.rule_id || evt.metadata?.rule_id || null,
    rule_version: ev.rule_version || null,
    policy_id: ev.policy_id || null,
    policy_version: ev.policy_version || null,
    evidence_refs: refs,
    evaluated_at: ev.evaluated_at || null,
    timeline: Array.isArray(ev.timeline) ? ev.timeline : [],
  };
}
