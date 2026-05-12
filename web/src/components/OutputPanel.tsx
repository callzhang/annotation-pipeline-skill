import { useEffect, useState } from "react";
import { fetchOutboxSummary, fetchReadinessReport } from "../api";
import { outboxFacts, outboxRecordTitle } from "../outbox";
import { readinessFacts, readinessTitle } from "../readiness";
import type { OutboxSummary, ReadinessReport } from "../types";

interface OutputPanelProps {
  projectId: string | null;
  storeKey: string | null;
}

export function OutputPanel({ projectId, storeKey }: OutputPanelProps) {
  const [readiness, setReadiness] = useState<ReadinessReport | null>(null);
  const [outbox, setOutbox] = useState<OutboxSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    setLoading(true);
    const readinessPromise = projectId
      ? fetchReadinessReport(projectId, storeKey)
      : Promise.resolve(null);
    Promise.all([readinessPromise, fetchOutboxSummary(projectId, storeKey)])
      .then(([nextReadiness, nextOutbox]) => {
        if (!active) return;
        setReadiness(nextReadiness);
        setOutbox(nextOutbox);
        setError(null);
      })
      .catch((reason: unknown) => {
        if (!active) return;
        setError(reason instanceof Error ? reason.message : "Unable to load output data");
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [projectId, storeKey]);

  if (loading) return <section className="runtime-panel">Loading output…</section>;

  return (
    <section className="runtime-panel" aria-label="Export and delivery">
      {error ? <div className="notice compact">{error}</div> : null}
      <div className="runtime-header">
        <div>
          <h2>Output</h2>
          <p>{projectId ?? "All projects"}</p>
        </div>
      </div>

      {/* Readiness */}
      <h3 style={{ marginTop: "1.5rem", borderBottom: "1px solid var(--border, #2a2f3a)", paddingBottom: "0.5rem" }}>Export Readiness</h3>
      {!projectId ? (
        <p className="runtime-muted">Select a project to see export readiness.</p>
      ) : !readiness ? (
        <p className="runtime-muted">Readiness report unavailable.</p>
      ) : (
        <>
          <div className="runtime-grid">
            <div className="runtime-card">
              <h3>Training Data</h3>
              <dl className="runtime-facts">
                {readinessFacts(readiness).map((fact) => (
                  <div key={fact.label}>
                    <dt>{fact.label}</dt>
                    <dd>{fact.value}</dd>
                  </div>
                ))}
              </dl>
            </div>

            <div className="runtime-card">
              <h3>Latest Export</h3>
              {readiness.latest_export ? (
                <dl className="runtime-facts">
                  <div>
                    <dt>Export</dt>
                    <dd>{readiness.latest_export.export_id}</dd>
                  </div>
                  <div>
                    <dt>Included</dt>
                    <dd>{readiness.latest_export.included}</dd>
                  </div>
                  <div>
                    <dt>Excluded</dt>
                    <dd>{readiness.latest_export.excluded}</dd>
                  </div>
                  <div>
                    <dt>Path</dt>
                    <dd>{readiness.latest_export.output_paths[0] ?? "none"}</dd>
                  </div>
                </dl>
              ) : (
                <p className="runtime-muted">No export manifest recorded.</p>
              )}
            </div>
          </div>

          <div className="runtime-card">
            <h3>Next Action</h3>
            <p className="runtime-muted">{readinessTitle(readiness)}</p>
            {readiness.next_command ? <pre className="json-block">{readiness.next_command}</pre> : null}
            {readiness.validation_blockers.length > 0 ? (
              <ul className="runtime-list compact-list">
                {readiness.validation_blockers.map((blocker) => (
                  <li key={`${String(blocker.task_id)}-${String(blocker.reason)}`}>
                    {String(blocker.task_id)}: {String(blocker.reason)}
                    {Array.isArray(blocker.errors) && blocker.errors.length > 0 ? (
                      <small>{blocker.errors.map((e) => String(e)).join(", ")}</small>
                    ) : null}
                  </li>
                ))}
              </ul>
            ) : null}
          </div>
        </>
      )}

      {/* Outbox */}
      <h3 style={{ marginTop: "1.5rem", borderBottom: "1px solid var(--border, #2a2f3a)", paddingBottom: "0.5rem" }}>External Delivery</h3>
      {!outbox ? (
        <p className="runtime-muted">Outbox unavailable.</p>
      ) : (
        <div className="runtime-grid">
          <div className="runtime-card">
            <h3>Delivery State</h3>
            <dl className="runtime-facts">
              {outboxFacts(outbox).map((fact) => (
                <div key={fact.label}>
                  <dt>{fact.label}</dt>
                  <dd>{fact.value}</dd>
                </div>
              ))}
            </dl>
          </div>

          <div className="runtime-card">
            <h3>Records</h3>
            {outbox.records.length === 0 ? (
              <p className="runtime-muted">No outbox records.</p>
            ) : (
              <div className="outbox-list">
                {outbox.records.map((record) => (
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
      )}
    </section>
  );
}
