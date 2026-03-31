// src/components/analysis/SolverView.tsx
import { useState, useEffect, useCallback, useReducer, useRef } from 'react';
import { Cpu, Loader2, Activity, Play, GitCompare, ListChecks, CheckCircle, XCircle, StopCircle } from 'lucide-react';
import { useAuth } from '../../context/AuthContext';
import { useJobPolling } from '../../hooks/useJobPolling';
import { StepItem } from './StepItem';
import { SolverCard } from './SolverCard';
import { InfeasibilityPanel } from './InfeasibilityPanel';
import { CompareResultsPanel } from './CompareResultsPanel';
import type { SolverData, ProblemProfile } from './types';

// ── Compare Mode Reducer ──
interface CompareJobState {
  jobId: number | null;
  status: string;
  progress: string;
  progressPct: number;
  result: any | null;
  error: string | null;
}
interface CompareState {
  jobs: Record<number, CompareJobState>;
  groupId: string | null;
}
type CompareAction =
  | { type: 'INIT'; groupId: string; indices: number[] }
  | { type: 'SUBMIT'; idx: number; jobId: number }
  | { type: 'UPDATE'; idx: number; status: string; progress?: string; progressPct?: number; result?: any; error?: string }
  | { type: 'RESET' };

function compareReducer(state: CompareState, action: CompareAction): CompareState {
  switch (action.type) {
    case 'INIT': {
      const jobs: Record<number, CompareJobState> = {};
      action.indices.forEach(i => { jobs[i] = { jobId: null, status: 'pending', progress: '대기 중', progressPct: 0, result: null, error: null }; });
      return { jobs, groupId: action.groupId };
    }
    case 'SUBMIT':
      return { ...state, jobs: { ...state.jobs, [action.idx]: { ...state.jobs[action.idx], jobId: action.jobId } } };
    case 'UPDATE':
      return {
        ...state,
        jobs: {
          ...state.jobs,
          [action.idx]: {
            ...state.jobs[action.idx],
            status: action.status,
            progress: action.progress ?? state.jobs[action.idx]?.progress ?? '',
            progressPct: action.progressPct ?? state.jobs[action.idx]?.progressPct ?? 0,
            result: action.result ?? state.jobs[action.idx]?.result,
            error: action.error ?? state.jobs[action.idx]?.error ?? null,
          },
        },
      };
    case 'RESET':
      return { jobs: {}, groupId: null };
    default:
      return state;
  }
}

// ── Main Component ──
export function SolverView({
  data,
  onAction,
  onResultReady,
  projectId,
}: {
  data: SolverData;
  onAction?: (type: string, message: string) => void;
  onResultReady?: (data: any) => void;
  projectId?: string;
}) {
  const { authFetch } = useAuth();
  const jobPoll = useJobPolling(authFetch);

  const [step, setStep] = useState(0);
  const [execMode, setExecMode] = useState<'auto' | 'step' | 'compare'>('auto');
  const [selectedSolver, setSelectedSolver] = useState<number>(0);
  const [compareSelection, setCompareSelection] = useState<Set<number>>(new Set([0]));
  const [stepPhase, setStepPhase] = useState<'select' | 'compiled' | 'running' | 'done'>('select');
  const [compileInfo, setCompileInfo] = useState<any>(null);
  const [infeasibilityInfo, setInfeasibilityInfo] = useState<any>(null);
  const [selectedStrategyId, setSelectedStrategyId] = useState<string>('');
  const [timeLimitOverride, setTimeLimitOverride] = useState<string>(''); // 런타임 시간 오버라이드
  const [selectedStrategyType, setSelectedStrategyType] = useState<string>('');

  // Compare mode state
  const [compareState, compareDispatch] = useReducer(compareReducer, { jobs: {}, groupId: null });
  const compareIntervalsRef = useRef<Map<number, ReturnType<typeof setInterval>>>(new Map());

  const solvers = data.recommended_solvers || [];
  const profile = data.problem_profile || {} as ProblemProfile;

  // Derive job status for UI from either single-job or compare mode
  const jobStatus = execMode === 'compare'
    ? (Object.keys(compareState.jobs).length > 0
      ? (Object.values(compareState.jobs).every(j => ['complete', 'failed', 'cancelled', 'COMPLETE', 'FAILED', 'CANCELLED'].includes(j.status)) ? 'done'
        : Object.values(compareState.jobs).some(j => ['running', 'pending', 'RUNNING', 'PENDING'].includes(j.status)) ? 'executing' : 'idle')
      : 'idle')
    : (['pending', 'running'].includes(jobPoll.status) ? 'executing'
      : jobPoll.status === 'complete' ? 'done'
      : ['failed', 'cancelled'].includes(jobPoll.status) ? 'error' : 'idle');

  useEffect(() => {
    setStep(0);
    jobPoll.reset();
    compareDispatch({ type: 'RESET' });
    const timer = setInterval(() => { setStep(prev => (prev < 3 ? prev + 1 : prev)); }, 800);
    return () => clearInterval(timer);
  }, [data]);

  // Single job: react to terminal status
  useEffect(() => {
    if (execMode === 'compare') return;
    const result = jobPoll.result;

    if (jobPoll.status === 'complete' && result) {
      const hasResult = result.success || (result.summary && Object.keys(result.summary).length > 0);
      if (hasResult) {
        const solver = solvers[selectedSolver];
        const label = `${solver?.provider || ''} ${solver?.solver_name || ''}`.trim();
        const resultView = {
          view_mode: 'result',
          ...result.summary,
          solver_id: result.solver_id,
          solver_name: result.solver_name,
          compile_summary: result.summary?.compile_summary,
          execute_summary: result.summary?.execute_summary,
        };
        onResultReady?.(resultView);
        const statusText = result.success ? '완료' : '완료 (일부 제약 미충족)';
        onAction?.('execute_done', `${label}으로 최적화 실행이 ${statusText}되었습니다. 결과를 설명해주세요.`);
      }
      // 성공 시 이전 infeasibility 정보 초기화, 실패 시 새 정보 설정
      if (result.success) {
        setInfeasibilityInfo(null);
      } else {
        setInfeasibilityInfo(result.infeasibility_info || result.summary?.infeasibility_info || null);
      }
    }

    // FAILED 상태에서도 infeasibility 진단 정보 표시 (INFEASIBLE → FAILED 경로)
    if (jobPoll.status === 'failed' && result) {
      setInfeasibilityInfo(result.infeasibility_info || result.summary?.infeasibility_info || null);
    }
  }, [jobPoll.status, jobPoll.result]);

  // ── Handlers ──

  const handleAutoRun = useCallback(async (strategy?: string) => {
    const solver = solvers[selectedSolver];
    if (!solver || !projectId) return;
    setInfeasibilityInfo(null);
    // 선택된 전략 사용 (없으면 인자로 전달된 strategy 사용)
    const effectiveStrategy = strategy || selectedStrategyType || undefined;
    // 전략에 따른 표시 이름 결정
    const relatedStrategies = data.execution_strategies?.filter((st: any) => {
      if (st.strategy_type === 'parallel_comparison') return false;
      const steps = st.steps || [];
      if (steps.length <= 1) {
        return steps.some((s: any) => s.solver_name === solver.solver_name);
      }
      return steps[0]?.solver_name === solver.solver_name;
    }) || [];
    const matchedStrategy = relatedStrategies.find((st: any) =>
      st.strategy_type === effectiveStrategy || st.strategy_id === selectedStrategyId
    );
    const displayName = matchedStrategy?.name
      || (effectiveStrategy === 'quantum_warmstart' ? 'CQM → CP-SAT Hybrid' : `${solver.provider} ${solver.solver_name}`.trim());

    const timeOverride = timeLimitOverride ? parseInt(timeLimitOverride) : undefined;
    await jobPoll.submitJob(
      projectId,
      solver.solver_id,
      displayName,
      undefined,
      effectiveStrategy,
      timeOverride,
    );
  }, [selectedSolver, solvers, projectId, jobPoll, selectedStrategyType, selectedStrategyId, data.execution_strategies, timeLimitOverride]);

  const handleStepRun = useCallback(async () => {
    if (stepPhase === 'select') {
      setStepPhase('compiled');
      const solver = solvers[selectedSolver];
      setTimeout(() => {
        setCompileInfo({
          solver_name: solver?.solver_name,
          variable_count: profile.variable_count || '-',
          constraint_count: profile.constraint_count || '-',
          variable_types: profile.variable_types?.join(', ') || '-',
          time_limit_sec: solver?.time_limit_sec || null,
        });
      }, 1000);
      return;
    }
    if (stepPhase === 'compiled') {
      setStepPhase('running');
      const solver = solvers[selectedSolver];
      if (!solver || !projectId) return;
      setInfeasibilityInfo(null);
      await jobPoll.submitJob(
        projectId,
        solver.solver_id,
        `${solver.provider} ${solver.solver_name}`.trim(),
      );
    }
  }, [stepPhase, selectedSolver, solvers, profile, projectId, jobPoll]);

  // ── Compare: Poll individual jobs ──
  const pollCompareJob = useCallback(async (idx: number, jobId: number) => {
    try {
      const res = await authFetch(`/api/jobs/${jobId}`);
      if (!res.ok) return;
      const d = await res.json();
      compareDispatch({
        type: 'UPDATE', idx,
        status: d.status,
        progress: d.progress,
        progressPct: d.progress_pct,
        result: d.result,
        error: d.error,
      });
      if (['COMPLETE', 'FAILED', 'CANCELLED'].includes(d.status)) {
        const iv = compareIntervalsRef.current.get(idx);
        if (iv) { clearInterval(iv); compareIntervalsRef.current.delete(idx); }
      }
    } catch { /* retry on next tick */ }
  }, [authFetch]);

  const handleCompareRun = useCallback(async () => {
    if (compareSelection.size < 2 || !projectId) return;
    const indices = Array.from(compareSelection);
    compareDispatch({ type: 'INIT', groupId: 'pending', indices });

    try {
      // 단일 compare API: 백엔드가 Column Gen 1회 + solver별 순차 실행 (동일 pool 보장)
      const selectedSolvers = indices.map(i => solvers[i]).filter(Boolean);
      const solverIds = selectedSolvers.map(s => s.solver_id);
      const solverNames: Record<string, string> = {};
      selectedSolvers.forEach(s => { solverNames[s.solver_id] = `${s.provider} ${s.solver_name}`.trim(); });

      const res = await authFetch(`/api/jobs/compare`, {
        method: 'POST',
        body: JSON.stringify({
          project_id: Number(projectId),
          solver_ids: solverIds,
          solver_names: solverNames,
        }),
      });

      if (res.ok) {
        const d = await res.json();
        // 각 job에 대해 폴링 시작
        d.jobs.forEach((jobInfo: any, i: number) => {
          const idx = indices[i];
          compareDispatch({ type: 'SUBMIT', idx, jobId: jobInfo.job_id });
          const iv = setInterval(() => pollCompareJob(idx, jobInfo.job_id), 2000);
          compareIntervalsRef.current.set(idx, iv);
        });
      }
    } catch (err: any) {
      indices.forEach(idx => {
        compareDispatch({ type: 'UPDATE', idx, status: 'FAILED', error: err.message });
      });
    }
  }, [compareSelection, solvers, projectId, authFetch, pollCompareJob]);

  // Compare: detect all done → pick best result
  useEffect(() => {
    const entries = Object.entries(compareState.jobs);
    if (entries.length === 0) return;
    const allDone = entries.every(([, j]) => ['COMPLETE', 'FAILED', 'CANCELLED', 'complete', 'failed', 'cancelled'].includes(j.status));
    if (!allDone) return;

    // Find best (minimize by default — check objective type from data)
    const successes = entries.filter(([, j]) => j.result?.success).map(([idx, j]) => ({ idx: Number(idx), result: j.result }));
    if (successes.length > 0) {
      // Pick min objective_value (most optimization problems minimize)
      const best = successes.reduce((a, b) => {
        const aObj = a.result?.summary?.objective_value ?? Infinity;
        const bObj = b.result?.summary?.objective_value ?? Infinity;
        return aObj <= bObj ? a : b;
      });
      const resultView = {
        view_mode: 'result',
        ...(best.result.summary || {}),
        compare_mode: true,
        solver_id: best.result.solver_id,
        solver_name: best.result.solver_name,
        compile_summary: best.result.summary?.compile_summary,
        execute_summary: best.result.summary?.execute_summary,
      };
      onResultReady?.(resultView);
    }
    onAction?.('execute_done', `${entries.length}개 솔버 비교 실행이 완료되었습니다.`);
  }, [compareState.jobs]);

  // Cleanup compare intervals on unmount
  useEffect(() => {
    return () => {
      compareIntervalsRef.current.forEach(iv => clearInterval(iv));
      compareIntervalsRef.current.clear();
    };
  }, []);

  const handleReset = () => {
    jobPoll.reset();
    compareDispatch({ type: 'RESET' });
    compareIntervalsRef.current.forEach(iv => clearInterval(iv));
    compareIntervalsRef.current.clear();
    setStepPhase('select');
    setCompileInfo(null);
    setInfeasibilityInfo(null);
  };

  const handleRun = () => {
    if (execMode === 'auto') handleAutoRun();
    else if (execMode === 'step') handleStepRun();
    else if (execMode === 'compare') handleCompareRun();
  };

  const getPriorityLabel = (p?: string) => {
    if (p === 'accuracy') return { icon: '🎯', label: '정확성 우선' };
    if (p === 'speed') return { icon: '⚡', label: '속도 우선' };
    if (p === 'cost') return { icon: '💰', label: '비용 우선' };
    return { icon: '🔄', label: '자동 (균형)' };
  };

  const priority = getPriorityLabel(data.priority);

  const handleSolverSelect = (idx: number) => {
    if (execMode === 'compare') {
      setCompareSelection(prev => {
        const next = new Set(prev);
        if (next.has(idx)) next.delete(idx);
        else if (next.size < 3) next.add(idx);
        return next;
      });
    } else {
      setSelectedSolver(idx);
      // 솔버 변경 시 전략 초기화 (새 솔버의 추천 전략이 자동 선택됨)
      setSelectedStrategyId('');
      setSelectedStrategyType('');
    }
  };

  const handleStrategySelect = (strategyId: string, strategyType: string) => {
    setSelectedStrategyId(strategyId);
    setSelectedStrategyType(strategyType);
  };

  const selectedSolverData = solvers[selectedSolver];
  const estimatedTime = selectedSolverData?.estimated_time;

  // 전략별 시간 계산
  const isHybridStrategy = selectedStrategyType === 'quantum_warmstart';
  const CQM_FIXED_TIME = 120; // D-Wave CQM 고정 시간 (초)
  const cpSatSolver = solvers.find(s => s.category?.startsWith('classical'));
  const cqmSolver = solvers.find(s => s.solver_id === 'dwave_hybrid_cqm');

  let timeLimitSec: number | null;
  let timeDisplayInfo: { total: number; cqm?: number; cpsat?: number } | null = null;

  if (isHybridStrategy && cpSatSolver && cqmSolver) {
    const cpSatTime = cpSatSolver.time_limit_sec || 900;
    const totalTime = CQM_FIXED_TIME + cpSatTime;
    timeLimitSec = totalTime;
    timeDisplayInfo = { total: totalTime, cqm: CQM_FIXED_TIME, cpsat: cpSatTime };
  } else {
    timeLimitSec = selectedSolverData?.time_limit_sec || null;
  }

  // Compare mode results for CompareResultsPanel
  const compareResults: Record<number, any> = {};
  const compareRunning = new Set<number>();
  Object.entries(compareState.jobs).forEach(([idx, j]) => {
    if (j.result) compareResults[Number(idx)] = j.result;
    if (['pending', 'running', 'PENDING', 'RUNNING'].includes(j.status)) compareRunning.add(Number(idx));
  });

  return (
    <div className="h-full flex flex-col bg-slate-900">
      {/* 고정 헤더 */}
      <div className="flex-shrink-0 p-6 pb-4 border-b border-slate-800">
        <div className="flex items-center space-x-3">
          <div className="p-3 bg-cyan-900/30 rounded-xl border border-cyan-500/30">
            <Cpu className="text-cyan-400" size={24} />
          </div>
          <div>
            <h2 className="text-lg font-bold text-white">Quantum Solver Engine</h2>
            <p className="text-sm text-slate-400">
              {jobStatus === 'executing' ? 'Running Hybrid Algorithm...' : 'Solver Recommendation'}
            </p>
          </div>
        </div>
      </div>

      {/* 스크롤 콘텐츠 */}
      <div className="flex-1 overflow-y-auto custom-scrollbar p-6 pt-4">

        {/* 실행 오류 표시 */}
        {jobStatus === 'error' && jobPoll.error && (
          <div className="mb-4 p-4 bg-red-900/30 border border-red-500/50 rounded-xl">
            <div className="flex items-center gap-2 mb-2">
              <XCircle size={14} className="text-red-400" />
              <span className="text-red-400 font-semibold">실행 오류</span>
            </div>
            <p className="text-sm text-red-300 whitespace-pre-wrap">{jobPoll.error}</p>
            <InfeasibilityPanel info={infeasibilityInfo} />
            <div className="mt-3 flex gap-2">
              <button onClick={handleReset} className="px-3 py-1.5 text-xs bg-slate-700 hover:bg-slate-600 text-white rounded-lg transition-colors">
                닫기
              </button>
              <button onClick={() => { handleReset(); setTimeout(handleRun, 100); }} className="px-3 py-1.5 text-xs bg-cyan-700 hover:bg-cyan-600 text-white rounded-lg transition-colors">
                다시 실행
              </button>
            </div>
          </div>
        )}

        {jobStatus !== 'executing' ? (
          <>
            <div className="space-y-3 mb-6">
              <StepItem label="Problem Profiling" status={step > 0 ? 'done' : 'active'} />
              <StepItem label="Solver Scoring" status={step > 1 ? 'done' : step === 1 ? 'active' : 'wait'} />
              <StepItem label="Recommendation" status={step > 2 ? 'done' : step === 2 ? 'active' : 'wait'} />
            </div>

            {step >= 3 && (
              <div className="space-y-4 animate-in slide-in-from-bottom-4 fade-in duration-500">
                {/* 추천 기준 */}
                <div className="bg-slate-800/50 rounded-xl border border-slate-700 p-3 flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <span className="text-[13px] text-slate-400">추천 기준:</span>
                    <span className="text-[13px] font-bold text-cyan-400">
                      {priority.icon} {priority.label}
                    </span>
                  </div>
                  <span className="text-[13px] text-slate-500">
                    {solvers.length}개 솔버 분석됨
                  </span>
                </div>

                {/* 문제 프로파일 */}
                <div className="bg-slate-800/50 rounded-xl border border-slate-700 p-4">
                  <h4 className="text-[13px] font-bold text-slate-400 uppercase mb-3 flex items-center">
                    <Activity size={12} className="mr-1 text-blue-400" /> Problem Profile
                  </h4>
                  <div className="grid grid-cols-3 gap-2 text-center text-sm">
                    <div>
                      <span className="block text-slate-500 text-[13px]">변수</span>
                      <span className="text-white font-mono">{profile.variable_count?.toLocaleString() ?? '-'}</span>
                    </div>
                    <div>
                      <span className="block text-slate-500 text-[13px]">제약조건</span>
                      <span className="text-white font-mono">{profile.constraint_count ?? '-'}</span>
                    </div>
                    <div>
                      <span className="block text-slate-500 text-[13px]">변수타입</span>
                      <span className="text-cyan-400 font-mono text-[13px]">{profile.variable_types?.join(', ') ?? '-'}</span>
                    </div>
                  </div>
                  {profile.problem_classes && (
                    <div className="mt-2 text-center">
                      <span className="text-slate-500 text-[13px]">문제 유형: </span>
                      <span className="text-slate-300 text-[13px]">{profile.problem_classes.join(', ')}</span>
                    </div>
                  )}
                </div>

                {/* 솔버 목록 */}
                <div className="space-y-2">
                  <h4 className="text-[13px] font-bold text-slate-400 uppercase">
                    Recommended ({solvers.length})
                  </h4>
                  {solvers.map((svr, idx) => (
                    <SolverCard
                      key={svr.solver_id || idx}
                      svr={svr}
                      idx={idx}
                      isSelected={execMode === 'compare' ? compareSelection.has(idx) : selectedSolver === idx}
                      isCompareMode={execMode === 'compare'}
                      strategies={data.execution_strategies}
                      recommendedStrategy={data.recommended_strategy}
                      selectedStrategyId={selectedSolver === idx ? selectedStrategyId : undefined}
                      onSelect={() => handleSolverSelect(idx)}
                      onStrategySelect={handleStrategySelect}
                    />
                  ))}
                </div>
              </div>
            )}
          </>
        ) : null}
      </div>

      {/* Execution Mode UI — idle 또는 done(결과 있지만 다른 솔버 선택 가능) */}
      {step >= 3 && ['idle', 'done', 'error'].includes(jobStatus) && (
        <div className="flex-shrink-0 border-t border-slate-800">
          {/* Mode selector */}
          <div className="flex p-2 gap-1 bg-slate-900/80">
            {(['auto', 'step', 'compare'] as const).map((mode) => (
              <button
                key={mode}
                onClick={() => { setExecMode(mode); handleReset(); }}
                className={`flex-1 py-1.5 px-2 rounded-lg text-[12px] font-medium transition-all flex items-center justify-center gap-1 ${
                  execMode === mode
                    ? 'bg-cyan-600 text-white'
                    : 'bg-slate-800 text-slate-400 hover:bg-slate-700'
                }`}
              >
                {mode === 'auto' && <><Play size={11} /> 자동 실행</>}
                {mode === 'step' && <><ListChecks size={11} /> 단계별</>}
                {mode === 'compare' && <><GitCompare size={11} /> 비교</>}
              </button>
            ))}
          </div>

          {/* Selected solver summary */}
          <div className="px-3 py-2">
            {execMode === 'compare' ? (
              <div className="text-[11px] text-slate-400">
                비교 솔버: {Array.from(compareSelection).map(i => solvers[i]?.solver_name).filter(Boolean).join(', ') || '위에서 2~3개를 클릭하세요'}
              </div>
            ) : (() => {
              // 현재 선택된 전략명 계산
              const _relStrats = data.execution_strategies?.filter((st: any) => {
                if (st.strategy_type === 'parallel_comparison') return false;
                const steps = st.steps || [];
                if (steps.length <= 1) {
                  return steps.some((s: any) => s.solver_name === selectedSolverData?.solver_name);
                }
                return steps[0]?.solver_name === selectedSolverData?.solver_name;
              }) || [];
              const _activeStrat = _relStrats.find((st: any) => st.strategy_id === selectedStrategyId)
                || _relStrats.find((st: any) => data.recommended_strategy?.strategy_id === st.strategy_id)
                || _relStrats[0];
              const stratLabel = _activeStrat?.name || '';
              return (
                <div className="text-[11px] text-slate-400 flex justify-between">
                  <span>
                    <span className="text-cyan-400 font-medium">{selectedSolverData?.solver_name || '-'}</span>
                    {stratLabel && <span className="text-slate-500"> › {stratLabel}</span>}
                  </span>
                  <div className="flex items-center gap-2">
                    <input
                      type="text"
                      inputMode="numeric"
                      placeholder={String(timeLimitSec || 900)}
                      value={timeLimitOverride}
                      onChange={(e) => { if (e.target.value === '' || /^\d+$/.test(e.target.value)) setTimeLimitOverride(e.target.value); }}
                      className="w-16 px-1.5 py-0.5 text-[11px] bg-slate-800 border border-slate-700 rounded text-white text-center font-mono focus:outline-none focus:border-cyan-500"
                    />
                    <span className="text-slate-500">초</span>
                    {timeDisplayInfo && (
                      <span className="text-slate-600 text-[10px]">
                        (CQM: {timeDisplayInfo.cqm}초 고정 / CP-SAT: {(parseInt(timeLimitOverride) || timeDisplayInfo.total) - timeDisplayInfo.cqm}초)
                      </span>
                    )}
                  </div>
                </div>
              );
            })()}
          </div>

          {/* Step-by-step: compile info */}
          {execMode === 'step' && compileInfo && (
            <div className="mx-3 mb-2 p-3 rounded-lg bg-slate-800/50 border border-slate-700 space-y-1">
              <div className="text-[11px] text-slate-500 uppercase mb-1">컴파일 결과</div>
              <div className="text-[12px] text-slate-300">솔버: <span className="text-cyan-400">{compileInfo.solver_name}</span></div>
              <div className="text-[12px] text-slate-300">변수: <span className="text-white font-mono">{compileInfo.variable_count}</span></div>
              <div className="text-[12px] text-slate-300">제약조건: <span className="text-white font-mono">{compileInfo.constraint_count}</span></div>
              <div className="text-[12px] text-slate-300">변수 타입: <span className="text-white font-mono">{compileInfo.variable_types}</span></div>
              {compileInfo.time_limit_sec && <div className="text-[12px] text-slate-300">최대 시간: <span className="text-white font-mono">{compileInfo.time_limit_sec}초</span></div>}
            </div>
          )}

          {/* Run button */}
          <div className="px-3 pb-3">
            <div className="flex gap-2">
              <button
                onClick={handleRun}
                disabled={execMode === 'compare' && compareSelection.size < 2}
                className={`flex-1 py-3 rounded-xl font-bold text-white transition-all ${
                  execMode === 'compare' && compareSelection.size < 2
                    ? 'bg-slate-700 cursor-not-allowed opacity-50'
                    : 'bg-gradient-to-r from-cyan-600 to-blue-600 hover:from-cyan-500 hover:to-blue-500'
                }`}
              >
                {execMode === 'auto' && '자동 실행'}
                {execMode === 'step' && stepPhase === 'select' && '컴파일'}
                {execMode === 'step' && stepPhase === 'compiled' && '실행'}
                {execMode === 'compare' && `${compareSelection.size}개 솔버 비교 실행`}
              </button>
              {/* Hybrid 버튼 제거 — 전략 선택이 SolverCard 라디오로 통합됨 */}
            </div>
          </div>
        </div>
      )}

      {/* Running status */}
      {jobStatus === 'executing' && (
        <div className="flex-shrink-0 p-4 border-t border-slate-800">
          <div className="flex flex-col items-center space-y-3 py-4">
            <Loader2 size={32} className="text-cyan-400 animate-spin" />
            <p className="text-sm text-slate-300">
              {execMode === 'compare'
                ? `비교 실행 중 (${Object.values(compareState.jobs).filter(j => ['COMPLETE', 'FAILED', 'CANCELLED'].includes(j.status)).length}/${Object.keys(compareState.jobs).length} 완료)`
                : jobPoll.progress || '실행 중...'}
            </p>
            <div className="w-full h-1.5 bg-slate-800 rounded-full overflow-hidden">
              <div
                className="h-full bg-gradient-to-r from-cyan-500 to-blue-500 rounded-full transition-all duration-500"
                style={{ width: `${execMode === 'compare' ? Math.round(Object.values(compareState.jobs).filter(j => ['COMPLETE', 'FAILED', 'CANCELLED'].includes(j.status)).length / Math.max(Object.keys(compareState.jobs).length, 1) * 100) : jobPoll.progressPct}%` }}
              />
            </div>
            <div className="flex items-center gap-4 text-[11px] text-slate-500">
              <span>경과: {jobPoll.elapsedSec}초</span>
              {execMode !== 'compare' && timeLimitSec && <span>최대: {timeLimitSec}초</span>}
            </div>
            {/* 취소 버튼 */}
            <button
              onClick={execMode === 'compare' ? handleReset : jobPoll.cancelJob}
              className="flex items-center gap-1.5 px-4 py-1.5 text-xs bg-slate-700 hover:bg-red-600/80 text-slate-300 hover:text-white rounded-lg transition-colors"
            >
              <StopCircle size={12} />
              취소
            </button>
          </div>
        </div>
      )}

      {/* Success result summary (single mode) */}
      {jobStatus === 'done' && jobPoll.result?.success && execMode !== 'compare' && (
        <div className="flex-shrink-0 p-4 border-t border-slate-800">
          <div className="p-3 rounded-lg bg-green-500/10 border border-green-500/30 mb-3">
            <div className="flex items-center gap-2 mb-2">
              <CheckCircle size={14} className="text-green-400" />
              <span className="text-[13px] font-bold text-green-400">최적화 완료</span>
            </div>
            <div className="grid grid-cols-3 gap-2 text-center">
              <div>
                <div className="text-[11px] text-slate-500">상태</div>
                <div className="text-[13px] text-white font-mono">{jobPoll.result.summary?.status || jobPoll.result.status}</div>
              </div>
              <div>
                <div className="text-[11px] text-slate-500">
                  {jobPoll.result.summary?.interpreted_result?.objective_label || '목적함수'}
                </div>
                <div className="text-[13px] text-cyan-400 font-mono">
                  {jobPoll.result.summary?.interpreted_result?.objective_display_value
                    || (jobPoll.result.summary?.objective_value ?? '-')}
                </div>
              </div>
              <div>
                <div className="text-[11px] text-slate-500">실행 시간</div>
                <div className="text-[13px] text-white font-mono">{jobPoll.result.summary?.timing?.total_sec ?? '-'}s</div>
              </div>
            </div>
          </div>
          <div className="flex gap-2">
            <button onClick={handleReset} className="flex-1 py-2 rounded-lg text-sm text-slate-300 bg-slate-800 hover:bg-slate-700 transition">
              다시 실행
            </button>
            <button
              onClick={() => onAction?.('show_result', '최적화 결과를 보여줘')}
              className="flex-1 py-2 rounded-lg text-sm text-white bg-cyan-600 hover:bg-cyan-500 transition"
            >
              상세 결과 보기
            </button>
          </div>
        </div>
      )}

      {/* Compare results */}
      {jobStatus === 'done' && Object.keys(compareResults).length > 0 && execMode === 'compare' && (
        <CompareResultsPanel
          compareResults={compareResults}
          compareRunning={compareRunning}
          solvers={solvers}
          onReset={handleReset}
        />
      )}
    </div>
  );
}
