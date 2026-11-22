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
  /** Latest human decision recorded in the backend for this signal's recommendation, if any. */
  decision?: {
    decision_id: string;
    recommendation_id: string;
    decision_type: string;
    decision_timestamp?: string | null;
  } | null;
}

interface SignalState {
  events: SignalEvent[];
  isConnected: boolean;
  stats: {
    activeSignals: number;
    patientFlow: number;
    criticalEvents: number;
    actionsTaken: number;
  };
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
}

export const fetchHistoricalSignals = createAsyncThunk(
  'signals/fetchHistoricalSignals',
  async () => {
    try {
      const response = await fetchWithAuth('/api/v1/signals');
      if (!response.ok) {
        throw new Error('Failed to fetch signals');
      }
      const data = (await response.json()) as SignalEvent[];
      // Card counts cover every signal in scope, not only the listed ones.
      let summary: SignalSummary | null = null;
      try {
        const summaryRes = await fetchWithAuth('/api/v1/signals/summary');
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

/** Reloads only the card counts (cheap); used while Autopilot works through the queue. */
export const fetchSignalSummary = createAsyncThunk(
  'signals/fetchSignalSummary',
  async () => {
    const res = await fetchWithAuth('/api/v1/signals/summary');
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
        
        // Update stats based on incoming signal
        state.stats.activeSignals += 1;
        
        if (signal.type === 'EHR' || signal.type === 'Patient') {
          state.stats.patientFlow += 1;
        }
        
        if (signal.risk_level === 'Critical' || signal.risk_level === 'High') {
          state.stats.criticalEvents += 1;
        }
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
      state.stats.actionsTaken += 1;
    },
    /** Records a decision stored in the backend on the signal held in the store. */
    markSignalDecided(state, action: PayloadAction<{ id: string; decision: NonNullable<SignalEvent['decision']> }>) {
      const signal = state.events.find(e => e.id === action.payload.id);
      if (signal) signal.decision = action.payload.decision;
    },
    removeSignal(state, action: PayloadAction<string>) {
      state.events = state.events.filter(e => e.id !== action.payload);
      state.stats.activeSignals = Math.max(0, state.stats.activeSignals - 1);
    }
  },
  extraReducers: (builder) => {
    builder.addCase(fetchSignalSummary.fulfilled, (state, action) => {
      state.stats.activeSignals = action.payload.active_signals;
      state.stats.patientFlow = action.payload.patient_flow;
      state.stats.criticalEvents = action.payload.critical_events;
      state.stats.actionsTaken = action.payload.actions_taken;
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
      } else {
        const open = state.events.filter(e => !e.decision && e.status !== 'dismissed');
        state.stats.activeSignals = open.length;
        state.stats.patientFlow = state.events.filter(e => e.type === 'EHR' || e.type === 'Patient').length;
        state.stats.criticalEvents = open.filter(e => e.risk_level === 'Critical' || e.risk_level === 'High').length;
        state.stats.actionsTaken = state.events.filter(e => e.decision).length;
      }
    });
  }
});

export const { addSignal, setConnectionStatus, acknowledgeSignal, incrementActionsTaken, removeSignal, markSignalDecided } = signalSlice.actions;
export default signalSlice.reducer;
