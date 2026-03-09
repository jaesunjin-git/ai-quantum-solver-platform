// ============================================================
// AnalysisReport.tsx v6.0 - 6-Step Flow Navigation
// ============================================================

import '../markdown.css';
import { useAnalysis } from '../context/AnalysisContext';
import type { StepId } from '../context/AnalysisContext';
import { FlowStepBar } from './analysis/FlowStepBar';
import type {
  AnalysisReportProps,
  ReportData, SolverData, ResultData, FileUploadedData, MathModelData,
  ProblemDefinitionData, NormalizationData,
} from './analysis/types';

import { MathModelView } from './analysis/MathModelView';
import { FileUploadedView } from './analysis/FileUploadedView';
import { ReportView } from './analysis/ReportView';
import { SolverView } from './analysis/SolverView';
import { OptimizationResultView } from './analysis/OptimizationResultView';
import { ProblemDefinitionView } from './analysis/ProblemDefinitionView';
import { NormalizationView } from './analysis/NormalizationView';

export default function AnalysisReport({
  projectId,
  onAction,
  onEvent,
}: Omit<AnalysisReportProps, 'data'> & {
  onEvent?: (message: string, eventType: string, eventData: any) => void;
}) {
  const { analysisData: data, setAnalysisData, completedSteps, switchToStep } = useAnalysis();

  if (!data) return (
    <div className="flex-1 flex flex-col items-center justify-center text-slate-500 p-8 text-center select-none">
      <div className="w-20 h-20 rounded-full bg-slate-800/50 flex items-center justify-center mb-6 animate-pulse">
        <svg className="text-slate-700" width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="12" cy="12" r="10"/><path d="M12 16v-4M12 8h.01"/></svg>
      </div>
      <h3 className="text-xl font-semibold text-slate-300 mb-2">Analysis Workspace</h3>
      <p className="text-sm opacity-60 max-w-xs leading-relaxed">
        {'\uC88C\uCE21 \uCC44\uD305\uCC3D\uC5D0 \uB370\uC774\uD130\uB97C \uC5C5\uB85C\uB4DC\uD558\uAC70\uB098'}<br/>{'\uC9C8\uBB38\uC744 \uC785\uB825\uD558\uBA74 \uBD84\uC11D \uACB0\uACFC\uAC00 \uD45C\uC2DC\uB429\uB2C8\uB2E4'}
      </p>
    </div>
  );

  const viewMode = (data as any).view_mode as string | undefined;

  const currentStep: StepId =
    viewMode === 'file_uploaded' || viewMode === 'report'
      ? 'analysis'
    : viewMode === 'problem_definition' || viewMode === 'problem_defined'
      ? 'problem_def'
    : viewMode === 'normalization' || viewMode === 'normalization_mapping'
      ? 'normalization'
    : viewMode === 'math_model' || viewMode === 'param_input'
      ? 'math_model'
    : viewMode === 'solver'
      ? 'solver'
    : viewMode === 'result'
      ? 'result'
    : 'analysis';

  const handleStepClick = (step: StepId) => {
    if (completedSteps.has(step)) switchToStep(step);
  };

  const showFlowBar = completedSteps.size > 0;

  const renderContent = () => {
    switch (viewMode) {
      case 'file_uploaded':
        return <FileUploadedView data={data as FileUploadedData} onAction={onAction} />;
      case 'problem_definition':
      case 'problem_defined':
        return <ProblemDefinitionView data={data as ProblemDefinitionData} onAction={onAction} onEvent={onEvent} />;
      case 'normalization':
      case 'normalization_mapping':
      // case 'normalization_complete':  // skip: auto-next handles this
        return <NormalizationView data={data as NormalizationData} onAction={onAction} />;
      case 'param_input':
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
