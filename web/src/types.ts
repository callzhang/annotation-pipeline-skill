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
  columns: KanbanColumn[];
}
