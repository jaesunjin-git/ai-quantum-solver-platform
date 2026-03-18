// src/components/analysis/CompileReportTab.tsx
// 컴파일 리포트 서브탭 (솔버, 변수, 제약조건, Gate3, 파라미터 소스, 경고)
import { useState } from 'react';
import {
  AlertTriangle, Package, ChevronDown, ChevronRight,
  CheckCircle, XCircle, Database, Info, Shield
} from 'lucide-react';
import type { CompileSummary } from './types';

const formatNumber = (n: any) => {
  if (n == null) return '-';
  if (typeof n === 'number') return Number.isInteger(n) ? n.toLocaleString() : n.toFixed(1);
  return String(n);
};

/** 파라미터 소스를 사람이 읽기 쉬운 형태로 변환 */
const formatSource = (source: string): { label: string; color: string } => {
  if (source.startsWith('csv:')) {
    const parts = source.split('::');
    const col = parts[1] || '';
    return { label: `CSV${col ? ` (${col})` : ''}`, color: 'text-blue-400' };
  }
  switch (source) {
    case 'confirmed_problem': return { label: '문제 정의', color: 'text-green-400' };
    case 'math_model_ir': return { label: '수학 모델', color: 'text-cyan-400' };
    case 'auto_inject': return { label: '자동 주입', color: 'text-purple-400' };
    case 'fallback': return { label: 'Fallback', color: 'text-yellow-400' };
    case 'not_found': return { label: '미발견', color: 'text-red-400' };
    default: return { label: source, color: 'text-slate-400' };
  }
};

export function CompileReportTab({
  compileSummary,
  compileWarnings,
}: {
  compileSummary: CompileSummary;
  compileWarnings: string[];
}) {
  const [warningsExpanded, setWarningsExpanded] = useState(false);
  const [sourcesExpanded, setSourcesExpanded] = useState(false);
  const [gate3Expanded, setGate3Expanded] = useState(false);

  const constraints = compileSummary.constraints || { total_in_model: 0, applied: 0, failed: 0 };
  const paramSources = compileSummary.parameter_sources || {};
  const paramWarnings = compileSummary.parameter_warnings || [];
  const gate3 = compileSummary.gate3;
  const sourceEntries = Object.entries(paramSources);

  return (
    <div className="space-y-4 animate-in fade-in duration-300">
      {/* ── 컴파일 요약 ── */}
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

      {/* ── Gate3 검증 결과 ── */}
      {gate3 && (
        <div className={`rounded-xl border p-4 ${
          gate3.pass
            ? 'bg-green-500/5 border-green-500/20'
            : 'bg-red-500/5 border-red-500/20'
        }`}>
          <button onClick={() => setGate3Expanded(!gate3Expanded)}
            className="w-full flex items-center justify-between text-sm font-bold">
            <span className={`flex items-center gap-2 ${gate3.pass ? 'text-green-400' : 'text-red-400'}`}>
              <Shield size={14} />
              컴파일 검증 (Gate 3)
              {gate3.pass
                ? <CheckCircle size={12} className="text-green-400" />
                : <XCircle size={12} className="text-red-400" />
              }
            </span>
            <span className="flex items-center gap-2">
              {gate3.errors.length > 0 && (
                <span className="text-[10px] px-1.5 py-0.5 rounded bg-red-500/20 text-red-400">
                  {gate3.errors.length} error
                </span>
              )}
              {gate3.warnings.length > 0 && (
                <span className="text-[10px] px-1.5 py-0.5 rounded bg-yellow-500/20 text-yellow-400">
                  {gate3.warnings.length} warn
                </span>
              )}
              {gate3Expanded ? <ChevronDown size={14} className="text-slate-400" /> : <ChevronRight size={14} className="text-slate-400" />}
            </span>
          </button>
          {gate3Expanded && (
            <div className="mt-3 space-y-3">
              {/* Gate3 통계 */}
              {gate3.stats && (
                <div className="grid grid-cols-2 gap-2 text-[11px]">
                  {gate3.stats.hard_apply_ratio != null && (
                    <div className="bg-slate-800/60 rounded-lg px-3 py-2">
                      <div className="text-slate-500">Hard 적용률</div>
                      <div className={`font-mono font-bold ${
                        gate3.stats.hard_apply_ratio >= 0.8 ? 'text-green-400' :
                        gate3.stats.hard_apply_ratio >= 0.5 ? 'text-yellow-400' : 'text-red-400'
                      }`}>{(gate3.stats.hard_apply_ratio * 100).toFixed(0)}%</div>
                    </div>
                  )}
                  {gate3.stats.failed_constraints != null && gate3.stats.failed_constraints > 0 && (
                    <div className="bg-slate-800/60 rounded-lg px-3 py-2">
                      <div className="text-slate-500">실패 제약</div>
                      <div className="font-mono font-bold text-red-400">{gate3.stats.failed_constraints}건</div>
                    </div>
                  )}
                  {gate3.stats.skipped_soft != null && gate3.stats.skipped_soft > 0 && (
                    <div className="bg-slate-800/60 rounded-lg px-3 py-2">
                      <div className="text-slate-500">Soft 스킵</div>
                      <div className="font-mono font-bold text-yellow-400">{gate3.stats.skipped_soft}건</div>
                    </div>
                  )}
                  {gate3.stats.unknown_operator_warnings != null && gate3.stats.unknown_operator_warnings > 0 && (
                    <div className="bg-slate-800/60 rounded-lg px-3 py-2">
                      <div className="text-slate-500">미지원 연산자</div>
                      <div className="font-mono font-bold text-yellow-400">{gate3.stats.unknown_operator_warnings}건</div>
                    </div>
                  )}
                </div>
              )}
              {/* Gate3 에러 */}
              {gate3.errors.length > 0 && (
                <div className="space-y-1">
                  {gate3.errors.map((e, i) => (
                    <div key={i} className="flex items-start gap-2 text-[11px] text-red-300">
                      <XCircle size={11} className="mt-0.5 shrink-0 text-red-400" />
                      {e}
                    </div>
                  ))}
                </div>
              )}
              {/* Gate3 경고 */}
              {gate3.warnings.length > 0 && (
                <div className="space-y-1">
                  {gate3.warnings.map((w, i) => (
                    <div key={i} className="flex items-start gap-2 text-[11px] text-yellow-300/80">
                      <AlertTriangle size={11} className="mt-0.5 shrink-0 text-yellow-400" />
                      {w}
                    </div>
                  ))}
                </div>
              )}
              {/* 에러/경고 없으면 통과 메시지 */}
              {gate3.errors.length === 0 && gate3.warnings.length === 0 && (
                <div className="text-[11px] text-green-400 flex items-center gap-1.5">
                  <CheckCircle size={11} /> 모든 검증 통과
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {/* ── 파라미터 소스 추적 ── */}
      {sourceEntries.length > 0 && (
        <div className="bg-slate-800/50 rounded-xl border border-slate-700 p-4">
          <button onClick={() => setSourcesExpanded(!sourcesExpanded)}
            className="w-full flex items-center justify-between text-sm font-bold text-slate-300">
            <span className="flex items-center gap-2">
              <Database size={14} className="text-purple-400" />
              파라미터 소스 ({sourceEntries.length})
            </span>
            {sourcesExpanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
          </button>
          {sourcesExpanded && (
            <div className="mt-3 space-y-1">
              {sourceEntries.map(([param, source]) => {
                const { label, color } = formatSource(source);
                return (
                  <div key={param} className="flex items-center justify-between text-[11px] py-1 border-b border-slate-700/50 last:border-0">
                    <span className="text-slate-300 font-mono">{param}</span>
                    <span className={`${color} font-medium`}>{label}</span>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      )}

      {/* ── 파라미터 검증 경고 ── */}
      {paramWarnings.length > 0 && (
        <div className="bg-orange-500/5 rounded-xl border border-orange-500/20 p-4">
          <h3 className="text-sm font-bold text-orange-400 mb-2 flex items-center gap-2">
            <Info size={14} /> 파라미터 검증 ({paramWarnings.length})
          </h3>
          <div className="space-y-1 max-h-32 overflow-y-auto custom-scrollbar">
            {paramWarnings.map((w, i) => (
              <div key={i} className="text-[11px] text-orange-300/80 flex items-start gap-2">
                <AlertTriangle size={10} className="mt-0.5 shrink-0" />
                {w}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* ── 컴파일 경고 ── */}
      {compileWarnings.length > 0 && (
        <div className="bg-yellow-500/5 rounded-xl border border-yellow-500/20 p-4">
          <button onClick={() => setWarningsExpanded(!warningsExpanded)}
            className="w-full flex items-center justify-between text-sm font-bold text-yellow-400">
            <span className="flex items-center gap-2"><AlertTriangle size={14} /> 컴파일 경고 ({compileWarnings.length})</span>
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
}
