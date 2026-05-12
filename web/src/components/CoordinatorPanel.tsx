import { useEffect, useMemo, useState } from "react";
import { fetchCoordinatorReport } from "../api";
import { buildCoordinatorViewModel } from "../coordinator";
import type { CoordinatorReport } from "../types";

interface CoordinatorPanelProps {
  projectId: string | null;
  storeKey: string | null;
}

export function CoordinatorPanel({ projectId, storeKey }: CoordinatorPanelProps) {
  const [report, setReport] = useState<CoordinatorReport | null>(null);
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
    fetchCoordinatorReport(projectId, storeKey)
      .then((nextReport) => {
        if (!active) return;
        setReport(nextReport);
        setError(null);
      })
      .catch((reason: unknown) => {
        if (!active) return;
        setReport(null);
        setError(reason instanceof Error ? reason.message : "Unable to load coordinator report");
      })
      .finally(() => {
        if (active) setLoading(false);
      });

    return () => {
      active = false;
    };
  }, [projectId, storeKey]);

  const viewModel = useMemo(() => (report ? buildCoordinatorViewModel(report) : null), [report]);

  if (!projectId) {
    return (
      <section className="coordinator-panel" aria-label="Coordinator">
        <div className="runtime-header">
          <div>
            <h2>Coordinator</h2>
            <p>Select one project to view guidance.</p>
          </div>
        </div>
      </section>
    );
  }

  if (loading) return <section className="work-panel">Loading coordinator</section>;
  if (!report || !viewModel) {
    return <section className="work-panel notice compact">{error ?? "Coordinator report unavailable"}</section>;
  }

  return (
    <section className="coordinator-panel" aria-label="Coordinator">
      <div className="runtime-header">
        <div>
          <h2>Coordinator</h2>
          <p>
            {report.project_id ?? projectId} · generated {viewModel.generatedAtLabel}
          </p>
        </div>
      </div>

      {error ? <div className="notice compact error">{error}</div> : null}

      {viewModel.emptyState ? (
        <div className="coordinator-empty">
          <h3>{viewModel.emptyState.title}</h3>
          <p>{viewModel.emptyState.detail}</p>
        </div>
      ) : null}

      <div className="coordinator-stats" aria-label="Coordinator overview">
        {viewModel.overviewStats.map((stat) => (
          <div className={`coordinator-stat ${stat.tone}`} key={stat.label}>
            <span>{stat.label}</span>
            <strong>{stat.value}</strong>
          </div>
        ))}
      </div>

      <div className="coordinator-layout">
        <section className="coordinator-section">
          <div className="coordinator-section-header">
            <h3>Recommended Actions</h3>
          </div>
          {viewModel.actionRows.length === 0 ? (
            <p className="runtime-muted">No recommended actions.</p>
          ) : (
            <div className="coordinator-row-list">
              {viewModel.actionRows.map((action) => (
                <div className={`coordinator-action ${action.severity}`} key={action.id}>
                  <span>{action.label}</span>
                  <small>{action.severity}</small>
                </div>
              ))}
            </div>
          )}
        </section>
      </div>
    </section>
  );
}
