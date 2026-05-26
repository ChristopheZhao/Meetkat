export type RoomEvent = {
  schema_version: string;
  event_id: string;
  room_id: string;
  room_seq: number;
  producer_id: string;
  role: string;
  event_type: string;
  ts_ms: number;
  payload: Record<string, unknown>;
};

export type Participant = {
  participant_id: string;
  role: string;
  identity: string;
  display_name: string;
  activation: string;
  speaking: boolean;
  capability_profile: string;
  join_reason: string;
  focus_areas: string[];
};

export type TranscriptEntry = {
  message_id: string;
  seq: number;
  ts_ms: number;
  role: string;
  producer_id: string;
  event_type: string;
  text: string;
  title: string;
  artifacts: Record<string, unknown>;
};

export type ConsensusState = {
  score: number;
  should_end: boolean;
  reason: string;
  support: number;
  confidence: number;
  disagreement_index: number;
  margin_top1_top2: number;
};

export type TurnPlan = {
  role: string;
  task: string;
};

export type PlannedSpecialist = {
  role: string;
  display_name: string;
  capability_profile: string;
  prompt_contract: string;
  join_reason: string;
  focus_areas: string[];
  ttl_rounds: number;
  turn_budget: number;
};

export type RoomStartContract = {
  room_start_ready: boolean;
  runtime_bootstrap_ready: boolean;
  missing_operator_inputs: string[];
  contextual_open_questions: string[];
  system_blockers: string[];
  known_context: string[];
  recommended_surface: string;
  root_cause_hypothesis: string;
};

export type RuntimeReadiness = {
  planner_target?: ProviderTarget;
  executor_targets?: ExecutorTargets;
  planner_mode?: string;
  primary_planner_ready?: boolean;
  fallback_planner_ready?: boolean;
  primary_unavailable_reason?: string;
  planner_missing_env?: string[];
  executor_mode?: string;
  executor_ready?: boolean;
  executor_reason?: string;
  executor_missing_env?: string[];
  [key: string]: unknown;
};

export type ProviderTarget = {
  supplier?: string;
  model?: string;
};

export type ExecutorTargets = {
  default?: ProviderTarget;
  escalation?: ProviderTarget;
  fallback?: ProviderTarget;
};

export type RoomPreflightReport = {
  requirement: string;
  topic: string;
  meeting_objective: string;
  initial_focus: string;
  constraints: string[];
  brief_source: string;
  brief_source_reason: string;
  candidate_specialist_roster: PlannedSpecialist[];
  room_start_contract: RoomStartContract;
  runtime_readiness: RuntimeReadiness;
  operator_context?: Record<string, unknown>;
};

export type RoomSnapshot = {
  room_id: string;
  requirement: string;
  topic: string;
  goal: string;
  brief_source: string;
  brief_source_reason: string;
  mode: string;
  status: string;
  phase: string;
  round_index: number;
  current_focus: string;
  current_turns: TurnPlan[];
  constraints: string[];
  planning_artifacts: Record<string, unknown>;
  participants: Participant[];
  transcript: TranscriptEntry[];
  live_chunks: Record<string, string>;
  consensus: ConsensusState;
  candidate_decision: string;
  action_items: string[];
  open_questions: string[];
  last_human_message: string;
  last_override: string;
  conclusion_type: string;
  conclusion_reason: string;
  ended_reason: string;
  control_reason: string;
  orchestration_end_reason: string;
  resume_token: string;
  created_at_ms: number;
  updated_at_ms: number;
  last_seq: number;
};

export type RoomSummary = {
  room_id: string;
  requirement: string;
  topic: string;
  goal: string;
  brief_source: string;
  brief_source_reason: string;
  mode: string;
  status: string;
  phase: string;
  round_index: number;
  current_focus: string;
  last_seq: number;
  updated_at_ms: number;
  created_at_ms: number;
  candidate_decision: string;
  conclusion_type: string;
};

export type CreateRoomInput = {
  requirement: string;
  mode: string;
  allow_planner_fallback?: boolean;
  require_preflight_ready?: boolean;
  entry_scope?: string;
  operator_context?: Record<string, unknown>;
};

export type PreflightRoomInput = {
  requirement: string;
  allow_planner_fallback?: boolean;
  entry_scope?: string;
  operator_context?: Record<string, unknown>;
};
