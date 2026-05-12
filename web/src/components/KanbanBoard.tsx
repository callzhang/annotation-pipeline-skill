import { cardSubtitle, visibleColumns } from "../kanban";
import type { KanbanSnapshot, TaskCard } from "../types";

interface KanbanBoardProps {
  snapshot: KanbanSnapshot;
  selectedTaskId: string | null;
  onSelectTask: (task: TaskCard) => void;
}

export function KanbanBoard({ snapshot, selectedTaskId, onSelectTask }: KanbanBoardProps) {
  return (
    <section className="kanban-board" aria-label="Task Kanban board">
      {visibleColumns(snapshot).map((column) => (
        <div className="kanban-column" key={column.id}>
          <div className="column-header">
            <h2>{column.title}</h2>
            <span>{column.cards.length}</span>
          </div>
          <div className="card-stack">
            {column.cards.map((card) => (
              <button
                className={card.task_id === selectedTaskId ? "task-card selected" : "task-card"}
                key={card.task_id}
                type="button"
                onClick={() => onSelectTask(card)}
              >
                <span className="task-id">{card.task_id}</span>
                <span className="task-subtitle">{cardSubtitle(card)}</span>
                <span className="task-meta">
                  {card.selected_annotator_id ?? "unassigned"} · {formatAge(card.status_age_seconds)}
                </span>
                <span className="task-meta">
                  {card.row_count !== null ? `${card.row_count} rows · ` : ""}
                  {card.attempt_count} {card.attempt_count === 1 ? "attempt" : "attempts"}
                </span>
                <span className="badges">
                  {card.feedback_count > 0 ? <span className="badge warn">{card.feedback_count} feedback</span> : null}
                  {card.retry_pending ? <span className="badge">retry</span> : null}
                  {card.external_sync_pending ? <span className="badge">sync</span> : null}
                </span>
              </button>
            ))}
          </div>
        </div>
      ))}
    </section>
  );
}

function formatAge(seconds: number): string {
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m`;
  return `${Math.floor(minutes / 60)}h`;
}
