// src/context/AnalysisContext.tsx
import { createContext, useContext, useState, useCallback, type ReactNode } from 'react';
import { useProjectContext } from './ProjectContext';
import { useEffect } from 'react';

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
  stepCache: StepCache;
  cacheCurrentStep: () => void;
  switchToStep: (step: StepId) => void;
  completedSteps: Set<StepId>;
}

const AnalysisContext = createContext<AnalysisContextType>({
  analysisData: null,
  setAnalysisData: () => {},
  stepCache: {},
  cacheCurrentStep: () => {},
  switchToStep: () => {},
  completedSteps: new Set(),
});

function viewModeToStepId(viewMode: string): { stepId: StepId; isFileUpload: boolean } | null {
  switch (viewMode) {
    case 'file_uploaded':          return { stepId: 'analysis',      isFileUpload: true };
    case 'report':                 return { stepId: 'analysis',      isFileUpload: false };
    case 'problem_definition':     return { stepId: 'problem_def',   isFileUpload: false };
    case 'problem_defined':        return { stepId: 'problem_def',   isFileUpload: false };
    case 'normalization':          return { stepId: 'normalization',  isFileUpload: false };
    case 'normalization_mapping':  return { stepId: 'normalization',  isFileUpload: false };
    case 'normalization_complete': return { stepId: 'normalization',  isFileUpload: false };
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

  useEffect(() => {
    setAnalysisDataRaw(null);
    setStepCache({});
    setCompletedSteps(new Set());
  }, [currentProject?.id]);

  const setAnalysisData = useCallback((data: any) => {
    setAnalysisDataRaw(data);
    if (data && data.view_mode) {
      const mapped = viewModeToStepId(data.view_mode);
      if (mapped) {
        const { stepId, isFileUpload } = mapped;
        if (isFileUpload) {
          setStepCache(prev => {
            if (!prev.analysis) return { ...prev, analysis: data };
            return prev;
          });
        } else {
          setStepCache(prev => ({ ...prev, [stepId]: data }));
          setCompletedSteps(prev => {
            const next = new Set(prev);
            next.add(stepId);
            return next;
          });
        }
      }
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
      analysisData, setAnalysisData, stepCache,
      cacheCurrentStep, switchToStep, completedSteps,
    }}>
      {children}
    </AnalysisContext.Provider>
  );
}

export function useAnalysis() {
  return useContext(AnalysisContext);
}
