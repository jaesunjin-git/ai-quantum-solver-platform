// src/components/analysis/ReportView.tsx
import { Activity, ArrowRight, Download, FileText, Play, Settings } from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import '../../markdown.css';
import type { ReportData } from './types';
import { downloadReport } from './downloadHelper';

export function ReportView({
  data,
  projectId,
  onAction,
}: {
  data: ReportData;
  projectId?: string;
  onAction?: (type: string, message: string) => void;
}) {
  const report = data.report || '';
  const status = data.agent_status || 'Analysis Completed';
  const actions = data.actions;

  return (
    <div className="h-full flex flex-col bg-slate-900 overflow-hidden animate-fade-in">
      {/* 헤더 */}
      <div className="p-6 border-b border-slate-800 bg-slate-900/95 sticky top-0 z-10">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="p-2 bg-indigo-500/20 rounded-lg text-indigo-400">
              <FileText size={20} />
            </div>
            <div>
              <h2 className="text-xl font-bold text-white">Data Analysis Insight</h2>
              <div className="flex items-center gap-2 text-[8px] text-slate-400">
                <Activity size={14} className="text-green-400" />
                <span>STATUS:</span>
                <span className="text-slate-200 font-medium">{status}</span>
              </div>
            </div>
          </div>

          {/* 다운로드 버튼 */}
          {report && projectId && (
            <div className="flex gap-2">
              <button
                onClick={() => downloadReport(projectId, 'md')}
                className="px-3 py-1.5 text-[8px] bg-slate-800 hover:bg-slate-700 text-slate-300 rounded-lg border border-slate-700 transition flex items-center gap-1"
              title="마크다운으로 다운로드"
              >
                <Download size={14} />
                <span>.md</span>
              </button>
              <button
                onClick={() => downloadReport(projectId, 'docx')}
                className="px-3 py-1.5 text-[8px] bg-slate-800 hover:bg-slate-700 text-slate-300 rounded-lg border border-slate-700 transition flex items-center gap-1"
              title="Word 파일로 다운로드"
              >
                <Download size={14} />
                <span>.docx</span>
              </button>
            </div>
          )}
        </div>
      </div>

      {/* 본문 */}
      <div className="flex-1 overflow-y-auto p-6 custom-scrollbar">
        {report ? (
          <article className="report-content">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{report}</ReactMarkdown>
          </article>
        ) : (
          <div className="flex items-center justify-center h-full text-slate-500">
          <p>리포트 데이터가 없습니다.</p>
          </div>
        )}
      </div>

      {/* 하단 옵션 버튼 */}
      {actions && (
        <div className="p-6 border-t border-slate-800 bg-slate-900 sticky bottom-0 z-10">
          <p className="text-[8px] text-slate-500 mb-3 font-medium uppercase tracking-wider">
            Suggested Next Steps
          </p>
          <div className="flex gap-3">
            {actions.primary && (
              <button
                onClick={() => onAction?.('send', actions.primary!.message)}
                className="flex-1 bg-gradient-to-r from-indigo-600 to-blue-600 hover:from-indigo-500 hover:to-blue-500 text-white font-bold py-3.5 rounded-xl transition shadow-lg shadow-indigo-900/20 flex items-center justify-center gap-2 group"
              >
                <Play size={18} className="fill-current" />
                <span>{actions.primary.label}</span>
                <ArrowRight
                  size={16}
                  className="opacity-70 group-hover:translate-x-1 transition-transform"
                />
              </button>
            )}
            {actions.secondary && (
              <button
                onClick={() => onAction?.('send', actions.secondary!.message)}
                className="px-6 bg-slate-800 hover:bg-slate-700 text-slate-300 font-bold py-3.5 rounded-xl transition border border-slate-700 flex items-center justify-center gap-2"
              >
                <Settings size={18} />
                <span>{actions.secondary.label}</span>
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

// ============================================================
// ⚡ [View 2] 솔버 추천 화면
// ============================================================
