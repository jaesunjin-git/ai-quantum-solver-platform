// src/context/AnalysisContext.tsx
import { createContext, useContext, useState, useCallback, type ReactNode } from 'react';
import { useProjectContext } from './ProjectContext';
import { useEffect } from 'react';
import type { StageValidation } from '../components/analysis/types';

export type StepId = 'analysis' | 'problem_def' | 'normalization' | 'math_model' | 'solver' | 'result';

interface StepCache {
  analysis?: any;
  problem_def?: any;
  normalization?: any;
  math_model?: any;
  solver?: any;
  result?: any;
}

interface AnalysisContextType {
  analysisData: any;
  setAnalysisData: (data: any) => void;
  restoreFromHistory: (cardDataList: any[]) => void;
  stepCache: StepCache;
  cacheCurrentStep: () => void;
  switchToStep: (step: StepId) => void;
  completedSteps: Set<StepId>;
  stageValidation: StageValidation | null;
  setStageValidation: (v: StageValidation | null) => void;
}

const AnalysisContext = createContext<AnalysisContextType>({
  analysisData: null,
  setAnalysisData: () => {},
  restoreFromHistory: () => {},
  stepCache: {},
  cacheCurrentStep: () => {},
  switchToStep: () => {},
  completedSteps: new Set(),
  stageValidation: null,
  setStageValidation: () => {},
});

function viewModeToStepId(viewMode: string): { stepId: StepId; isFileUpload: boolean } | null {
  switch (viewMode) {
    case 'file_uploaded':          return { stepId: 'analysis',      isFileUpload: true };
    case 'report':                 return { stepId: 'analysis',      isFileUpload: false };
    case 'problem_definition':     return { stepId: 'problem_def',   isFileUpload: false };
    case 'problem_defined':        return { stepId: 'problem_def',   isFileUpload: false };
    case 'normalization':          return { stepId: 'normalization',  isFileUpload: false };
    case 'normalization_mapping':  return { stepId: 'normalization',  isFileUpload: false };
    case 'normalization_complete': return null;  // auto-next: skip tab switch
    case 'param_input':             return { stepId: 'math_model',    isFileUpload: false };
    case 'math_model':             return { stepId: 'math_model',    isFileUpload: false };
    case 'solver':                 return { stepId: 'solver',        isFileUpload: false };
    case 'result':                 return { stepId: 'result',        isFileUpload: false };
    default: return null;
  }
}

export function AnalysisProvider({ children }: { children: ReactNode }) {
  const { currentProject } = useProjectContext();
  const [analysisData, setAnalysisDataRaw] = useState<any>(null);
  const [stepCache, setStepCache] = useState<StepCache>({});
  const [completedSteps, setCompletedSteps] = useState<Set<StepId>>(new Set());
  const [stageValidation, setStageValidation] = useState<StageValidation | null>(null);

  useEffect(() => {
    setAnalysisDataRaw(null);
    setStepCache({});
    setCompletedSteps(new Set());
    setStageValidation(null);
  }, [currentProject?.id]);

  const setAnalysisData = useCallback((data: any) => {
    // ★ 항상 새 객체 참조 생성 — React useEffect dependency 갱신 보장
    // 백엔드가 같은 state 객체를 반환해도 프론트엔드에서 변경 감지 가능
    const freshData = data ? structuredClone(data) : data;

    // Auto-extract validation from incoming data (any stage can include it)
    if (freshData?.validation) {
      setStageValidation(freshData.validation);
    }

    // target_tab만 있고 view_mode 없는 경우: 캐시된 데이터로 탭 전환만 수행
    if (freshData && !freshData.view_mode && freshData.target_tab) {
      const tabToStep: Record<string, StepId> = {
        analysis: 'analysis', problem_def: 'problem_def', normalization: 'normalization',
        math_model: 'math_model', solver: 'solver', result: 'result',
      };
      const targetStep = tabToStep[freshData.target_tab];
      if (targetStep && stepCache[targetStep]) {
        // 캐시된 데이터가 있으면 해당 탭으로 전환
        setAnalysisDataRaw(stepCache[targetStep]);
        return;
      }
      // 캐시 없으면 현재 뷰 유지 (무의미한 전환 방지)
      return;
    }

    setAnalysisDataRaw(freshData);
    if (freshData && freshData.view_mode) {
      const mapped = viewModeToStepId(freshData.view_mode);
      if (mapped) {
        const { stepId, isFileUpload } = mapped;
        if (isFileUpload) {
          setStepCache(prev => {
            if (!prev.analysis) return { ...prev, analysis: freshData };
            return prev;
          });
        } else {
          setStepCache(prev => ({ ...prev, [stepId]: freshData }));
          setCompletedSteps(prev => {
            const next = new Set(prev);
            next.add(stepId);
            return next;
          });
        }
      }
    }
  }, [stepCache]);

  // Bulk restore: history의 모든 card_data를 처리하여 completedSteps + stepCache 복원
  const restoreFromHistory = useCallback((cardDataList: any[]) => {
    const restoredSteps = new Set<StepId>();
    const restoredCache: StepCache = {};
    let lastData: any = null;

    for (const data of cardDataList) {
      if (!data || !data.view_mode) continue;
      const mapped = viewModeToStepId(data.view_mode);
      if (!mapped) continue;
      const { stepId, isFileUpload } = mapped;
      if (isFileUpload) {
        if (!restoredCache.analysis) restoredCache.analysis = data;
      } else {
        restoredSteps.add(stepId);
        restoredCache[stepId] = data;
      }
      lastData = data;
    }

    if (restoredSteps.size > 0) {
      setCompletedSteps(restoredSteps);
      setStepCache(restoredCache);
    }
    if (lastData) {
      setAnalysisDataRaw(lastData);
    }
  }, []);

  const cacheCurrentStep = useCallback(() => {
    if (analysisData?.view_mode) {
      const mapped = viewModeToStepId(analysisData.view_mode);
      if (mapped) {
        setStepCache(prev => ({ ...prev, [mapped.stepId]: analysisData }));
      }
    }
  }, [analysisData]);

  const switchToStep = useCallback((step: StepId) => {
    const cached = stepCache[step];
    if (cached) setAnalysisDataRaw(cached);
  }, [stepCache]);

  return (
    <AnalysisContext.Provider value={{
      analysisData, setAnalysisData, restoreFromHistory, stepCache,
      cacheCurrentStep, switchToStep, completedSteps,
      stageValidation, setStageValidation,
    }}>
      {children}
    </AnalysisContext.Provider>
  );
}

export function useAnalysis() {
  return useContext(AnalysisContext);
}
