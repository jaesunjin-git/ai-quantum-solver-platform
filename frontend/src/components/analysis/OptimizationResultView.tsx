// src/components/analysis/OptimizationResultView.tsx
// v4.0 - Sub-components: KPIDashboard | DutyScheduleTab | ConstraintCheckTab | CompileReportTab
import { useState, useEffect } from 'react';
import {
  CheckCircle, XCircle, AlertTriangle,
  Download, RotateCcw,
  Package, Users, Shield, TrendingUp,
  ChevronDown, ChevronUp, Info
} from 'lucide-react';
import { KPIDashboard } from './KPIDashboard';
import { DutyScheduleTab } from './DutyScheduleTab';
import { ConstraintCheckTab } from './ConstraintCheckTab';
import { CompileReportTab } from './CompileReportTab';
import type { ResultData, CompileSummary } from './types';

type SubTab = 'compile' | 'kpi' | 'schedule' | 'constraints';

const formatNumber = (n: any) => {
  if (n == null) return '-';
  if (typeof n === 'number') return Number.isInteger(n) ? n.toLocaleString() : n.toFixed(1);
  return String(n);
};

export function OptimizationResultView({
  data,
  projectId,
  onAction,
}: {
  data: ResultData;
  projectId?: string;
  onAction?: (type: string, message: string) => void;
}) {
  const interpreted = data.interpreted_result;
  const hasInterpreted = !!interpreted && !!interpreted.duties?.length;

  const compileSummary: CompileSummary = data.compile_summary || {
    solver_name: data.solver_name || data.solver_id,
    solver_type: data.solver_type,
    variables_created: data.model_stats?.total_variables,
    constraints: {
      total_in_model: data.model_stats?.total_constraints || 0,
      applied: (data.model_stats?.total_constraints || 0),
      failed: 0,
    },
    objective_parsed: true,
    compile_time_sec: data.timing?.compile_sec,
    warnings: data.compile_warnings,
    warning_count: data.compile_warnings?.length || 0,
  };
  const compileWarnings = compileSummary.warnings || data.compile_warnings || [];

  const hasExecuteResult = !!(data.status && data.status !== 'PENDING');
  const [activeTab, setActiveTab] = useState<SubTab>(hasInterpreted ? 'kpi' : hasExecuteResult ? 'kpi' : 'compile');
  const [validationExpanded, setValidationExpanded] = useState(false);

  useEffect(() => {
    if (hasInterpreted) setActiveTab('kpi');
    else if (hasExecuteResult) setActiveTab('kpi');
    else setActiveTab('compile');
  }, [data.status, hasInterpreted]);

  const status = data.status || 'UNKNOWN';
  const statusConfig: Record<string, { icon: any; color: string; bg: string; border: string; label: string }> = {
    OPTIMAL: { icon: CheckCircle, color: 'text-green-400', bg: 'bg-green-500/10', border: 'border-green-500/30', label: 'Optimal' },
    FEASIBLE: { icon: CheckCircle, color: 'text-blue-400', bg: 'bg-blue-500/10', border: 'border-blue-500/30', label: 'Feasible' },
    INFEASIBLE_BEST: { icon: AlertTriangle, color: 'text-amber-400', bg: 'bg-amber-500/10', border: 'border-amber-500/30', label: 'Best Effort' },
    INFEASIBLE: { icon: XCircle, color: 'text-red-400', bg: 'bg-red-500/10', border: 'border-red-500/30', label: 'Infeasible' },
    UNKNOWN: { icon: AlertTriangle, color: 'text-yellow-400', bg: 'bg-yellow-500/10', border: 'border-yellow-500/30', label: 'Unknown' },
  };
  const sc = statusConfig[status] || statusConfig.UNKNOWN;
  const StatusIcon = sc.icon;

  const tabs: { key: SubTab; label: string; icon: any; badge?: string }[] = [
    { key: 'kpi', label: 'KPI', icon: TrendingUp,
      badge: hasInterpreted ? `${interpreted!.kpi.active_duties}` : undefined },
    { key: 'schedule', label: '배정표', icon: Users,
      badge: hasInterpreted ? `${interpreted!.duties.length}` : undefined },
    { key: 'constraints', label: '제약검증', icon: Shield,
      badge: hasInterpreted && interpreted!.kpi.constraint_violations > 0
        ? `${interpreted!.kpi.constraint_violations}` : undefined },
    { key: 'compile', label: '컴파일', icon: Package,
      badge: compileWarnings.length > 0 ? `${compileWarnings.length}` : undefined },
  ];

  return (
    <div className="h-full flex flex-col bg-slate-900 animate-fade-in">
      {/* Header */}
      <div className="p-4 pb-2 border-b border-slate-800">
        <div className="flex items-center justify-between mb-3">
          <div className="flex items-center gap-3">
            <div className={`p-2 rounded-lg ${sc.bg} ${sc.color}`}><StatusIcon size={20} /></div>
            <div>
              <h2 className="text-base font-bold text-white flex items-center gap-2">
                {hasInterpreted ? interpreted!.objective_label : 'Optimization Result'}
                <span className={`text-lg ${sc.color}`}>{formatNumber(data.objective_value)}</span>
              </h2>
              <p className="text-[12px] text-slate-400">{data.solver_name} · {sc.label}</p>
            </div>
          </div>
        </div>

        {/* Validation summary (오류/경고/안내) — 클릭 시 상세 펼치기 */}
        {data.validation && (data.validation.error_count > 0 || data.validation.warning_count > 0 || data.validation.info_count > 0) && (
          <div className="mb-2">
            <button
              onClick={() => setValidationExpanded(!validationExpanded)}
              className="flex items-center gap-2 text-[11px] w-full"
            >
              {data.validation.error_count > 0 && (
                <span className="flex items-center gap-1 px-2 py-0.5 rounded bg-red-500/15 text-red-400">
                  <XCircle size={10} /> {data.validation.error_count} 오류
                </span>
              )}
              {data.validation.warning_count > 0 && (
                <span className="flex items-center gap-1 px-2 py-0.5 rounded bg-yellow-500/15 text-yellow-400">
                  <AlertTriangle size={10} /> {data.validation.warning_count} 경고
                </span>
              )}
              {data.validation.info_count > 0 && (
                <span className="flex items-center gap-1 px-2 py-0.5 rounded bg-blue-500/15 text-blue-400">
                  <Info size={10} /> {data.validation.info_count} 안내
                </span>
              )}
              <span className="ml-auto text-slate-500">
                {validationExpanded ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
              </span>
            </button>
            {validationExpanded && data.validation.items && (
              <div className="mt-2 space-y-1 bg-slate-800/30 rounded-lg p-2 max-h-40 overflow-y-auto custom-scrollbar">
                {data.validation.items.map((item, i) => (
                  <div key={i} className={`flex items-start gap-2 text-[11px] py-0.5 ${
                    item.severity === 'error' ? 'text-red-300' :
                    item.severity === 'warning' ? 'text-yellow-300' : 'text-blue-300'
                  }`}>
                    {item.severity === 'error' ? <XCircle size={10} className="mt-0.5 shrink-0 text-red-400" /> :
                     item.severity === 'warning' ? <AlertTriangle size={10} className="mt-0.5 shrink-0 text-yellow-400" /> :
                     <Info size={10} className="mt-0.5 shrink-0 text-blue-400" />}
                    <span>{item.message}</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        {/* Tab selector */}
        <div className="flex gap-1 bg-slate-800/50 rounded-lg p-0.5">
          {tabs.map(tab => {
            const TabIcon = tab.icon;
            return (
              <button key={tab.key} onClick={() => setActiveTab(tab.key)}
                className={`flex-1 py-1.5 px-2 rounded-md text-xs font-medium transition-all flex items-center justify-center gap-1 ${
                  activeTab === tab.key
                    ? 'bg-slate-700 text-white shadow-sm'
                    : 'text-slate-400 hover:text-slate-300'
                }`}>
                <TabIcon size={12} />
                {tab.label}
                {tab.badge && (
                  <span className={`ml-0.5 px-1 py-0 text-xs rounded-full ${
                    tab.key === 'constraints' && interpreted?.kpi.constraint_violations
                      ? 'bg-red-500/20 text-red-400'
                      : 'bg-slate-600/50 text-slate-400'
                  }`}>{tab.badge}</span>
                )}
              </button>
            );
          })}
        </div>
      </div>

      {/* Content */}
      <div className="flex-1 min-h-0 overflow-y-auto custom-scrollbar p-4">
        {activeTab === 'kpi' && <KPIDashboard data={data} interpreted={interpreted} />}
        {activeTab === 'schedule' && <DutyScheduleTab interpreted={interpreted} />}
        {activeTab === 'constraints' && <ConstraintCheckTab interpreted={interpreted} />}
        {activeTab === 'compile' && <CompileReportTab compileSummary={compileSummary} compileWarnings={compileWarnings} />}
      </div>

      {/* Bottom actions */}
      <div className="flex-shrink-0 p-3 border-t border-slate-800 flex gap-2">
        {data.artifacts?.duty_schedule && (
          <button onClick={() => {
            const link = document.createElement('a');
            link.href = `${(window as any).__API_BASE || 'http://localhost:8000'}/uploads/${projectId}/results/${data.artifacts!.duty_schedule!.split('/').pop()}`;
            link.download = 'duty_schedule.csv';
            link.click();
          }}
            className="flex-1 py-2 rounded-xl text-[12px] font-medium text-slate-300 bg-slate-800 hover:bg-slate-700 transition flex items-center justify-center gap-1.5">
            <Download size={13} /> 배정표 다운로드
          </button>
        )}
        <button onClick={() => onAction?.('switch_step', 'solver')}
          className="flex-1 py-2 rounded-xl text-[12px] font-medium text-slate-300 bg-slate-800 hover:bg-slate-700 transition flex items-center justify-center gap-1.5">
          <RotateCcw size={13} /> 다른 솔버
        </button>
      </div>
    </div>
  );
}
