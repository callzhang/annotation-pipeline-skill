import type { KanbanSnapshot, TaskDetail } from "./types";

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
