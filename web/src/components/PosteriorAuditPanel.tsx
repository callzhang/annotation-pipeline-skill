import React, { useState } from "react";
import type { PosteriorAudit, TaskDeviation, ContestedSpan } from "../types";

export type PosteriorAuditPanelProps = {
  projectId: string | null;
  initialPayload?: PosteriorAudit | null;
  onSendToHr: (taskId: string) => Promise<void> | void;
  onDeclareCanonical: (span: string, entityType: string) => Promise<void> | void;
};

export function PosteriorAuditPanel({
  projectId,
  initialPayload = null,
  onSendToHr,
  onDeclareCanonical,
}: PosteriorAuditPanelProps): React.ReactElement {
  const [payload, setPayload] = useState<PosteriorAudit | null>(initialPayload);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleCheck() {
    if (!projectId) {
      setError("Select a project first.");
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const r = await fetch(
        `/api/posterior-audit?project=${encodeURIComponent(projectId)}`,
      );
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      setPayload(await r.json());
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }

  return (
    <section className="runtime-panel posterior-audit-panel" aria-label="Posterior audit">
      <div className="runtime-header">
        <div>
          <h2>Posterior Audit</h2>
          <p>Scan accepted tasks against current project statistics.</p>
        </div>
        <button
          className="primary-button"
          type="button"
          onClick={handleCheck}
          disabled={loading || !projectId}
        >
          {loading ? "Checking…" : "Check"}
        </button>
      </div>
      {error ? <div className="notice compact">{error}</div> : null}
      {payload === null && !loading ? (
        <p className="runtime-muted">
          Click <strong>Check</strong> to scan accepted tasks against project
          statistics.
        </p>
      ) : null}
      {payload &&
       payload.task_deviations.length === 0 &&
       payload.contested_spans.length === 0 ? (
        <p className="runtime-muted">
          All accepted tasks agree with current statistics; no contested spans.
        </p>
      ) : null}
      {payload && payload.task_deviations.length > 0 ? (
        <div className="runtime-card">
          <h3>Task-level deviations ({payload.task_deviations.length})</h3>
          <table>
            <thead>
              <tr>
                <th>Task</th>
                <th>Span</th>
                <th>Current type</th>
                <th>Prior dominant</th>
                <th>Prior distribution</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {payload.task_deviations.map((d: TaskDeviation) => (
                <tr key={`${d.task_id}-${d.row_index}-${d.span}`}>
                  <td>{d.task_id}</td>
                  <td>{d.span}</td>
                  <td>{d.current_type}</td>
                  <td>
                    {d.prior_dominant_type} (
                    {d.prior_total > 0
                      ? Math.round((d.prior_distribution[d.prior_dominant_type] / d.prior_total) * 100)
                      : 0}
                    %)
                  </td>
                  <td>{JSON.stringify(d.prior_distribution)}</td>
                  <td>
                    <button type="button" onClick={() => onSendToHr(d.task_id)}>
                      Send to HR
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
      {payload && payload.contested_spans.length > 0 ? (
        <div className="runtime-card">
          <h3>Contested spans ({payload.contested_spans.length})</h3>
          <table>
            <thead>
              <tr>
                <th>Span</th>
                <th>Distribution</th>
                <th>Top / runner-up</th>
                <th>Declare canonical</th>
              </tr>
            </thead>
            <tbody>
              {payload.contested_spans.map((c: ContestedSpan) => (
                <tr key={c.span}>
                  <td>{c.span}</td>
                  <td>{JSON.stringify(c.prior_distribution)}</td>
                  <td>
                    {Math.round(c.top_share * 100)}% / {Math.round(c.runner_up_share * 100)}%
                  </td>
                  <td>
                    <ContestedSpanForm
                      span={c.span}
                      types={Object.keys(c.prior_distribution)}
                      onSubmit={onDeclareCanonical}
                    />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </section>
  );
}

function ContestedSpanForm({
  span,
  types,
  onSubmit,
}: {
  span: string;
  types: string[];
  onSubmit: (span: string, entityType: string) => Promise<void> | void;
}): React.ReactElement {
  const [selected, setSelected] = useState(types[0] ?? "");
  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        if (selected) onSubmit(span, selected);
      }}
    >
      <select value={selected} onChange={(e) => setSelected(e.target.value)}>
        {types.map((t) => (
          <option key={t} value={t}>
            {t}
          </option>
        ))}
      </select>
      <button type="submit">Declare</button>
    </form>
  );
}
