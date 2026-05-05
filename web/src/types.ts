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
