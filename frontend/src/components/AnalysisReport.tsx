// ============================================================
// AnalysisReport.tsx v7.0 — 3-Layer Panel Architecture
//
// Layer 1: VersionBar (top, conditional)    — version timeline + compare mode
// Layer 2: FlowStepBar + StageContent (mid) — existing stage navigation
// Layer 3: ValidationDrawer (bottom)        — validation findings + user fixes
// ============================================================

import { useState, useCallback, useEffect } from 'react';
import '../markdown.css';
import { API_BASE_URL } from '../config';
import { useAuth } from '../context/AuthContext';
import { useAnalysis } from '../context/AnalysisContext';
import type { StepId } from '../context/AnalysisContext';
import { FlowStepBar } from './analysis/FlowStepBar';
import type {
  AnalysisReportProps,
  ReportData, SolverData, ResultData, FileUploadedData, MathModelData,
  ProblemDefinitionData, NormalizationData,
  VersionTimelineEntry, VersionCompare,
} from './analysis/types';

import { MathModelView } from './analysis/MathModelView';
import { FileUploadedView } from './analysis/FileUploadedView';
import { ReportView } from './analysis/ReportView';
import { SolverView } from './analysis/SolverView';
import { OptimizationResultView } from './analysis/OptimizationResultView';
import { ProblemDefinitionView } from './analysis/ProblemDefinitionView';
import { NormalizationView } from './analysis/NormalizationView';
import VersionBar from './analysis/VersionBar';
import VersionCompareView from './analysis/VersionCompareView';
import ValidationDrawer from './analysis/ValidationDrawer';

export default function AnalysisReport({
  projectId,
  onAction,
  onEvent,
}: Omit<AnalysisReportProps, 'data'> & {
  onEvent?: (message: string, eventType: string, eventData: any) => void;
}) {
  const { authFetch } = useAuth();
  const {
    analysisData: data, setAnalysisData, stepCache, completedSteps, switchToStep,
    stageValidation, setStageValidation,
  } = useAnalysis();

  // ── Layer 1: Version timeline state ──
  const [versionTimeline, setVersionTimeline] = useState<VersionTimelineEntry[]>([]);
  const [currentVersionIndex, setCurrentVersionIndex] = useState(0);
  const [compareData, setCompareData] = useState<VersionCompare | null>(null);

  const currentViewMode = (data as any)?.view_mode as string | undefined;

  // ── Fetch version timeline when result view is shown ──
  useEffect(() => {
    if (!projectId || currentViewMode !== 'result') return;

    let cancelled = false;
    (async () => {
      try {
        const res = await authFetch(`${API_BASE_URL}/api/projects/${projectId}/versions/timeline`);
        if (res.ok && !cancelled) {
          const body = await res.json();
          const raw: VersionTimelineEntry[] = body.timeline || [];
          // Filter out empty versions (no run_id and no status = never executed)
          const entries = raw.filter(e => e.run_id != null || e.status != null);
          setVersionTimeline(entries);
          // Point to latest version
          if (entries.length > 0) {
            setCurrentVersionIndex(entries.length - 1);
          }
        }
      } catch (err) {
        console.error('Failed to fetch version timeline:', err);
      }
    })();
    return () => { cancelled = true; };
  }, [projectId, currentViewMode]);

  // ── Version handlers ──
  const handleVersionSelect = useCallback(async (entry: VersionTimelineEntry) => {
    // Find array index for VersionBar highlighting (0-based)
    const idx = versionTimeline.findIndex(e => e.version_label === entry.version_label);
    setCurrentVersionIndex(idx >= 0 ? idx : 0);
    setCompareData(null);
    // Load this version's result data if it has a run_id
    if (projectId && entry.run_id) {
      try {
        const res = await authFetch(`${API_BASE_URL}/api/projects/${projectId}/versions/runs/${entry.run_id}`);
        if (res.ok) {
          const runData = await res.json();
          // Reconstruct ResultData shape: result_json contains the solve summary
          const resultJson = runData.result_json || {};
          setAnalysisData({
            view_mode: 'result',
            solver_id: runData.solver_id,
            solver_name: runData.solver_name,
            status: runData.status,
            objective_value: runData.objective_value,
            ...resultJson,
          });
        }
      } catch (err) {
        console.error('Failed to load version data:', err);
      }
    }
  }, [projectId, versionTimeline, setAnalysisData]);

  const handleCompareRequest = useCallback(
    async (entryA: VersionTimelineEntry, entryB: VersionTimelineEntry) => {
      if (!projectId) return;
      if (!entryA.run_id || !entryB.run_id) {
        console.warn('Cannot compare: one or both versions have no run_id', { a: entryA, b: entryB });
        return;
      }
      try {
        const url = `${API_BASE_URL}/api/projects/${projectId}/versions/compare?run_id_a=${entryA.run_id}&run_id_b=${entryB.run_id}`;
        const res = await authFetch(url);
        if (res.ok) {
          setCompareData(await res.json());
        } else {
          console.error('Version compare API error:', res.status, await res.text());
        }
      } catch (err) {
        console.error('Version compare failed:', err);
      }
    },
    [projectId],
  );

  // ── Validation handlers ──
  const callApplyFix = useCallback(async (
    stage: number,
    fixes: { code: string; action: string; value?: any }[],
  ) => {
    if (!projectId) return;
    try {
      const res = await authFetch(`${API_BASE_URL}/api/validation/apply-fix`, {
        method: 'POST',
        body: JSON.stringify({
          project_id: Number(projectId),
          stage,
          fixes,
        }),
      });
      if (res.ok) {
        const result = await res.json();
        setStageValidation(result.validation);
      }
    } catch (err) {
      console.error('Apply fix failed:', err);
    }
  }, [projectId]);

  const handleApplyFix = useCallback((code: string) => {
    if (!stageValidation) return;
    // auto_fix 정보를 찾아서 value로 전달
    const item = stageValidation.items.find(i => i.code === code);
    const fixValue = item?.auto_fix || {};
    callApplyFix(stageValidation.stage, [{ code, action: 'auto_fix', value: fixValue }]);
  }, [stageValidation, callApplyFix]);

  const handleDismiss = useCallback((code: string) => {
    if (!stageValidation) return;
    callApplyFix(stageValidation.stage, [{ code, action: 'dismiss' }]);
  }, [stageValidation, callApplyFix]);

  const handleUserInput = useCallback((code: string, value: any) => {
    if (!stageValidation) return;
    callApplyFix(stageValidation.stage, [{ code, action: 'user_input', value }]);
  }, [stageValidation, callApplyFix]);

  const handleValidationProceed = useCallback(() => {
    setStageValidation(null);
  }, []);

  // ── Empty state ──
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

  // ── Compare overlay: replaces stage content when active ──
  if (compareData) {
    return (
      <div className="h-full flex flex-col">
        {versionTimeline.length > 0 && (
          <VersionBar
            timeline={versionTimeline}
            currentVersionIndex={currentVersionIndex}
            onVersionSelect={handleVersionSelect}
            onCompareRequest={handleCompareRequest}
          />
        )}
        <div className="flex-1 overflow-auto">
          <VersionCompareView data={compareData} onClose={() => setCompareData(null)} />
        </div>
      </div>
    );
  }

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

  // Stateless views: conditionally rendered (unmount is fine)
  const renderLightContent = () => {
    switch (viewMode) {
      case 'file_uploaded':
        return <FileUploadedView data={data as FileUploadedData} onAction={onAction} />;
      case 'problem_definition':
      case 'problem_defined':
        return <ProblemDefinitionView data={data as ProblemDefinitionData} onAction={onAction} onEvent={onEvent} />;
      case 'normalization':
      case 'normalization_mapping':
        return <NormalizationView data={data as NormalizationData} onAction={onAction} />;
      case 'param_input':
      case 'math_model':
        return <MathModelView data={data as MathModelData} onAction={onAction} onEvent={onEvent} projectId={projectId} />;
      case 'solver':
      case 'result':
        return null; // rendered persistently below
      default:
        return <ReportView data={data as ReportData} projectId={projectId} onAction={onAction} />;
    }
  };

  // Stateful views: keep mounted (hidden via CSS) to preserve local state on tab switch
  const isSolverView = viewMode === 'solver';
  const isResultView = viewMode === 'result';
  const hasSolverData = !!stepCache.solver;
  const hasResultData = !!stepCache.result;

  return (
    <div className="h-full flex flex-col">
      {/* Layer 1: Version Timeline (result 뷰에서만 표시) */}
      {versionTimeline.length > 0 && currentViewMode === 'result' && (
        <VersionBar
          timeline={versionTimeline}
          currentVersionIndex={currentVersionIndex}
          onVersionSelect={handleVersionSelect}
          onCompareRequest={handleCompareRequest}
        />
      )}

      {/* Layer 2: Flow Navigation + Stage Content */}
      {showFlowBar && (
        <FlowStepBar
          currentStep={currentStep}
          completedSteps={completedSteps}
          onStepClick={handleStepClick}
        />
      )}
      <div className="flex-1 overflow-auto">
        {renderLightContent()}
        {/* Stateful views: kept mounted but hidden to preserve local state */}
        {hasSolverData && (
          <div className="h-full" style={{ display: isSolverView ? 'block' : 'none' }}>
            <SolverView data={stepCache.solver as SolverData} onAction={onAction} projectId={projectId} onResultReady={setAnalysisData} />
          </div>
        )}
        {hasResultData && (
          <div className="h-full" style={{ display: isResultView ? 'block' : 'none' }}>
            <OptimizationResultView data={(isResultView ? data : stepCache.result) as ResultData} projectId={projectId} onAction={(action, msg) => {
              if (action === 'switch_step' && msg) {
                switchToStep(msg as any);
              } else {
                onAction?.(action, msg);
              }
            }} />
          </div>
        )}
      </div>

      {/* Layer 3: Validation Drawer — 결과 뷰에서는 숨김 (헤더 배지에서 표시) */}
      {!isResultView && <ValidationDrawer
        validation={stageValidation}
        onApplyFix={handleApplyFix}
        onDismiss={handleDismiss}
        onUserInput={handleUserInput}
        onProceed={handleValidationProceed}
      />}
    </div>
  );
}
