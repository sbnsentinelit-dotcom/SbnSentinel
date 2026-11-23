import { createSlice, PayloadAction, createAsyncThunk } from '@reduxjs/toolkit';

export interface SignalEvent {
  id: string;
  source: string;
  type: string;
  message: string;
  timestamp: string;
  metadata?: any;
  risk_level?: string;
  problem?: string;
  reason?: string;
  business_impact?: string;
  recommended_action?: string;
  expected_outcome?: string;
  explainability_log?: string;
  priority_score?: number;
  primary_context?: string;
  secondary_context?: string;
  context_validation_status?: string;
  context_reason?: string;
  revenue_risk_category?: string;
  estimated_financial_exposure?: string;
  exposure_amount?: number;
  currency?: string;
  revenue_impact_status?: string;
  operational_dependency?: string;
  ai_insight?: string;
  status?: 'active' | 'acknowledged' | 'dismissed' | 'expired' | 'superseded' | 'APPROVED' | 'REJECTED';
  decision_status?: string;
  correlation_id?: string;
  /** Risk level evaluation from the source data (level, reason, deadline, autopilot eligibility). */
  risk_evaluation?: {
    level: string;
    reason?: string;
    deadline?: string | null;
    time_left?: string | null;
    autopilot_eligible?: boolean;
    autopilot_action?: 'resubmit_claim' | 'permitted_action' | null;
    [key: string]: unknown;
  } | null;
  /** Latest human decision recorded in the backend for this signal's recommendation, if any. */
  decision?: {
    decision_id: string;
    recommendation_id: string;
    decision_type: string;
    decision_timestamp?: string | null;
  } | null;
}

export interface SignalStats {
  activeSignals: number | 'Unavailable';
  patientFlow: number | 'Unavailable';
  criticalEvents: number | 'Unavailable';
  actionsTaken: number | 'Unavailable';
  trend?: string;
  trendUp?: boolean;
  /** Per-card change against the earlier cutoff, from the server summary (null trend = unavailable). */
  cardTrends?: Record<string, { trend: string | null; trend_up: boolean | null; trend_good: boolean | null }>;
  lastUpdated?: string;
  isStale?: boolean;
}

interface SignalState {
  events: SignalEvent[];
  isConnected: boolean;
  stats: SignalStats;
}

const defaultInitialSignals: SignalEvent[] = [];

const initialState: SignalState = {
  events: defaultInitialSignals,
  isConnected: false,
  stats: {
    activeSignals: 0,
    patientFlow: 0,
    criticalEvents: 0,
    actionsTaken: 0,
    trend: '0%',
    trendUp: true,
    lastUpdated: undefined,
    isStale: false,
  }
};

import { fetchWithAuth } from '@/utils/fetchWithAuth';

const MAX_KEPT_EVENTS = 150;
const NO_ACTION = new Set(['', 'None required.', 'Monitor progress.', 'NO_RECOMMENDATION']);
const isOpenWithRecommendation = (e: SignalEvent) =>
  !NO_ACTION.has(e.recommended_action || '') && !e.decision && e.status !== 'dismissed';

export interface SignalSummary {
  active_signals: number;
  patient_flow: number;
  critical_events: number;
  actions_taken: number;
  prior_active_signals?: number;
  trend?: string;
  trend_up?: boolean;
  server_timestamp?: string;
  reporting_cutoff?: string;
  clinic_timestamp?: string;
  clinic_timezone?: string;
  awaiting_decision?: number;
  critical_awaiting?: number;
  card_trends?: SignalStats['cardTrends'];
}

export const fetchHistoricalSignals = createAsyncThunk(
  'signals/fetchHistoricalSignals',
  async (params?: { provider?: string; date_range?: string; cutoff?: string }) => {
    try {
      const q = new URLSearchParams();
      if (params?.provider && params.provider !== 'All Providers') q.set('provider', params.provider);
      if (params?.date_range) q.set('date_range', params.date_range);
      if (params?.cutoff) q.set('cutoff', params.cutoff);
      const qs = q.toString() ? `?${q.toString()}` : '';

      const response = await fetchWithAuth(`/api/v1/signals${qs}`);
      if (!response.ok) {
        throw new Error('Failed to fetch signals');
      }
      const data = (await response.json()) as SignalEvent[];
      // Card counts cover every signal in scope, not only the listed ones.
      let summary: SignalSummary | null = null;
      try {
        const summaryRes = await fetchWithAuth(`/api/v1/signals/summary${qs}`);
        if (summaryRes.ok) summary = await summaryRes.json();
      } catch {
        summary = null;
      }
      return { events: data, summary };
    } catch (error) {
      console.error("Error fetching signals:", error);
      throw error;
    }
  }
);

/** Reloads only the card counts (cheap); used while Autopilot works through the queue or on live updates. */
export const fetchSignalSummary = createAsyncThunk(
  'signals/fetchSignalSummary',
  async (params?: { provider?: string; date_range?: string; cutoff?: string }) => {
    const q = new URLSearchParams();
    if (params?.provider && params.provider !== 'All Providers') q.set('provider', params.provider);
    if (params?.date_range) q.set('date_range', params.date_range);
    if (params?.cutoff) q.set('cutoff', params.cutoff);
    const qs = q.toString() ? `?${q.toString()}` : '';

    const res = await fetchWithAuth(`/api/v1/signals/summary${qs}`);
    if (!res.ok) throw new Error('Failed to fetch signal summary');
    return (await res.json()) as SignalSummary;
  }
);

const signalSlice = createSlice({
  name: 'signals',
  initialState,
  reducers: {
    addSignal(state, action: PayloadAction<SignalEvent>) {
      const signal = action.payload;
      // Prevent duplicates
      if (!state.events.find(e => e.id === signal.id)) {
        state.events.unshift(signal);
        // Trim the oldest items that need no action; open signals with a recommendation stay.
        while (state.events.length > MAX_KEPT_EVENTS) {
          let idx = -1;
          for (let i = state.events.length - 1; i >= 0; i--) {
            if (!isOpenWithRecommendation(state.events[i])) { idx = i; break; }
          }
          if (idx === -1) break;
          state.events.splice(idx, 1);
        }
        // Local arithmetic removed per Report 01 (01.2).
        // Authoritative card counts are refreshed via backend summary.
      }
    },
    setConnectionStatus(state, action: PayloadAction<boolean>) {
      state.isConnected = action.payload;
    },
    acknowledgeSignal: (state, action: PayloadAction<string>) => {
      const signal = state.events.find(s => s.id === action.payload);
      if (signal) {
        signal.status = 'acknowledged';
      }
    },
    incrementActionsTaken(state) {
      if (typeof state.stats.actionsTaken === 'number') {
        state.stats.actionsTaken += 1;
      }
    },
    /** Records a decision stored in the backend on the signal held in the store. */
    markSignalDecided(state, action: PayloadAction<{ id: string; decision: NonNullable<SignalEvent['decision']> }>) {
      const signal = state.events.find(e => e.id === action.payload.id);
      if (signal) signal.decision = action.payload.decision;
    },
    removeSignal(state, action: PayloadAction<string>) {
      state.events = state.events.filter(e => e.id !== action.payload);
      // Local arithmetic removed per Report 01 (01.2).
    }
  },
  extraReducers: (builder) => {
    builder.addCase(fetchSignalSummary.fulfilled, (state, action) => {
      state.stats.activeSignals = action.payload.active_signals;
      state.stats.patientFlow = action.payload.patient_flow;
      state.stats.criticalEvents = action.payload.critical_events;
      state.stats.actionsTaken = action.payload.actions_taken;
      state.stats.trend = action.payload.trend;
      state.stats.trendUp = action.payload.trend_up;
      state.stats.cardTrends = action.payload.card_trends;
      state.stats.lastUpdated = action.payload.clinic_timestamp || action.payload.server_timestamp;
      state.stats.isStale = false;
    });
    builder.addCase(fetchSignalSummary.rejected, (state) => {
      if (state.stats.activeSignals === undefined || state.stats.activeSignals === null) {
        state.stats.activeSignals = 'Unavailable';
      }
      state.stats.isStale = true;
    });
    builder.addCase(fetchHistoricalSignals.fulfilled, (state, action) => {
      // The backend list is authoritative: replace local state even when it is empty.
      state.events = [...(action.payload?.events || [])];
      const summary = action.payload?.summary;
      if (summary) {
        state.stats.activeSignals = summary.active_signals;
        state.stats.patientFlow = summary.patient_flow;
        state.stats.criticalEvents = summary.critical_events;
        state.stats.actionsTaken = summary.actions_taken;
        state.stats.trend = summary.trend;
        state.stats.trendUp = summary.trend_up;
        state.stats.cardTrends = summary.card_trends;
        state.stats.lastUpdated = summary.clinic_timestamp || summary.server_timestamp;
        state.stats.isStale = false;
      }
      // Without a server summary the cards keep their last confirmed values (marked by
      // lastUpdated); the loaded list is never counted, it does not hold the full scope.
    });
  }
});

export const { addSignal, setConnectionStatus, acknowledgeSignal, incrementActionsTaken, removeSignal, markSignalDecided } = signalSlice.actions;
export default signalSlice.reducer;
