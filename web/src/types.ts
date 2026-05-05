export interface TaskCard {
  task_id: string;
  status: string;
  modality: string;
  annotation_types: string[];
  selected_annotator_id: string | null;
  status_age_seconds: number;
  latest_attempt_status: string | null;
  feedback_count: number;
  retry_pending: boolean;
  blocked: boolean;
  external_sync_pending: boolean;
}

export interface KanbanColumn {
  id: string;
  title: string;
  cards: TaskCard[];
}

export interface KanbanSnapshot {
  project_id: string | null;
  columns: KanbanColumn[];
}

export interface ProjectSummary {
  project_id: string;
  task_count: number;
  status_counts: Record<string, number>;
}

export interface ProjectSnapshot {
  projects: ProjectSummary[];
}

export interface TaskDetailArtifact {
  artifact_id: string;
  task_id: string;
  kind: string;
  path: string;
  content_type: string;
  created_at: string;
  metadata: Record<string, unknown>;
  payload: unknown;
}

export interface TaskDetail {
  task: {
    task_id: string;
    pipeline_id: string;
    source_ref: Record<string, unknown>;
    modality: string;
    annotation_requirements: Record<string, unknown>;
    selected_annotator_id: string | null;
    status: string;
    current_attempt: number;
    metadata: Record<string, unknown>;
  };
  attempts: Array<Record<string, unknown>>;
  artifacts: TaskDetailArtifact[];
  events: Array<Record<string, unknown>>;
  feedback: Array<Record<string, unknown>>;
  feedback_discussions: Array<Record<string, unknown>>;
  feedback_consensus: {
    total_feedback: number;
    consensus_feedback: number;
    open_feedback: string[];
    can_accept_by_consensus: boolean;
  };
}

export interface ConfigFile {
  id: string;
  title: string;
  path: string;
  exists: boolean;
  content: string;
}

export interface ConfigSnapshot {
  files: ConfigFile[];
}

export interface EventLog {
  events: Array<Record<string, unknown>>;
}

export interface RuntimeStatus {
  healthy: boolean;
  heartbeat_at: string | null;
  heartbeat_age_seconds: number | null;
  active: boolean;
  errors: string[];
}

export interface QueueCounts {
  draft: number;
  pending: number;
  annotating: number;
  validating: number;
  qc: number;
  human_review: number;
  accepted: number;
  rejected: number;
  blocked: number;
  cancelled: number;
}

export interface ActiveRun {
  run_id: string;
  task_id: string;
  stage: string;
  attempt_id: string;
  provider_target: string;
  started_at: string;
  heartbeat_at: string;
  metadata: Record<string, unknown>;
}

export interface CapacitySnapshot {
  max_concurrent_tasks: number;
  max_starts_per_cycle: number;
  active_count: number;
  available_slots: number;
}

export interface RuntimeCycleStats {
  cycle_id: string;
  started_at: string;
  finished_at: string;
  started: number;
  accepted: number;
  failed: number;
  capacity_available: number;
  errors: Array<Record<string, unknown>>;
}

export interface RuntimeSnapshot {
  generated_at: string;
  runtime_status: RuntimeStatus;
  queue_counts: QueueCounts;
  active_runs: ActiveRun[];
  capacity: CapacitySnapshot;
  stale_tasks: string[];
  due_retries: string[];
  project_summaries: ProjectSummary[];
  cycle_stats: RuntimeCycleStats[];
}

export interface RuntimeCyclesResponse {
  cycles: RuntimeCycleStats[];
}

export interface RuntimeMonitorReport {
  ok: boolean;
  failures: string[];
  details: Record<string, Record<string, unknown>>;
}

export interface RuntimeRunOnceResponse {
  ok: boolean;
  snapshot: RuntimeSnapshot;
}

export interface ReadinessReport {
  project_id: string;
  ready_for_training: boolean;
  accepted_count: number;
  exported_count: number;
  exportable_count: number;
  open_feedback_count: number;
  human_review_count: number;
  validation_blockers: Array<Record<string, string>>;
  pending_outbox_count: number;
  latest_export: {
    export_id: string;
    created_at: string;
    output_paths: string[];
    included: number;
    excluded: number;
  } | null;
  recommended_next_action: string;
  next_command: string | null;
}

export type ProviderName = "openai_responses" | "openai_compatible" | "local_cli";
export type ProviderFlavor = "deepseek" | "glm" | "minimax";
export type CliKind = "codex" | "claude";

export interface ProviderProfileConfig {
  name: string;
  provider: ProviderName;
  provider_flavor: ProviderFlavor | null;
  cli_kind: CliKind | null;
  cli_binary: string | null;
  model: string;
  api_key_env: string | null;
  base_url: string | null;
  reasoning_effort: string | null;
  permission_mode: string | null;
  timeout_seconds: number | null;
  max_retries: number | null;
  concurrency_limit: number | null;
  no_progress_timeout_seconds: number | null;
}

export interface ProviderCheck {
  id: string;
  status: "ok" | "warning" | "error";
  message: string;
}

export interface ProviderDiagnostic {
  status: "ok" | "warning" | "error";
  checks: ProviderCheck[];
}

export interface ProviderConfigSnapshot {
  config_valid: boolean;
  profiles: ProviderProfileConfig[];
  targets: Record<string, string>;
  limits: {
    local_cli_global_concurrency: number | null;
  };
  diagnostics: Record<string, ProviderDiagnostic>;
}
