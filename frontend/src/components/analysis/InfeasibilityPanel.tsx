// src/components/analysis/InfeasibilityPanel.tsx
// INFEASIBLE 진단 정보 표시 패널 (SP + Legacy 양쪽 지원)

interface InfeasibilityPanelProps {
  info: any;
}

export function InfeasibilityPanel({ info }: InfeasibilityPanelProps) {
  if (!info) return null;

  // SP 진단 형식 감지 (causes/suggestions/user_message)
  const isSP = info.model_type === 'SetPartitioning' || info.causes;

  if (isSP) {
    return <SPDiagnosis info={info} />;
  }

  return <LegacyDiagnosis info={info} />;
}

/** Set Partitioning INFEASIBLE 진단 */
function SPDiagnosis({ info }: { info: any }) {
  const causes = info.causes || [];
  const suggestions = info.suggestions || [];
  const spDiag = info.sp_diagnostics || {};
  const stats = info.solver_stats || {};

  return (
    <div className="mt-3 space-y-3">
      {/* 원인 분석 */}
      {causes.length > 0 && (
        <div className="p-3 bg-red-900/30 border border-red-500/30 rounded-lg">
          <p className="text-xs font-semibold text-red-300 mb-2">
            INFEASIBLE 원인 ({causes.length}건)
          </p>
          <div className="space-y-2">
            {causes.map((cause: any, i: number) => (
              <div key={i} className="flex items-start gap-2">
                <span className="text-red-400 text-xs font-mono mt-0.5">{i + 1}.</span>
                <div>
                  <p className="text-xs text-red-200">{cause.message}</p>
                  {cause.constraint && (
                    <p className="text-[10px] text-red-400/60 mt-0.5">
                      제약: {cause.constraint}
                    </p>
                  )}
                  {cause.type && (
                    <span className={`inline-block mt-1 px-1.5 py-0.5 text-[10px] rounded ${
                      cause.type === 'INFEASIBLE_CERTAIN' ? 'bg-red-800/50 text-red-300' :
                      cause.type === 'PRESOLVE_INFEASIBLE' ? 'bg-red-800/50 text-red-300' :
                      'bg-amber-800/50 text-amber-300'
                    }`}>
                      {cause.type}
                    </span>
                  )}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* 제안 */}
      {suggestions.length > 0 && (
        <div className="p-3 bg-cyan-900/20 border border-cyan-500/30 rounded-lg">
          <p className="text-xs font-semibold text-cyan-300 mb-2">해결 방안</p>
          <div className="space-y-1.5">
            {suggestions.map((s: string, i: number) => (
              <div key={i} className="flex items-start gap-2 text-xs text-cyan-200">
                <span className="text-cyan-400 mt-0.5">•</span>
                <span>{s}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* SP 진단 요약 */}
      {(spDiag.column_count || spDiag.task_count) && (
        <div className="p-3 bg-slate-800/60 rounded-lg">
          <p className="text-xs font-semibold text-slate-300 mb-2">SP 모델 현황</p>
          <div className="grid grid-cols-2 gap-2 text-xs">
            <div className="text-slate-400">
              총 column: <span className="text-white font-mono">{spDiag.column_count || '-'}</span>
            </div>
            <div className="text-slate-400">
              총 task: <span className="text-white font-mono">{spDiag.task_count || '-'}</span>
            </div>
            <div className="text-slate-400">
              최소 coverage: <span className="text-white font-mono">{spDiag.min_coverage_density ?? '-'}</span>
            </div>
            <div className="text-slate-400">
              취약 task: <span className={`font-mono ${(spDiag.weak_tasks_count || 0) > 0 ? 'text-amber-400' : 'text-white'}`}>
                {spDiag.weak_tasks_count || 0}개
              </span>
            </div>
          </div>
          {/* column_type 분포 */}
          {spDiag.column_type_distribution && (
            <div className="mt-2 pt-2 border-t border-slate-700">
              <p className="text-[10px] text-slate-500 mb-1">Column Type 분포</p>
              <div className="flex gap-3 text-xs">
                {Object.entries(spDiag.column_type_distribution).map(([type, count]) => (
                  <span key={type} className="text-slate-300">
                    {type}: <span className="text-white font-mono">{String(count)}</span>
                  </span>
                ))}
              </div>
            </div>
          )}
          {/* 제약 리스크 */}
          {spDiag.constraint_risks?.length > 0 && (
            <div className="mt-2 pt-2 border-t border-slate-700">
              <p className="text-[10px] text-slate-500 mb-1">제약조건 리스크</p>
              <div className="space-y-1">
                {spDiag.constraint_risks.map((risk: any, i: number) => (
                  <div key={i} className="text-xs">
                    <span className={`inline-block w-2 h-2 rounded-full mr-1.5 ${
                      risk.risk === 'INFEASIBLE_CERTAIN' ? 'bg-red-400' : 'bg-amber-400'
                    }`} />
                    <span className="text-slate-300">{risk.message || risk.label}</span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}

      {/* 솔버 통계 */}
      {stats.wall_time && (
        <div className="text-[10px] text-slate-500 px-1">
          탐색 시간: {Number(stats.wall_time).toFixed(1)}s, conflicts: {stats.conflicts || 0}, branches: {stats.branches || 0}
        </div>
      )}
    </div>
  );
}

/** Legacy (I×J) INFEASIBLE 진단 */
function LegacyDiagnosis({ info }: { info: any }) {
  return (
    <div className="mt-3 space-y-3">
      {/* 제약조건 요약 */}
      <div className="p-3 bg-slate-800/60 rounded-lg">
        <p className="text-xs font-semibold text-slate-300 mb-2">적용된 제약조건</p>
        <div className="grid grid-cols-2 gap-2 text-xs">
          <div className="text-slate-400">
            Hard 제약: <span className="text-white font-mono">
              {info.summary?.hard_constraint_count || 0}개 ({info.summary?.hard_instance_count || 0} 인스턴스)
            </span>
          </div>
          <div className="text-slate-400">
            Soft 제약: <span className="text-white font-mono">
              {info.summary?.soft_constraint_count || 0}개 ({info.summary?.soft_instance_count || 0} 인스턴스)
            </span>
          </div>
        </div>
        {info.applied_constraints?.length > 0 && (
          <div className="mt-2 space-y-1">
            {info.applied_constraints.map((c: any, i: number) => (
              <div key={i} className="flex items-center gap-2 text-xs">
                <span className={`w-1.5 h-1.5 rounded-full ${c.category === 'hard' ? 'bg-red-400' : 'bg-yellow-400'}`} />
                <span className="text-slate-300">{c.name}</span>
                <span className="text-slate-500">({c.count})</span>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* 충돌 힌트 */}
      {info.conflict_hints?.length > 0 && (
        <div className="p-3 bg-amber-900/30 border border-amber-500/30 rounded-lg">
          <p className="text-xs font-semibold text-amber-300 mb-2">충돌 가능성 분석</p>
          {info.conflict_hints.map((hint: any, i: number) => (
            <div key={i} className="mb-2 last:mb-0">
              <p className="text-xs text-amber-200">{hint.message}</p>
              {hint.constraints && (
                <p className="text-xs text-amber-400/70 mt-0.5">
                  관련 제약: {hint.constraints.join(', ')}
                </p>
              )}
            </div>
          ))}
        </div>
      )}

      {/* 컴파일 실패 제약 */}
      {info.failed_constraints?.length > 0 && (
        <div className="p-3 bg-slate-800/60 rounded-lg">
          <p className="text-xs font-semibold text-orange-300 mb-1">
            미적용 제약조건 ({info.summary?.failed_constraint_count}개)
          </p>
          <p className="text-xs text-slate-400">아래 제약은 컴파일에 실패하여 적용되지 않았습니다.</p>
          <div className="mt-1 space-y-0.5">
            {info.failed_constraints.map((c: any, i: number) => (
              <div key={i} className="text-xs text-orange-300/70">• {c.name}</div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
