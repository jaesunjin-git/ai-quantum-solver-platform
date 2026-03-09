// src/components/analysis/types.ts
// Backend API 응답 구조에 맞춘 타입 정의

export interface ActionItem { label: string; message: string; }
export interface ReportActions { primary?: ActionItem; secondary?: ActionItem; }

export interface ReportData {
  view_mode?: string; report?: string; agent_status?: string;
  actions?: ReportActions; domain?: string; domain_confidence?: number;
}

// ── Solver (백엔드 pre_decision.py formatted_solvers 구조) ──
export interface Solver {
  solver_id: string;
  solver_name: string;
  solver_type?: string;
  provider: string;
  category: string;
  suitability: string;
  total_score: number;
  scores?: Record<string, number>;
  reasons?: string[];
  warnings?: string[];
  description?: string;
  strengths?: string[];
  weaknesses?: string[];
  typical_time_seconds?: number | number[];
  estimated_time?: number | number[];
  estimated_cost?: number | number[];
}

export interface ProblemProfile {
  variable_count: number; constraint_count: number;
  variable_types: string[]; has_constraints: boolean;
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
  day: string; crew_id: string; shifts: string;
  crew: string; work_hours: number;
}

export interface OptSummary {
  total_cost_reduction?: number; schedule_efficiency?: number;
  constraint_satisfaction?: number; computation_time?: number;
  coverage_rate?: number; crew_satisfaction_score?: number;
  total_operation_cost?: number; total_crews_assigned?: number;
  total_trips_covered?: number;
  [key: string]: string | number | undefined;
}

export interface OptConstraints {
  hard_met: number; hard_total: number;
  soft_met: number; soft_total: number;
}

export interface OptResult {
  summary?: OptSummary; constraints?: OptConstraints;
  schedule_preview?: ScheduleItem[];
  solver_used?: string; solver?: string;
  job_id?: string; execution_time_sec?: number;
}

// ── Compile Summary (솔버 실행 결과의 컴파일 정보) ──
export interface CompileSummary {
  solver_name?: string;
  solver_type?: string;
  variables_created?: number;
  constraints?: {
    total_in_model: number;
    applied: number;
    failed: number;
  };
  objective_parsed?: boolean;
  compile_time_sec?: number;
  warnings?: string[];
  warning_count?: number;
}

// ── Result Data (솔버 실행 결과) ──
export interface ResultData {
  view_mode: string;
  status?: string;
  objective_value?: number;
  solver_id?: string;
  solver_name?: string;
  solver_type?: string;
  model_stats?: any;
  timing?: any;
  solution?: any;
  solver_info?: any;
  compile_warnings?: string[];
  compile_summary?: CompileSummary;
  interpreted_result?: InterpretedResult;
  artifacts?: Record<string, string>;
  compare_mode?: boolean;
  legacy?: OptResult;
}

export interface FileUploadedData {
  view_mode: 'file_uploaded';
  files?: string[]; file_count?: number;
}

export interface MathModelData {
  view_mode: 'math_model';
  math_model?: {
    version?: number;
    name?: string;
    problem_name?: string;
    domain?: string;
    sets?: any; parameters?: any; variables?: any;
    objective?: any; constraints?: any; metadata?: any;
  };
  summary?: any;
}

export interface ProblemDefinitionData {
  view_mode: 'problem_definition' | 'problem_defined';
  proposal?: {
    stage?: string; variant?: string;
    detected_data_types?: string[];
    objective?: {
      type?: string; target?: string;
      description?: string;
      alternatives?: { target: string; description: string }[];
    };
    hard_constraints?: Record<string, any>;
    soft_constraints?: Record<string, any>;
    parameters?: Record<string, { value: any; source: string }>;
  };
  confirmed_problem?: any;
  agent_status?: string;
}

export interface NormalizationData {
  view_mode: 'normalization' | 'normalization_mapping' | 'normalization_complete';
  mappings?: {
    auto_confirmed?: NormalizationMapping[];
    needs_review?: NormalizationMapping[];
  };
  results?: string[];
  errors?: string[];
  agent_status?: string;
}

export interface NormalizationMapping {
  target_table: string;
  source_file: string;
  source_sheet?: string;
  transform_type?: string;
  confidence: number;
  reason?: string;
  column_mapping?: Record<string, string>;
}

export type AnalysisData =
  | ReportData | SolverData | ResultData
  | FileUploadedData | MathModelData
  | ProblemDefinitionData | NormalizationData;

export interface AnalysisReportProps {
  data: AnalysisData;
  projectId?: string;
  onAction?: (type: string, message: string) => void;
}


// ── 해석된 결과 타입 ──
export interface DutyTrip {
  trip_id: number;
  direction: string;
  dep_station: string;
  arr_station: string;
  dep_time: number;
  arr_time: number;
  dep_hhmm: string;
  arr_hhmm: string;
  duration: number;
}

export interface DutyDetail {
  duty_id: number;
  crew_id?: number;
  trip_count: number;
  trips: DutyTrip[];
  start_time_min: number;
  end_time_min: number;
  start_hhmm: string;
  end_hhmm: string;
  total_driving_min: number;
  total_work_min: number;
  total_stay_min: number;
  idle_min: number;
  violations?: string[];
}

export interface ConstraintCheck {
  name: string;
  limit: string;
  max_actual: string;
  satisfied: boolean;
}

export interface InterpretedResult {
  objective_type: string;
  objective_label: string;
  objective_value: number;
  solver_id: string;
  solver_name: string;
  status: string;
  kpi: Record<string, any>;
  duties: DutyDetail[];
  constraint_status: ConstraintCheck[];
  warnings: string[];
}
