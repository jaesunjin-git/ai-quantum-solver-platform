// src/components/analysis/SolverCard.tsx
// 개별 솔버 추천 카드 — 펼침/접힘 + 전략 라디오 선택
import { Clock, TrendingUp } from 'lucide-react';

interface SolverCardProps {
  svr: any;
  idx: number;
  isSelected: boolean;
  isCompareMode: boolean;
  strategies?: any[];
  recommendedStrategy?: any;
  selectedStrategyId?: string;
  onSelect: () => void;
  onStrategySelect?: (strategyId: string, strategyType: string) => void;
}

const formatTime = (times?: number | number[]) => {
  if (typeof times === 'number') return `${times.toFixed(0)}s`;
  if (!times || times.length < 2) return '-';
  const min = times[0];
  const max = times[1];
  if (max <= 0) return '-';
  if (max < 1) return '< 1초';
  if (max < 60) return `${min.toFixed(0)}~${max.toFixed(0)}초`;
  if (max < 3600) return `${(min / 60).toFixed(0)}~${(max / 60).toFixed(0)}분`;
  return `${(min / 3600).toFixed(1)}~${(max / 3600).toFixed(1)}시간`;
};

const formatCost = (costs?: number | number[]) => {
  if (typeof costs === 'number') return `$${costs.toFixed(2)}`;
  if (!costs || costs.length < 2) return '-';
  if (costs[0] === 0 && costs[1] === 0) return '무료';
  if (costs[1] < 0.01) return '< $0.01';
  return `$${costs[0].toFixed(2)}~$${costs[1].toFixed(2)}`;
};

const getCategoryLabel = (cat: string) => {
  const map: Record<string, string> = {
    quantum_hybrid: '양자 하이브리드',
    quantum_native: '양자 네이티브',
    quantum_gate: '양자 게이트',
    quantum_analog: '양자 아날로그',
    quantum_simulator: '양자 시뮬레이터',
    classical: '클래식',
  };
  return map[cat] || cat;
};

const getSuitabilityColor = (suit: string) => {
  if (suit === 'Best Choice') return 'bg-green-500/20 text-green-400';
  if (suit === 'Recommended') return 'bg-blue-500/20 text-blue-400';
  if (suit === 'Possible') return 'bg-yellow-500/20 text-yellow-400';
  if (suit === 'Limited') return 'bg-orange-500/20 text-orange-400';
  return 'bg-red-500/20 text-red-400';
};

export function SolverCard({
  svr, idx: _idx, isSelected, isCompareMode: _isCompareMode,
  strategies, recommendedStrategy, selectedStrategyId,
  onSelect, onStrategySelect,
}: SolverCardProps) {

  // 이 솔버와 관련된 전략들
  const relatedStrategies = strategies?.filter((st: any) =>
    st.steps?.some((s: any) => s.solver_name === svr.solver_name)
  ) || [];
  const bestStrategy = relatedStrategies.find((st: any) =>
    recommendedStrategy?.strategy_id === st.strategy_id
  ) || relatedStrategies[0];

  return (
    <div
      onClick={onSelect}
      className={`rounded-lg border cursor-pointer transition-all duration-200 ${
        isSelected
          ? 'bg-cyan-900/30 border-cyan-400 ring-1 ring-cyan-400/50'
          : 'bg-slate-800 border-slate-700 hover:border-slate-500'
      }`}
    >
      {/* 공통 헤더: 솔버 이름 + 적합도 + 점수/시간 */}
      <div className="p-3">
        <div className="flex justify-between items-start">
          <div className="flex-1">
            <div className="font-bold text-white text-sm">{svr.solver_name}</div>
            <div className="text-[13px] text-slate-400">{svr.provider} · {getCategoryLabel(svr.category)}</div>
          </div>
          <div className="text-right flex-shrink-0 ml-3">
            <span className={`text-[13px] px-1.5 py-0.5 rounded font-bold ${getSuitabilityColor(svr.suitability)}`}>
              {svr.suitability}
            </span>
            <div className="text-[13px] text-slate-500 mt-1">점수: {svr.total_score}</div>
          </div>
        </div>

        {/* 추정 시간 · 비용 (항상 표시) */}
        <div className="mt-2 flex gap-3">
          <div className="flex items-center gap-1 text-[13px]">
            <Clock size={10} className="text-slate-500" />
            <span className="text-slate-400">추정:</span>
            <span className="text-white font-mono">{formatTime(svr.estimated_time)}</span>
          </div>
          <div className="flex items-center gap-1 text-[13px]">
            <TrendingUp size={10} className="text-slate-500" />
            <span className="text-white font-mono">{formatCost(svr.estimated_cost)}</span>
          </div>
        </div>

        {/* 접힌 상태: 추천 전략명 한 줄 */}
        {!isSelected && bestStrategy && (
          <div className="mt-2 text-[12px] text-slate-500 flex items-center gap-1.5">
            <span>📋</span>
            <span>{bestStrategy.name}</span>
            {relatedStrategies.length > 1 && (
              <span className="text-slate-600">외 {relatedStrategies.length - 1}개</span>
            )}
          </div>
        )}
      </div>

      {/* 펼친 상태: 상세 정보 + 전략 라디오 */}
      <div
        className={`overflow-hidden transition-all duration-300 ease-in-out ${
          isSelected ? 'max-h-[600px] opacity-100' : 'max-h-0 opacity-0'
        }`}
      >
        <div className="px-3 pb-3 space-y-2">
          {/* 설명 */}
          {svr.description && (
            <div className="text-[13px] text-slate-500">{svr.description}</div>
          )}

          {/* 점수 바 */}
          <div className="grid grid-cols-4 gap-1">
            {Object.entries(svr.scores || {}).map(([key, val]) => (
              <div key={key} className="text-center">
                <div className="text-[12px] text-slate-500">
                  {key === 'structure' ? '구조' : key === 'scale' ? '규모' : key === 'cost' ? '비용' : '속도'}
                </div>
                <div className="w-full h-1 bg-slate-700 rounded-full mt-0.5">
                  <div className="h-full bg-cyan-500 rounded-full" style={{ width: `${val}%` }} />
                </div>
                <div className="text-[13px] text-slate-600 mt-0.5">{typeof val === 'number' ? val.toFixed(0) : String(val)}</div>
              </div>
            ))}
          </div>

          {/* 추천 근거 */}
          {(svr.reasons?.length ?? 0) > 0 && (
            <div className="space-y-0.5">
              {svr.reasons?.map((r: string, i: number) => (
                <div key={i} className="text-[13px] text-green-400">✅ {r}</div>
              ))}
            </div>
          )}
          {(svr.warnings?.length ?? 0) > 0 && (
            <div className="space-y-0.5">
              {svr.warnings?.map((w: string, i: number) => (
                <div key={i} className="text-[13px] text-yellow-400">⚠️ {w}</div>
              ))}
            </div>
          )}

          {/* 전략 섹션 */}
          {relatedStrategies.length === 1 && bestStrategy && (
            /* 전략 1개: 라디오 없이 확정 전략만 표시 */
            <div className="mt-1 border-t border-slate-700/50 pt-3">
              <div className="p-2.5 rounded-lg border border-cyan-500/20 bg-cyan-900/10">
                <div className="flex items-center gap-2">
                  <span className="text-[12px] text-slate-400">📋 실행 전략:</span>
                  <span className="text-[13px] font-bold text-cyan-400">{bestStrategy.name}</span>
                </div>
                <div className="text-[12px] text-slate-500 mt-0.5">{bestStrategy.description}</div>
                {bestStrategy.steps && bestStrategy.steps.length > 0 && (
                  <div className="flex gap-1 mt-1 flex-wrap">
                    {bestStrategy.steps.map((step: any, si: number) => (
                      <span key={si} className="text-[11px] px-1.5 py-0.5 rounded bg-slate-700/50 text-slate-400">
                        {si > 0 && " → "}{step.solver_name}
                      </span>
                    ))}
                  </div>
                )}
              </div>
            </div>
          )}
          {relatedStrategies.length > 1 && (
            /* 전략 2개 이상: 라디오 버튼으로 선택 */
            <div className="mt-1 border-t border-slate-700/50 pt-3">
              <div className="text-[12px] text-slate-400 uppercase mb-2 font-medium">전략 선택</div>
              <div className="space-y-1.5">
                {relatedStrategies.map((st: any) => {
                  const isRecommended = bestStrategy?.strategy_id === st.strategy_id;
                  const isStrategySelected = selectedStrategyId === st.strategy_id
                    || (!selectedStrategyId && isRecommended);

                  return (
                    <label
                      key={st.strategy_id}
                      onClick={(e) => {
                        e.stopPropagation();
                        onStrategySelect?.(st.strategy_id, st.strategy_type || '');
                      }}
                      className={`flex items-start gap-2.5 p-2.5 rounded-lg border cursor-pointer transition-all ${
                        isStrategySelected
                          ? 'border-cyan-500/40 bg-cyan-900/20'
                          : 'border-slate-700/30 bg-slate-800/30 hover:border-slate-600'
                      }`}
                    >
                      {/* 라디오 버튼 */}
                      <div className={`mt-0.5 w-3.5 h-3.5 rounded-full border-2 flex-shrink-0 flex items-center justify-center ${
                        isStrategySelected ? 'border-cyan-400' : 'border-slate-600'
                      }`}>
                        {isStrategySelected && (
                          <div className="w-1.5 h-1.5 rounded-full bg-cyan-400" />
                        )}
                      </div>
                      {/* 전략 정보 */}
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2">
                          <span className={`text-[13px] font-bold ${isStrategySelected ? 'text-cyan-400' : 'text-slate-300'}`}>
                            {st.name}
                          </span>
                          {isRecommended && (
                            <span className="text-[10px] px-1.5 py-0.5 rounded bg-cyan-500/20 text-cyan-400 font-medium">
                              추천
                            </span>
                          )}
                          <span className="text-[11px] text-slate-500 ml-auto flex-shrink-0">
                            {st.confidence}
                          </span>
                        </div>
                        <div className="text-[12px] text-slate-500 mt-0.5">{st.description}</div>
                        {st.steps && st.steps.length > 0 && (
                          <div className="flex gap-1 mt-1 flex-wrap">
                            {st.steps.map((step: any, si: number) => (
                              <span key={si} className="text-[11px] px-1.5 py-0.5 rounded bg-slate-700/50 text-slate-400">
                                {si > 0 && " → "}{step.solver_name}
                              </span>
                            ))}
                          </div>
                        )}
                      </div>
                    </label>
                  );
                })}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
