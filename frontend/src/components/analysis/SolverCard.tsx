// src/components/analysis/SolverCard.tsx
// 개별 솔버 추천 카드 (점수, 추정 시간/비용, 전략, 추천 근거)
import { useState } from 'react';
import { Clock, TrendingUp } from 'lucide-react';

interface SolverCardProps {
  svr: any;
  idx: number;
  isSelected: boolean;
  isCompareMode: boolean;
  strategies?: any[];
  recommendedStrategy?: any;
  onSelect: () => void;
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

export function SolverCard({ svr, idx: _idx, isSelected, isCompareMode: _isCompareMode, strategies, recommendedStrategy, onSelect }: SolverCardProps) {
  const [isExpanded, setIsExpanded] = useState(false);

  const relatedStrategies = strategies?.filter((st: any) =>
    st.steps?.some((s: any) => s.solver_name === svr.solver_name)
  ) || [];
  const bestStrategy = relatedStrategies.find((st: any) =>
    recommendedStrategy?.strategy_id === st.strategy_id
  ) || relatedStrategies[0];
  const otherStrategies = relatedStrategies.filter((st: any) => st !== bestStrategy);

  return (
    <div
      onClick={onSelect}
      className={`p-3 rounded-lg border cursor-pointer transition-all ${
        isSelected
          ? 'bg-cyan-900/30 border-cyan-400 ring-1 ring-cyan-400/50'
          : 'bg-slate-800 border-slate-700 hover:border-slate-500'
      }`}
    >
      {/* 솔버 이름 + 적합도 */}
      <div className="flex justify-between items-start">
        <div className="flex-1">
          <div className="font-bold text-white text-sm">{svr.solver_name}</div>
          <div className="text-[13px] text-slate-400">{svr.provider} · {getCategoryLabel(svr.category)}</div>
          <div className="text-[13px] text-slate-500 mt-1">{svr.description}</div>
        </div>
        <div className="text-right flex-shrink-0 ml-3">
          <span className={`text-[13px] px-1.5 py-0.5 rounded font-bold ${getSuitabilityColor(svr.suitability)}`}>
            {svr.suitability}
          </span>
          <div className="text-[13px] text-slate-500 mt-1">점수: {svr.total_score}</div>
        </div>
      </div>

      {/* 추정 시간 · 비용 */}
      <div className="mt-2 flex gap-3">
        <div className="flex items-center gap-1 text-[13px]">
          <Clock size={10} className="text-slate-500" />
          <span className="text-slate-400">추정 시간:</span>
          <span className="text-white font-mono">{formatTime(svr.estimated_time)}</span>
        </div>
        <div className="flex items-center gap-1 text-[13px]">
          <TrendingUp size={10} className="text-slate-500" />
          <span className="text-slate-400">추정 비용:</span>
          <span className="text-white font-mono">{formatCost(svr.estimated_cost)}</span>
        </div>
      </div>

      {/* 점수 바 */}
      <div className="mt-2 grid grid-cols-4 gap-1">
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
        <div className="mt-2 space-y-0.5">
          {svr.reasons?.map((r: string, i: number) => (
            <div key={i} className="text-[13px] text-green-400">✅ {r}</div>
          ))}
        </div>
      )}
      {(svr.warnings?.length ?? 0) > 0 && (
        <div className="mt-1 space-y-0.5">
          {svr.warnings?.map((w: string, i: number) => (
            <div key={i} className="text-[13px] text-yellow-400">⚠️ {w}</div>
          ))}
        </div>
      )}

      {/* 추천 실행 전략 */}
      {bestStrategy && (
        <div className="mt-3 border-t border-slate-700/50 pt-3">
          <div className="p-2.5 rounded-lg border border-cyan-500/20 bg-cyan-900/10">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <span className="text-[12px] text-slate-400">📋 추천 전략:</span>
                <span className="text-[12px] font-bold text-cyan-400">{bestStrategy.name}</span>
              </div>
              <span className="text-[12px] font-bold text-cyan-400">{bestStrategy.confidence} 신뢰도</span>
            </div>
            <div className="text-[13px] text-slate-400 mt-1">{bestStrategy.description}</div>
            <div className="flex gap-1 mt-1.5 flex-wrap">
              {bestStrategy.steps?.map((step: any, si: number) => (
                <span key={si} className="text-[13px] px-1.5 py-0.5 rounded bg-slate-700/50 text-slate-300">
                  {si > 0 && " → "}{step.solver_name}
                </span>
              ))}
            </div>
          </div>
          {otherStrategies.length > 0 && (
            <div className="mt-2">
              <button
                onClick={(e) => { e.stopPropagation(); setIsExpanded(!isExpanded); }}
                className="text-[13px] text-slate-500 hover:text-slate-300 transition flex items-center gap-1"
              >
                <span>{isExpanded ? "▼" : "▶"}</span>
                다른 {otherStrategies.length}개 전략 {isExpanded ? "숨기기" : "보기"}
              </button>
              {isExpanded && otherStrategies.map((st: any, oi: number) => (
                <div key={oi} className="mt-2 p-2 rounded-lg border border-slate-700/30 bg-slate-800/30">
                  <div className="flex items-center justify-between">
                    <span className="text-[13px] font-bold text-slate-400">{st.name}</span>
                    <span className="text-[13px] text-slate-500">{st.confidence} 신뢰도</span>
                  </div>
                  <div className="text-[13px] text-slate-500 mt-1">{st.description}</div>
                  <div className="flex gap-1 mt-1 flex-wrap">
                    {st.steps?.map((step: any, si: number) => (
                      <span key={si} className="text-[12px] px-1 py-0.5 rounded bg-slate-700/30 text-slate-400">
                        {si > 0 && " → "}{step.solver_name}
                      </span>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
