/**
 * VersionBar — Top version timeline bar.
 *
 * Shows a horizontal timeline of optimization versions.
 * Each version dot shows: label, solver, objective value.
 * Current version is highlighted; past versions are clickable.
 * "Compare" button enters side-by-side comparison mode.
 *
 * Platform-generic: no domain-specific logic.
 */

import { useState } from 'react';
import { GitBranch, ArrowLeftRight } from 'lucide-react';
import type { VersionTimelineEntry } from './types';

interface VersionBarProps {
  timeline: VersionTimelineEntry[];
  currentVersionIndex: number;
  onVersionSelect?: (entry: VersionTimelineEntry) => void;
  onCompareRequest?: (entryA: VersionTimelineEntry, entryB: VersionTimelineEntry) => void;
}

export default function VersionBar({
  timeline,
  currentVersionIndex,
  onVersionSelect,
  onCompareRequest,
}: VersionBarProps) {
  const [compareMode, setCompareMode] = useState(false);
  const [compareSelection, setCompareSelection] = useState<number[]>([]);

  if (!timeline || timeline.length === 0) {
    return null;
  }

  const handleVersionClick = (entry: VersionTimelineEntry, idx: number) => {
    if (compareMode) {
      setCompareSelection(prev => {
        const next = prev.includes(idx) ? prev.filter(i => i !== idx) : [...prev, idx];
        // When two selected, trigger compare
        if (next.length === 2 && onCompareRequest) {
          onCompareRequest(timeline[next[0]], timeline[next[1]]);
          setCompareMode(false);
          setCompareSelection([]);
        }
        return next.length <= 2 ? next : [idx];
      });
    } else if (onVersionSelect) {
      onVersionSelect(entry);
    }
  };

  const toggleCompareMode = () => {
    if (!compareMode) {
      // 비교 모드 진입 시 현재 버전을 첫 번째 선택으로 자동 포함
      setCompareMode(true);
      setCompareSelection(currentVersionIndex >= 0 ? [currentVersionIndex] : []);
    } else {
      setCompareMode(false);
      setCompareSelection([]);
    }
  };

  return (
    <div className="border-b border-slate-700 bg-slate-800/60 px-4 py-2">
      <div className="flex items-center justify-between">
        {/* Timeline dots */}
        <div className="flex items-center gap-1 overflow-x-auto flex-1 min-w-0">
          <GitBranch size={14} className="text-slate-500 flex-shrink-0 mr-1" />
          {timeline.map((entry, idx) => {
            const isCurrent = idx === currentVersionIndex;
            const isSelected = compareSelection.includes(idx);
            const statusColor = _statusColor(entry.status);

            return (
              <button
                key={entry.version_label}
                onClick={() => handleVersionClick(entry, idx)}
                className={`
                  flex-shrink-0 px-2.5 py-1 rounded-lg text-xs transition-all
                  ${isCurrent
                    ? 'bg-cyan-600/30 text-cyan-300 border border-cyan-500/50'
                    : isSelected
                      ? 'bg-amber-600/30 text-amber-300 border border-amber-500/50'
                      : 'text-slate-400 hover:text-slate-200 hover:bg-slate-700/50 border border-transparent'
                  }
                `}
                title={`${entry.version_label} | ${entry.solver_name || 'N/A'} | ${entry.status || 'N/A'}`}
              >
                <div className="flex items-center gap-1.5">
                  <span className={`w-2 h-2 rounded-full ${statusColor}`} />
                  <span className="font-medium">{entry.version_label}</span>
                  {entry.objective_value !== null && (
                    <span className="text-slate-500">
                      {typeof entry.objective_value === 'number'
                        ? entry.objective_value.toLocaleString()
                        : entry.objective_value}
                    </span>
                  )}
                </div>
                {entry.solver_name && (
                  <div className="text-[10px] text-slate-500 mt-0.5">
                    {entry.solver_name}
                  </div>
                )}
              </button>
            );
          })}
        </div>

        {/* Action buttons */}
        <div className="flex items-center gap-2 ml-3 flex-shrink-0">
          {timeline.length >= 2 && (
            <button
              onClick={toggleCompareMode}
              className={`
                inline-flex items-center gap-1 px-2.5 py-1.5 rounded text-xs transition-colors
                ${compareMode
                  ? 'bg-amber-600/30 text-amber-300 border border-amber-500/50'
                  : 'text-slate-400 hover:text-slate-200 hover:bg-slate-700/50'
                }
              `}
            >
              <ArrowLeftRight size={12} />
              {compareMode ? '비교 취소' : '비교'}
            </button>
          )}
        </div>
      </div>

      {/* Compare mode hint */}
      {compareMode && (
        <p className="text-[10px] text-amber-400/70 mt-1">
          비교할 두 버전을 선택하세요 ({compareSelection.length}/2)
        </p>
      )}
    </div>
  );
}

function _statusColor(status: string | null): string {
  switch (status) {
    case 'OPTIMAL': return 'bg-emerald-400';
    case 'FEASIBLE': return 'bg-blue-400';
    case 'INFEASIBLE': return 'bg-red-400';
    case 'TIMEOUT': return 'bg-amber-400';
    case 'model_confirmed': return 'bg-slate-400';
    default: return 'bg-slate-500';
  }
}
