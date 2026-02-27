// ============================================================
// AnalysisReport.tsx v5.0 - Flow Step Navigation
// ============================================================

import '../markdown.css';
import { useAnalysis } from '../context/AnalysisContext';
import { FlowStepBar } from './analysis/FlowStepBar';
import type {
  AnalysisReportProps,
  ReportData, SolverData, ResultData, FileUploadedData, MathModelData,
} from './analysis/types';

import { MathModelView } from './analysis/MathModelView';
import { FileUploadedView } from './analysis/FileUploadedView';
import { ReportView } from './analysis/ReportView';
import { SolverView } from './analysis/SolverView';
import { OptimizationResultView } from './analysis/OptimizationResultView';

type StepId = 'analysis' | 'math_model' | 'solver' | 'result';

export default function AnalysisReport({
  projectId,
  onAction,
}: Omit<AnalysisReportProps, 'data'>) {
  const { analysisData: data, setAnalysisData, completedSteps, switchToStep } = useAnalysis();
  console.log('🔍 AnalysisReport render: view_mode=' + ((data as any)?.view_mode || 'NONE') + ', keys=' + (data ? Object.keys(data).join(',') : 'null'));

  if (!data) return (
    <div className="flex-1 flex flex-col items-center justify-center text-slate-500 p-8 text-center select-none">
      <div className="w-20 h-20 rounded-full bg-slate-800/50 flex items-center justify-center mb-6 animate-pulse">
        <svg className="text-slate-700" width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="12" cy="12" r="10"/><path d="M12 16v-4M12 8h.01"/></svg>
      </div>
      <h3 className="text-xl font-semibold text-slate-300 mb-2">Analysis Workspace</h3>
      <p className="text-sm opacity-60 max-w-xs leading-relaxed">
        좌측 채팅창에 데이터를 업로드하거나<br/>질문을 입력하면 이곳에 분석 결과가 표시됩니다.
      </p>
    </div>
  );

  const viewMode = (data as any).view_mode as string | undefined;

  const currentStep: StepId =
    viewMode === 'file_uploaded' || viewMode === 'report' ? 'analysis' :
    viewMode === 'math_model' ? 'math_model' :
    viewMode === 'solver' ? 'solver' :
    viewMode === 'result' ? 'result' :
    'analysis';

  const handleStepClick = (step: StepId) => {
    if (completedSteps.has(step)) {
      switchToStep(step);
    }
  };

  const showFlowBar = completedSteps.size > 0;

  const renderContent = () => {
    switch (viewMode) {
      case 'file_uploaded':
        return <FileUploadedView data={data as FileUploadedData} onAction={onAction} />;
      case 'math_model':
        return <MathModelView data={data as MathModelData} onAction={onAction} />;
      case 'solver':
        return <SolverView data={data as SolverData} onAction={onAction} projectId={projectId} onResultReady={setAnalysisData} />;
      case 'result':
        return <OptimizationResultView data={data as ResultData} projectId={projectId} onAction={onAction} />;
      default:
        return <ReportView data={data as ReportData} projectId={projectId} onAction={onAction} />;
    }
  };

  return (
    <div className="h-full flex flex-col">
      {showFlowBar && (
        <FlowStepBar
          currentStep={currentStep}
          completedSteps={completedSteps}
          onStepClick={handleStepClick}
        />
      )}
      <div className="flex-1 overflow-auto">
        {renderContent()}
      </div>
    </div>
  );
}
