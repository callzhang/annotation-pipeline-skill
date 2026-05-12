import { useEffect, useState } from "react";
import { fetchRuntimeCycles, fetchRuntimeMonitor, fetchRuntimeSnapshot, runRuntimeOnce } from "../api";
import { formatRuntimeDate, monitorLabel, orderedQueueCounts, runtimeHealthLabel } from "../runtime";
import type { RuntimeCycleStats, RuntimeMonitorReport, RuntimeSnapshot } from "../types";

interface RuntimePanelProps {
  storeKey: string | null;
}

export function RuntimePanel({ storeKey }: RuntimePanelProps) {
  const [snapshot, setSnapshot] = useState<RuntimeSnapshot | null>(null);
  const [cycles, setCycles] = useState<RuntimeCycleStats[]>([]);
  const [monitor, setMonitor] = useState<RuntimeMonitorReport | null>(null);
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function loadRuntime() {
    const [nextSnapshot, nextCycles, nextMonitor] = await Promise.all([
      fetchRuntimeSnapshot(storeKey),
      fetchRuntimeCycles(storeKey),
      fetchRuntimeMonitor(storeKey),
    ]);
    setSnapshot(nextSnapshot);
    setCycles(nextCycles.cycles);
    setMonitor(nextMonitor);
  }

  useEffect(() => {
    let active = true;
    setLoading(true);
    loadRuntime()
      .then(() => {
        if (active) setError(null);
      })
      .catch((reason: unknown) => {
        if (active) setError(reason instanceof Error ? reason.message : "Unable to load runtime data");
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [storeKey]);

  async function runOnce() {
    setRunning(true);
    setError(null);
    try {
      const result = await runRuntimeOnce(storeKey);
      setSnapshot(result.snapshot);
      const [nextCycles, nextMonitor] = await Promise.all([fetchRuntimeCycles(storeKey), fetchRuntimeMonitor(storeKey)]);
      setCycles(nextCycles.cycles);
      setMonitor(nextMonitor);
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : "Unable to run runtime cycle");
    } finally {
      setRunning(false);
    }
  }

  if (loading) return <section className="runtime-panel">Loading runtime...</section>;
  if (!snapshot) return <section className="runtime-panel notice compact">{error ?? "Runtime unavailable"}</section>;

  return (
    <section className="runtime-panel">
      {error ? <div className="notice compact">{error}</div> : null}
      <div className="runtime-header">
        <div>
          <h2>Runtime</h2>
          <p>
            {runtimeHealthLabel(snapshot)} · Monitor {monitorLabel(monitor)}
          </p>
        </div>
        <button className="primary-button" type="button" disabled={running} onClick={runOnce}>
          {running ? "Running..." : "Run once"}
        </button>
      </div>

      <div className="runtime-grid">
        <div className="runtime-card">
          <h3>Status</h3>
          <dl className="runtime-facts">
            <div>
              <dt>Heartbeat</dt>
              <dd>{formatRuntimeDate(snapshot.runtime_status.heartbeat_at)}</dd>
            </div>
            <div>
              <dt>Age</dt>
              <dd>{snapshot.runtime_status.heartbeat_age_seconds ?? "missing"}</dd>
            </div>
            <div>
              <dt>Active</dt>
              <dd>{snapshot.runtime_status.active ? "yes" : "no"}</dd>
            </div>
            <div>
              <dt>Generated</dt>
              <dd>{formatRuntimeDate(snapshot.generated_at)}</dd>
            </div>
          </dl>
        </div>

        <div className="runtime-card">
          <h3>Capacity</h3>
          <dl className="runtime-facts">
            <div>
              <dt>Active</dt>
              <dd>{snapshot.capacity.active_count}</dd>
            </div>
            <div>
              <dt>Available</dt>
              <dd>{snapshot.capacity.available_slots}</dd>
            </div>
            <div>
              <dt>Max concurrent</dt>
              <dd>{snapshot.capacity.max_concurrent_tasks}</dd>
            </div>
          </dl>
        </div>

        <div className="runtime-card">
          <h3>Monitor</h3>
          {monitor?.ok ? <p className="runtime-muted">No runtime failures detected.</p> : null}
          {!monitor?.ok ? (
            <div className="runtime-list">
              {(monitor?.failures ?? []).map((failure) => (
                <div key={failure}>
                  <strong>{failure}</strong>
                  <pre>{JSON.stringify(monitor?.details[failure] ?? {}, null, 2)}</pre>
                </div>
              ))}
            </div>
          ) : null}
        </div>
      </div>

      <div className="runtime-grid secondary">
        <div className="runtime-card">
          <h3>Queue Counts</h3>
          <div className="runtime-counts">
            {orderedQueueCounts(snapshot).map((item) => (
              <div key={item.key}>
                <span>{item.key}</span>
                <strong>{item.value}</strong>
              </div>
            ))}
          </div>
        </div>

        <RuntimeList title="Active Runs" values={snapshot.active_runs.map((run) => `${run.task_id} · ${run.provider_target}`)} empty="No active runs" />
        <RuntimeList title="Stale Tasks" values={snapshot.stale_tasks} empty="No stale tasks" />
        <RuntimeList title="Due Retries" values={snapshot.due_retries} empty="No due retries" />
      </div>

      <div className="runtime-card">
        <h3>Recent Cycles</h3>
        <table className="runtime-table">
          <thead>
            <tr>
              <th>Cycle</th>
              <th>Started</th>
              <th>Accepted</th>
              <th>Failed</th>
              <th>Capacity</th>
            </tr>
          </thead>
          <tbody>
            {cycles.slice(-8).reverse().map((cycle) => (
              <tr key={cycle.cycle_id}>
                <td>{cycle.cycle_id}</td>
                <td>{cycle.started}</td>
                <td>{cycle.accepted}</td>
                <td>{cycle.failed}</td>
                <td>{cycle.capacity_available}</td>
              </tr>
            ))}
            {cycles.length === 0 ? (
              <tr>
                <td colSpan={5}>No cycles recorded</td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function RuntimeList({ title, values, empty }: { title: string; values: string[]; empty: string }) {
  return (
    <div className="runtime-card">
      <h3>{title}</h3>
      {values.length === 0 ? <p className="runtime-muted">{empty}</p> : null}
      {values.length > 0 ? (
        <ul className="runtime-list compact-list">
          {values.map((value) => (
            <li key={value}>{value}</li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
