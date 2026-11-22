import { useEffect } from 'react';
import { useDispatch } from 'react-redux';
import { addSignal, setConnectionStatus } from '@/store/slices/signalSlice';
import { getBackendWsBaseUrl } from '@/utils/wsUrl';

export const useSignalStream = () => {
  const dispatch = useDispatch();

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
      } catch (err) {
        console.error("Failed to parse signal message:", err);
      }
    };

    ws.onclose = () => {
      dispatch(setConnectionStatus(false));
      // Basic reconnect logic could go here
    };

    return () => {
      ws.close();
    };
  }, [dispatch]);
};
