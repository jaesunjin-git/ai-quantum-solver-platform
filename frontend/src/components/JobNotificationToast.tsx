/**
 * JobNotificationToast — 멀티 프로젝트 solver 완료 알림 토스트
 *
 * 다른 프로젝트의 solver가 완료되면 화면 상단에 토스트로 안내.
 */

import { CheckCircle, XCircle, X } from 'lucide-react';
import type { JobNotification } from '../hooks/useJobNotifier';

interface Props {
  notifications: JobNotification[];
  onDismiss: (jobId: number) => void;
  onNavigate?: (projectId: number) => void;
}

export function JobNotificationToast({ notifications, onDismiss, onNavigate }: Props) {
  if (notifications.length === 0) return null;

  return (
    <div className="fixed top-4 right-4 z-50 space-y-2 max-w-sm">
      {notifications.map((n) => {
        const isSuccess = n.status === 'COMPLETE';
        return (
          <div
            key={n.job_id}
            className={`flex items-start gap-3 p-3 rounded-xl border shadow-lg backdrop-blur-sm animate-in slide-in-from-right duration-300 ${
              isSuccess
                ? 'bg-green-900/90 border-green-500/30'
                : 'bg-red-900/90 border-red-500/30'
            }`}
          >
            {isSuccess
              ? <CheckCircle size={16} className="text-green-400 mt-0.5 flex-shrink-0" />
              : <XCircle size={16} className="text-red-400 mt-0.5 flex-shrink-0" />}
            <div className="flex-1 min-w-0">
              <div className="text-[12px] font-bold text-white truncate">
                {n.project_name}
              </div>
              <div className="text-[11px] text-slate-300 mt-0.5">
                {n.solver_name} — {isSuccess ? '실행 완료' : '실행 실패'}
              </div>
              {onNavigate && (
                <button
                  onClick={() => onNavigate(n.project_id)}
                  className="text-[10px] text-cyan-400 hover:text-cyan-300 mt-1 underline"
                >
                  프로젝트로 이동
                </button>
              )}
            </div>
            <button
              onClick={() => onDismiss(n.job_id)}
              className="text-slate-500 hover:text-slate-300 flex-shrink-0"
            >
              <X size={14} />
            </button>
          </div>
        );
      })}
    </div>
  );
}
