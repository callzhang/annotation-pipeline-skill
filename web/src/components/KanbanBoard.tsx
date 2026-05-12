import { useState } from "react";
import { cardSubtitle, visibleColumns } from "../kanban";
import type { KanbanSnapshot, TaskCard } from "../types";

interface KanbanBoardProps {
  snapshot: KanbanSnapshot;
  selectedTaskId: string | null;
  onSelectTask: (task: TaskCard) => void;
}

interface ColumnState {
  page: number;
  filter: string;
}

const PAGE_SIZE = 20;

function filterCard(card: TaskCard, query: string): boolean {
  const q = query.toLowerCase();
  return (
    card.task_id.toLowerCase().includes(q) ||
    card.modality.toLowerCase().includes(q) ||
    (card.annotator_model?.toLowerCase().includes(q) ?? false) ||
    (card.qc_model?.toLowerCase().includes(q) ?? false)
  );
}

export function KanbanBoard({ snapshot, selectedTaskId, onSelectTask }: KanbanBoardProps) {
  const [columnStates, setColumnStates] = useState<Record<string, ColumnState>>({});

  function getState(columnId: string): ColumnState {
    return columnStates[columnId] ?? { page: 0, filter: "" };
  }

  function setFilter(columnId: string, filter: string) {
    setColumnStates((prev) => ({ ...prev, [columnId]: { page: 0, filter } }));
  }

  function setPage(columnId: string, page: number) {
    setColumnStates((prev) => ({ ...prev, [columnId]: { ...getState(columnId), page } }));
  }

  return (
    <section className="kanban-board" aria-label="Task Kanban board">
      {visibleColumns(snapshot).map((column) => {
        const { page, filter } = getState(column.id);

        const sorted = [...column.cards].sort((a, b) => a.status_age_seconds - b.status_age_seconds);
        const filtered = filter ? sorted.filter((card) => filterCard(card, filter)) : sorted;
        const pageCount = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
        const safePage = Math.min(page, pageCount - 1);
        const pageCards = filtered.slice(safePage * PAGE_SIZE, (safePage + 1) * PAGE_SIZE);

        return (
          <div className="kanban-column" key={column.id}>
            <div className="column-header">
              <h2>{column.title}</h2>
              <span>{column.cards.length}</span>
            </div>

            <div className="column-pagination">
              <button
                className="page-arrow"
                type="button"
                disabled={safePage === 0}
                aria-label="Previous page"
                onClick={() => setPage(column.id, safePage - 1)}
              >
                ‹
              </button>
              <input
                className="column-filter"
                type="text"
                value={filter}
                placeholder={pageCount > 1 ? `${safePage + 1} / ${pageCount}` : "filter…"}
                aria-label={`Filter ${column.title} tasks`}
                onChange={(e) => setFilter(column.id, e.target.value)}
              />
              <button
                className="page-arrow"
                type="button"
                disabled={safePage >= pageCount - 1}
                aria-label="Next page"
                onClick={() => setPage(column.id, safePage + 1)}
              >
                ›
              </button>
            </div>

            <div className="card-stack">
              {pageCards.map((card) => (
                <button
                  className={card.task_id === selectedTaskId ? "task-card selected" : "task-card"}
                  key={card.task_id}
                  type="button"
                  onClick={() => onSelectTask(card)}
                >
                  <span className="task-id">{card.task_id}</span>
                  <span className="task-subtitle">{cardSubtitle(card)}</span>
                  <span className="task-meta">{formatAge(card.status_age_seconds)}</span>
                  <span className="task-meta">
                    {card.row_count !== null ? `${card.row_count} rows · ` : ""}
                    {card.attempt_count} {card.attempt_count === 1 ? "attempt" : "attempts"}
                  </span>
                  <span className="badges">
                    {card.annotator_model ? <span className="badge model-a" title="Annotator model">A: {card.annotator_model}</span> : null}
                    {card.qc_model ? <span className="badge model-q" title="QC model">Q: {card.qc_model}</span> : null}
                    {card.feedback_count > 0 ? <span className="badge warn">{card.feedback_count} feedback</span> : null}
                    {card.retry_pending ? <span className="badge">retry</span> : null}
                    {card.external_sync_pending ? <span className="badge">sync</span> : null}
                  </span>
                </button>
              ))}
              {filtered.length === 0 && filter ? (
                <p className="column-empty">No matches</p>
              ) : null}
            </div>
          </div>
        );
      })}
    </section>
  );
}

function formatAge(seconds: number): string {
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  return `${(seconds / 3600).toFixed(1)}h ago`;
}
