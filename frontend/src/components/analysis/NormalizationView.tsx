// src/components/analysis/NormalizationView.tsx
import { Database, Check, RefreshCw, AlertTriangle, ArrowRight, FileText } from 'lucide-react';
import type { NormalizationData, NormalizationMapping } from './types';

export function NormalizationView({
  data, onAction,
}: {
  data: NormalizationData;
  onAction?: (type: string, message: string) => void;
}) {
  const isComplete = data.view_mode === 'normalization_complete';
  const mappings = data.mappings || {};
  const autoConfirmed = mappings.auto_confirmed || [];
  const needsReview = mappings.needs_review || [];
  const allMappings = [...autoConfirmed, ...needsReview];
  const results = data.results || [];
  const errors = data.errors || [];

  const grouped: Record<string, NormalizationMapping[]> = {};
  for (const m of allMappings) {
    const k = m.target_table || 'unknown';
    if (!grouped[k]) grouped[k] = [];
    grouped[k].push(m);
  }

  const avgConfidence = allMappings.length > 0
    ? allMappings.reduce((s, m) => s + (m.confidence || 0), 0) / allMappings.length
    : 0;

  return (
    <div className="h-full flex flex-col bg-slate-900 overflow-hidden animate-fade-in">
      <div className="p-6 border-b border-slate-800 bg-slate-900/95 sticky top-0 z-10">
        <div className="flex items-center gap-3">
          <div className={`p-2 rounded-lg ${isComplete ? 'bg-emerald-500/20 text-emerald-400' : 'bg-blue-500/20 text-blue-400'}`}>
            <Database size={20} />
          </div>
          <div>
            <h2 className="text-xl font-bold text-white">
              {isComplete ? '\uB370\uC774\uD130 \uC815\uADDC\uD654 \uC644\uB8CC' : '\uB370\uC774\uD130 \uC815\uADDC\uD654 \uB9E4\uD551'}
            </h2>
            <p className="text-xs text-slate-400">
              {isComplete
                ? `${results.length}\uAC1C \uD30C\uC77C \uC0DD\uC131 \uC644\uB8CC`
                : `${allMappings.length}\uAC1C \uB9E4\uD551 (${needsReview.length}\uAC1C \uAC80\uD1A0 \uD544\uC694)`}
            </p>
          </div>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto p-6 space-y-6">
        {!isComplete && allMappings.length > 0 && (
          <div className="bg-slate-800/50 rounded-xl p-4 border border-slate-700">
            <div className="flex justify-between text-xs text-slate-400 mb-1">
              <span>{'\uD3C9\uADE0 \uC2E0\uB8B0\uB3C4'}</span>
              <span>{(avgConfidence * 100).toFixed(0)}%</span>
            </div>
            <div className="w-full bg-slate-700 rounded-full h-2">
              <div
                className={`h-2 rounded-full transition-all ${avgConfidence >= 0.8 ? 'bg-emerald-500' : avgConfidence >= 0.5 ? 'bg-amber-500' : 'bg-red-500'}`}
                style={{ width: `${avgConfidence * 100}%` }}
              />
            </div>
          </div>
        )}

        {isComplete && results.length > 0 && (
          <section className="bg-slate-800/50 rounded-xl p-4 border border-slate-700">
            <h3 className="text-sm font-semibold text-emerald-400 mb-2">{'\uC0DD\uC131\uB41C \uD30C\uC77C'}</h3>
            {results.map((r, i) => (
              <div key={i} className="flex items-center gap-2 text-sm py-1">
                <FileText size={14} className="text-emerald-400" />
                <span className="text-slate-200">{r}</span>
              </div>
            ))}
          </section>
        )}

        {!isComplete && Object.entries(grouped).map(([table, items]) => (
          <section key={table} className="bg-slate-800/50 rounded-xl p-4 border border-slate-700">
            <h3 className="text-sm font-semibold text-slate-300 mb-2 flex items-center gap-2">
              <ArrowRight size={14} className="text-cyan-400" />
              <code className="text-cyan-300 font-mono">{table}</code>
              <span className="text-xs text-slate-500">({items.length})</span>
            </h3>
            {items.map((m, i) => {
              const isReview = needsReview.some(
                r => r.source_file === m.source_file && r.target_table === m.target_table
              );
              return (
                <div key={i} className={`flex items-start gap-3 py-2 text-sm border-b border-slate-700/50 last:border-0 ${isReview ? 'bg-amber-500/5 -mx-2 px-2 rounded' : ''}`}>
                  {isReview
                    ? <AlertTriangle size={14} className="text-amber-400 mt-0.5 flex-shrink-0" />
                    : <Check size={14} className="text-emerald-400 mt-0.5 flex-shrink-0" />
                  }
                  <div className="flex-1 min-w-0">
                    <p className="text-slate-200 truncate">
                      {m.source_file}{m.source_sheet ? ` : ${m.source_sheet}` : ''}
                    </p>
                    {m.reason && <p className="text-xs text-slate-500 mt-0.5">{m.reason}</p>}
                  </div>
                  <span className={`text-xs font-mono px-1.5 py-0.5 rounded ${
                    (m.confidence || 0) >= 0.8 ? 'text-emerald-400 bg-emerald-500/10'
                    : (m.confidence || 0) >= 0.5 ? 'text-amber-400 bg-amber-500/10'
                    : 'text-red-400 bg-red-500/10'
                  }`}>
                    {((m.confidence || 0) * 100).toFixed(0)}%
                  </span>
                </div>
              );
            })}
          </section>
        ))}

        {errors.length > 0 && (
          <section className="bg-red-500/10 rounded-xl p-4 border border-red-500/30">
            <h3 className="text-sm font-semibold text-red-400 mb-2">{'\uACBD\uACE0'}</h3>
            {errors.map((e, i) => (
              <p key={i} className="text-xs text-red-300">{e}</p>
            ))}
          </section>
        )}
      </div>

      <div className="p-4 border-t border-slate-800 bg-slate-900 flex gap-3">
        {isComplete ? (
          <button onClick={() => onAction?.('send', '\uC218\uD559 \uBAA8\uB378 \uC0DD\uC131\uD574\uC918')}
            className="flex-1 bg-gradient-to-r from-indigo-600 to-blue-600 hover:from-indigo-500 hover:to-blue-500 text-white font-bold py-3 rounded-xl transition flex items-center justify-center gap-2">
            <ArrowRight size={18} /> {'\uC218\uD559 \uBAA8\uB378 \uC0DD\uC131'}
          </button>
        ) : (
          <>
            <button onClick={() => onAction?.('send', '\uD655\uC778')}
              className="flex-1 bg-emerald-600 hover:bg-emerald-500 text-white font-bold py-3 rounded-xl transition flex items-center justify-center gap-2">
              <Check size={18} /> {'\uD655\uC778'}
            </button>
            <button onClick={() => onAction?.('send', '\uC7AC\uC2DC\uB3C4')}
              className="px-6 bg-slate-800 hover:bg-slate-700 text-slate-300 font-bold py-3 rounded-xl transition border border-slate-700 flex items-center justify-center gap-2">
              <RefreshCw size={18} /> {'\uC7AC\uC2DC\uB3C4'}
            </button>
          </>
        )}
      </div>
    </div>
  );
}
