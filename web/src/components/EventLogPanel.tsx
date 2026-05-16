import { useEffect, useMemo, useState } from "react";
import { fetchEventLog } from "../api";

interface EventLogPanelProps {
  projectId: string | null;
  storeKey: string | null;
}

const PAGE_SIZE = 100;

export function EventLogPanel({ projectId, storeKey }: EventLogPanelProps) {
  const [events, setEvents] = useState<Array<Record<string, unknown>>>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1); // 1-based
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Reset to first page when project/store changes.
  useEffect(() => {
    setPage(1);
  }, [projectId, storeKey]);

  useEffect(() => {
    let active = true;
    setLoading(true);
    fetchEventLog(projectId, storeKey, { limit: PAGE_SIZE, offset: (page - 1) * PAGE_SIZE })
      .then((payload) => {
        if (!active) return;
        setEvents(payload.events);
        setTotal(payload.total ?? payload.events.length);
        setError(null);
      })
      .catch((reason: unknown) => {
        if (!active) return;
        setError(reason instanceof Error ? reason.message : "Unable to load event log");
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [projectId, storeKey, page]);

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const pageNumbers = useMemo(() => buildPageList(page, totalPages), [page, totalPages]);

  return (
    <section className="work-panel event-log" aria-label="Event log">
      <div className="panel-header">
        <div>
          <h2>Event Log</h2>
          <p>
            {total.toLocaleString()} audit events from {projectId ?? "all projects"}
            {totalPages > 1 ? ` · page ${page} of ${totalPages}` : ""}
          </p>
        </div>
      </div>
      {loading ? <div className="drawer-state">Loading event log</div> : null}
      {error ? <div className="drawer-error">{error}</div> : null}
      <div className="event-table">
        {events.map((event) => (
          <details className="event-row" key={String(event.event_id)}>
            <summary>
              <span>{String(event.task_id)}</span>
              <span>{String(event.previous_status)} → {String(event.next_status)}</span>
              <span>{String(event.actor)}</span>
              <span>{String(event.created_at)}</span>
            </summary>
            <pre className="json-block">{JSON.stringify(event, null, 2)}</pre>
          </details>
        ))}
      </div>
      {totalPages > 1 ? (
        <nav className="event-log-pager" aria-label="Event log pagination">
          <button
            type="button"
            className="page-arrow"
            onClick={() => setPage((p) => Math.max(1, p - 1))}
            disabled={page <= 1}
          >
            ‹ prev
          </button>
          {pageNumbers.map((entry, idx) =>
            entry === "…" ? (
              <span key={`gap-${idx}`} className="page-gap">…</span>
            ) : (
              <button
                key={entry}
                type="button"
                className={entry === page ? "page-num selected" : "page-num"}
                onClick={() => setPage(entry)}
              >
                {entry}
              </button>
            ),
          )}
          <button
            type="button"
            className="page-arrow"
            onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
            disabled={page >= totalPages}
          >
            next ›
          </button>
        </nav>
      ) : null}
    </section>
  );
}

// Compact page list with ellipsis: 1 … 4 5 6 … 12 (current=5, total=12).
function buildPageList(current: number, total: number): (number | "…")[] {
  if (total <= 7) {
    return Array.from({ length: total }, (_, i) => i + 1);
  }
  const out: (number | "…")[] = [1];
  const start = Math.max(2, current - 2);
  const end = Math.min(total - 1, current + 2);
  if (start > 2) out.push("…");
  for (let i = start; i <= end; i++) out.push(i);
  if (end < total - 1) out.push("…");
  out.push(total);
  return out;
}
