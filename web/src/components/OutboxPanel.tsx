import { useEffect, useState } from "react";
import { fetchOutboxSummary } from "../api";
import { outboxFacts, outboxRecordTitle } from "../outbox";
import type { OutboxSummary } from "../types";

interface OutboxPanelProps {
  projectId: string | null;
  storeKey: string | null;
}

export function OutboxPanel({ projectId, storeKey }: OutboxPanelProps) {
  const [summary, setSummary] = useState<OutboxSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    setLoading(true);
    fetchOutboxSummary(projectId, storeKey)
      .then((nextSummary) => {
        if (!active) return;
        setSummary(nextSummary);
        setError(null);
      })
      .catch((reason: unknown) => {
        if (!active) return;
        setError(reason instanceof Error ? reason.message : "Unable to load outbox");
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [projectId, storeKey]);

  if (loading) return <section className="work-panel">Loading outbox</section>;
  if (!summary) return <section className="work-panel notice compact">{error ?? "Outbox unavailable"}</section>;

  return (
    <section className="runtime-panel" aria-label="External outbox">
      {error ? <div className="notice compact">{error}</div> : null}
      <div className="runtime-header">
        <div>
          <h2>Outbox</h2>
          <p>{projectId ?? "All projects"}</p>
        </div>
      </div>

      <div className="runtime-grid">
        <div className="runtime-card">
          <h3>Delivery State</h3>
          <dl className="runtime-facts">
            {outboxFacts(summary).map((fact) => (
              <div key={fact.label}>
                <dt>{fact.label}</dt>
                <dd>{fact.value}</dd>
              </div>
            ))}
          </dl>
        </div>

        <div className="runtime-card">
          <h3>Records</h3>
          {summary.records.length === 0 ? (
            <p className="runtime-muted">No outbox records.</p>
          ) : (
            <div className="outbox-list">
              {summary.records.map((record) => (
                <details className="timeline-item" key={record.record_id}>
                  <summary>
                    <span>{record.task_id}</span>
                    <small>{outboxRecordTitle(record)} · retries {record.retry_count}</small>
                  </summary>
                  <dl className="runtime-facts compact">
                    <div>
                      <dt>Next retry</dt>
                      <dd>{record.next_retry_at ?? "none"}</dd>
                    </div>
                    <div>
                      <dt>Last error</dt>
                      <dd>{record.last_error ?? "none"}</dd>
                    </div>
                  </dl>
                  <pre className="json-block">{JSON.stringify(record.payload, null, 2)}</pre>
                </details>
              ))}
            </div>
          )}
        </div>
      </div>
    </section>
  );
}
