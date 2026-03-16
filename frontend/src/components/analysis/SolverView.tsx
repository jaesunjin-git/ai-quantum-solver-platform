// src/components/analysis/SolverView.tsx
import { useState, useEffect, useCallback } from 'react';
import { Cpu, Loader2, Activity, Play, GitCompare, ListChecks, CheckCircle, XCircle } from 'lucide-react';
import { API_BASE_URL } from '../../config';
import { useAuth } from '../../context/AuthContext';
import { StepItem } from './StepItem';
import { SolverCard } from './SolverCard';
import { InfeasibilityPanel } from './InfeasibilityPanel';
import { CompareResultsPanel } from './CompareResultsPanel';
import type { SolverData, ProblemProfile } from './types';

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
  const [step, setStep] = useState(0);
  const [jobStatus, setJobStatus] = useState<'idle' | 'running' | 'compiling' | 'executing' | 'done' | 'error'>('idle');
  const [execMode, setExecMode] = useState<'auto' | 'step' | 'compare'>('auto');
  const [selectedSolver, setSelectedSolver] = useState<number>(0);
  const [compareSelection, setCompareSelection] = useState<Set<number>>(new Set([0]));
  const [stepPhase, setStepPhase] = useState<'select' | 'compiled' | 'running' | 'done'>('select');
  const [compileInfo, setCompileInfo] = useState<any>(null);
  const [execResult, setExecResult] = useState<any>(null);
  const [execError, setExecError] = useState<string | null>(null);
  const [infeasibilityInfo, setInfeasibilityInfo] = useState<any>(null);
  const [progressText, setProgressText] = useState('');
  const [compareResults, setCompareResults] = useState<Record<number, any>>({});
  const [compareRunning, setCompareRunning] = useState<Set<number>>(new Set());

  const solvers = data.recommended_solvers || [];
  const profile = data.problem_profile || {} as ProblemProfile;

  useEffect(() => {
    setStep(0);
    setJobStatus('idle');
    const timer = setInterval(() => {
      setStep((prev) => (prev < 3 ? prev + 1 : prev));
    }, 800);
    return () => clearInterval(timer);
  }, [data]);

  const executeSolver = useCallback(async (solverIdx: number) => {
    const solver = solvers[solverIdx];
    if (!solver || !projectId) return null;
    try {
      const res = await authFetch(`${API_BASE_URL}/api/solve`, {
        method: 'POST',
        body: JSON.stringify({
          project_id: projectId,
          solver_id: solver.solver_id,
          solver_name: `${solver.provider} ${solver.solver_name}`.trim(),
          time_limit_sec: 120,
        }),
      });
      if (!res.ok) { const t = await res.text(); throw new Error(t); }
      return await res.json();
    } catch (err: any) {
      return { success: false, error: err.message || String(err) };
    }
  }, [solvers, projectId, authFetch]);

  const handleAutoRun = useCallback(async () => {
    setJobStatus('compiling');
    setProgressText('모델 컴파일 중...');
    setExecError(null);
    setExecResult(null);
    setInfeasibilityInfo(null);
    setTimeout(() => { setJobStatus('executing'); setProgressText('솔버 실행 중...'); }, 1500);
    const result = await executeSolver(selectedSolver);
    if (result?.success) {
      setJobStatus('done');
      setExecResult(result);
      setProgressText('완료!');
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
      onAction?.('execute_done', `${label}으로 최적화 실행이 완료되었습니다. 결과를 설명해주세요.`);
    } else {
      setJobStatus('error');
      setExecError(result?.error || '실행 실패');
      setInfeasibilityInfo(result?.infeasibility_info || null);
      setProgressText('');
    }
  }, [selectedSolver, executeSolver, solvers, onAction, onResultReady]);

  const handleStepRun = useCallback(async () => {
    if (stepPhase === 'select') {
      setStepPhase('compiled');
      setJobStatus('compiling');
      setProgressText('모델 컴파일 중...');
      const solver = solvers[selectedSolver];
      setTimeout(() => {
        setCompileInfo({
          solver_name: solver?.solver_name,
          variable_count: data.problem_profile?.variable_count || '-',
          constraint_count: data.problem_profile?.constraint_count || '-',
          variable_types: data.problem_profile?.variable_types?.join(', ') || '-',
        });
        setJobStatus('idle');
        setProgressText('');
      }, 1000);
      return;
    }
    if (stepPhase === 'compiled') {
      setStepPhase('running');
      setJobStatus('executing');
      setProgressText('솔버 실행 중...');
      setExecError(null);
      setInfeasibilityInfo(null);
      const result = await executeSolver(selectedSolver);
      if (result?.success) {
        setStepPhase('done');
        setJobStatus('done');
        setExecResult(result);
        setProgressText('완료!');
        const solver = solvers[selectedSolver];
        const label = `${solver?.provider || ''} ${solver?.solver_name || ''}`.trim();
        onAction?.('execute_done', `${label}으로 최적화 실행이 완료되었습니다. 결과를 설명해주세요.`);
      } else {
        setJobStatus('error');
        setExecError(result?.error || '실행 실패');
        setInfeasibilityInfo(result?.infeasibility_info || null);
      }
    }
  }, [stepPhase, selectedSolver, executeSolver, solvers, data, onAction]);

  const handleCompareRun = useCallback(async () => {
    if (compareSelection.size < 2) return;
    setJobStatus('executing');
    setCompareResults({});
    const indices = Array.from(compareSelection);
    setCompareRunning(new Set(indices));
    const promises = indices.map(async (idx) => {
      const result = await executeSolver(idx);
      setCompareResults(prev => ({ ...prev, [idx]: result }));
      setCompareRunning(prev => { const next = new Set(prev); next.delete(idx); return next; });
      return { idx, result };
    });
    await Promise.all(promises);
    setJobStatus('done');
    const firstSuccess = Object.values(compareResults).find((r: any) => r?.success);
    if (firstSuccess) {
      const resultView = {
        view_mode: 'result',
        ...(firstSuccess as any).summary,
        compare_mode: true,
        compile_summary: (firstSuccess as any).summary?.compile_summary,
        execute_summary: (firstSuccess as any).summary?.execute_summary,
      };
      onResultReady?.(resultView);
    }
    onAction?.('execute_done', `${indices.length}개 솔버 비교 실행이 완료되었습니다.`);
  }, [compareSelection, executeSolver, solvers, onAction]);

  const handleReset = () => {
    setJobStatus('idle');
    setStepPhase('select');
    setExecResult(null);
    setExecError(null);
    setCompileInfo(null);
    setCompareResults({});
    setProgressText('');
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
    }
  };

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
              {jobStatus === 'running' ? 'Running Hybrid Algorithm...' : 'Solver Recommendation'}
            </p>
          </div>
        </div>
      </div>

      {/* 스크롤 콘텐츠 */}
      <div className="flex-1 overflow-y-auto custom-scrollbar p-6 pt-4">

        {/* 실행 오류 표시 */}
        {execError && (
          <div className="mx-6 mt-4 p-4 bg-red-900/30 border border-red-500/50 rounded-xl">
            <div className="flex items-center gap-2 mb-2">
              <span className="text-red-400 font-semibold">⚠️ 실행 오류</span>
            </div>
            <p className="text-sm text-red-300 whitespace-pre-wrap">{execError}</p>

            <InfeasibilityPanel info={infeasibilityInfo} />

            <div className="mt-3 flex gap-2">
              <button
                onClick={() => { setExecError(null); setInfeasibilityInfo(null); setJobStatus('idle'); }}
                className="px-3 py-1.5 text-xs bg-slate-700 hover:bg-slate-600 text-white rounded-lg transition-colors"
              >
                닫기
              </button>
              <button
                onClick={() => { setExecError(null); setInfeasibilityInfo(null); setJobStatus('idle'); handleRun(); }}
                className="px-3 py-1.5 text-xs bg-cyan-700 hover:bg-cyan-600 text-white rounded-lg transition-colors"
              >
                다시 실행
              </button>
            </div>
          </div>
        )}
        {(jobStatus !== 'compiling' && jobStatus !== 'executing') ? (
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
                      onSelect={() => handleSolverSelect(idx)}
                    />
                  ))}
                </div>
              </div>
            )}
          </>
        ) : null}
      </div>

      {/* Execution Mode UI */}
      {step >= 3 && jobStatus === 'idle' && !execResult && Object.keys(compareResults).length === 0 && (
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
             ) : (
               <div className="text-[11px] text-slate-400">
                 선택 솔버: <span className="text-cyan-400 font-medium">{solvers[selectedSolver]?.solver_name || '위에서 솔버를 선택하세요'}</span>
               </div>
             )}
           </div>

            {/* Step-by-step: compile info */}
            {execMode === 'step' && compileInfo && (
              <div className="p-3 rounded-lg bg-slate-800/50 border border-slate-700 space-y-1">
                <div className="text-[11px] text-slate-500 uppercase mb-1">컴파일 결과</div>
                <div className="text-[12px] text-slate-300">솔버: <span className="text-cyan-400">{compileInfo.solver_name}</span></div>
                <div className="text-[12px] text-slate-300">변수: <span className="text-white font-mono">{compileInfo.variable_count}</span></div>
                <div className="text-[12px] text-slate-300">제약조건: <span className="text-white font-mono">{compileInfo.constraint_count}</span></div>
                <div className="text-[12px] text-slate-300">변수 타입: <span className="text-white font-mono">{compileInfo.variable_types}</span></div>
              </div>
            )}

            {/* Run button */}
            <button
              onClick={handleRun}
              disabled={execMode === 'compare' && compareSelection.size < 2}
              className={`w-full py-3 rounded-xl font-bold text-white transition-all ${
                execMode === 'compare' && compareSelection.size < 2
                  ? 'bg-slate-700 cursor-not-allowed opacity-50'
                  : 'bg-gradient-to-r from-cyan-600 to-blue-600 hover:from-cyan-500 hover:to-blue-500'
              }`}
            >
              {execMode === 'auto' && <><span></span> 자동 실행</>}
              {execMode === 'step' && stepPhase === 'select' && <><span></span> 컴파일</>}
              {execMode === 'step' && stepPhase === 'compiled' && <><span></span> 실행</>}
              {execMode === 'compare' && <><span></span> {compareSelection.size}개 솔버 비교 실행</>}
            </button>
          </div>
      )}

      {/* Running status */}
      {(jobStatus === 'compiling' || jobStatus === 'executing') && (
        <div className="flex-shrink-0 p-4 border-t border-slate-800">
          <div className="flex flex-col items-center space-y-3 py-4">
            <Loader2 size={32} className="text-cyan-400 animate-spin" />
            <p className="text-sm text-slate-300">{progressText}</p>
            <div className="w-full h-1.5 bg-slate-800 rounded-full overflow-hidden">
              <div className={`h-full bg-gradient-to-r from-cyan-500 to-blue-500 rounded-full transition-all duration-1000 ${
                jobStatus === 'compiling' ? 'w-1/3' : 'w-2/3'
              } animate-pulse`} />
            </div>
          </div>
        </div>
      )}

      {/* Error state */}
      {jobStatus === 'error' && (
        <div className="flex-shrink-0 p-4 border-t border-slate-800">
          <div className="p-3 rounded-lg bg-red-500/10 border border-red-500/30 mb-3">
            <div className="flex items-center gap-2 mb-1">
              <XCircle size={14} className="text-red-400" />
              <span className="text-[13px] font-bold text-red-400">실행 오류</span>
            </div>
            <p className="text-[12px] text-red-300/80">{execError}</p>
          </div>
          <button onClick={handleReset} className="w-full py-2 rounded-lg text-sm text-slate-300 bg-slate-800 hover:bg-slate-700 transition">
            다시 시도
          </button>
        </div>
      )}

      {/* Success result summary */}
      {jobStatus === 'done' && execResult && execMode !== 'compare' && (
        <div className="flex-shrink-0 p-4 border-t border-slate-800">
          <div className="p-3 rounded-lg bg-green-500/10 border border-green-500/30 mb-3">
            <div className="flex items-center gap-2 mb-2">
              <CheckCircle size={14} className="text-green-400" />
              <span className="text-[13px] font-bold text-green-400">최적화 완료</span>
            </div>
            <div className="grid grid-cols-3 gap-2 text-center">
              <div>
                <div className="text-[11px] text-slate-500">상태</div>
                <div className="text-[13px] text-white font-mono">{execResult.status}</div>
              </div>
              <div>
                <div className="text-[11px] text-slate-500">목적함수</div>
                <div className="text-[13px] text-cyan-400 font-mono">{execResult.summary?.objective_value ?? '-'}</div>
              </div>
              <div>
                <div className="text-[11px] text-slate-500">실행 시간</div>
                <div className="text-[13px] text-white font-mono">{execResult.summary?.timing?.total_sec ?? '-'}s</div>
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
