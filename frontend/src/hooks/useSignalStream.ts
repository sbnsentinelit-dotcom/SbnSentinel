import { useEffect, useRef } from 'react';
import { useDispatch } from 'react-redux';
import { addSignal, setConnectionStatus, fetchSignalSummary } from '@/store/slices/signalSlice';
import type { AppDispatch } from '@/store';
import { getBackendWsBaseUrl } from '@/utils/wsUrl';

export const useSignalStream = (filterParams?: { provider?: string; date_range?: string; cutoff?: string }) => {
  const dispatch = useDispatch<AppDispatch>();
  const refreshTimeoutRef = useRef<NodeJS.Timeout | null>(null);

  useEffect(() => {
    const wsUrl = getBackendWsBaseUrl();
    // Browsers cannot set an Authorization header on a WebSocket; the session token goes in the query.
    const token = typeof window !== 'undefined' ? localStorage.getItem('token') : null;
    if (!wsUrl || !token) return;
    const ws = new WebSocket(`${wsUrl}/api/v1/signals/ws?token=${encodeURIComponent(token)}`);
    
    ws.onopen = () => {
      dispatch(setConnectionStatus(true));
    };

    ws.onmessage = (event) => {
      try {
        const signal = JSON.parse(event.data);
        dispatch(addSignal(signal));

        // Coalesce overlapping requests on incoming delivery (Report 01 §01.2)
        if (refreshTimeoutRef.current) {
          clearTimeout(refreshTimeoutRef.current);
        }
        refreshTimeoutRef.current = setTimeout(() => {
          dispatch(fetchSignalSummary(filterParams));
        }, 300);
      } catch (err) {
        console.error("Failed to parse signal message:", err);
      }
    };

    ws.onclose = () => {
      dispatch(setConnectionStatus(false));
    };

    return () => {
      if (refreshTimeoutRef.current) {
        clearTimeout(refreshTimeoutRef.current);
      }
      ws.close();
    };
  }, [dispatch, filterParams?.provider, filterParams?.date_range, filterParams?.cutoff]);
};
