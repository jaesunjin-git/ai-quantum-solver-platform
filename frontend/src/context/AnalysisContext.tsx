// src/context/AnalysisContext.tsx
import { createContext, useContext, useState, useCallback, type ReactNode } from 'react';
import { useProjectContext } from './ProjectContext';
import { useEffect } from 'react';

type StepId = 'analysis' | 'math_model' | 'solver' | 'result';

interface StepCache {
  analysis?: any;
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

export function AnalysisProvider({ children }: { children: ReactNode }) {
  const { currentProject } = useProjectContext();
  const [analysisData, setAnalysisDataRaw] = useState<any>(null);
  const [stepCache, setStepCache] = useState<StepCache>({});
  const [completedSteps, setCompletedSteps] = useState<Set<StepId>>(new Set());

  // Reset on project change
  useEffect(() => {
    setAnalysisDataRaw(null);
    setStepCache({});
    setCompletedSteps(new Set());
  }, [currentProject?.id]);

  // Wrap setAnalysisData to auto-cache by view_mode
  const setAnalysisData = useCallback((data: any) => {
    setAnalysisDataRaw(data);
    console.log('🔍 AnalysisContext setAnalysisData called: view_mode=' + (data?.view_mode || 'NONE') + ', keys=' + (data ? Object.keys(data).join(',') : 'null'));
    if (data && data.view_mode) {
      const mapped = viewModeToStepId(data.view_mode);
      if (mapped) {
        const { stepId, isFileUpload } = mapped;
        if (isFileUpload) {
          // file_uploaded: only cache if no analysis result exists yet
          setStepCache(prev => {
            if (!prev.analysis) {
              return { ...prev, analysis: data };
            }
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
    if (cached) {
      setAnalysisDataRaw(cached);
    }
  }, [stepCache]);

  return (
    <AnalysisContext.Provider value={{
      analysisData,
      setAnalysisData,
      stepCache,
      cacheCurrentStep,
      switchToStep,
      completedSteps,
    }}>
      {children}
    </AnalysisContext.Provider>
  );
}

export function useAnalysis() {
  return useContext(AnalysisContext);
}

function viewModeToStepId(viewMode: string): { stepId: StepId; isFileUpload: boolean } | null {
  switch (viewMode) {
    case 'file_uploaded': return { stepId: 'analysis', isFileUpload: true };
    case 'report': return { stepId: 'analysis', isFileUpload: false };
    case 'math_model': return { stepId: 'math_model', isFileUpload: false };
    case 'solver': return { stepId: 'solver', isFileUpload: false };
    case 'result': return { stepId: 'result', isFileUpload: false };
    default: return null;
  }
}
