// src/components/analysis/ProblemDefinitionView.tsx
import { ClipboardList, Check, Edit3, AlertTriangle, ChevronDown, ChevronUp } from 'lucide-react';
import { useState } from 'react';
import type { ProblemDefinitionData } from './types';

export function ProblemDefinitionView({
  data, onAction,
}: {
  data: ProblemDefinitionData;
  onAction?: (type: string, message: string) => void;
}) {
  const [showParams, setShowParams] = useState(true);
  const proposal = data.proposal || data.confirmed_problem;
  const isConfirmed = data.view_mode === 'problem_defined';

  if (!proposal) {
    return (
      <div className="flex-1 flex items-center justify-center text-slate-500 p-8">
        <p>{'\uBB38\uC81C \uC815\uC758 \uB370\uC774\uD130\uB97C \uBD88\uB7EC\uC624\uB294 \uC911...'}</p>
      </div>
    );
  }

  const objective = proposal.objective || {};
  const hardConstraints = proposal.hard_constraints || {};
  const softConstraints = proposal.soft_constraints || {};
  const parameters = proposal.parameters || {};

  const dataParams = Object.entries(parameters).filter(([, v]: [string, any]) => v.source === 'data');
  const defaultParams = Object.entries(parameters).filter(([, v]: [string, any]) => v.source === 'default');
  const missingParams = Object.entries(parameters).filter(([, v]: [string, any]) => v.source === 'user_input_required');

  return (
    <div className="h-full flex flex-col bg-slate-900 overflow-hidden animate-fade-in">
      <div className="p-6 border-b border-slate-800 bg-slate-900/95 sticky top-0 z-10">
        <div className="flex items-center gap-3">
          <div className={`p-2 rounded-lg ${isConfirmed ? 'bg-emerald-500/20 text-emerald-400' : 'bg-amber-500/20 text-amber-400'}`}>
            <ClipboardList size={20} />
          </div>
          <div>
            <h2 className="text-xl font-bold text-white">
              {isConfirmed ? '\uBB38\uC81C \uC815\uC758 \uD655\uC815' : '\uBB38\uC81C \uC815\uC758 \uC81C\uC548'}
            </h2>
            <p className="text-xs text-slate-400">
              {isConfirmed ? '\uD655\uC815\uB428 - \uB2E4\uC74C \uB2E8\uACC4\uB85C \uC9C4\uD589 \uAC00\uB2A5' : '\uAC80\uD1A0 \uD6C4 \uD655\uC778 \uB610\uB294 \uC218\uC815\uD574 \uC8FC\uC138\uC694'}
            </p>
          </div>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto p-6 space-y-6">
        {(proposal.stage || proposal.variant) && (
          <section className="bg-slate-800/50 rounded-xl p-4 border border-slate-700">
            <h3 className="text-sm font-semibold text-slate-300 mb-2">{'\uBB38\uC81C \uC720\uD615'}</h3>
            <div className="flex gap-4 text-sm">
              {proposal.stage && <span className="text-cyan-400">{'\uB2E8\uACC4'}: {proposal.stage}</span>}
              {proposal.variant && <span className="text-slate-300">{'\uC138\uBD80'}: {proposal.variant}</span>}
            </div>
            {proposal.detected_data_types && proposal.detected_data_types.length > 0 && (
              <div className="flex gap-2 mt-2 flex-wrap">
                {proposal.detected_data_types.map((dt: string) => (
                  <span key={dt} className="px-2 py-0.5 text-xs bg-slate-700 text-slate-300 rounded-full">{dt}</span>
                ))}
              </div>
            )}
          </section>
        )}

        <section className="bg-slate-800/50 rounded-xl p-4 border border-slate-700">
          <h3 className="text-sm font-semibold text-slate-300 mb-2">{'\uBAA9\uC801\uD568\uC218'}</h3>
          <div className="flex items-center gap-2">
            <span className="px-2 py-0.5 text-xs bg-indigo-500/20 text-indigo-400 rounded">{objective.type || 'minimize'}</span>
            <p className="text-sm text-slate-200">{objective.description || objective.target || '-'}</p>
          </div>
          {objective.alternatives && objective.alternatives.length > 0 && (
            <div className="mt-2">
              <p className="text-xs text-slate-500 mb-1">{'\uB300\uC548'}:</p>
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

        <section className="bg-slate-800/50 rounded-xl p-4 border border-slate-700">
          <h3 className="text-sm font-semibold text-slate-300 mb-3">{'\uC81C\uC57D\uC870\uAC74'}</h3>
          {Object.keys(hardConstraints).length > 0 && (
            <div className="mb-3">
              <p className="text-xs text-red-400 font-semibold mb-1">{'\uD544\uC218 (Hard)'}</p>
              {Object.entries(hardConstraints).map(([k, v]: [string, any]) => (
                <div key={k} className="flex items-start gap-2 text-sm py-1 border-b border-slate-700/50 last:border-0">
                  <AlertTriangle size={14} className="text-red-400 mt-0.5 flex-shrink-0" />
                  <div>
                    <span className="text-slate-200 font-medium">{v.name_ko || k}</span>
                    {v.description && <p className="text-xs text-slate-500">{v.description}</p>}
                  </div>
                </div>
              ))}
            </div>
          )}
          {Object.keys(softConstraints).length > 0 && (
            <div>
              <p className="text-xs text-amber-400 font-semibold mb-1">{'\uC120\uD638 (Soft)'}</p>
              {Object.entries(softConstraints).map(([k, v]: [string, any]) => (
                <div key={k} className="flex items-start gap-2 text-sm py-1 border-b border-slate-700/50 last:border-0">
                  <Edit3 size={14} className="text-amber-400 mt-0.5 flex-shrink-0" />
                  <div className="flex-1">
                    <span className="text-slate-200 font-medium">{v.name_ko || k}</span>
                    {v.description && <p className="text-xs text-slate-500">{v.description}</p>}
                  </div>
                  {v.weight != null && (
                    <span className="text-xs text-slate-500">{'\uAC00\uC911\uCE58'}: {v.weight}</span>
                  )}
                </div>
              ))}
            </div>
          )}
        </section>

        <section className="bg-slate-800/50 rounded-xl p-4 border border-slate-700">
          <button onClick={() => setShowParams(!showParams)}
            className="flex items-center justify-between w-full text-sm font-semibold text-slate-300">
            <span>{'\uD30C\uB77C\uBBF8\uD130'} ({Object.keys(parameters).length})</span>
            {showParams ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
          </button>
          {showParams && (
            <div className="mt-3 space-y-3">
              {dataParams.length > 0 && (
                <div>
                  <p className="text-xs text-emerald-400 mb-1">{'\uB370\uC774\uD130 \uCD94\uCD9C'}</p>
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
                  <p className="text-xs text-amber-400 mb-1">{'\uAE30\uBCF8\uAC12 (\uC218\uC815 \uAC00\uB2A5)'}</p>
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
                  <p className="text-xs text-red-400 mb-1">{'\uC785\uB825 \uD544\uC694'}</p>
                  {missingParams.map(([k]: [string, any]) => (
                    <div key={k} className="flex justify-between text-sm py-0.5">
                      <span className="text-slate-400">{k}</span>
                      <span className="text-red-400 font-mono">???</span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </section>
      </div>

      {!isConfirmed && (
        <div className="p-4 border-t border-slate-800 bg-slate-900 flex gap-3">
          <button onClick={() => onAction?.('send', '\uD655\uC778')}
            className="flex-1 bg-emerald-600 hover:bg-emerald-500 text-white font-bold py-3 rounded-xl transition flex items-center justify-center gap-2">
            <Check size={18} /> {'\uD655\uC778'}
          </button>
          <button onClick={() => onAction?.('send', '\uC218\uC815')}
            className="px-6 bg-slate-800 hover:bg-slate-700 text-slate-300 font-bold py-3 rounded-xl transition border border-slate-700 flex items-center justify-center gap-2">
            <Edit3 size={18} /> {'\uC218\uC815'}
          </button>
        </div>
      )}
    </div>
  );
}
