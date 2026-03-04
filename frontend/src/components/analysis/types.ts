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
  estimated_time?: number[];
  estimated_cost?: number[];
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

// -- Problem Definition --

export interface ProblemParameter {
  name: string;
  value: string | number | null;
  unit?: string;
  source?: string;
}

export interface ProblemConstraint {
  id: string;
  description: string;
  type: 'hard' | 'soft';
  enabled: boolean;
  weight?: number;
  weight_range?: number[];
}

export interface ProblemDefinitionData {
  view_mode: 'problem_definition';
  problem_name?: string;
  domain?: string;
  objective?: {
    type?: string;
    target?: string;
    description?: string;
    alternatives?: Array<{ target: string; description: string }>;
  };
  parameters?: ProblemParameter[];
  hard_constraints?: ProblemConstraint[];
  soft_constraints?: ProblemConstraint[];
  status?: 'draft' | 'confirmed';
  stage?: string;
  variant?: string;
  detected_data_types?: string[];
}

// -- Data Normalization --

export interface ColumnMapping {
  target_table: string;
  source_file: string;
  source_sheet?: string;
  transform_type?: string;
  confidence: number;
  reason?: string;
  column_mapping?: Record<string, string>;
}

export interface NormalizationData {
  view_mode: 'normalization';
  mappings?: ColumnMapping[];
  auto_confirmed?: ColumnMapping[];
  needs_review?: ColumnMapping[];
  results?: string[];
  errors?: string[];
  status?: 'proposed' | 'confirmed' | 'complete' | 'error';
  warnings?: string[];
}

export type AnalysisData =
  | ReportData
  | SolverData
  | ResultData
  | FileUploadedData
  | MathModelData
  | ProblemDefinitionData
  | NormalizationData;

export interface AnalysisReportProps {
  data: AnalysisData;
  projectId?: string;
  onAction?: (type: string, message: string) => void;
}
