/**
 * useJobPolling — 비동기 솔버 Job 제출 + 적응형 폴링 + 취소 훅
 *
 * 폴링 전략:
 *   0~10초:  1초 간격
 *   10~30초: 3초 간격
 *   30초~:   5초 간격
 *   터미널 상태 도달 시 자동 중지
 */
import { useState, useRef, useCallback, useEffect } from 'react';
import { API_BASE_URL } from '../config';

type TerminalStatus = 'complete' | 'failed' | 'cancelled';
type JobStatus = 'idle' | 'pending' | 'running' | TerminalStatus;

export interface JobPollState {
  jobId: number | null;
  status: JobStatus;
  progress: string;
  progressPct: number;
  result: any | null;
  error: string | null;
  elapsedSec: number;
}

interface UseJobPollingReturn extends JobPollState {
  submitJob: (projectId: string, solverId: string, solverName: string, compareGroupId?: string) => Promise<number | null>;
  cancelJob: () => Promise<void>;
  reset: () => void;
}

const TERMINAL_STATUSES = new Set(['COMPLETE', 'FAILED', 'CANCELLED']);

function mapBackendStatus(s: string): JobStatus {
  switch (s) {
    case 'PENDING': return 'pending';
    case 'RUNNING': return 'running';
    case 'COMPLETE': return 'complete';
    case 'FAILED': return 'failed';
    case 'CANCELLED': return 'cancelled';
    default: return 'running';
  }
}

function getPollingInterval(elapsedSec: number): number {
  if (elapsedSec < 10) return 1000;
  if (elapsedSec < 30) return 3000;
  return 5000;
}

const INITIAL_STATE: JobPollState = {
  jobId: null,
  status: 'idle',
  progress: '',
  progressPct: 0,
  result: null,
  error: null,
  elapsedSec: 0,
};

export function useJobPolling(
  authFetch: (url: string, init?: RequestInit) => Promise<Response>,
): UseJobPollingReturn {
  const [state, setState] = useState<JobPollState>(INITIAL_STATE);

  const intervalRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const elapsedRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const jobIdRef = useRef<number | null>(null);
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      stopPolling();
    };
  }, []);

  const stopPolling = useCallback(() => {
    if (intervalRef.current) { clearTimeout(intervalRef.current); intervalRef.current = null; }
    if (elapsedRef.current) { clearInterval(elapsedRef.current); elapsedRef.current = null; }
  }, []);

  const poll = useCallback(async () => {
    const jid = jobIdRef.current;
    if (!jid || !mountedRef.current) return;

    try {
      const res = await authFetch(`${API_BASE_URL}/api/jobs/${jid}`);
      if (!res.ok) return;

      const data = await res.json();
      const mappedStatus = mapBackendStatus(data.status);

      if (!mountedRef.current) return;

      setState(prev => ({
        ...prev,
        status: mappedStatus,
        progress: data.progress || prev.progress,
        progressPct: data.progress_pct ?? prev.progressPct,
        error: data.error || null,
        result: data.result || null,
      }));

      if (TERMINAL_STATUSES.has(data.status)) {
        stopPolling();
        return;
      }

      // 적응형 폴링: 다음 폴 스케줄
      setState(prev => {
        const interval = getPollingInterval(prev.elapsedSec);
        intervalRef.current = setTimeout(poll, interval);
        return prev;
      });
    } catch {
      // 네트워크 오류 시 5초 후 재시도
      if (mountedRef.current) {
        intervalRef.current = setTimeout(poll, 5000);
      }
    }
  }, [authFetch, stopPolling]);

  const startPolling = useCallback((jobId: number) => {
    jobIdRef.current = jobId;

    // 경과 시간 카운터
    elapsedRef.current = setInterval(() => {
      if (mountedRef.current) {
        setState(prev => ({ ...prev, elapsedSec: prev.elapsedSec + 1 }));
      }
    }, 1000);

    // 첫 폴링 (1초 후)
    intervalRef.current = setTimeout(poll, 1000);
  }, [poll]);

  const submitJob = useCallback(async (
    projectId: string,
    solverId: string,
    solverName: string,
    compareGroupId?: string,
  ): Promise<number | null> => {
    stopPolling();

    setState({
      ...INITIAL_STATE,
      status: 'pending',
      progress: '대기 중',
      progressPct: 0,
    });

    try {
      const body: Record<string, any> = {
        project_id: Number(projectId),
        solver_id: solverId,
        solver_name: solverName,
      };
      if (compareGroupId) body.compare_group_id = compareGroupId;

      const res = await authFetch(`${API_BASE_URL}/api/jobs/submit`, {
        method: 'POST',
        body: JSON.stringify(body),
      });

      if (!res.ok) {
        const errText = await res.text();
        setState(prev => ({ ...prev, status: 'failed', error: errText }));
        return null;
      }

      const data = await res.json();
      const jobId = data.job_id;
      jobIdRef.current = jobId;  // 취소 시 사용할 수 있도록 즉시 설정

      setState(prev => ({
        ...prev,
        jobId,
        status: mapBackendStatus(data.status),
        progress: data.progress || '대기 중',
        progressPct: data.progress_pct ?? 0,
        // sync fallback인 경우 이미 결과가 올 수 있음
        result: data.result || null,
      }));

      // 이미 터미널 상태면 폴링 불필요 (sync fallback)
      if (TERMINAL_STATUSES.has(data.status)) {
        return jobId;
      }

      startPolling(jobId);
      return jobId;
    } catch (err: any) {
      setState(prev => ({ ...prev, status: 'failed', error: err.message || String(err) }));
      return null;
    }
  }, [authFetch, startPolling, stopPolling]);

  const cancelJob = useCallback(async () => {
    const jid = jobIdRef.current;
    if (!jid) return;

    stopPolling();
    try {
      const res = await authFetch(`${API_BASE_URL}/api/jobs/${jid}`, { method: 'DELETE' });
      if (mountedRef.current) {
        if (res.ok || res.status === 409) {
          // 409 = 이미 터미널 상태 (COMPLETE/FAILED) — 취소 성공으로 간주
          setState(prev => ({ ...prev, status: 'cancelled', progress: '사용자 취소' }));
        }
      }
    } catch {
      // 네트워크 에러 시에도 UI는 취소 상태로 전환
      if (mountedRef.current) {
        setState(prev => ({ ...prev, status: 'cancelled', progress: '취소 요청됨' }));
      }
    }
  }, [authFetch, stopPolling]);

  const reset = useCallback(() => {
    stopPolling();
    jobIdRef.current = null;
    setState(INITIAL_STATE);
  }, [stopPolling]);

  return { ...state, submitJob, cancelJob, reset };
}
