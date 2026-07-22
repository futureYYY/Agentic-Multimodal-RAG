/**
 * 轮询 Hook
 */

import { useEffect, useRef, useCallback } from 'react';

interface UsePollingOptions {
  interval?: number;
  enabled?: boolean;
}

export const usePolling = (
  callback: () => void | Promise<void>,
  options: UsePollingOptions = {}
) => {
  const { interval = 3000, enabled = true } = options;
  const savedCallback = useRef(callback);
  const timerRef = useRef<NodeJS.Timeout | null>(null);

  useEffect(() => {
    savedCallback.current = callback;
  }, [callback]);

  const stop = useCallback(() => {
    if (timerRef.current) {
      clearInterval(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  const start = useCallback(() => {
    stop();
    timerRef.current = setInterval(() => {
      savedCallback.current();
    }, interval);
  }, [interval, stop]);

  useEffect(() => {
    if (enabled) {
      start();
    } else {
      stop();
    }

    return stop;
  }, [enabled, start, stop]);

  return { start, stop };
};
