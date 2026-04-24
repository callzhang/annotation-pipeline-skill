import type { KanbanSnapshot } from "./types";

export async function fetchKanbanSnapshot(): Promise<KanbanSnapshot> {
  const response = await fetch("/api/kanban");
  if (!response.ok) {
    throw new Error(`Kanban API returned ${response.status}`);
  }
  return response.json() as Promise<KanbanSnapshot>;
}
