// src/components/analysis/StepItem.tsx
import { CheckCircle, Clock } from 'lucide-react';

export function StepItem({ label, status }: { label: string; status: 'wait' | 'active' | 'done' }) {
  return (
    <div
      className={`flex items-center justify-between p-3 rounded-lg border transition-all ${
        status === 'active'
          ? 'bg-slate-800 border-blue-500/50'
          : status === 'done'
            ? 'bg-slate-800/50 border-green-500/30'
            : 'border-slate-800 opacity-50'
      }`}
    >
      <span className={`text-sm ${status === 'done' ? 'text-slate-300' : 'text-white'}`}>
        {label}
      </span>
      {status === 'active' ? (
        <Clock size={16} className="text-blue-400 animate-spin" />
      ) : status === 'done' ? (
        <CheckCircle size={16} className="text-green-400" />
      ) : null}
    </div>
  );
}
