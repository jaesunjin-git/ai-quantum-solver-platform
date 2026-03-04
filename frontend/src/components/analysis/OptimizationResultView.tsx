// src/components/analysis/OptimizationResultView.tsx
// v2.0 - Sub-tab structure: Compile Report | Execution Report
import { useState, useEffect } from 'react';
import {
  CheckCircle, XCircle, AlertTriangle, Cpu, BarChart3,
  Download, RotateCcw, ChevronDown, ChevronRight,
  Package, Zap
} from 'lucide-react';
import type { ResultData, CompileSummary } from './types';
import { downloadReport } from './downloadHelper';

type SubTab = 'compile' | 'execute';

export function OptimizationResultView({
  data,
  projectId,
  onAction,
}: {
  data: ResultData;
  projectId?: string;
  onAction?: (type: string, message: string) => void;
}) {

  // compile_summary가 없으면 기존 필드에서 구성
  const compileSummary: CompileSummary = data.compile_summary || {
    solver_name: data.solver_name || data.solver_id,
    solver_type: data.solver_type,
    variables_created: data.model_stats?.total_variables,
    constraints: {
      total_in_model: data.model_stats?.total_constraints || 0,
      applied: (data.model_stats?.total_constraints || 0) - (data.compile_warnings?.filter((w: string) => w.toLowerCase().includes('could not parse')).length || 0),
      failed: data.compile_warnings?.filter((w: string) => w.toLowerCase().includes('could not parse')).length || 0,
    },
    objective_parsed: !data.compile_warnings?.some((w: string) => w.toLowerCase().includes('objective')),
    compile_time_sec: data.timing?.compile_sec,
    warnings: data.compile_warnings,
    warning_count: data.compile_warnings?.length || 0,
  };
  const compileWarnings = compileSummary.warnings || data.compile_warnings || [];
  const constraints = compileSummary.constraints || { total_in_model: 0, applied: 0, failed: 0 };

  // Determine initial sub-tab based on data availability
  const hasExecuteResult = !!(data.status && data.status !== 'PENDING');
  const hasCompileOnly = !hasExecuteResult && (compileSummary.variables_created != null || compileWarnings.length > 0);
  const [activeTab, setActiveTab] = useState<SubTab>(hasCompileOnly ? 'compile' : 'execute');
  const [warningsExpanded, setWarningsExpanded] = useState(false);
  const [solutionExpanded, setSolutionExpanded] = useState(false);

  // When data changes, switch to appropriate tab
  useEffect(() => {
    if (hasCompileOnly && !hasExecuteResult) {
      setActiveTab('compile');
    } else if (hasExecuteResult) {
      setActiveTab('execute');
    }
  }, [data.status, hasCompileOnly, hasExecuteResult]);

  // Status display config
  const status = data.status || 'UNKNOWN';
  const objectiveValue = data.objective_value;
  const solverName = data.solver_name || data.solver_id || 'Unknown';
  const solverType = data.solver_type || '';
  const modelStats = data.model_stats || {};
  const timing = data.timing || {};
  const solution = data.solution || {};

  const isOptimal = status === 'OPTIMAL';
  const isFeasible = status === 'FEASIBLE' || isOptimal;
  const isAbnormal = !isFeasible && hasExecuteResult;

  const statusConfig: Record<string, { icon: any; color: string; bg: string; border: string; label: string }> = {
    OPTIMAL: { icon: CheckCircle, color: 'text-green-400', bg: 'bg-green-500/10', border: 'border-green-500/30', label: 'Optimal Solution' },
    FEASIBLE: { icon: CheckCircle, color: 'text-blue-400', bg: 'bg-blue-500/10', border: 'border-blue-500/30', label: 'Feasible Solution' },
    INFEASIBLE: { icon: XCircle, color: 'text-red-400', bg: 'bg-red-500/10', border: 'border-red-500/30', label: 'Infeasible' },
    TIMEOUT: { icon: AlertTriangle, color: 'text-orange-400', bg: 'bg-orange-500/10', border: 'border-orange-500/30', label: 'Timeout' },
    UNKNOWN: { icon: AlertTriangle, color: 'text-yellow-400', bg: 'bg-yellow-500/10', border: 'border-yellow-500/30', label: 'Unknown Status' },
    PENDING: { icon: Cpu, color: 'text-slate-400', bg: 'bg-slate-500/10', border: 'border-slate-500/30', label: 'Pending Execution' },
  };
  const sc = statusConfig[status] || statusConfig.UNKNOWN;
  const StatusIcon = sc.icon;

  // Solution entries for display
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
  const displaySolution = solutionEntries.slice(0, 30);
  const hasMoreSolution = solutionEntries.length > 30;

  const formatNumber = (n: any) => {
    if (n == null) return '-';
    if (typeof n === 'number') {
      if (Number.isInteger(n)) return n.toLocaleString();
      return n.toLocaleString(undefined, { maximumFractionDigits: 4 });
    }
    return String(n);
  };

  const handleRunAgain = () => {
    onAction?.('send', '솔버 추천 결과 보여줘');
  };

  // ── Compile Report Tab Content ──
  const renderCompileReport = () => (
    <div className="space-y-4 animate-in fade-in duration-300">
      {/* Compile Summary Card */}
      <div className="bg-slate-800/50 rounded-xl border border-slate-700 p-4">
        <h3 className="text-sm font-bold text-slate-300 mb-3 flex items-center gap-2">
          <Package size={14} className="text-cyan-400" /> 컴파일 요약
        </h3>
        <div className="space-y-2 text-[13px]">
          <div className="flex justify-between">
            <span className="text-slate-500">솔버</span>
            <span className="text-cyan-400 font-mono">
              {compileSummary.solver_name || solverName} {compileSummary.solver_type || solverType ? `(${compileSummary.solver_type || solverType})` : ''}
            </span>
          </div>
          <div className="flex justify-between">
            <span className="text-slate-500">생성된 변수</span>
            <span className="text-white font-mono">{formatNumber(compileSummary.variables_created || modelStats.total_variables)}개</span>
          </div>
          <div className="flex justify-between items-center">
            <span className="text-slate-500">제약조건 적용</span>
            <div className="flex items-center gap-2">
              <span className={`font-mono font-bold ${constraints.failed > 0 ? 'text-yellow-400' : 'text-green-400'}`}>
                {constraints.applied}/{constraints.total_in_model}
              </span>
              {constraints.failed > 0 && (
                <span className="text-[11px] text-red-400">
                  ({constraints.failed}개 파싱 실패)
                </span>
              )}
            </div>
          </div>

          {/* Constraint progress bar */}
          {constraints.total_in_model > 0 && (
            <div className="mt-1">
              <div className="w-full h-2 bg-slate-700 rounded-full overflow-hidden">
                <div
                  className={`h-full rounded-full transition-all ${
                    constraints.failed === 0 ? 'bg-green-500' :
                    constraints.applied > 0 ? 'bg-yellow-500' : 'bg-red-500'
                  }`}
                  style={{ width: `${(constraints.applied / constraints.total_in_model) * 100}%` }}
                />
              </div>
              <div className="flex justify-between text-[11px] text-slate-600 mt-0.5">
                <span>0</span>
                <span>{constraints.total_in_model}개 제약</span>
              </div>
            </div>
          )}

          <div className="flex justify-between">
            <span className="text-slate-500">목적함수 파싱</span>
            <span className={compileSummary.objective_parsed === false ? 'text-yellow-400' : 'text-green-400'}>
              {compileSummary.objective_parsed === false ? '⚠️ 기본값 사용' : '✅ 성공'}
            </span>
          </div>
          <div className="flex justify-between">
            <span className="text-slate-500">컴파일 시간</span>
            <span className="text-white font-mono">{compileSummary.compile_time_sec ?? timing.compile_sec ?? '-'}s</span>
          </div>
        </div>
      </div>

      {/* Warnings Section */}
      {compileWarnings.length > 0 && (
        <div className="bg-yellow-500/5 rounded-xl border border-yellow-500/20 p-4">
          <button
            onClick={() => setWarningsExpanded(!warningsExpanded)}
            className="w-full flex items-center justify-between text-sm font-bold text-yellow-400"
          >
            <span className="flex items-center gap-2">
              <AlertTriangle size={14} />
              경고 ({compileWarnings.length}건)
            </span>
            {warningsExpanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
          </button>
          {warningsExpanded && (
            <div className="mt-3 space-y-1.5 max-h-60 overflow-y-auto custom-scrollbar">
              {compileWarnings.map((w: string, i: number) => (
                <div key={i} className="text-[12px] text-yellow-300/80 flex gap-2">
                  <span className="text-yellow-500/50 flex-shrink-0">#{i + 1}</span>
                  <span className="break-all">{w}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Step-by-step: action buttons when no execution result yet */}
      {!hasExecuteResult && (
        <div className="flex gap-2 mt-4">
          <button
            onClick={() => onAction?.('send', '최적화 실행해줘')}
            className="flex-1 py-2.5 rounded-xl text-sm font-bold text-white bg-gradient-to-r from-cyan-600 to-blue-600 hover:from-cyan-500 hover:to-blue-500 transition-all flex items-center justify-center gap-2"
          >
            <Zap size={14} /> 실행 진행
          </button>
          <button
            onClick={() => onAction?.('send', '수학 모델 수정해줘')}
            className="flex-1 py-2.5 rounded-xl text-sm font-medium text-slate-300 bg-slate-800 hover:bg-slate-700 transition flex items-center justify-center gap-2"
          >
            <RotateCcw size={14} /> 모델 수정
          </button>
        </div>
      )}
    </div>
  );

  // ── Execution Report Tab Content ──
  const renderExecutionReport = () => (
    <div className="space-y-4 animate-in fade-in duration-300">
      {/* Status + KPI Cards */}
      <div className="grid grid-cols-3 gap-3">
        <div className="bg-slate-800/50 rounded-xl p-4 border border-slate-700 text-center">
          <div className="text-[11px] text-slate-500 uppercase mb-1">Objective Value</div>
          <div className={`text-2xl font-bold ${isFeasible ? 'text-green-400' : 'text-red-400'}`}>
            {formatNumber(objectiveValue)}
          </div>
        </div>
        <div className="bg-slate-800/50 rounded-xl p-4 border border-slate-700 text-center">
          <div className="text-[11px] text-slate-500 uppercase mb-1">Execute Time</div>
          <div className="text-2xl font-bold text-cyan-400">
            {timing.execute_sec != null ? `${timing.execute_sec}s` : '-'}
          </div>
          <div className="text-[11px] text-slate-500 mt-1">
            total: {timing.total_sec ?? '-'}s
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

      {/* Abnormal result hint */}
      {isAbnormal && constraints.failed > 0 && (
        <div className="bg-orange-500/5 rounded-xl border border-orange-500/20 p-3">
          <div className="flex items-start gap-2">
            <AlertTriangle size={14} className="text-orange-400 mt-0.5 flex-shrink-0" />
            <div className="text-[13px] text-orange-300">
              <span className="font-bold">참고:</span> {constraints.total_in_model}개 제약 중 {constraints.applied}개만 적용되었습니다
              ({constraints.failed}개 파싱 실패).
              <button
                onClick={() => setActiveTab('compile')}
                className="ml-1 text-cyan-400 hover:text-cyan-300 underline transition"
              >
                컴파일 리포트 확인 →
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Infeasible message */}
      {!isFeasible && hasExecuteResult && (
        <div className="bg-red-500/5 rounded-xl border border-red-500/20 p-6 text-center">
          <XCircle size={48} className="text-red-400 mx-auto mb-3" />
          <h3 className="text-lg font-bold text-red-400 mb-2">No Feasible Solution Found</h3>
          <p className="text-sm text-slate-400">
            The solver could not find a solution satisfying all constraints.
            Try relaxing constraints or adjusting parameters.
          </p>
        </div>
      )}

      {/* Solution Preview */}
      {isFeasible && displaySolution.length > 0 && (
        <div className="bg-slate-800/30 rounded-xl border border-slate-700 p-4">
          <button
            onClick={() => setSolutionExpanded(!solutionExpanded)}
            className="w-full flex items-center justify-between"
          >
            <h3 className="text-sm font-bold text-slate-300 flex items-center gap-2">
              <BarChart3 size={14} className="text-slate-400" /> Solution Preview
              <span className="text-[11px] text-slate-500 font-normal">
                ({solutionEntries.length} nonzero variables)
              </span>
            </h3>
            {solutionExpanded ? <ChevronDown size={14} className="text-slate-500" /> : <ChevronRight size={14} className="text-slate-500" />}
          </button>
          {solutionExpanded && (
            <div className="grid grid-cols-2 gap-1 max-h-60 overflow-y-auto custom-scrollbar mt-3">
              {displaySolution.map(([name, val], i) => (
                <div key={i} className="flex justify-between text-[12px] py-0.5 px-2 rounded hover:bg-slate-700/30">
                  <span className="text-slate-400 truncate mr-2">{name}</span>
                  <span className="text-white font-mono flex-shrink-0">{formatNumber(val)}</span>
                </div>
              ))}
              {hasMoreSolution && (
                <div className="col-span-2 text-center text-[11px] text-slate-500 pt-1">
                  ... 외 {solutionEntries.length - 30}개
                </div>
              )}
            </div>
          )}
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
  );

  return (
    <div className="h-full flex flex-col bg-slate-900 animate-fade-in">
      {/* Header */}
      <div className="p-6 pb-3 border-b border-slate-800">
        <div className="flex items-center justify-between mb-4">
          <div className="flex items-center gap-3">
            <div className={`p-2 rounded-lg ${sc.bg} ${sc.color}`}>
              <StatusIcon size={24} />
            </div>
            <div>
              <h2 className="text-lg font-bold text-white">Optimization Result</h2>
              <p className="text-sm text-slate-400">{solverName} {solverType}</p>
            </div>
          </div>
          <div className={`px-3 py-1 rounded-full text-sm font-bold ${sc.bg} ${sc.color} border ${sc.border}`}>
            {sc.label}
          </div>
        </div>

        {/* Sub-tab selector */}
        <div className="flex gap-1 bg-slate-800/50 rounded-lg p-1">
          <button
            onClick={() => setActiveTab('compile')}
            className={`flex-1 py-2 px-3 rounded-md text-[13px] font-medium transition-all flex items-center justify-center gap-1.5 ${
              activeTab === 'compile'
                ? 'bg-slate-700 text-white shadow-sm'
                : 'text-slate-400 hover:text-slate-300 hover:bg-slate-800/50'
            }`}
          >
            <Package size={13} />
            컴파일 리포트
            {compileWarnings.length > 0 && (
              <span className="ml-1 px-1.5 py-0.5 text-[10px] rounded-full bg-yellow-500/20 text-yellow-400">
                {compileWarnings.length}
              </span>
            )}
          </button>
          <button
            onClick={() => setActiveTab('execute')}
            className={`flex-1 py-2 px-3 rounded-md text-[13px] font-medium transition-all flex items-center justify-center gap-1.5 ${
              activeTab === 'execute'
                ? 'bg-slate-700 text-white shadow-sm'
                : 'text-slate-400 hover:text-slate-300 hover:bg-slate-800/50'
            }`}
          >
            <Zap size={13} />
            실행 리포트
            {hasExecuteResult && (
              <span className={`ml-1 px-1.5 py-0.5 text-[10px] rounded-full ${
                isFeasible ? 'bg-green-500/20 text-green-400' : 'bg-red-500/20 text-red-400'
              }`}>
                {status}
              </span>
            )}
          </button>
        </div>
      </div>

      {/* Scrollable tab content */}
      <div className="flex-1 overflow-y-auto custom-scrollbar p-6">
        {activeTab === 'compile' ? renderCompileReport() : renderExecutionReport()}
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
          <RotateCcw size={14} /> 다른 솔버로 재실행
        </button>
      </div>
    </div>
  );
}
