import type {
  ConfigSnapshot,
  CoordinatorLongTailIssue,
  CoordinatorLongTailIssuePayload,
  CoordinatorReport,
  CoordinatorRuleUpdate,
  CoordinatorRuleUpdatePayload,
  EventLog,
  KanbanSnapshot,
  ProjectSnapshot,
  ProviderConfigSnapshot,
  RuntimeCyclesResponse,
  RuntimeMonitorReport,
  RuntimeRunOnceResponse,
  RuntimeSnapshot,
  TaskDetail,
  ReadinessReport,
  OutboxSummary,
} from "./types";

function projectQuery(projectId: string | null): string {
  return projectId ? `?project=${encodeURIComponent(projectId)}` : "";
}

export async function fetchProjects(): Promise<ProjectSnapshot> {
  const response = await fetch("/api/projects");
  if (!response.ok) {
    throw new Error(`Projects API returned ${response.status}`);
  }
  return response.json() as Promise<ProjectSnapshot>;
}

export async function fetchKanbanSnapshot(projectId: string | null = null): Promise<KanbanSnapshot> {
  const response = await fetch(`/api/kanban${projectQuery(projectId)}`);
  if (!response.ok) {
    throw new Error(`Kanban API returned ${response.status}`);
  }
  return response.json() as Promise<KanbanSnapshot>;
}

export async function fetchTaskDetail(taskId: string): Promise<TaskDetail> {
  const response = await fetch(`/api/tasks/${encodeURIComponent(taskId)}`);
  if (!response.ok) {
    throw new Error(`Task detail API returned ${response.status}`);
  }
  return response.json() as Promise<TaskDetail>;
}

export async function postFeedbackDiscussion(
  taskId: string,
  payload: Record<string, unknown>,
): Promise<TaskDetail> {
  const response = await fetch(`/api/tasks/${encodeURIComponent(taskId)}/feedback-discussions`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    const errorPayload = (await response.json().catch(() => null)) as { detail?: string; error?: string } | null;
    throw new Error(errorPayload?.detail ?? errorPayload?.error ?? `Feedback discussion API returned ${response.status}`);
  }
  await response.json();
  return fetchTaskDetail(taskId);
}

export async function postHumanReviewDecision(
  taskId: string,
  payload: Record<string, unknown>,
): Promise<TaskDetail> {
  const response = await fetch(`/api/tasks/${encodeURIComponent(taskId)}/human-review`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    const errorPayload = (await response.json().catch(() => null)) as { detail?: string; error?: string } | null;
    throw new Error(errorPayload?.detail ?? errorPayload?.error ?? `Human Review API returned ${response.status}`);
  }
  await response.json();
  return fetchTaskDetail(taskId);
}

export async function saveTaskQcPolicy(
  taskId: string,
  payload: Record<string, unknown>,
): Promise<TaskDetail> {
  const response = await fetch(`/api/tasks/${encodeURIComponent(taskId)}/qc-policy`, {
    method: "PUT",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    const errorPayload = (await response.json().catch(() => null)) as { detail?: string; error?: string } | null;
    throw new Error(errorPayload?.detail ?? errorPayload?.error ?? `QC policy API returned ${response.status}`);
  }
  return response.json() as Promise<TaskDetail>;
}

export async function fetchConfigSnapshot(): Promise<ConfigSnapshot> {
  const response = await fetch("/api/config");
  if (!response.ok) {
    throw new Error(`Config API returned ${response.status}`);
  }
  return response.json() as Promise<ConfigSnapshot>;
}

export async function saveConfigFile(id: string, content: string): Promise<void> {
  const response = await fetch(`/api/config/${encodeURIComponent(id)}`, {
    method: "PUT",
    headers: { "content-type": "application/yaml; charset=utf-8" },
    body: content,
  });
  if (!response.ok) {
    const payload = (await response.json().catch(() => null)) as { detail?: string; error?: string } | null;
    throw new Error(payload?.detail ?? payload?.error ?? `Config save returned ${response.status}`);
  }
}

export async function fetchEventLog(projectId: string | null = null): Promise<EventLog> {
  const response = await fetch(`/api/events${projectQuery(projectId)}`);
  if (!response.ok) {
    throw new Error(`Event log API returned ${response.status}`);
  }
  return response.json() as Promise<EventLog>;
}

export async function fetchRuntimeSnapshot(): Promise<RuntimeSnapshot> {
  const response = await fetch("/api/runtime");
  if (!response.ok) {
    throw new Error(`Runtime API returned ${response.status}`);
  }
  return response.json() as Promise<RuntimeSnapshot>;
}

export async function fetchRuntimeCycles(): Promise<RuntimeCyclesResponse> {
  const response = await fetch("/api/runtime/cycles");
  if (!response.ok) {
    throw new Error(`Runtime cycles API returned ${response.status}`);
  }
  return response.json() as Promise<RuntimeCyclesResponse>;
}

export async function fetchRuntimeMonitor(): Promise<RuntimeMonitorReport> {
  const response = await fetch("/api/runtime/monitor");
  if (!response.ok) {
    throw new Error(`Runtime monitor API returned ${response.status}`);
  }
  return response.json() as Promise<RuntimeMonitorReport>;
}

export async function fetchReadinessReport(projectId: string): Promise<ReadinessReport> {
  const response = await fetch(`/api/readiness?project=${encodeURIComponent(projectId)}`);
  if (!response.ok) {
    throw new Error(`Readiness API returned ${response.status}`);
  }
  return response.json() as Promise<ReadinessReport>;
}

export async function fetchOutboxSummary(projectId: string | null = null): Promise<OutboxSummary> {
  const response = await fetch(`/api/outbox${projectQuery(projectId)}`);
  if (!response.ok) {
    throw new Error(`Outbox API returned ${response.status}`);
  }
  return response.json() as Promise<OutboxSummary>;
}

export async function runRuntimeOnce(): Promise<RuntimeRunOnceResponse> {
  const response = await fetch("/api/runtime/run-once", { method: "POST", body: "{}" });
  if (!response.ok) {
    const payload = (await response.json().catch(() => null)) as { error?: string } | null;
    throw new Error(payload?.error ?? `Runtime run-once API returned ${response.status}`);
  }
  return response.json() as Promise<RuntimeRunOnceResponse>;
}

export async function fetchProviderConfig(): Promise<ProviderConfigSnapshot> {
  const response = await fetch("/api/providers");
  if (!response.ok) {
    const payload = (await response.json().catch(() => null)) as { detail?: string; error?: string } | null;
    throw new Error(payload?.detail ?? payload?.error ?? `Provider API returned ${response.status}`);
  }
  return response.json() as Promise<ProviderConfigSnapshot>;
}

export async function saveProviderConfig(payload: {
  profiles: ProviderConfigSnapshot["profiles"];
  targets: ProviderConfigSnapshot["targets"];
  limits: ProviderConfigSnapshot["limits"];
}): Promise<ProviderConfigSnapshot> {
  const response = await fetch("/api/providers", {
    method: "PUT",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    const errorPayload = (await response.json().catch(() => null)) as { detail?: string; error?: string } | null;
    throw new Error(errorPayload?.detail ?? errorPayload?.error ?? `Provider save returned ${response.status}`);
  }
  return response.json() as Promise<ProviderConfigSnapshot>;
}

export async function fetchCoordinatorReport(projectId: string | null = null): Promise<CoordinatorReport> {
  const response = await fetch(`/api/coordinator${projectQuery(projectId)}`);
  if (!response.ok) {
    throw new Error(`Coordinator API returned ${response.status}`);
  }
  return response.json() as Promise<CoordinatorReport>;
}

export async function postCoordinatorRuleUpdate(
  payload: CoordinatorRuleUpdatePayload,
): Promise<CoordinatorRuleUpdate> {
  const response = await fetch("/api/coordinator/rule-updates", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    const errorPayload = (await response.json().catch(() => null)) as { detail?: string; error?: string } | null;
    throw new Error(
      errorPayload?.detail ?? errorPayload?.error ?? `Coordinator rule update returned ${response.status}`,
    );
  }
  return response.json() as Promise<CoordinatorRuleUpdate>;
}

export async function postCoordinatorLongTailIssue(
  payload: CoordinatorLongTailIssuePayload,
): Promise<CoordinatorLongTailIssue> {
  const response = await fetch("/api/coordinator/long-tail-issues", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    const errorPayload = (await response.json().catch(() => null)) as { detail?: string; error?: string } | null;
    throw new Error(
      errorPayload?.detail ?? errorPayload?.error ?? `Coordinator long-tail issue returned ${response.status}`,
    );
  }
  return response.json() as Promise<CoordinatorLongTailIssue>;
}
