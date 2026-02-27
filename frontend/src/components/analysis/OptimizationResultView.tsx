// src/components/analysis/OptimizationResultView.tsx
import { CheckCircle, XCircle, AlertTriangle, Cpu, BarChart3, Download, RotateCcw } from 'lucide-react';
import type { ResultData } from './types';
import { downloadReport } from './downloadHelper';

export function OptimizationResultView({
  data,
  projectId,
  onAction,
}: {
  data: ResultData;
  projectId?: string;
  onAction?: (type: string, message: string) => void;
}) {
  // /api/solve 응답이 view_mode: 'result'와 함께 spread 되어 들어옴
  // data = { view_mode, solver_id, solver_name, status, objective_value, model_stats, timing, solution, ... }
  const handleRunAgain = () => {
    onAction?.('send', '솔버 추천 결과 보여줘');
  };

  const status = data.status || 'UNKNOWN';
  const objectiveValue = data.objective_value;
  const solverName = data.solver_name || data.solver_id || 'Unknown';
  const solverType = data.solver_type || '';
  const modelStats = data.model_stats || {};
  const timing = data.timing || {};
  const solution = data.solution || {};
  const compileWarnings = data.compile_warnings || [];
  // const compareMode = data.compare_mode || false; // reserved for future

  const isOptimal = status === 'OPTIMAL';
  const isFeasible = status === 'FEASIBLE' || isOptimal;

  const statusConfig = {
    OPTIMAL: { icon: CheckCircle, color: 'text-green-400', bg: 'bg-green-500/10', border: 'border-green-500/30', label: 'Optimal Solution' },
    FEASIBLE: { icon: CheckCircle, color: 'text-blue-400', bg: 'bg-blue-500/10', border: 'border-blue-500/30', label: 'Feasible Solution' },
    INFEASIBLE: { icon: XCircle, color: 'text-red-400', bg: 'bg-red-500/10', border: 'border-red-500/30', label: 'Infeasible' },
    UNKNOWN: { icon: AlertTriangle, color: 'text-yellow-400', bg: 'bg-yellow-500/10', border: 'border-yellow-500/30', label: 'Unknown Status' },
  };
  const sc = statusConfig[status as keyof typeof statusConfig] || statusConfig.UNKNOWN;
  const StatusIcon = sc.icon;

  // 솔루션에서 비영 변수 추출 (최대 20개)
  const solutionEntries: [string, any][] = [];
  for (const [key, val] of Object.entries(solution)) {
    if (typeof val === 'object' && val !== null) {
      for (const [subKey, subVal] of Object.entries(val as Record<string, any>)) {
        if (subVal !== 0) solutionEntries.push([`${key}[${subKey}]`, subVal]);
      }
    } else if (val !== 0) {
      solutionEntries.push([key, val]);
    }
  }
  const displaySolution = solutionEntries.slice(0, 20);
  const hasMoreSolution = solutionEntries.length > 20;

  const formatNumber = (n: any) => {
    if (n == null) return '-';
    if (typeof n === 'number') {
      if (Number.isInteger(n)) return n.toLocaleString();
      return n.toLocaleString(undefined, { maximumFractionDigits: 4 });
    }
    return String(n);
  };

  return (
    <div className="h-full flex flex-col bg-slate-900 animate-fade-in">
      {/* Header */}
      <div className="p-6 border-b border-slate-800">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className={`p-2 rounded-lg ${sc.bg} ${sc.color}`}>
              <StatusIcon size={24} />
            </div>
            <div>
              <h2 className="text-lg font-bold text-white">Optimization Result</h2>
              <p className="text-sm text-slate-400">{solverName}  {solverType}</p>
            </div>
          </div>
          <div className={`px-3 py-1 rounded-full text-sm font-bold ${sc.bg} ${sc.color} border ${sc.border}`}>
            {sc.label}
          </div>
        </div>
      </div>

      {/* Scrollable content */}
      <div className="flex-1 overflow-y-auto custom-scrollbar p-6 space-y-4">

        {/* KPI Cards */}
        <div className="grid grid-cols-3 gap-3">
          <div className="bg-slate-800/50 rounded-xl p-4 border border-slate-700 text-center">
            <div className="text-[11px] text-slate-500 uppercase mb-1">Objective Value</div>
            <div className={`text-2xl font-bold ${isFeasible ? 'text-green-400' : 'text-red-400'}`}>
              {formatNumber(objectiveValue)}
            </div>
          </div>
          <div className="bg-slate-800/50 rounded-xl p-4 border border-slate-700 text-center">
            <div className="text-[11px] text-slate-500 uppercase mb-1">Total Time</div>
            <div className="text-2xl font-bold text-cyan-400">
              {timing.total_sec != null ? `${timing.total_sec}s` : '-'}
            </div>
            <div className="text-[11px] text-slate-500 mt-1">
              compile: {timing.compile_sec ?? '-'}s  execute: {timing.execute_sec ?? '-'}s
            </div>
          </div>
          <div className="bg-slate-800/50 rounded-xl p-4 border border-slate-700 text-center">
            <div className="text-[11px] text-slate-500 uppercase mb-1">Variables</div>
            <div className="text-2xl font-bold text-white">
              {formatNumber(modelStats.total_variables)}
            </div>
            <div className="text-[11px] text-slate-500 mt-1">
              nonzero: {formatNumber(modelStats.nonzero_variables)}
            </div>
          </div>
        </div>

        {/* Model Stats */}
        <div className="bg-slate-800/30 rounded-xl border border-slate-700 p-4">
          <h3 className="text-sm font-bold text-slate-300 mb-3 flex items-center gap-2">
            <Cpu size={14} className="text-slate-400" /> Model Statistics
          </h3>
          <div className="grid grid-cols-2 gap-2 text-[13px]">
            <div className="flex justify-between">
              <span className="text-slate-500">Total Variables</span>
              <span className="text-white font-mono">{formatNumber(modelStats.total_variables)}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-slate-500">Total Constraints</span>
              <span className="text-white font-mono">{formatNumber(modelStats.total_constraints)}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-slate-500">Nonzero Variables</span>
              <span className="text-white font-mono">{formatNumber(modelStats.nonzero_variables)}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-slate-500">Solver Type</span>
              <span className="text-cyan-400 font-mono">{solverType}</span>
            </div>
          </div>
        </div>

        {/* Compile Warnings */}
        {compileWarnings.length > 0 && (
          <div className="bg-yellow-500/5 rounded-xl border border-yellow-500/20 p-4">
            <h3 className="text-sm font-bold text-yellow-400 mb-2 flex items-center gap-2">
              <AlertTriangle size={14} /> Compile Warnings ({compileWarnings.length})
            </h3>
            <div className="space-y-1">
              {compileWarnings.map((w: string, i: number) => (
                <div key={i} className="text-[12px] text-yellow-300/80"> {w}</div>
              ))}
            </div>
          </div>
        )}

        {/* Solution Preview */}
        {isFeasible && displaySolution.length > 0 && (
          <div className="bg-slate-800/30 rounded-xl border border-slate-700 p-4">
            <h3 className="text-sm font-bold text-slate-300 mb-3 flex items-center gap-2">
              <BarChart3 size={14} className="text-slate-400" /> Solution Preview
              <span className="text-[11px] text-slate-500 font-normal">
                ({solutionEntries.length} nonzero variables{hasMoreSolution ? ', showing top 20' : ''})
              </span>
            </h3>
            <div className="grid grid-cols-2 gap-1 max-h-60 overflow-y-auto custom-scrollbar">
              {displaySolution.map(([name, val], i) => (
                <div key={i} className="flex justify-between text-[12px] py-0.5 px-2 rounded hover:bg-slate-700/30">
                  <span className="text-slate-400 truncate mr-2">{name}</span>
                  <span className="text-white font-mono flex-shrink-0">{formatNumber(val)}</span>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Infeasible message */}
        {!isFeasible && (
          <div className="bg-red-500/5 rounded-xl border border-red-500/20 p-6 text-center">
            <XCircle size={48} className="text-red-400 mx-auto mb-3" />
            <h3 className="text-lg font-bold text-red-400 mb-2">No Feasible Solution Found</h3>
            <p className="text-sm text-slate-400">
              The solver could not find a solution satisfying all constraints.
              Try relaxing constraints or adjusting parameters.
            </p>
          </div>
        )}

        {/* Solver Info */}
        {data.solver_info && Object.keys(data.solver_info).length > 0 && (
          <div className="bg-slate-800/30 rounded-xl border border-slate-700 p-4">
            <h3 className="text-sm font-bold text-slate-300 mb-3">Solver Info</h3>
            <div className="space-y-1 text-[12px]">
              {Object.entries(data.solver_info).map(([k, v]) => (
                <div key={k} className="flex justify-between">
                  <span className="text-slate-500">{k}</span>
                  <span className="text-white font-mono">{String(v)}</span>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>

      {/* Bottom actions */}
      <div className="flex-shrink-0 p-4 border-t border-slate-800 flex gap-2">
        {projectId && (
          <button
            onClick={() => projectId && downloadReport(projectId, 'json', 'solve_result')}
            className="flex-1 py-2.5 rounded-xl text-sm font-medium text-slate-300 bg-slate-800 hover:bg-slate-700 transition flex items-center justify-center gap-2"
          >
            <Download size={14} /> 리포트 다운로드
          </button>
        )}
        <button
          onClick={handleRunAgain}
          className="flex-1 py-2.5 rounded-xl text-sm font-medium text-slate-300 bg-slate-800 hover:bg-slate-700 transition flex items-center justify-center gap-2"
        >
          <RotateCcw size={14} /> 다시 실행
        </button>
      </div>
    </div>
  );
}
