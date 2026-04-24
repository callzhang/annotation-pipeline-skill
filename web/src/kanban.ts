import type { KanbanColumn, KanbanSnapshot, TaskCard } from "./types";

export function countCards(snapshot: KanbanSnapshot): number {
  return snapshot.columns.reduce((total, column) => total + column.cards.length, 0);
}

export function visibleColumns(snapshot: KanbanSnapshot): KanbanColumn[] {
  return snapshot.columns;
}

export function cardSubtitle(card: Pick<TaskCard, "modality" | "annotation_types">): string {
  const types = card.annotation_types.length > 0 ? card.annotation_types.join(", ") : "untyped";
  return `${card.modality} · ${types}`;
}
