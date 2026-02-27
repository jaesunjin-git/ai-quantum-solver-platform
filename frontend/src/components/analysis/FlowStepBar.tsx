// src/components/analysis/FlowStepBar.tsx
import { BarChart3, Braces, Cpu, Target } from 'lucide-react';

type StepId = 'analysis' | 'math_model' | 'solver' | 'result';

interface FlowStep {
  id: StepId;
  label: string;
  icon: React.ReactNode;
}

interface FlowStepBarProps {
  currentStep: StepId;
  completedSteps: Set<StepId>;
  onStepClick: (step: StepId) => void;
}

const STEPS: FlowStep[] = [
  { id: 'analysis',   label: '데이터 분석', icon: <BarChart3 size={14} /> },
  { id: 'math_model', label: '수학 모델',   icon: <Braces size={14} /> },
  { id: 'solver',     label: '솔버',       icon: <Cpu size={14} /> },
  { id: 'result',     label: '결과',       icon: <Target size={14} /> },
];

export function FlowStepBar({ currentStep, completedSteps, onStepClick }: FlowStepBarProps) {
  return (
    <div className="flex items-center px-2 h-10 bg-slate-950 border-b border-slate-800 flex-shrink-0 select-none">
      {STEPS.map((step, idx) => {
        const isActive = step.id === currentStep;
        const isDone = completedSteps.has(step.id);
        const isLocked = !isActive && !isDone;

        return (
          <div key={step.id} className="flex items-center">
            {idx > 0 && (
              <span className={`mx-1 text-[10px] ${isDone || isActive ? 'text-slate-600' : 'text-slate-800'}`}>
                ›
              </span>
            )}
            <button
              onClick={() => !isLocked && onStepClick(step.id)}
              disabled={isLocked}
              className={`
                flex items-center gap-1.5 px-2.5 py-1 rounded-md text-xs font-medium
                transition-all duration-200 whitespace-nowrap
                ${isActive
                  ? 'text-cyan-400 bg-cyan-950/50'
                  : isDone
                    ? 'text-emerald-400 hover:bg-slate-800 cursor-pointer'
                    : 'text-slate-600 cursor-default'
                }
              `}
            >
              <span className={`
                ${isActive ? 'text-cyan-400' : isDone ? 'text-emerald-400' : 'text-slate-700'}
              `}>
                {step.icon}
              </span>
              <span>{step.label}</span>
              {isDone && !isActive && (
                <span className="ml-0.5 w-1.5 h-1.5 rounded-full bg-emerald-500" />
              )}
            </button>
          </div>
        );
      })}
    </div>
  );
}
