// src/components/analysis/OptimizationResultView.tsx
// v3.0 - Sub-tabs: Compile Report | KPI Dashboard | Duty Schedule | Constraint Check
import { useState, useEffect, useMemo } from 'react';
import {
  CheckCircle, XCircle, AlertTriangle, Cpu, BarChart3,
  Download, RotateCcw, ChevronDown, ChevronRight,
  Package, Zap, Clock, Users, Route, Shield,
  ArrowUpDown, TrendingUp
} from 'lucide-react';
import type { ResultData, CompileSummary, InterpretedResult, DutyDetail } from './types';

type SubTab = 'compile' | 'kpi' | 'schedule' | 'constraints';

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

  // compile_summary
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
  const constraints = compileSummary.constraints || { total_in_model: 0, applied: 0, failed: 0 };

  const hasExecuteResult = !!(data.status && data.status !== 'PENDING');
  const [activeTab, setActiveTab] = useState<SubTab>(hasInterpreted ? 'kpi' : hasExecuteResult ? 'kpi' : 'compile');
  const [warningsExpanded, setWarningsExpanded] = useState(false);
  const [expandedDuty, setExpandedDuty] = useState<number | null>(null);
  const [scheduleSortBy, setScheduleSortBy] = useState<'duty_id' | 'trip_count' | 'start_time' | 'driving'>('duty_id');
  const [scheduleFilter, setScheduleFilter] = useState<'all' | 'violations'>('all');

  useEffect(() => {
    if (hasInterpreted) setActiveTab('kpi');
    else if (hasExecuteResult) setActiveTab('kpi');
    else setActiveTab('compile');
  }, [data.status, hasInterpreted]);

  const status = data.status || 'UNKNOWN';
  const isFeasible = status === 'FEASIBLE' || status === 'OPTIMAL';

  const statusConfig: Record<string, { icon: any; color: string; bg: string; border: string; label: string }> = {
    OPTIMAL: { icon: CheckCircle, color: 'text-green-400', bg: 'bg-green-500/10', border: 'border-green-500/30', label: 'Optimal' },
    FEASIBLE: { icon: CheckCircle, color: 'text-blue-400', bg: 'bg-blue-500/10', border: 'border-blue-500/30', label: 'Feasible' },
    INFEASIBLE: { icon: XCircle, color: 'text-red-400', bg: 'bg-red-500/10', border: 'border-red-500/30', label: 'Infeasible' },
    UNKNOWN: { icon: AlertTriangle, color: 'text-yellow-400', bg: 'bg-yellow-500/10', border: 'border-yellow-500/30', label: 'Unknown' },
  };
  const sc = statusConfig[status] || statusConfig.UNKNOWN;
  const StatusIcon = sc.icon;

  // Sorted duties
  const sortedDuties = useMemo(() => {
    if (!interpreted?.duties) return [];
    const duties = [...interpreted.duties];
    const filtered = scheduleFilter === 'violations'
      ? duties.filter(d => d.violations && d.violations.length > 0)
      : duties;
    switch (scheduleSortBy) {
      case 'trip_count': return filtered.sort((a, b) => b.trip_count - a.trip_count);
      case 'start_time': return filtered.sort((a, b) => a.start_time_min - b.start_time_min);
      case 'driving': return filtered.sort((a, b) => b.total_driving_min - a.total_driving_min);
      default: return filtered.sort((a, b) => a.duty_id - b.duty_id);
    }
  }, [interpreted?.duties, scheduleSortBy, scheduleFilter]);

  const formatNumber = (n: any) => {
    if (n == null) return '-';
    if (typeof n === 'number') return Number.isInteger(n) ? n.toLocaleString() : n.toFixed(1);
    return String(n);
  };

  // ── KPI Dashboard ──
  const renderKPI = () => {
    if (!hasInterpreted) {
      // fallback: 기존 실행 결과
      return (
        <div className="space-y-4">
          <div className="grid grid-cols-3 gap-3">
            <div className="bg-slate-800/50 rounded-xl p-4 border border-slate-700 text-center">
              <div className="text-[11px] text-slate-500 uppercase mb-1">Objective</div>
              <div className={`text-2xl font-bold ${isFeasible ? 'text-green-400' : 'text-red-400'}`}>
                {formatNumber(data.objective_value)}
              </div>
            </div>
            <div className="bg-slate-800/50 rounded-xl p-4 border border-slate-700 text-center">
              <div className="text-[11px] text-slate-500 uppercase mb-1">Time</div>
              <div className="text-2xl font-bold text-cyan-400">{data.timing?.total_sec ?? '-'}s</div>
            </div>
            <div className="bg-slate-800/50 rounded-xl p-4 border border-slate-700 text-center">
              <div className="text-[11px] text-slate-500 uppercase mb-1">Variables</div>
              <div className="text-2xl font-bold text-white">{formatNumber(data.model_stats?.total_variables)}</div>
            </div>
          </div>
        </div>
      );
    }

    const kpi = interpreted!.kpi;
    return (
      <div className="space-y-4 animate-in fade-in duration-300">
        {/* 핵심 KPI */}
        <div className="grid grid-cols-2 gap-3">
          <div className="bg-gradient-to-br from-cyan-900/30 to-blue-900/30 rounded-xl p-4 border border-cyan-700/30">
            <div className="flex items-center gap-2 mb-1">
              <Users size={14} className="text-cyan-400" />
              <span className="text-[11px] text-slate-400 uppercase">활성 듀티</span>
            </div>
            <div className="text-3xl font-bold text-cyan-400">{kpi.active_duties}</div>
            <div className="text-[11px] text-slate-500 mt-1">
              {kpi.duty_reduction_vs_trips && `트립 대비 ${kpi.duty_reduction_vs_trips}% 감축`}
            </div>
          </div>
          <div className="bg-gradient-to-br from-green-900/30 to-emerald-900/30 rounded-xl p-4 border border-green-700/30">
            <div className="flex items-center gap-2 mb-1">
              <Route size={14} className="text-green-400" />
              <span className="text-[11px] text-slate-400 uppercase">트립 커버리지</span>
            </div>
            <div className="text-3xl font-bold text-green-400">{kpi.coverage_rate}%</div>
            <div className="text-[11px] text-slate-500 mt-1">
              {kpi.covered_trips}/{kpi.total_trips} 트립 배정
            </div>
          </div>
        </div>

        {/* 상세 KPI */}
        <div className="grid grid-cols-3 gap-3">
          <div className="bg-slate-800/50 rounded-xl p-3 border border-slate-700 text-center">
            <div className="text-[11px] text-slate-500 mb-1">평균 트립/듀티</div>
            <div className="text-xl font-bold text-white">{kpi.avg_trips_per_duty}</div>
          </div>
          <div className="bg-slate-800/50 rounded-xl p-3 border border-slate-700 text-center">
            <div className="text-[11px] text-slate-500 mb-1">운전 효율</div>
            <div className="text-xl font-bold text-amber-400">{kpi.driving_efficiency}%</div>
          </div>
          <div className="bg-slate-800/50 rounded-xl p-3 border border-slate-700 text-center">
            <div className="text-[11px] text-slate-500 mb-1">제약 위반</div>
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
              <span className="text-white">{kpi.earliest_start || '-'} ~ {kpi.latest_end || '-'}</span>
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
              <div className="flex justify-between text-[11px] text-slate-600 mt-0.5">
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
              <div className="text-white font-mono text-[11px] mt-0.5">{interpreted!.solver_name}</div>
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
        {interpreted!.warnings.length > 0 && (
          <div className="bg-yellow-500/5 rounded-xl border border-yellow-500/20 p-3">
            {interpreted!.warnings.map((w, i) => (
              <div key={i} className="flex items-center gap-2 text-[12px] text-yellow-300">
                <AlertTriangle size={12} className="text-yellow-500 flex-shrink-0" />
                {w}
              </div>
            ))}
          </div>
        )}
      </div>
    );
  };

  // ── Duty Schedule ──
  const renderSchedule = () => {
    if (!hasInterpreted) {
      return <div className="text-center text-slate-500 py-8">해석된 결과가 없습니다</div>;
    }

    return (
      <div className="space-y-3 animate-in fade-in duration-300">
        {/* 정렬/필터 */}
        <div className="flex gap-2 items-center">
          <select value={scheduleSortBy} onChange={e => setScheduleSortBy(e.target.value as any)}
            className="bg-slate-800 border border-slate-700 rounded-lg px-2 py-1 text-[12px] text-slate-300">
            <option value="duty_id">듀티 번호순</option>
            <option value="start_time">시작 시각순</option>
            <option value="trip_count">트립 수순</option>
            <option value="driving">운전시간순</option>
          </select>
          <select value={scheduleFilter} onChange={e => setScheduleFilter(e.target.value as any)}
            className="bg-slate-800 border border-slate-700 rounded-lg px-2 py-1 text-[12px] text-slate-300">
            <option value="all">전체 ({interpreted!.duties.length})</option>
            <option value="violations">위반만 ({interpreted!.duties.filter(d => d.violations?.length).length})</option>
          </select>
          <span className="text-[11px] text-slate-500 ml-auto">{sortedDuties.length}개 듀티</span>
        </div>

        {/* 듀티 목록 */}
        <div className="space-y-2 max-h-[calc(100vh-320px)] overflow-y-auto custom-scrollbar">
          {sortedDuties.map(duty => {
            const isExpanded = expandedDuty === duty.duty_id;
            const hasViolation = duty.violations && duty.violations.length > 0;
            return (
              <div key={duty.duty_id}
                className={`rounded-xl border transition-all ${
                  hasViolation
                    ? 'bg-red-500/5 border-red-500/20'
                    : 'bg-slate-800/50 border-slate-700'
                }`}>
                <button onClick={() => setExpandedDuty(isExpanded ? null : duty.duty_id)}
                  className="w-full p-3 flex items-center gap-3 text-left">
                  <div className={`w-10 h-10 rounded-lg flex items-center justify-center text-sm font-bold ${
                    hasViolation ? 'bg-red-500/20 text-red-400' : 'bg-cyan-500/10 text-cyan-400'
                  }`}>
                    D{duty.duty_id}
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="text-[13px] text-white font-medium">
                        {duty.start_hhmm} ~ {duty.end_hhmm}
                      </span>
                      <span className="text-[11px] text-slate-500">
                        {duty.trip_count}트립
                      </span>
                      {hasViolation && (
                        <span className="text-[10px] px-1.5 py-0.5 rounded bg-red-500/20 text-red-400">위반</span>
                      )}
                    </div>
                    <div className="flex gap-3 text-[11px] text-slate-500 mt-0.5">
                      <span>운전 {duty.total_driving_min}분</span>
                      <span>대기 {duty.idle_min}분</span>
                      <span>체류 {duty.total_stay_min}분</span>
                    </div>
                  </div>
                  {isExpanded ? <ChevronDown size={14} className="text-slate-500" />
                    : <ChevronRight size={14} className="text-slate-500" />}
                </button>

                {isExpanded && (
                  <div className="px-3 pb-3 border-t border-slate-700/50">
                    {/* 위반 사항 */}
                    {hasViolation && (
                      <div className="mt-2 mb-2">
                        {duty.violations!.map((v, i) => (
                          <div key={i} className="flex items-center gap-1 text-[11px] text-red-400">
                            <XCircle size={10} /> {v}
                          </div>
                        ))}
                      </div>
                    )}
                    {/* 트립 타임라인 */}
                    <div className="mt-2 space-y-1">
                      {duty.trips.map((trip, i) => (
                        <div key={trip.trip_id}
                          className="flex items-center gap-2 text-[11px] py-1 px-2 rounded bg-slate-800/50">
                          <span className="text-slate-600 w-4">{i+1}</span>
                          <span className="text-cyan-400 font-mono w-16">{trip.dep_hhmm}→{trip.arr_hhmm}</span>
                          <span className="text-slate-400 flex-1 truncate">
                            {trip.dep_station} → {trip.arr_station}
                          </span>
                          <span className="text-slate-500 w-10 text-right">{trip.duration}분</span>
                          <span className={`w-12 text-right ${trip.direction === 'forward' ? 'text-blue-400' : 'text-amber-400'}`}>
                            {trip.direction === 'forward' ? '상행' : '하행'}
                          </span>
                        </div>
                      ))}
                    </div>
                    {/* 듀티 요약 바 */}
                    <div className="mt-2 flex gap-2 text-[10px] text-slate-500">
                      {duty.crew_id && <span>승무원 #{duty.crew_id}</span>}
                      <span>근무 {duty.total_work_min}분</span>
                      <span>체류 {duty.total_stay_min}분</span>
                    </div>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </div>
    );
  };

  // ── Constraint Check ──
  const renderConstraints = () => {
    if (!hasInterpreted) {
      return <div className="text-center text-slate-500 py-8">해석된 결과가 없습니다</div>;
    }

    return (
      <div className="space-y-4 animate-in fade-in duration-300">
        {/* 전체 현황 */}
        <div className="grid grid-cols-2 gap-3">
          <div className={`rounded-xl p-4 border text-center ${
            interpreted!.kpi.constraint_violations === 0
              ? 'bg-green-500/5 border-green-500/20'
              : 'bg-red-500/5 border-red-500/20'
          }`}>
            <Shield size={24} className={`mx-auto mb-2 ${
              interpreted!.kpi.constraint_violations === 0 ? 'text-green-400' : 'text-red-400'
            }`} />
            <div className="text-lg font-bold text-white">
              {interpreted!.constraint_status.filter(c => c.satisfied).length}/{interpreted!.constraint_status.length}
            </div>
            <div className="text-[11px] text-slate-500">제약 충족</div>
          </div>
          <div className={`rounded-xl p-4 border text-center ${
            interpreted!.kpi.constraint_violations === 0
              ? 'bg-green-500/5 border-green-500/20'
              : 'bg-red-500/5 border-red-500/20'
          }`}>
            <AlertTriangle size={24} className={`mx-auto mb-2 ${
              interpreted!.kpi.constraint_violations === 0 ? 'text-green-400' : 'text-red-400'
            }`} />
            <div className="text-lg font-bold text-white">{interpreted!.kpi.constraint_violations}건</div>
            <div className="text-[11px] text-slate-500">듀티 위반</div>
          </div>
        </div>

        {/* 제약별 상세 */}
        <div className="space-y-2">
          {interpreted!.constraint_status.map((cs, i) => (
            <div key={i} className={`rounded-xl p-4 border ${
              cs.satisfied
                ? 'bg-slate-800/50 border-slate-700'
                : 'bg-red-500/5 border-red-500/20'
            }`}>
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  {cs.satisfied
                    ? <CheckCircle size={16} className="text-green-400" />
                    : <XCircle size={16} className="text-red-400" />}
                  <span className="text-[13px] text-white font-medium">{cs.name}</span>
                </div>
                <span className={`text-[13px] font-mono ${cs.satisfied ? 'text-green-400' : 'text-red-400'}`}>
                  {cs.max_actual}
                </span>
              </div>
              <div className="mt-2">
                <div className="flex justify-between text-[11px] text-slate-500 mb-1">
                  <span>사용량</span>
                  <span>상한 {cs.limit}</span>
                </div>
                <div className="w-full h-2 bg-slate-700 rounded-full overflow-hidden">
                  <div className={`h-full rounded-full transition-all ${cs.satisfied ? 'bg-green-500' : 'bg-red-500'}`}
                    style={{
                      width: `${Math.min(100, (parseFloat(cs.max_actual) / parseFloat(cs.limit)) * 100)}%`
                    }} />
                </div>
              </div>
            </div>
          ))}
        </div>

        {/* 위반 듀티 목록 */}
        {interpreted!.duties.some(d => d.violations?.length) && (
          <div className="bg-red-500/5 rounded-xl border border-red-500/20 p-4">
            <h3 className="text-sm font-bold text-red-400 mb-2">위반 듀티 상세</h3>
            {interpreted!.duties
              .filter(d => d.violations?.length)
              .map(d => (
                <div key={d.duty_id} className="mb-2 text-[12px]">
                  <span className="text-white font-medium">Duty {d.duty_id}</span>
                  {d.violations!.map((v, i) => (
                    <div key={i} className="text-red-300 ml-4">• {v}</div>
                  ))}
                </div>
              ))}
          </div>
        )}
      </div>
    );
  };

  // ── Compile Report (기존 유지) ──
  const renderCompileReport = () => (
    <div className="space-y-4 animate-in fade-in duration-300">
      <div className="bg-slate-800/50 rounded-xl border border-slate-700 p-4">
        <h3 className="text-sm font-bold text-slate-300 mb-3 flex items-center gap-2">
          <Package size={14} className="text-cyan-400" /> 컴파일 요약
        </h3>
        <div className="space-y-2 text-[13px]">
          <div className="flex justify-between">
            <span className="text-slate-500">솔버</span>
            <span className="text-cyan-400 font-mono">{compileSummary.solver_name} ({compileSummary.solver_type})</span>
          </div>
          <div className="flex justify-between">
            <span className="text-slate-500">변수</span>
            <span className="text-white font-mono">{formatNumber(compileSummary.variables_created)}개</span>
          </div>
          <div className="flex justify-between items-center">
            <span className="text-slate-500">제약조건</span>
            <span className={`font-mono font-bold ${constraints.failed > 0 ? 'text-yellow-400' : 'text-green-400'}`}>
              {constraints.applied}/{constraints.total_in_model}
            </span>
          </div>
          {constraints.total_in_model > 0 && (
            <div className="mt-1">
              <div className="w-full h-2 bg-slate-700 rounded-full overflow-hidden">
                <div className={`h-full rounded-full ${constraints.failed === 0 ? 'bg-green-500' : 'bg-yellow-500'}`}
                  style={{ width: `${(constraints.applied / constraints.total_in_model) * 100}%` }} />
              </div>
            </div>
          )}
          <div className="flex justify-between">
            <span className="text-slate-500">목적함수</span>
            <span className={compileSummary.objective_parsed === false ? 'text-yellow-400' : 'text-green-400'}>
              {compileSummary.objective_parsed === false ? 'default' : 'parsed'}
            </span>
          </div>
          <div className="flex justify-between">
            <span className="text-slate-500">컴파일 시간</span>
            <span className="text-white font-mono">{compileSummary.compile_time_sec ?? '-'}s</span>
          </div>
        </div>
      </div>

      {compileWarnings.length > 0 && (
        <div className="bg-yellow-500/5 rounded-xl border border-yellow-500/20 p-4">
          <button onClick={() => setWarningsExpanded(!warningsExpanded)}
            className="w-full flex items-center justify-between text-sm font-bold text-yellow-400">
            <span className="flex items-center gap-2"><AlertTriangle size={14} /> 경고 ({compileWarnings.length})</span>
            {warningsExpanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
          </button>
          {warningsExpanded && (
            <div className="mt-3 space-y-1 max-h-40 overflow-y-auto custom-scrollbar">
              {compileWarnings.map((w: string, i: number) => (
                <div key={i} className="text-[11px] text-yellow-300/80">#{i+1} {w}</div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );

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

        {/* Tab selector */}
        <div className="flex gap-1 bg-slate-800/50 rounded-lg p-0.5">
          {tabs.map(tab => {
            const TabIcon = tab.icon;
            return (
              <button key={tab.key} onClick={() => setActiveTab(tab.key)}
                className={`flex-1 py-1.5 px-2 rounded-md text-[11px] font-medium transition-all flex items-center justify-center gap-1 ${
                  activeTab === tab.key
                    ? 'bg-slate-700 text-white shadow-sm'
                    : 'text-slate-400 hover:text-slate-300'
                }`}>
                <TabIcon size={12} />
                {tab.label}
                {tab.badge && (
                  <span className={`ml-0.5 px-1 py-0 text-[9px] rounded-full ${
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
      <div className="flex-1 overflow-y-auto custom-scrollbar p-4">
        {activeTab === 'kpi' && renderKPI()}
        {activeTab === 'schedule' && renderSchedule()}
        {activeTab === 'constraints' && renderConstraints()}
        {activeTab === 'compile' && renderCompileReport()}
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
        <button onClick={() => onAction?.('send', '솔버 추천 결과 보여줘')}
          className="flex-1 py-2 rounded-xl text-[12px] font-medium text-slate-300 bg-slate-800 hover:bg-slate-700 transition flex items-center justify-center gap-1.5">
          <RotateCcw size={13} /> 다른 솔버
        </button>
      </div>
    </div>
  );
}
