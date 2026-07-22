/**
 * 加载状态 Hook
 */

import { useState, useCallback } from 'react';
import type { LoadingState } from '@/types';

interface UseLoadingReturn {
  loading: boolean;
  loadingState: LoadingState;
  setLoading: (loading: boolean) => void;
  setLoadingState: (state: LoadingState) => void;
  withLoading: <T>(fn: () => Promise<T>) => Promise<T>;
}

export const useLoading = (initialState: boolean = false): UseLoadingReturn => {
  const [loading, setLoading] = useState(initialState);
  const [loadingState, setLoadingState] = useState<LoadingState>(
    initialState ? 'loading' : 'idle'
  );

  const withLoading = useCallback(async <T>(fn: () => Promise<T>): Promise<T> => {
    setLoading(true);
    setLoadingState('loading');
    try {
      const result = await fn();
      setLoadingState('success');
      return result;
    } catch (error) {
      setLoadingState('error');
      throw error;
    } finally {
      setLoading(false);
    }
  }, []);

  return {
    loading,
    loadingState,
    setLoading,
    setLoadingState,
    withLoading,
  };
};
