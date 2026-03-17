// src/components/analysis/MathModelView.tsx
import { Activity, ChevronDown, ChevronUp, Download, Edit3, FileText, Play, Save, X } from 'lucide-react';
import { useState, useCallback } from 'react';
import { useAuth } from '../../context/AuthContext';
import { downloadReport } from './downloadHelper';
import type { MathModelData } from './types';

export function MathModelView({
  data,
  onAction,
  projectId,
}: {
  data: MathModelData;
  onAction?: (type: string, message: string) => void;
  onEvent?: (message: string, eventType: string, eventData: any) => void;
  projectId?: string;
}) {
  const { authFetch } = useAuth();
  const model = data.math_model || {};
  const meta = model.metadata || {};
  const variables = model.variables || [];
  const constraints = model.constraints || [];
  const objective = model.objective || {};
  const sets = model.sets || {};
  const parameters = model.parameters || [];

  const varCount = meta.estimated_variable_count || variables.length || 0;
  const conCount = meta.estimated_constraint_count || constraints.length || 0;
  const varTypes = meta.variable_types || meta.variable_types || [];
  const recSolvers = meta.recommended_solvers || [];

  const [showAllConstraints, setShowAllConstraints] = useState(false);
  const [showParams, setShowParams] = useState(true);
  const [editingParam, setEditingParam] = useState<string | null>(null);
  const [editValue, setEditValue] = useState('');
  const CONSTRAINTS_PREVIEW = 8;

  // 파라미터를 배열 또는 객체에서 통일된 리스트로 변환
  const paramList: { id: string; value: any; type: string; source?: string; description?: string; boundSource?: string; boundReasoning?: string }[] =
    Array.isArray(parameters)
      ? parameters.map((p: any) => ({
          id: p.id || p.name || '',
          value: p.default_value ?? p.value ?? null,
          type: p.type || 'scalar',
          source: p.source_file || p.source || '',
          description: p.description || '',
          boundSource: p.auto_bound_source || '',
          boundReasoning: p.auto_bound_reasoning || '',
        }))
      : Object.entries(parameters).map(([k, v]: [string, any]) => ({
          id: k,
          value: typeof v === 'object' ? (v.default_value ?? v.value ?? null) : v,
          type: typeof v === 'object' ? (v.type || 'scalar') : 'scalar',
          source: typeof v === 'object' ? (v.source_file || v.source || '') : '',
          description: typeof v === 'object' ? (v.description || '') : '',
          boundSource: typeof v === 'object' ? (v.auto_bound_source || '') : '',
          boundReasoning: typeof v === 'object' ? (v.auto_bound_reasoning || '') : '',
        }));

  const scalarParams = paramList.filter(p => p.type === 'scalar' || p.type === 'number');
  const indexedParams = paramList.filter(p => p.type !== 'scalar' && p.type !== 'number');

  const handleParamEdit = useCallback((paramId: string) => {
    const p = paramList.find(pp => pp.id === paramId);
    setEditingParam(paramId);
    setEditValue(p?.value?.toString() ?? '');
  }, [paramList]);

  const handleParamSave = useCallback(() => {
    if (editingParam && onAction) {
      onAction('send', `${editingParam} = ${editValue}`);
      setEditingParam(null);
      setEditValue('');
    }
  }, [editingParam, editValue, onAction]);

  return (
    <div className="h-full flex flex-col bg-slate-900 overflow-hidden animate-fade-in">
      {/* 헤더 - 분석 리포트와 파일의 구조 */}
      <div className="p-6 border-b border-slate-800 bg-slate-900/95 sticky top-0 z-10">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="p-2 bg-emerald-500/20 rounded-lg text-emerald-400">
              <FileText size={20} />
            </div>
            <div>
              <h2 className="text-xl font-bold text-white">Mathematical Model</h2>
              <div className="flex items-center gap-2 text-xs text-slate-400">
                <Activity size={14} className="text-emerald-400" />
                <span>{model.problem_name || model.name || '수학 모델'}</span>
                <span>·</span>
                <span className="text-slate-200 font-medium">{model.domain || 'general'}</span>
              </div>
            </div>
          </div>

          {/* 다운로드 버튼 */}
          {projectId && (
            <div className="flex gap-2">
              <button
                onClick={() => downloadReport(projectId!, 'json', 'math_model', authFetch)}
                className="px-3 py-1.5 text-xs bg-slate-800 hover:bg-slate-700 text-slate-300 rounded-lg border border-slate-700 transition flex items-center gap-1"
                title="JSON으로 다운로드"
              >
                <Download size={14} />
                <span>.json</span>
              </button>
              <button
                onClick={() => downloadReport(projectId!, 'md', 'math_model', authFetch)}
                className="px-3 py-1.5 text-xs bg-slate-800 hover:bg-slate-700 text-slate-300 rounded-lg border border-slate-700 transition flex items-center gap-1"
                title="Markdown으로 다운로드"
              >
                <Download size={14} />
                <span>.md</span>
              </button>
              <button
                onClick={() => downloadReport(projectId!, 'docx', 'math_model', authFetch)}
                className="px-3 py-1.5 text-xs bg-slate-800 hover:bg-slate-700 text-slate-300 rounded-lg border border-slate-700 transition flex items-center gap-1"
                title="Word로 다운로드"
              >
                <Download size={14} />
                <span>.docx</span>
              </button>
            </div>
          )}
        </div>
      </div>

      {/* 본문 - 스크롤 영역 */}
      <div className="flex-1 overflow-y-auto p-6 custom-scrollbar space-y-6">
        {/* Summary Cards */}
        <div className="grid grid-cols-3 gap-3">
          <div className="bg-slate-800 rounded-xl p-4 text-center">
            <div className="text-2xl font-bold text-indigo-400">{varCount}</div>
                <div className="text-xs text-slate-400 mt-1">변수 수</div>
          </div>
          <div className="bg-slate-800 rounded-xl p-4 text-center">
            <div className="text-2xl font-bold text-amber-400">{conCount}</div>
            <div className="text-xs text-slate-400 mt-1">제약 조건</div>
          </div>
          <div className="bg-slate-800 rounded-xl p-4 text-center">
            <div className="text-2xl font-bold text-emerald-400">{varTypes.length > 0 ? varTypes.join(', ') : 'binary'}</div>
            <div className="text-xs text-slate-400 mt-1">변수 유형</div>
          </div>
        </div>

        {/* Objective Function */}
        {objective && (objective.description || objective.type) && (
          <div className="bg-slate-800 rounded-xl p-4 space-y-2">
          <h3 className="text-[15px] font-semibold text-white flex items-center gap-2">📊 목적함수</h3>
            <div className="text-[13px] text-slate-300">
              <span className="text-indigo-400 font-mono">{objective.type || 'minimize'}</span>
              {objective.description && <p className="mt-1">{objective.description}</p>}
              {objective.expression && (
                <code className="block mt-2 bg-slate-900 p-2 rounded text-xs text-green-400 overflow-x-auto">
                  {objective.expression}
                </code>
              )}
            </div>
            {objective.alternatives && objective.alternatives.length > 0 && (
              <div className="mt-2">
                <p className="text-xs text-slate-500">대안 목적함수:</p>
                {objective.alternatives.map((alt: any, idx: number) => (
                  <div key={idx} className="text-xs text-slate-400 mt-1">
                    • {alt.description || alt.expression || JSON.stringify(alt)}
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        {/* Sets */}
        {(Array.isArray(sets) ? sets.length > 0 : Object.keys(sets).length > 0) && (
          <div className="bg-slate-800 rounded-xl p-4 space-y-2">
          <h3 className="text-[15px] font-semibold text-white">📊 집합 (Sets)</h3>
            <div className="space-y-1">
              {(Array.isArray(sets) ? sets : Object.entries(sets).map(([k, v]) => ({ id: k, ...(typeof v === 'object' ? v : { description: v }) }))).map((s: any, idx: number) => (
                <div key={idx} className="flex justify-between text-xs">
                  <span className="text-indigo-400 font-mono">{s.id || s.name || idx}</span>
                  <span className="text-slate-400">{s.description || s.size || ''}</span>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Variables */}
        {variables.length > 0 && (
          <div className="bg-slate-800 rounded-xl p-4 space-y-2">
          <h3 className="text-[15px] font-semibold text-white">📊 변수 정의</h3>
            <div className="space-y-1">
              {variables.slice(0, 10).map((v: any, idx: number) => (
                <div key={idx} className="flex justify-between text-xs">
                  <span className="text-green-400 font-mono">{v.name || v.id || `x_${idx}`}</span>
                  <span className="text-slate-400">{v.type || 'binary'} · {v.description || ''}</span>
                </div>
              ))}
              {variables.length > 10 && (
                 <p className="text-[13px] text-slate-500">... 외 {variables.length - 10}개</p>
              )}
            </div>
          </div>
        )}

        {/* Parameters */}
        {paramList.length > 0 && (
          <div className="bg-slate-800 rounded-xl p-4 space-y-2">
            <button
              onClick={() => setShowParams(!showParams)}
              className="flex items-center justify-between w-full"
            >
              <h3 className="text-[15px] font-semibold text-white flex items-center gap-2">
                {'🔢 파라미터'}
                <span className="text-xs text-slate-500 font-normal">{paramList.length}개</span>
              </h3>
              {showParams ? <ChevronUp size={14} className="text-slate-400" /> : <ChevronDown size={14} className="text-slate-400" />}
            </button>
            {showParams && (
              <div className="space-y-3 mt-2">
                {/* Scalar parameters */}
                {scalarParams.length > 0 && (
                  <div>
                    <p className="text-xs text-emerald-400 font-semibold mb-1 uppercase tracking-wider">{'Scalar'}</p>
                    <div className="space-y-0.5">
                      {scalarParams.map(p => (
                        <div key={p.id} className="flex items-center justify-between text-xs py-1 border-b border-slate-700/30 last:border-0 group">
                          <div className="flex items-center gap-2 min-w-0">
                            <span className="text-indigo-400 font-mono">{p.id}</span>
                            {p.description && <span className="text-slate-500 truncate max-w-[200px]">{p.description}</span>}
                          </div>
                          <div className="flex items-center gap-1.5">
                            {editingParam === p.id ? (
                              <>
                                <input
                                  type="text"
                                  value={editValue}
                                  onChange={e => setEditValue(e.target.value)}
                                  onKeyDown={e => e.key === 'Enter' && handleParamSave()}
                                  className="w-20 px-1.5 py-0.5 text-xs bg-slate-900 border border-indigo-500 rounded text-slate-200 focus:outline-none"
                                  autoFocus
                                />
                                <button onClick={handleParamSave} className="text-emerald-400 hover:text-emerald-300"><Save size={12} /></button>
                                <button onClick={() => setEditingParam(null)} className="text-slate-500 hover:text-slate-300"><X size={12} /></button>
                              </>
                            ) : (
                              <>
                                <span className={`font-mono ${p.value != null ? 'text-emerald-300' : 'text-red-400'}`}>
                                  {p.value != null ? String(p.value) : '???'}
                                </span>
                                {p.boundSource && (
                                  <span
                                    className={`text-[10px] px-1.5 py-0.5 rounded-full font-medium ${
                                      p.boundSource === 'confirmed_problem'
                                        ? 'bg-blue-500/20 text-blue-300'
                                        : p.boundSource.includes('parameters.csv')
                                        ? 'bg-cyan-500/20 text-cyan-300'
                                        : p.boundSource.includes('reference_ranges')
                                        ? 'bg-amber-500/20 text-amber-300'
                                        : 'bg-purple-500/20 text-purple-300'
                                    }`}
                                    title={p.boundReasoning || p.boundSource}
                                  >
                                    {p.boundSource === 'confirmed_problem'
                                      ? '사용자 정의'
                                      : p.boundSource.includes('parameters.csv')
                                      ? '데이터'
                                      : p.boundSource.includes('reference_ranges')
                                      ? '도메인 기준'
                                      : p.boundSource.length > 15 ? p.boundSource.slice(0, 15) + '…' : p.boundSource}
                                  </span>
                                )}
                                {!p.boundSource && p.source && (
                                  <span className="text-xs text-slate-600 hidden group-hover:inline">
                                    {String(p.source).replace('normalized/', '').slice(0, 20)}
                                  </span>
                                )}
                                <button
                                  onClick={() => handleParamEdit(p.id)}
                                  className="opacity-0 group-hover:opacity-100 text-slate-500 hover:text-indigo-400 transition-opacity"
                                  title="편집"
                                >
                                  <Edit3 size={11} />
                                </button>
                              </>
                            )}
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                {/* Indexed parameters */}
                {indexedParams.length > 0 && (
                  <div>
                    <p className="text-xs text-amber-400 font-semibold mb-1 uppercase tracking-wider">{'Indexed / Array'}</p>
                    <div className="space-y-0.5">
                      {indexedParams.map(p => {
                        // 값 요약: 배열이면 개수, scalar면 값 표시
                        const valueSummary = p.value != null
                          ? Array.isArray(p.value)
                            ? `[${p.value.length}건]`
                            : typeof p.value === 'object'
                            ? `{${Object.keys(p.value).length}건}`
                            : String(p.value)
                          : null;
                        return (
                        <div key={p.id} className="flex items-center justify-between text-xs py-1 border-b border-slate-700/30 last:border-0">
                          <div className="flex items-center gap-2 min-w-0">
                            <span className="text-indigo-400 font-mono">{p.id}</span>
                            {p.description && <span className="text-slate-500 truncate max-w-[200px]">{p.description}</span>}
                          </div>
                          <div className="flex items-center gap-1.5">
                            {valueSummary && (
                              <span className="font-mono text-amber-300">{valueSummary}</span>
                            )}
                            <span className="text-xs text-slate-500 bg-slate-900 px-1.5 py-0.5 rounded">{p.type}</span>
                            {p.boundSource ? (
                              <span
                                className={`text-[10px] px-1.5 py-0.5 rounded-full font-medium ${
                                  p.boundSource.includes('parameters.csv')
                                    ? 'bg-cyan-500/20 text-cyan-300'
                                    : p.boundSource === 'confirmed_problem'
                                    ? 'bg-blue-500/20 text-blue-300'
                                    : 'bg-purple-500/20 text-purple-300'
                                }`}
                                title={p.boundReasoning || p.boundSource}
                              >
                                {p.boundSource.includes('parameters.csv') ? '데이터' : p.boundSource === 'confirmed_problem' ? '사용자 정의' : p.boundSource.slice(0, 15)}
                              </span>
                            ) : p.source ? (
                              <span className="text-xs text-slate-600">{String(p.source).replace('normalized/', '').slice(0, 25)}</span>
                            ) : null}
                          </div>
                        </div>
                        );
                      })}
                    </div>
                  </div>
                )}
              </div>
            )}
          </div>
        )}

        {/* Constraints */}
        {constraints.length > 0 && (
          <div className="bg-slate-800 rounded-xl p-4 space-y-2">
            <div className="flex items-center justify-between">
              <h3 className="text-[15px] font-semibold text-white">{'🗣️ 제약 조건'}</h3>
              <div className="flex items-center gap-2">
                <span className="text-xs bg-red-500/15 text-red-400 px-1.5 py-0.5 rounded">
                  Hard {constraints.filter((c: any) => (c.priority || c.category || c.type || 'hard') === 'hard').length}
                </span>
                <span className="text-xs bg-yellow-500/15 text-yellow-400 px-1.5 py-0.5 rounded">
                  Soft {constraints.filter((c: any) => (c.priority || c.category || c.type || 'hard') !== 'hard').length}
                </span>
              </div>
            </div>
            <div className="space-y-2">
              {(showAllConstraints ? constraints : constraints.slice(0, CONSTRAINTS_PREVIEW)).map((c: any, idx: number) => {
                const cat = c.priority || c.category || c.type || 'hard';
                const isHard = cat === 'hard';
                return (
                  <div key={idx} className="bg-slate-900 rounded-lg p-2">
                    <div className="flex justify-between items-center">
                      <span className="text-xs font-mono text-amber-400">{c.name || c.id || `c${idx + 1}`}</span>
                      <span className={`text-xs px-2 py-0.5 rounded ${isHard ? 'bg-red-500/20 text-red-400' : 'bg-yellow-500/20 text-yellow-400'}`}>
                        {isHard ? 'Hard' : 'Soft'}
                      </span>
                    </div>
                    <p className="text-xs text-slate-400 mt-1">{c.description || ''}</p>
                    {c.expression && (
                      <code className="text-xs text-green-400 mt-1 block">{c.expression}</code>
                    )}
                  </div>
                );
              })}
              {constraints.length > CONSTRAINTS_PREVIEW && (
                <button
                  onClick={() => setShowAllConstraints(v => !v)}
                  className="w-full flex items-center justify-center gap-1.5 py-2 text-[13px] text-slate-400 hover:text-slate-200 hover:bg-slate-700/50 rounded-lg transition"
                >
                  {showAllConstraints ? (
                    <>
                      <ChevronUp size={14} />
                      <span>접기</span>
                    </>
                  ) : (
                    <>
                      <ChevronDown size={14} />
                      <span>나머지 {constraints.length - CONSTRAINTS_PREVIEW}개 더 보기</span>
                    </>
                  )}
                </button>
              )}
            </div>
          </div>
        )}

        {/* Recommended Solvers */}
        {recSolvers.length > 0 && (
          <div className="bg-slate-800 rounded-xl p-4 space-y-2">
          <h3 className="text-[15px] font-semibold text-white">📊 추천 솔버</h3>
            <div className="flex flex-wrap gap-2">
              {recSolvers.map((s: string, idx: number) => (
                <span key={idx} className="px-3 py-1 bg-indigo-500/20 text-indigo-400 rounded-full text-xs">{s}</span>
              ))}
            </div>
          </div>
        )}
      </div>

      {/* 하단 옵션 버튼 - 고정 */}
      <div className="p-6 border-t border-slate-800 bg-slate-900 sticky bottom-0 z-10">
        <p className="text-xs text-slate-500 mb-3 font-medium uppercase tracking-wider">
          Suggested Next Steps
        </p>
        <div className="flex gap-3">
          <button
            onClick={() => onAction?.('send', '수학 모델 확정')}
            className="flex-1 bg-gradient-to-r from-emerald-600 to-green-600 hover:from-emerald-500 hover:to-green-500 text-white font-bold py-3.5 rounded-xl transition shadow-lg shadow-emerald-900/20 flex items-center justify-center gap-2"
          >
            <Play size={18} className="fill-current" />
            <span>📐 모델 확정 </span>
          </button>
          <button
            onClick={() => onAction?.('send', '수학 모델 다시 생성해줘')}
            className="px-6 py-3.5 bg-slate-800 hover:bg-slate-700 text-slate-300 font-bold rounded-xl transition border border-slate-700 flex items-center gap-2"
          >
            <span>재생성</span>
          </button>
        </div>
      </div>
    </div>
  );
}

