/**
 * useJobNotifier — 멀티 프로젝트 job 완료 알림
 *
 * 다른 프로젝트에서 실행 중인 solver가 완료되면 토스트 알림.
 * App 레벨에서 사용 (프로젝트 이동해도 유지).
 */

import { useCallback, useEffect, useRef, useState } from 'react';

export interface JobNotification {
  job_id: number;
  project_id: number;
  project_name: string;
  solver_name: string;
  status: string;
  completed_at: string | null;
}

export function useJobNotifier(
  authFetch: (url: string, init?: RequestInit) => Promise<Response>,
  currentProjectId?: string,
  pollIntervalMs: number = 15000,
  enabled: boolean = true,
) {
  const [notifications, setNotifications] = useState<JobNotification[]>([]);
  const seenJobIds = useRef<Set<number>>(new Set());
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const checkCompletions = useCallback(async () => {
    try {
      const res = await authFetch('/api/jobs/recent-completions?since_minutes=3');
      if (!res.ok) return;
      const jobs: JobNotification[] = await res.json();

      // 현재 프로젝트의 job은 제외 (SolverView에서 이미 처리)
      // 이미 본 job도 제외
      const newJobs = jobs.filter(j =>
        String(j.project_id) !== currentProjectId &&
        !seenJobIds.current.has(j.job_id)
      );

      if (newJobs.length > 0) {
        newJobs.forEach(j => seenJobIds.current.add(j.job_id));
        setNotifications(prev => [...prev, ...newJobs]);
      }
    } catch { /* silent */ }
  }, [authFetch, currentProjectId]);

  // 주기적 폴링 (인증 + enabled일 때만)
  useEffect(() => {
    if (!enabled) {
      if (intervalRef.current) clearInterval(intervalRef.current);
      return;
    }
    checkCompletions(); // 즉시 1회
    intervalRef.current = setInterval(checkCompletions, pollIntervalMs);
    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current);
    };
  }, [checkCompletions, pollIntervalMs, enabled]);

  // 알림 제거
  const dismissNotification = useCallback((jobId: number) => {
    setNotifications(prev => prev.filter(n => n.job_id !== jobId));
  }, []);

  const dismissAll = useCallback(() => {
    setNotifications([]);
  }, []);

  return { notifications, dismissNotification, dismissAll };
}
