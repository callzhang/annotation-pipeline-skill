import { useEffect, useState } from "react";
import { fetchEventLog } from "../api";

export function EventLogPanel() {
  const [events, setEvents] = useState<Array<Record<string, unknown>>>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    fetchEventLog()
      .then((payload) => {
        if (!active) return;
        setEvents(payload.events);
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
  }, []);

  return (
    <section className="work-panel event-log" aria-label="Event log">
      <div className="panel-header">
        <div>
          <h2>Event Log</h2>
          <p>{events.length} audit events from task lifecycle transitions</p>
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
    </section>
  );
}
