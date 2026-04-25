import { useEffect, useState } from "react";
import { fetchKanbanSnapshot, fetchTaskDetail } from "./api";
import { ConfigPanel } from "./components/ConfigPanel";
import { EventLogPanel } from "./components/EventLogPanel";
import { KanbanBoard } from "./components/KanbanBoard";
import { TaskDrawer } from "./components/TaskDrawer";
import { countCards } from "./kanban";
import type { KanbanSnapshot, TaskCard, TaskDetail } from "./types";

const emptySnapshot: KanbanSnapshot = { columns: [] };
type ViewMode = "kanban" | "config" | "events";

export default function App() {
  const [snapshot, setSnapshot] = useState<KanbanSnapshot>(emptySnapshot);
  const [selectedTask, setSelectedTask] = useState<TaskCard | null>(null);
  const [selectedDetail, setSelectedDetail] = useState<TaskDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [viewMode, setViewMode] = useState<ViewMode>("kanban");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;

    fetchKanbanSnapshot()
      .then((nextSnapshot) => {
        if (!active) return;
        setSnapshot(nextSnapshot);
        setError(null);
      })
      .catch((reason: unknown) => {
        if (!active) return;
        setError(reason instanceof Error ? reason.message : "Unable to load dashboard data");
      })
      .finally(() => {
        if (active) setLoading(false);
      });

    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    if (!selectedTask) {
      setSelectedDetail(null);
      setDetailError(null);
      setDetailLoading(false);
      return;
    }

    let active = true;
    setDetailLoading(true);
    setDetailError(null);
    fetchTaskDetail(selectedTask.task_id)
      .then((detail) => {
        if (!active) return;
        setSelectedDetail(detail);
      })
      .catch((reason: unknown) => {
        if (!active) return;
        setSelectedDetail(null);
        setDetailError(reason instanceof Error ? reason.message : "Unable to load task detail");
      })
      .finally(() => {
        if (active) setDetailLoading(false);
      });

    return () => {
      active = false;
    };
  }, [selectedTask]);

  return (
    <main className="app-shell">
      <header className="topbar">
        <div>
          <h1>Annotation Pipeline</h1>
          <p>{countCards(snapshot)} tasks across operational stages</p>
        </div>
        <div className="status-pill">{loading ? "Loading" : error ? "API error" : "Live snapshot"}</div>
      </header>

      <nav className="view-tabs" aria-label="Dashboard views">
        <button className={viewMode === "kanban" ? "view-tab selected" : "view-tab"} type="button" onClick={() => setViewMode("kanban")}>
          Kanban
        </button>
        <button className={viewMode === "config" ? "view-tab selected" : "view-tab"} type="button" onClick={() => setViewMode("config")}>
          Configuration
        </button>
        <button className={viewMode === "events" ? "view-tab selected" : "view-tab"} type="button" onClick={() => setViewMode("events")}>
          Event Log
        </button>
      </nav>

      {error ? <div className="notice">{error}</div> : null}
      {viewMode === "kanban" ? (
        <KanbanBoard snapshot={snapshot} selectedTaskId={selectedTask?.task_id ?? null} onSelectTask={setSelectedTask} />
      ) : null}
      {viewMode === "config" ? <ConfigPanel /> : null}
      {viewMode === "events" ? <EventLogPanel /> : null}
      <TaskDrawer
        task={selectedTask}
        detail={selectedDetail}
        loading={detailLoading}
        error={detailError}
        onClose={() => setSelectedTask(null)}
      />
    </main>
  );
}
