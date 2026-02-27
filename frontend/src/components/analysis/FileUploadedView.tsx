// src/components/analysis/FileUploadedView.tsx
import { Upload, CheckCircle, FileText, Activity, ArrowRight } from 'lucide-react';
import type { FileUploadedData } from './types';

export function FileUploadedView({
  data,
  onAction,
}: {
  data: FileUploadedData;
  onAction?: (type: string, message: string) => void;
}) {
  const files = data.files || [];
  const count = data.file_count || files.length;

  return (
    <div className="h-full flex flex-col bg-slate-900 animate-fade-in">
        {/* 헤더 */}
      <div className="p-6 border-b border-slate-800">
        <div className="flex items-center gap-3">
          <div className="p-2 bg-emerald-500/20 rounded-lg text-emerald-400">
            <Upload size={20} />
          </div>
          <div>
            <h2 className="text-xl font-bold text-white">파일 업로드 완료</h2>
              <p className="text-sm text-slate-400">{count}개 파일이 업로드되었습니다</p>
          </div>
        </div>
      </div>

      {/* 파일 목록 */}
      <div className="flex-1 p-6 overflow-y-auto">
        <div className="space-y-2">
          {files.map((filename, idx) => (
            <div
              key={idx}
              className="flex items-center gap-3 p-3 bg-slate-800/50 rounded-lg border border-slate-700"
            >
              <FileText size={16} className="text-slate-400 flex-shrink-0" />
              <span className="text-sm text-slate-300 truncate">{filename}</span>
              <CheckCircle size={14} className="text-green-400 ml-auto flex-shrink-0" />
            </div>
          ))}
        </div>
      </div>

        {/* 옵션 */}
      <div className="p-6 border-t border-slate-800">
        <p className="text-[8px] text-slate-500 mb-3 uppercase tracking-wider">Next Step</p>
        <button
          onClick={() => onAction?.('send', '데이터 분석 시작해줘')}
          className="w-full bg-gradient-to-r from-indigo-600 to-blue-600 hover:from-indigo-500 hover:to-blue-500 text-white font-bold py-3.5 rounded-xl transition shadow-lg shadow-indigo-900/20 flex items-center justify-center gap-2"
        >
          <Activity size={18} />
          <span>데이터 분석 시작</span>
          <ArrowRight size={16} className="opacity-70" />
        </button>
      </div>
    </div>
  );
}

