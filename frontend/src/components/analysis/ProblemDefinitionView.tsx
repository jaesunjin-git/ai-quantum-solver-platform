// src/components/analysis/ProblemDefinitionView.tsx
// v3.0 - 즉시 일괄 토글 + 확정 이벤트 전송
import {
  ClipboardList, Check, Edit3, AlertTriangle,
  ChevronDown, ChevronUp, X, Lock, RefreshCw, Info, Shield, ShieldAlert, RotateCcw
} from 'lucide-react';
import { useState, useCallback, useEffect } from 'react';
import type { ProblemDefinitionData } from './types';

interface ConstraintEdit {
  name: string;
  category: 'hard' | 'soft';
  origCategory: 'hard' | 'soft';
  fixed: boolean;
  changeable: boolean;
  desc: string;
  nameKo: string;
  changed: boolean;
}

function buildEdits(hardCs: any, softCs: any): ConstraintEdit[] {
  const list: ConstraintEdit[] = [];
  const parse = (cs: any, cat: 'hard' | 'soft') => {
    Object.entries(cs).forEach(([k, c]: [string, any]) => {
      list.push({
        name: k,
        category: cat,
        origCategory: cat,
        fixed: c.fixed === true || c.changeable === false,
        changeable: c.changeable !== false,
        desc: c.description || '',
        nameKo: c.name_ko || c.korean_name || c.description || '',
        changed: false,
      });
    });
  };
  parse(hardCs, 'hard');
  parse(softCs, 'soft');
  return list;
}

export function ProblemDefinitionView({
  data, onAction, onEvent,
}: {
  data: ProblemDefinitionData;
  onAction?: (type: string, message: string) => void;
  onEvent?: (message: string, eventType: string, eventData: any) => void;
}) {
  const [showParams, setShowParams] = useState(true);
  const [isEditMode, setIsEditMode] = useState(false);
  const [edits, setEdits] = useState<ConstraintEdit[]>([]);
  const [showObjGate, setShowObjGate] = useState(false);
  const [selectedIdxs, setSelectedIdxs] = useState<Set<number>>(new Set());

  const proposal = data.proposal || data.confirmed_problem;
  const isConfirmed = data.view_mode === 'problem_defined';
  const agentStatus = (data as any)?.agent_status || '';

  const hardConstraints = proposal?.hard_constraints || {};
  const softConstraints = proposal?.soft_constraints || {};
  const objective = proposal?.objective || {};
  const parameters = proposal?.parameters || {};

  // proposal이 바뀔 때 edits 재초기화 (편집 모드는 유지)
  useEffect(() => {
    if (!proposal) return;
    setEdits(buildEdits(hardConstraints, softConstraints));
    setSelectedIdxs(new Set());
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [proposal]);

  // 목적함수 재구성 완료 시 리셋
  useEffect(() => {
    if (agentStatus === 'objective_changed_constraints_rebuilt') {
      setIsEditMode(false);
      setEdits([]);
      setShowObjGate(false);
      setSelectedIdxs(new Set());
    }
  }, [agentStatus]);

  if (!proposal) {
    return (
      <div className="flex-1 flex items-center justify-center text-slate-500 p-8">
        <p>{'문제 정의 데이터를 불러오는 중...'}</p>
      </div>
    );
  }

  const effectiveEdits = edits.length > 0
    ? edits
    : buildEdits(hardConstraints, softConstraints);

  const hardEdits = effectiveEdits.filter(e => e.category === 'hard');
  const softEdits = effectiveEdits.filter(e => e.category === 'soft');
  const changedCount = effectiveEdits.filter(e => e.changed).length;
  const selectedHardCount = hardEdits.filter(e => selectedIdxs.has(effectiveEdits.indexOf(e))).length;
  const selectedSoftCount = softEdits.filter(e => selectedIdxs.has(effectiveEdits.indexOf(e))).length;

  const dataParams = Object.entries(parameters).filter(([, v]: [string, any]) => v.source === 'data');
  const defaultParams = Object.entries(parameters).filter(([, v]: [string, any]) => v.source === 'default');
  const missingParams = Object.entries(parameters).filter(([, v]: [string, any]) => v.source === 'user_input_required');

  const enterEditMode = useCallback(() => {
    setEdits(buildEdits(hardConstraints, softConstraints));
    setIsEditMode(true);
    setSelectedIdxs(new Set());
  }, [hardConstraints, softConstraints]);

  // 취소: 편집 모드 종료만, 변경됨 배지는 유지
  const cancelEdit = () => {
    setIsEditMode(false);
    setSelectedIdxs(new Set());
  };

  const toggleSelect = (idx: number) => {
    const e = effectiveEdits[idx];
    if (!e || e.fixed || !e.changeable) return;
    setSelectedIdxs(prev => {
      const next = new Set(prev);
      if (next.has(idx)) next.delete(idx);
      else next.add(idx);
      return next;
    });
  };

  // 일괄 변경: 적용 후 뷰 모드로 복귀 (결과 확인)
  const applyBatchToggle = () => {
    setEdits(prev =>
      prev.map((e, i) => {
        if (!selectedIdxs.has(i)) return e;
        const nc = e.category === 'hard' ? 'soft' as const : 'hard' as const;
        return { ...e, category: nc, changed: nc !== e.origCategory };
      })
    );
    setSelectedIdxs(new Set());
    setIsEditMode(false);
  };

  const resetEdits = useCallback(() => {
    setEdits(buildEdits(hardConstraints, softConstraints));
    setSelectedIdxs(new Set());
  }, [hardConstraints, softConstraints]);

  // 확정: onEvent가 있으면 구조화 이벤트로, 없으면 텍스트 메시지로
  const confirmProblem = () => {
    const changes = effectiveEdits
      .filter(e => e.changed)
      .map(e => ({ name: e.name, to: e.category }));

    const changeCount = changes.length;
    const displayMsg = changeCount > 0
      ? `문제 정의 확정 (제약조건 ${changeCount}개 수정 포함)`
      : '문제 정의 확정';

    if (onEvent) {
      onEvent(displayMsg, 'problem_definition_confirm', { constraint_changes: changes });
    } else {
      const msgs = changes.map(e => `${e.name} ${e.to}로 변경`);
      const finalMsg = msgs.length > 0 ? `확인\n${msgs.join('\n')}` : '확인';
      onAction?.('send', finalMsg);
    }
    setIsEditMode(false);
  };

  const confirmObjChange = () => {
    setShowObjGate(false);
    setIsEditMode(false);
    setEdits([]);
    onAction?.('send', '목적함수 변경');
  };

  return (
    <div className="h-full flex flex-col bg-slate-900 overflow-hidden animate-fade-in">

      {/* Header */}
      <div className="p-6 border-b border-slate-800 bg-slate-900/95 sticky top-0 z-10">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className={isConfirmed
              ? 'p-2 rounded-lg bg-emerald-500/20 text-emerald-400'
              : 'p-2 rounded-lg bg-amber-500/20 text-amber-400'}>
              <ClipboardList size={20} />
            </div>
            <div>
              <h2 className="text-xl font-bold text-white">
                {isConfirmed ? '문제 정의 확정' : '문제 정의 제안'}
              </h2>
              <p className="text-xs text-slate-400">
                {isEditMode && changedCount > 0
                  ? `${changedCount}개 수정됨 · 확정 전 계속 변경 가능`
                  : isConfirmed
                    ? '확정됨 · 수정 후 재확정 가능'
                    : '검토 후 확인 또는 수정'}
              </p>
            </div>
          </div>

          {/* 편집 모드 헤더 버튼: 확정 상태여도 표시 */}
          {isEditMode && (
            <div className="flex items-center gap-2">
              {selectedIdxs.size > 0 && (
                <button onClick={applyBatchToggle}
                  className="flex items-center gap-1 px-3 py-1.5 text-xs bg-amber-600 text-white rounded-lg hover:bg-amber-500 transition-colors">
                  <AlertTriangle size={13} /> {`${selectedIdxs.size}개 일괄 변경`}
                </button>
              )}
              {changedCount > 0 && (
                <button onClick={resetEdits}
                  className="flex items-center gap-1 px-2.5 py-1.5 text-xs bg-slate-700 text-slate-300 rounded-lg hover:bg-slate-600">
                  <RotateCcw size={13} /> {'초기화'}
                </button>
              )}
              <button onClick={cancelEdit}
                className="flex items-center gap-1 px-2.5 py-1.5 text-xs bg-slate-700 text-slate-300 rounded-lg hover:bg-slate-600">
                <X size={13} /> {'취소'}
              </button>
            </div>
          )}

          {/* 비편집 모드 헤더 버튼: 확정 상태여도 수정 진입 가능 */}
          {!isEditMode && (
            <button onClick={enterEditMode}
              className="flex items-center gap-1 px-3 py-1.5 text-xs bg-blue-500/20 text-blue-400 rounded-lg hover:bg-blue-500/30 transition-colors">
              <Edit3 size={13} /> {'수정'}
            </button>
          )}
        </div>
      </div>

      <div className="flex-1 overflow-y-auto p-6 space-y-4">

        {/* 목적함수 변경 경고 */}
        {showObjGate && (
          <div className="bg-orange-500/10 border border-orange-500/30 rounded-xl p-4">
            <div className="flex items-start gap-2">
              <RefreshCw size={16} className="text-orange-400 mt-0.5 flex-shrink-0" />
              <div className="flex-1">
                <p className="text-sm font-medium text-orange-300">{'목적함수를 변경하면 제약조건이 새로 구성됩니다.'}</p>
                <p className="text-xs text-orange-400/70 mt-1">{'현재 수정한 제약조건 편집 내용은 초기화됩니다.'}</p>
                <div className="flex gap-2 mt-2">
                  <button onClick={confirmObjChange}
                    className="px-3 py-1 text-xs bg-orange-600 text-white rounded hover:bg-orange-500">
                    {'계속 변경'}
                  </button>
                  <button onClick={() => setShowObjGate(false)}
                    className="px-3 py-1 text-xs bg-slate-700 text-slate-300 rounded hover:bg-slate-600">
                    {'취소'}
                  </button>
                </div>
              </div>
            </div>
          </div>
        )}

        {/* 문제 유형 */}
        {(proposal.stage || proposal.variant) && (
          <section className="bg-slate-800/50 rounded-xl p-4 border border-slate-700">
            <h3 className="text-sm font-semibold text-slate-300 mb-2">{'문제 유형'}</h3>
            <div className="flex gap-4 text-sm">
              {proposal.stage && <span className="text-cyan-400">{'단계: ' + proposal.stage}</span>}
              {proposal.variant && <span className="text-slate-300">{'세부: ' + proposal.variant}</span>}
            </div>
          </section>
        )}

        {/* 목적함수 */}
        <section className="bg-slate-800/50 rounded-xl p-4 border border-slate-700">
          <div className="flex items-center justify-between mb-2">
            <h3 className="text-sm font-semibold text-slate-300">{'목적함수'}</h3>
            {!isConfirmed && !isEditMode && (
              <button onClick={() => setShowObjGate(true)}
                className="text-xs px-2 py-1 bg-orange-500/20 text-orange-400 rounded hover:bg-orange-500/30 transition-colors">
                {'변경'}
              </button>
            )}
          </div>
          <div className="flex items-center gap-2">
            <span className="px-2 py-0.5 text-xs bg-indigo-500/20 text-indigo-400 rounded">
              {objective.type || 'minimize'}
            </span>
            <p className="text-sm text-slate-200">
              {objective.description_ko || objective.description || objective.target || '-'}
            </p>
          </div>
          {objective.alternatives && objective.alternatives.length > 0 && !isEditMode && (
            <div className="mt-2">
              <p className="text-xs text-slate-500 mb-1">{'대안:'}</p>
              <div className="flex gap-2 flex-wrap">
                {objective.alternatives.map((alt: any, i: number) => (
                  <span key={i} className="px-2 py-0.5 text-xs bg-slate-700 text-slate-400 rounded-full">
                    {alt.description || alt.target}
                  </span>
                ))}
              </div>
            </div>
          )}
        </section>

        {/* 제약조건 */}
        <section className="bg-slate-800/50 rounded-xl p-4 border border-slate-700">
          <h3 className="text-sm font-semibold text-slate-300 mb-3">{'제약조건'}</h3>

          {/* 비편집 모드: effectiveEdits 기반으로 표시 */}
          {!isEditMode ? (
            <>
              {hardEdits.length > 0 && (
                <div className="mb-3">
                  <p className="text-xs text-red-400 font-semibold mb-1">
                    {'필수 (Hard) - ' + hardEdits.length + '개'}
                  </p>
                  {hardEdits.map((e) => (
                    <div key={e.name} className="flex items-start justify-between text-sm py-1.5 border-b border-slate-700/50 last:border-0">
                      <div className="flex items-start gap-2">
                        <Shield size={14} className="text-red-400 mt-0.5 flex-shrink-0" />
                        <div>
                          <span className="text-slate-200 font-medium">{e.nameKo || e.name}</span>
                          {e.desc && <p className="text-xs text-slate-500">{e.desc}</p>}
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              )}
              {softEdits.length > 0 && (
                <div>
                  <p className="text-xs text-amber-400 font-semibold mb-1">
                    {'선호 (Soft) - ' + softEdits.length + '개'}
                  </p>
                  {softEdits.map((e) => (
                    <div key={e.name} className="flex items-start justify-between text-sm py-1.5 border-b border-slate-700/50 last:border-0">
                      <div className="flex items-start gap-2">
                        <ShieldAlert size={14} className="text-amber-400 mt-0.5 flex-shrink-0" />
                        <div>
                          <span className="text-slate-200 font-medium">{e.nameKo || e.name}</span>
                          {e.desc && <p className="text-xs text-slate-500">{e.desc}</p>}
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </>
          ) : (
            /* 편집 모드 */
            <>
              {/* Hard 제약조건 */}
              <div className="mb-3">
                <div className="flex items-center justify-between mb-1">
                  <p className="text-xs text-red-400 font-semibold">
                    {'필수 (Hard) - ' + hardEdits.length + '개'}
                  </p>
                  {selectedHardCount > 0 && (
                    <span className="text-[10px] text-amber-400">{selectedHardCount + '개 선택'}</span>
                  )}
                </div>
                {hardEdits.map((e) => {
                  const gIdx = effectiveEdits.indexOf(e);
                  const isSelected = selectedIdxs.has(gIdx);
                  return (
                    <div key={e.name} className={
                      'flex items-center text-sm py-1.5 border-b border-slate-700/50 last:border-0' +
                      (e.changed ? ' bg-amber-500/10' : isSelected ? ' bg-blue-500/10' : '')
                    }>
                      {e.changeable && !e.fixed ? (
                        <label className="flex items-center cursor-pointer mr-2 flex-shrink-0">
                          <input
                            type="checkbox"
                            checked={isSelected}
                            onChange={() => toggleSelect(gIdx)}
                            className="w-3.5 h-3.5 rounded border-slate-500 bg-slate-700 text-amber-500 focus:ring-amber-500 focus:ring-1 cursor-pointer"
                          />
                        </label>
                      ) : (
                        <span className="w-3.5 mr-2 flex-shrink-0" />
                      )}
                      <div className="flex items-start gap-2 flex-1 min-w-0">
                        <Shield size={14} className="text-red-400 mt-0.5 flex-shrink-0" />
                        <div className="min-w-0">
                          <div className="flex items-center gap-1.5 flex-wrap">
                            <span className="text-slate-200 font-medium">{e.nameKo || e.name}</span>
                            {e.fixed && (
                              <span className="inline-flex items-center gap-0.5 text-[10px] bg-slate-700 text-slate-500 px-1.5 py-0.5 rounded flex-shrink-0">
                                <Lock size={9} /> {'고정'}
                              </span>
                            )}
                            {e.changed && (
                              <span className="text-[10px] bg-amber-500/30 text-amber-400 px-1.5 py-0.5 rounded flex-shrink-0">
                                {'변경됨'}
                              </span>
                            )}
                          </div>
                          <p className="text-xs text-slate-500 font-mono truncate">{e.name}</p>
                        </div>
                      </div>
                      {e.changeable && !e.fixed && (
                        <span className="text-[10px] text-red-400/60 ml-2 whitespace-nowrap flex-shrink-0">{'→ Soft'}</span>
                      )}
                    </div>
                  );
                })}
              </div>

              {/* Soft 제약조건 */}
              <div>
                <div className="flex items-center justify-between mb-1">
                  <p className="text-xs text-amber-400 font-semibold">
                    {'선호 (Soft) - ' + softEdits.length + '개'}
                  </p>
                  {selectedSoftCount > 0 && (
                    <span className="text-[10px] text-amber-400">{selectedSoftCount + '개 선택'}</span>
                  )}
                </div>
                {softEdits.map((e) => {
                  const gIdx = effectiveEdits.indexOf(e);
                  const isSelected = selectedIdxs.has(gIdx);
                  return (
                    <div key={e.name} className={
                      'flex items-center text-sm py-1.5 border-b border-slate-700/50 last:border-0' +
                      (e.changed ? ' bg-amber-500/10' : isSelected ? ' bg-blue-500/10' : '')
                    }>
                      {e.changeable && !e.fixed ? (
                        <label className="flex items-center cursor-pointer mr-2 flex-shrink-0">
                          <input
                            type="checkbox"
                            checked={isSelected}
                            onChange={() => toggleSelect(gIdx)}
                            className="w-3.5 h-3.5 rounded border-slate-500 bg-slate-700 text-amber-500 focus:ring-amber-500 focus:ring-1 cursor-pointer"
                          />
                        </label>
                      ) : (
                        <span className="w-3.5 mr-2 flex-shrink-0" />
                      )}
                      <div className="flex items-start gap-2 flex-1 min-w-0">
                        <ShieldAlert size={14} className="text-amber-400 mt-0.5 flex-shrink-0" />
                        <div className="min-w-0">
                          <div className="flex items-center gap-1.5 flex-wrap">
                            <span className="text-slate-200 font-medium">{e.nameKo || e.name}</span>
                            {e.changed && (
                              <span className="text-[10px] bg-amber-500/30 text-amber-400 px-1.5 py-0.5 rounded flex-shrink-0">
                                {'변경됨'}
                              </span>
                            )}
                          </div>
                          <p className="text-xs text-slate-500 font-mono truncate">{e.name}</p>
                        </div>
                      </div>
                      {e.changeable && !e.fixed && (
                        <span className="text-[10px] text-blue-400/60 ml-2 whitespace-nowrap flex-shrink-0">{'→ Hard'}</span>
                      )}
                    </div>
                  );
                })}
              </div>
            </>
          )}
        </section>

        {/* 편집 모드 안내 */}
        {isEditMode && (
          <div className="flex items-start gap-2 p-3 bg-blue-500/10 rounded-xl border border-blue-500/20">
            <Info size={13} className="text-blue-400 mt-0.5 flex-shrink-0" />
            <p className="text-[11px] text-blue-300 leading-relaxed">
              {'체크박스로 변경할 제약을 선택 후 [일괄 변경] 버튼을 누르면 즉시 반영됩니다. 확정 전까지 계속 수정 가능합니다.'}
            </p>
          </div>
        )}

        {/* 파라미터 */}
        <section className="bg-slate-800/50 rounded-xl p-4 border border-slate-700">
          <button onClick={() => setShowParams(!showParams)}
            className="flex items-center justify-between w-full text-sm font-semibold text-slate-300">
            <span>{'파라미터 (' + Object.keys(parameters).length + ')'}</span>
            {showParams ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
          </button>
          {showParams && (
            <div className="mt-3 space-y-3">
              {dataParams.length > 0 && (
                <div>
                  <p className="text-xs text-emerald-400 mb-1">{'데이터 추출'}</p>
                  {dataParams.map(([k, v]: [string, any]) => (
                    <div key={k} className="flex justify-between text-sm py-0.5">
                      <span className="text-slate-400">{k}</span>
                      <span className="text-emerald-300 font-mono">{v.value ?? '-'}</span>
                    </div>
                  ))}
                </div>
              )}
              {defaultParams.length > 0 && (
                <div>
                  <p className="text-xs text-amber-400 mb-1">{'기본값 (수정 가능)'}</p>
                  {defaultParams.map(([k, v]: [string, any]) => (
                    <div key={k} className="flex justify-between text-sm py-0.5">
                      <span className="text-slate-400">{k}</span>
                      <span className="text-amber-300 font-mono">{v.value ?? '-'}</span>
                    </div>
                  ))}
                </div>
              )}
              {missingParams.length > 0 && (
                <div>
                  <p className="text-xs text-red-400 mb-1">{'입력 필요'}</p>
                  {missingParams.map(([k]: [string, any]) => (
                    <div key={k} className="flex justify-between text-sm py-0.5">
                      <span className="text-slate-400">{k}</span>
                      <span className="text-red-400 font-mono">{'???'}</span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </section>
      </div>

      {/* 하단 액션 바: 확정 여부와 관계없이 수정/확정 항상 가능 */}
      <div className="p-4 border-t border-slate-800 bg-slate-900 flex gap-3">
        <button onClick={confirmProblem}
          className="flex-1 font-bold py-3 rounded-xl transition flex items-center justify-center gap-2 text-white bg-emerald-600 hover:bg-emerald-500">
          <Check size={18} />
          {changedCount > 0 ? `문제 정의 확정 (${changedCount}개 수정 포함)` : '문제 정의 확정'}
        </button>
        {!isEditMode && (
          <button onClick={enterEditMode}
            className="px-6 bg-slate-800 hover:bg-slate-700 text-slate-300 font-bold py-3 rounded-xl transition border border-slate-700 flex items-center justify-center gap-2">
            <Edit3 size={18} /> {'수정'}
          </button>
        )}
      </div>
    </div>
  );
}
