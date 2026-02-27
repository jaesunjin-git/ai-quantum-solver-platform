// src/components/analysis/types.ts

export interface ActionItem {
  label: string;
  message: string;
}

export interface ReportActions {
  primary?: ActionItem;
  secondary?: ActionItem;
}

export interface ReportData {
  report?: string;
  agent_status?: string;
  actions?: ReportActions;
  domain?: string;
  domain_confidence?: number;
}

export interface Solver {
  solver_id: string;
  solver_name: string;
  solver_type: string;
  provider: string;
  category: string;
  suitability: string;
  total_score: number;
  scores: {
    structure: number;
    scale: number;
    cost: number;
    speed: number;
  };
  reasons: string[];
  warnings: string[];
  description: string;
  strengths: string[];
  weaknesses: string[];
  typical_time_seconds: number[];
  estimated_time?: number[];      // 시간 추정
  estimated_cost?: number[];      // 비용 추정
}

export interface ProblemProfile {
  variable_count: number;
  constraint_count: number;
  variable_types: string[];
  has_constraints: boolean;
  problem_classes: string[];
}

export interface SolverData {
  view_mode: 'solver';
  problem_profile?: ProblemProfile;
  recommended_solvers?: Solver[];
  top_recommendation?: Solver;
  priority?: string;
  execution_strategies?: any[];
  recommended_strategy?: any;
  model_analysis?: any;
}

export interface ScheduleItem {
  day?: string;
  crew_id?: string;
  shifts?: number;
  crew?: number;
  work_hours?: number;
}

export interface OptSummary {
  total_cost_reduction?: string;
  schedule_efficiency?: string;
  constraint_satisfaction?: string;
  computation_time?: string;
  coverage_rate?: number;
  crew_satisfaction_score?: number;
  total_operation_cost?: number;
  total_crews_assigned?: number;
  total_trips_covered?: number;
  [key: string]: string | number | undefined;
}

export interface OptConstraints {
  hard_constraints_met?: number;
  hard_constraints_total?: number;
  soft_constraints_met?: number;
  soft_constraints_total?: number;
}

export interface OptResult {
  summary?: OptSummary;
  constraints?: OptConstraints;
  schedule_preview?: ScheduleItem[];
  solver_used?: string;
  solver?: string;
  job_id?: string;
  execution_time_sec?: number;
}

export interface ResultData {
  view_mode: string;
  // solve API response fields
  status?: string;
  objective_value?: number;
  solver_id?: string;
  solver_name?: string;
  solver_type?: string;
  model_stats?: {
    total_variables?: number;
    total_constraints?: number;
    nonzero_variables?: number;
  };
  timing?: {
    compile_sec?: number;
    execute_sec?: number;
    total_sec?: number;
  };
  solution?: Record<string, any>;
  solver_info?: Record<string, any>;
  compile_warnings?: string[];
  compare_mode?: boolean;
  // legacy fields for backward compatibility
  result?: any;
}

export interface FileUploadedData {
  view_mode: 'file_uploaded';
  files?: string[];
  file_count?: number;
}

export interface MathModelData {
  view_mode: 'math_model';
  math_model?: {
    model_version?: string;
    problem_name?: string;
    domain?: string;
    sets?: Record<string, any>;
    parameters?: Record<string, any>;
    variables?: any[];
    objective?: any;
    constraints?: any[];
    metadata?: {
      estimated_variable_count?: number;
      estimated_constraint_count?: number;
      variable_types?: string[];
      recommended_solvers?: string[];
    };
  };
  math_model_summary?: string;
}

export type AnalysisData = ReportData | SolverData | ResultData | FileUploadedData | MathModelData;


export interface AnalysisReportProps {
  data: AnalysisData;
  projectId?: string;
  onAction?: (type: string, message: string) => void;
}
