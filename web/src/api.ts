import type {
  ConfigSnapshot,
  EventLog,
  KanbanSnapshot,
  ProjectSnapshot,
  RuntimeCyclesResponse,
  RuntimeMonitorReport,
  RuntimeRunOnceResponse,
  RuntimeSnapshot,
  TaskDetail,
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

export async function runRuntimeOnce(): Promise<RuntimeRunOnceResponse> {
  const response = await fetch("/api/runtime/run-once", { method: "POST", body: "{}" });
  if (!response.ok) {
    const payload = (await response.json().catch(() => null)) as { error?: string } | null;
    throw new Error(payload?.error ?? `Runtime run-once API returned ${response.status}`);
  }
  return response.json() as Promise<RuntimeRunOnceResponse>;
}
