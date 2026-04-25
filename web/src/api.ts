import type { ConfigSnapshot, EventLog, KanbanSnapshot, TaskDetail } from "./types";

export async function fetchKanbanSnapshot(): Promise<KanbanSnapshot> {
  const response = await fetch("/api/kanban");
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

export async function fetchEventLog(): Promise<EventLog> {
  const response = await fetch("/api/events");
  if (!response.ok) {
    throw new Error(`Event log API returned ${response.status}`);
  }
  return response.json() as Promise<EventLog>;
}
