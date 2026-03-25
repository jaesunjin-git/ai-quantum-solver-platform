// src/components/analysis/KPIDashboard.tsx
// KPI 대시보드 서브탭 (활성 듀티, 커버리지, 시간 분석)
import { Users, Route, Clock, AlertTriangle } from 'lucide-react';
import type { ResultData, InterpretedResult } from './types';

const formatNumber = (n: any) => {
  if (n == null) return '-';
  if (typeof n === 'number') return Number.isInteger(n) ? n.toLocaleString() : n.toFixed(1);
  return String(n);
};

export function KPIDashboard({
  data,
  interpreted,
}: {
  data: ResultData;
  interpreted?: InterpretedResult;
}) {
  const isFeasible = data.status === 'FEASIBLE' || data.status === 'OPTIMAL' || data.status === 'INFEASIBLE_BEST';

  if (!interpreted || !interpreted.duties?.length) {
    // fallback: 기존 실행 결과
    return (
      <div className="space-y-4">
        <div className="grid grid-cols-3 gap-3">
          <div className="bg-slate-800/50 rounded-xl p-4 border border-slate-700 text-center">
            <div className="text-xs text-slate-500 uppercase mb-1">목적함수 값</div>
            <div className={`text-2xl font-bold ${isFeasible ? 'text-green-400' : 'text-red-400'}`}>
              {formatNumber(data.objective_value)}
            </div>
          </div>
          <div className="bg-slate-800/50 rounded-xl p-4 border border-slate-700 text-center">
            <div className="text-xs text-slate-500 uppercase mb-1">Time</div>
            <div className="text-2xl font-bold text-cyan-400">{data.timing?.total_sec ?? '-'}s</div>
          </div>
          <div className="bg-slate-800/50 rounded-xl p-4 border border-slate-700 text-center">
            <div className="text-xs text-slate-500 uppercase mb-1">Variables</div>
            <div className="text-2xl font-bold text-white">{formatNumber(data.model_stats?.total_variables)}</div>
          </div>
        </div>
      </div>
    );
  }

  const kpi = interpreted.kpi;
  return (
    <div className="space-y-4 animate-in fade-in duration-300">
      {/* 최적화 목적 표시 */}
      <div className="bg-slate-800/30 rounded-xl p-3 border border-slate-700 flex items-center gap-3">
        <div className="text-xs text-slate-500 uppercase whitespace-nowrap">최적화 목적</div>
        <div className="text-sm font-bold text-cyan-400">
          {interpreted.objective_label || interpreted.objective_type || '목적함수 최적화'}
          {(interpreted as any).objective_display_value && (
            <span className="ml-2 text-white">{(interpreted as any).objective_display_value}</span>
          )}
        </div>
        <div className="text-xs text-slate-500 ml-auto">
          {(interpreted as any).objective_direction && <span className="mr-2 text-slate-400">({(interpreted as any).objective_direction})</span>}
          원시값: <span className="text-white font-mono">{formatNumber(interpreted.objective_value)}</span>
        </div>
      </div>

      {/* Hybrid Info (CQM → CP-SAT) */}
      {data?.hybrid_info && (
        <div className="bg-gradient-to-r from-purple-900/20 to-indigo-900/20 rounded-xl p-3 border border-purple-700/30">
          <div className="text-[11px] text-purple-400 font-bold mb-1.5">Quantum Hybrid: {data.hybrid_info.strategy_used === 'hybrid_warmstart' ? 'CQM → CP-SAT' : 'CP-SAT Fallback'}</div>
          <div className="grid grid-cols-3 gap-2 text-[11px]">
            {data.hybrid_info.cqm_phase && (
              <div className="text-slate-400">
                CQM: <span className="text-purple-300">{data.hybrid_info.cqm_phase.status}</span>
                <span className="text-slate-500 ml-1">({data.hybrid_info.cqm_phase.time_sec}s)</span>
              </div>
            )}
            {data.hybrid_info.cpsat_phase && (
              <div className="text-slate-400">
                CP-SAT: <span className="text-cyan-300">{data.hybrid_info.cpsat_phase.status}</span>
                <span className="text-slate-500 ml-1">({data.hybrid_info.cpsat_phase.time_sec}s)</span>
              </div>
            )}
            <div className="text-slate-400">
              Hints: <span className="text-white">{data.hybrid_info.hints_injected}</span>
              {data.hybrid_info.improvement_pct != null && (
                <span className="text-green-400 ml-1">+{data.hybrid_info.improvement_pct}%</span>
              )}
            </div>
          </div>
        </div>
      )}

      {/* 핵심 KPI */}
      <div className="grid grid-cols-2 gap-3">
        <div className="bg-gradient-to-br from-cyan-900/30 to-blue-900/30 rounded-xl p-4 border border-cyan-700/30">
          <div className="flex items-center gap-2 mb-1">
            <Users size={14} className="text-cyan-400" />
            <span className="text-xs text-slate-400 uppercase">활성 듀티</span>
          </div>
          <div className="text-3xl font-bold text-cyan-400">{kpi.active_duties}</div>
          <div className="text-xs text-slate-500 mt-1">
            {kpi.duty_reduction_vs_trips && `트립 대비 ${kpi.duty_reduction_vs_trips}% 감축`}
          </div>
        </div>
        <div className="bg-gradient-to-br from-green-900/30 to-emerald-900/30 rounded-xl p-4 border border-green-700/30">
          <div className="flex items-center gap-2 mb-1">
            <Route size={14} className="text-green-400" />
            <span className="text-xs text-slate-400 uppercase">트립 커버리지</span>
          </div>
          <div className="text-3xl font-bold text-green-400">{kpi.coverage_rate}%</div>
          <div className="text-xs text-slate-500 mt-1">
            {kpi.covered_trips}/{kpi.total_trips} 트립 배정
          </div>
        </div>
      </div>

      {/* 상세 KPI */}
      <div className="grid grid-cols-3 gap-3">
        <div className="bg-slate-800/50 rounded-xl p-3 border border-slate-700 text-center">
          <div className="text-xs text-slate-500 mb-1">평균 트립/듀티</div>
          <div className="text-xl font-bold text-white">{kpi.avg_trips_per_duty}</div>
        </div>
        <div className="bg-slate-800/50 rounded-xl p-3 border border-slate-700 text-center">
          <div className="text-xs text-slate-500 mb-1">운전 효율</div>
          <div className="text-xl font-bold text-amber-400">{kpi.driving_efficiency}%</div>
        </div>
        <div className="bg-slate-800/50 rounded-xl p-3 border border-slate-700 text-center">
          <div className="text-xs text-slate-500 mb-1">제약 위반</div>
          <div className={`text-xl font-bold ${kpi.constraint_violations === 0 ? 'text-green-400' : 'text-red-400'}`}>
            {kpi.constraint_violations}건
          </div>
        </div>
      </div>

      {/* 시간 분포 */}
      <div className="bg-slate-800/50 rounded-xl p-4 border border-slate-700">
        <h3 className="text-sm font-bold text-slate-300 mb-3 flex items-center gap-2">
          <Clock size={14} className="text-slate-400" /> 시간 분석
        </h3>
        <div className="space-y-2 text-[13px]">
          <div className="flex justify-between">
            <span className="text-slate-500">운행 시간대</span>
            <span className="text-white">
              {kpi.earliest_start_hhmm || kpi.earliest_start || '-'} ~ {kpi.latest_end_hhmm || kpi.latest_end || '-'}
            </span>
          </div>
          <div className="flex justify-between">
            <span className="text-slate-500">총 운전시간</span>
            <span className="text-white">{formatNumber(kpi.total_driving_min)}분</span>
          </div>
          <div className="flex justify-between">
            <span className="text-slate-500">총 대기시간</span>
            <span className="text-white">{formatNumber(kpi.total_idle_min)}분</span>
          </div>
          <div className="flex justify-between">
            <span className="text-slate-500">평균 운전/듀티</span>
            <span className="text-white">{formatNumber(kpi.avg_driving_per_duty)}분</span>
          </div>
          <div className="flex justify-between">
            <span className="text-slate-500">평균 대기/듀티</span>
            <span className="text-white">{formatNumber(kpi.avg_idle_per_duty)}분</span>
          </div>
          {/* 운전/대기 비율 바 */}
          <div className="mt-2">
            <div className="w-full h-3 bg-slate-700 rounded-full overflow-hidden flex">
              <div className="h-full bg-cyan-500 transition-all"
                style={{ width: `${kpi.driving_efficiency || 0}%` }} />
              <div className="h-full bg-slate-600 transition-all"
                style={{ width: `${100 - (kpi.driving_efficiency || 0)}%` }} />
            </div>
            <div className="flex justify-between text-xs text-slate-600 mt-0.5">
              <span>운전 {kpi.driving_efficiency}%</span>
              <span>대기 {(100 - (kpi.driving_efficiency || 0)).toFixed(1)}%</span>
            </div>
          </div>
        </div>
      </div>

      {/* 실행 정보 */}
      <div className="bg-slate-800/30 rounded-xl p-4 border border-slate-700">
        <div className="grid grid-cols-3 gap-3 text-[12px]">
          <div>
            <span className="text-slate-500">솔버</span>
            <div className="text-white font-mono text-xs mt-0.5">{interpreted.solver_name}</div>
          </div>
          <div>
            <span className="text-slate-500">컴파일</span>
            <div className="text-white font-mono mt-0.5">{data.timing?.compile_sec}s</div>
          </div>
          <div>
            <span className="text-slate-500">실행</span>
            <div className="text-white font-mono mt-0.5">{data.timing?.execute_sec}s</div>
          </div>
        </div>
      </div>

      {/* 경고 */}
      {interpreted.warnings.length > 0 && (
        <div className="bg-yellow-500/5 rounded-xl border border-yellow-500/20 p-3">
          {interpreted.warnings.map((w, i) => (
            <div key={i} className="flex items-center gap-2 text-[12px] text-yellow-300">
              <AlertTriangle size={12} className="text-yellow-500 flex-shrink-0" />
              {w}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
