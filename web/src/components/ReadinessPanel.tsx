import { useEffect, useState } from "react";
import { fetchReadinessReport } from "../api";
import { readinessFacts, readinessTitle } from "../readiness";
import type { ReadinessReport } from "../types";

interface ReadinessPanelProps {
  projectId: string | null;
}

export function ReadinessPanel({ projectId }: ReadinessPanelProps) {
  const [report, setReport] = useState<ReadinessReport | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!projectId) {
      setReport(null);
      setLoading(false);
      setError(null);
      return;
    }
    let active = true;
    setLoading(true);
    fetchReadinessReport(projectId)
      .then((nextReport) => {
        if (!active) return;
        setReport(nextReport);
        setError(null);
      })
      .catch((reason: unknown) => {
        if (!active) return;
        setError(reason instanceof Error ? reason.message : "Unable to load readiness report");
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [projectId]);

  if (!projectId) {
    return (
      <section className="work-panel" aria-label="Training data readiness">
        <div className="panel-header">
          <div>
            <h2>Readiness</h2>
            <p>Select one project to inspect export and delivery status.</p>
          </div>
        </div>
      </section>
    );
  }

  if (loading) return <section className="work-panel">Loading readiness</section>;
  if (!report) return <section className="work-panel notice compact">{error ?? "Readiness unavailable"}</section>;

  return (
    <section className="runtime-panel" aria-label="Training data readiness">
      {error ? <div className="notice compact">{error}</div> : null}
      <div className="runtime-header">
        <div>
          <h2>Readiness</h2>
          <p>{readinessTitle(report)} · {report.project_id}</p>
        </div>
      </div>

      <div className="runtime-grid">
        <div className="runtime-card">
          <h3>Training Data</h3>
          <dl className="runtime-facts">
            {readinessFacts(report).map((fact) => (
              <div key={fact.label}>
                <dt>{fact.label}</dt>
                <dd>{fact.value}</dd>
              </div>
            ))}
          </dl>
        </div>

        <div className="runtime-card">
          <h3>Latest Export</h3>
          {report.latest_export ? (
            <dl className="runtime-facts">
              <div>
                <dt>Export</dt>
                <dd>{report.latest_export.export_id}</dd>
              </div>
              <div>
                <dt>Included</dt>
                <dd>{report.latest_export.included}</dd>
              </div>
              <div>
                <dt>Excluded</dt>
                <dd>{report.latest_export.excluded}</dd>
              </div>
              <div>
                <dt>Path</dt>
                <dd>{report.latest_export.output_paths[0] ?? "none"}</dd>
              </div>
            </dl>
          ) : (
            <p className="runtime-muted">No export manifest recorded.</p>
          )}
        </div>
      </div>

      <div className="runtime-card">
        <h3>Next Action</h3>
        <p className="runtime-muted">{readinessTitle(report)}</p>
        {report.next_command ? <pre className="json-block">{report.next_command}</pre> : null}
        {report.validation_blockers.length > 0 ? (
          <ul className="runtime-list compact-list">
            {report.validation_blockers.map((blocker) => (
              <li key={`${String(blocker.task_id)}-${String(blocker.reason)}`}>
                {String(blocker.task_id)}: {String(blocker.reason)}
                {Array.isArray(blocker.errors) && blocker.errors.length > 0 ? (
                  <small>{blocker.errors.map((error) => String(error)).join(", ")}</small>
                ) : null}
              </li>
            ))}
          </ul>
        ) : null}
      </div>
    </section>
  );
}
