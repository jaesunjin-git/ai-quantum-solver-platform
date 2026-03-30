// src/components/analysis/ConstraintCheckTab.tsx
// 제약검증 서브탭 (Hard/Soft 현황, 위반 듀티)
import { CheckCircle, XCircle, AlertTriangle, Shield } from 'lucide-react';
import type { InterpretedResult } from './types';

export function ConstraintCheckTab({
  interpreted,
}: {
  interpreted?: InterpretedResult;
}) {
  if (!interpreted || !interpreted.duties?.length) {
    return <div className="text-center text-slate-500 py-8">해석된 결과가 없습니다</div>;
  }

  return (
    <div className="space-y-4 animate-in fade-in duration-300">
      {/* 전체 현황 - Hard / Soft / 위반 3카드 */}
      <div className="grid grid-cols-3 gap-3">
        {/* Hard 제약 */}
        {(() => {
          const hardMet = interpreted.constraint_status.filter(c => c.satisfied).length;
          const hardTotal = interpreted.constraint_status.length;
          const allOk = hardMet === hardTotal;
          return (
            <div className={`rounded-xl p-3 border text-center ${allOk ? 'bg-green-500/5 border-green-500/20' : 'bg-red-500/5 border-red-500/20'}`}>
              <Shield size={20} className={`mx-auto mb-1.5 ${allOk ? 'text-green-400' : 'text-red-400'}`} />
              <div className="text-base font-bold text-white">{hardMet}/{hardTotal}</div>
              <div className="text-[10px] text-slate-500 mt-0.5">하드 제약</div>
            </div>
          );
        })()}

        {/* Soft 제약 */}
        {(() => {
          const softAll = interpreted.soft_constraint_status || [];
          const softTotal = softAll.length;
          // side constraint: satisfied 기반, crew soft: status 기반
          const softApplied = softAll.filter((s: any) =>
            s.satisfied === true || s.status === 'applied'
          ).length;
          const hasSkipped = softTotal > 0 && softApplied < softTotal;
          return (
            <div className={`rounded-xl p-3 border text-center ${hasSkipped ? 'bg-yellow-500/5 border-yellow-500/20' : 'bg-slate-800/50 border-slate-700'}`}>
              <AlertTriangle size={20} className={`mx-auto mb-1.5 ${hasSkipped ? 'text-yellow-400' : 'text-slate-500'}`} />
              <div className="text-base font-bold text-white">
                {softTotal > 0 ? `${softApplied}/${softTotal}` : '-'}
              </div>
              <div className="text-[10px] text-slate-500 mt-0.5">소프트 제약</div>
            </div>
          );
        })()}

        {/* 듀티 위반 */}
        {(() => {
          const violations = interpreted.kpi.constraint_violations;
          const allOk = violations === 0;
          return (
            <div className={`rounded-xl p-3 border text-center ${allOk ? 'bg-green-500/5 border-green-500/20' : 'bg-red-500/5 border-red-500/20'}`}>
              <AlertTriangle size={20} className={`mx-auto mb-1.5 ${allOk ? 'text-green-400' : 'text-red-400'}`} />
              <div className="text-base font-bold text-white">{violations}건</div>
              <div className="text-[10px] text-slate-500 mt-0.5">듀티 위반</div>
            </div>
          );
        })()}
      </div>

      {/* 하드 제약별 상세 */}
      <div className="space-y-2">
        {interpreted.constraint_status.map((cs, i) => {
          const isParametric = cs.constraint_type === 'parametric' || cs.constraint_type === undefined;
          const actualNum = parseFloat(cs.max_actual);
          const limitNum = parseFloat(cs.limit);
          const barWidth = isParametric && !isNaN(actualNum) && !isNaN(limitNum) && limitNum > 0
            ? Math.min(100, (actualNum / limitNum) * 100)
            : cs.satisfied ? 100 : 0;

          return (
            <div key={i} className={`rounded-xl p-4 border ${
              cs.satisfied ? 'bg-slate-800/50 border-slate-700' : 'bg-red-500/5 border-red-500/20'
            }`}>
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  {cs.satisfied
                    ? <CheckCircle size={16} className="text-green-400" />
                    : <XCircle size={16} className="text-red-400" />}
                  <span className="text-[13px] text-white font-medium">{cs.name}</span>
                  {cs.constraint_type === 'structural' && (
                    <span className="text-[10px] text-slate-500 bg-slate-700 px-1.5 py-0.5 rounded">구조적</span>
                  )}
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
                  <div
                    className={`h-full rounded-full transition-all ${cs.satisfied ? 'bg-green-500' : 'bg-red-500'}`}
                    style={{ width: `${barWidth}%` }}
                  />
                </div>
              </div>
            </div>
          );
        })}
      </div>

      {/* 소프트 제약 현황 */}
      {interpreted.soft_constraint_status && interpreted.soft_constraint_status.length > 0 && (
        <div className="bg-slate-800/30 rounded-xl border border-slate-700 p-4">
          <h3 className="text-[12px] font-bold text-slate-400 mb-2 flex items-center gap-2">
            <AlertTriangle size={13} className="text-yellow-500" />
            소프트 제약 ({interpreted.soft_constraint_status.length}개)
          </h3>
          <div className="space-y-1.5">
            {interpreted.soft_constraint_status.map((sc: any, i: number) => {
              // Side Constraint (solver 실제 동작 기반)
              if (sc.type === 'aggregate_avg' || sc.type === 'cardinality' || sc.type === 'aggregate') {
                const satisfied = sc.satisfied;
                return (
                  <div key={i} className={`flex items-center justify-between p-2 rounded-lg text-[11px] ${
                    satisfied ? 'bg-green-500/5 border border-green-500/15' : 'bg-amber-500/5 border border-amber-500/15'
                  }`}>
                    <div className="flex items-center gap-1.5">
                      {satisfied
                        ? <CheckCircle size={12} className="text-green-400" />
                        : <AlertTriangle size={12} className="text-amber-400" />}
                      <span className="text-slate-300">{sc.constraint_ref || sc.name}</span>
                      <span className="text-[9px] text-slate-600 bg-slate-700 px-1 rounded">{sc.type}</span>
                    </div>
                    <span className={`font-mono ${satisfied ? 'text-green-400' : 'text-amber-400'}`}>
                      {sc.description || (satisfied ? '충족' : '위반')}
                    </span>
                  </div>
                );
              }
              // Crew 전용 soft (기존 표시)
              return (
                <div key={i} className={`flex items-center justify-between p-2 rounded-lg text-[11px] ${
                  sc.status === 'applied' ? 'bg-slate-800/50' : 'bg-amber-500/5 border border-amber-500/15'
                }`}>
                  <div className="flex items-center gap-1.5">
                    <span className="w-1.5 h-1.5 rounded-full bg-yellow-500/50 shrink-0" />
                    <span className="text-slate-400">{sc.name}</span>
                  </div>
                  {sc.actual && (
                    <span className={`font-mono text-[10px] ${sc.status === 'applied' ? 'text-green-400' : 'text-amber-400'}`}>
                      {sc.actual} / {sc.target}
                    </span>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* 위반 듀티 목록 */}
      {interpreted.duties.some(d => d.violations?.length) && (
        <div className="bg-red-500/5 rounded-xl border border-red-500/20 p-4">
          <h3 className="text-sm font-bold text-red-400 mb-2">위반 듀티 상세</h3>
          {interpreted.duties
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
}
