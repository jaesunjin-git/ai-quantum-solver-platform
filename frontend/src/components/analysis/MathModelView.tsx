// src/components/analysis/MathModelView.tsx
import { Activity, ChevronDown, ChevronUp, Download, FileText, Play } from 'lucide-react';
import { useState } from 'react';
import type { MathModelData } from './types';

export function MathModelView({
  data,
  onAction,
  projectId,
}: {
  data: MathModelData;
  onAction?: (type: string, message: string) => void;
  projectId?: string;
}) {
  const model = data.math_model || {};
  const meta = model.metadata || {};
  const variables = model.variables || [];
  const constraints = model.constraints || [];
  const objective = model.objective || {};
  const sets = model.sets || {};

  const varCount = meta.estimated_variable_count || variables.length || 0;
  const conCount = meta.estimated_constraint_count || constraints.length || 0;
  const varTypes = meta.variable_types || meta.variable_types || [];
  const recSolvers = meta.recommended_solvers || [];

  const [showAllConstraints, setShowAllConstraints] = useState(false);
  const CONSTRAINTS_PREVIEW = 8;

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
              <div className="flex items-center gap-2 text-[11px] text-slate-400">
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
                onClick={async () => {
                  try {
                    const token = localStorage.getItem('token') || '';
                    const res = await fetch(
                      `/api/projects/${projectId}/report/download?format=json&type=math_model`,
                      { headers: token ? { Authorization: `Bearer ${token}` } : {} }
                    );
                    if (!res.ok) { alert('다운로드 실패'); return; }
                    const blob = await res.blob();
                    const url = URL.createObjectURL(blob);
                    const a = document.createElement('a');
                    a.href = url; a.download = 'math_model.json';
                    document.body.appendChild(a); a.click();
                    document.body.removeChild(a); URL.revokeObjectURL(url);
                  } catch (e) { console.error(e); alert('다운로드 오류'); }
                }}
                className="px-3 py-1.5 text-[11px] bg-slate-800 hover:bg-slate-700 text-slate-300 rounded-lg border border-slate-700 transition flex items-center gap-1"
              title="JSON으로 다운로드"
              >
                <Download size={14} />
                <span>.json</span>
              </button>
              <button
                onClick={async () => {
                  try {
                    const token = localStorage.getItem('token') || '';
                    const res = await fetch(
                      `/api/projects/${projectId}/report/download?format=md&type=math_model`,
                      { headers: token ? { Authorization: `Bearer ${token}` } : {} }
                    );
                    if (!res.ok) { alert('다운로드 실패'); return; }
                    const blob = await res.blob();
                    const url = URL.createObjectURL(blob);
                    const a = document.createElement('a');
                    a.href = url; a.download = 'math_model.md';
                    document.body.appendChild(a); a.click();
                    document.body.removeChild(a); URL.revokeObjectURL(url);
                  } catch (e) { console.error(e); alert('다운로드 오류'); }
                }}
                className="px-3 py-1.5 text-[11px] bg-slate-800 hover:bg-slate-700 text-slate-300 rounded-lg border border-slate-700 transition flex items-center gap-1"
              title="Markdown으로 다운로드"
              >
                <Download size={14} />
                <span>.md</span>
              </button>
              <button
                onClick={async () => {
                  try {
                    const token = localStorage.getItem('token') || '';
                    const res = await fetch(
                      `/api/projects/${projectId}/report/download?format=docx&type=math_model`,
                      { headers: token ? { Authorization: `Bearer ${token}` } : {} }
                    );
                    if (!res.ok) { alert('다운로드 실패'); return; }
                    const blob = await res.blob();
                    const url = URL.createObjectURL(blob);
                    const a = document.createElement('a');
                    a.href = url; a.download = 'math_model.docx';
                    document.body.appendChild(a); a.click();
                    document.body.removeChild(a); URL.revokeObjectURL(url);
                  } catch (e) { console.error(e); alert('다운로드 오류'); }
                }}
                className="px-3 py-1.5 text-[11px] bg-slate-800 hover:bg-slate-700 text-slate-300 rounded-lg border border-slate-700 transition flex items-center gap-1"
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
                <div className="text-[11px] text-slate-400 mt-1">변수 수</div>
          </div>
          <div className="bg-slate-800 rounded-xl p-4 text-center">
            <div className="text-2xl font-bold text-amber-400">{conCount}</div>
            <div className="text-[11px] text-slate-400 mt-1">제약 조건</div>
          </div>
          <div className="bg-slate-800 rounded-xl p-4 text-center">
            <div className="text-2xl font-bold text-emerald-400">{varTypes.length > 0 ? varTypes.join(', ') : 'binary'}</div>
            <div className="text-[11px] text-slate-400 mt-1">변수 유형</div>
          </div>
        </div>

        {/* Objective Function */}
        {objective && (objective.description || objective.type) && (
          <div className="bg-slate-800 rounded-xl p-4 space-y-2">
          <h3 className="text-sm font-semibold text-white flex items-center gap-2">📊 목적함수</h3>
            <div className="text-sm text-slate-300">
              <span className="text-indigo-400 font-mono">{objective.type || 'minimize'}</span>
              {objective.description && <p className="mt-1">{objective.description}</p>}
              {objective.expression && (
                <code className="block mt-2 bg-slate-900 p-2 rounded text-[11px] text-green-400 overflow-x-auto">
                  {objective.expression}
                </code>
              )}
            </div>
            {objective.alternatives && objective.alternatives.length > 0 && (
              <div className="mt-2">
                <p className="text-[11px] text-slate-500">대안 목적함수:</p>
                {objective.alternatives.map((alt: any, idx: number) => (
                  <div key={idx} className="text-[11px] text-slate-400 mt-1">
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
          <h3 className="text-sm font-semibold text-white">📊 집합 (Sets)</h3>
            <div className="space-y-1">
              {(Array.isArray(sets) ? sets : Object.entries(sets).map(([k, v]) => ({ id: k, ...(typeof v === 'object' ? v : { description: v }) }))).map((s: any, idx: number) => (
                <div key={idx} className="flex justify-between text-[11px]">
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
          <h3 className="text-sm font-semibold text-white">📊 변수 정의</h3>
            <div className="space-y-1">
              {variables.slice(0, 10).map((v: any, idx: number) => (
                <div key={idx} className="flex justify-between text-[11px]">
                  <span className="text-green-400 font-mono">{v.name || v.id || `x_${idx}`}</span>
                  <span className="text-slate-400">{v.type || 'binary'} · {v.description || ''}</span>
                </div>
              ))}
              {variables.length > 10 && (
                 <p className="text-[12px] text-slate-500">... 외 {variables.length - 10}개</p>
              )}
            </div>
          </div>
        )}

        {/* Constraints */}
        {constraints.length > 0 && (
          <div className="bg-slate-800 rounded-xl p-4 space-y-2">
            <div className="flex items-center justify-between">
              <h3 className="text-sm font-semibold text-white">🗣️ 제약 조건</h3>
              <span className="text-[11px] text-slate-500">{constraints.length}개</span>
            </div>
            <div className="space-y-2">
              {(showAllConstraints ? constraints : constraints.slice(0, CONSTRAINTS_PREVIEW)).map((c: any, idx: number) => (
                <div key={idx} className="bg-slate-900 rounded-lg p-2">
                  <div className="flex justify-between items-center">
                    <span className="text-[11px] font-mono text-amber-400">{c.name || c.id || `c${idx + 1}`}</span>
                    <span className={`text-[11px] px-2 py-0.5 rounded ${c.category === 'hard' || c.type === 'hard' ? 'bg-red-500/20 text-red-400' : 'bg-yellow-500/20 text-yellow-400'}`}>
                      {c.category || c.type || 'hard'}
                    </span>
                  </div>
                  <p className="text-[11px] text-slate-400 mt-1">{c.description || ''}</p>
                  {c.expression && (
                    <code className="text-[11px] text-green-400 mt-1 block">{c.expression}</code>
                  )}
                </div>
              ))}
              {constraints.length > CONSTRAINTS_PREVIEW && (
                <button
                  onClick={() => setShowAllConstraints(v => !v)}
                  className="w-full flex items-center justify-center gap-1.5 py-2 text-[12px] text-slate-400 hover:text-slate-200 hover:bg-slate-700/50 rounded-lg transition"
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
          <h3 className="text-sm font-semibold text-white">📊 추천 솔버</h3>
            <div className="flex flex-wrap gap-2">
              {recSolvers.map((s: string, idx: number) => (
                <span key={idx} className="px-3 py-1 bg-indigo-500/20 text-indigo-400 rounded-full text-[11px]">{s}</span>
              ))}
            </div>
          </div>
        )}
      </div>

      {/* 하단 옵션 버튼 - 고정 */}
      <div className="p-6 border-t border-slate-800 bg-slate-900 sticky bottom-0 z-10">
        <p className="text-[11px] text-slate-500 mb-3 font-medium uppercase tracking-wider">
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

