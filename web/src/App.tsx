import { useEffect, useState } from "react";
import { fetchKanbanSnapshot } from "./api";
import { KanbanBoard } from "./components/KanbanBoard";
import { TaskDrawer } from "./components/TaskDrawer";
import { countCards } from "./kanban";
import type { KanbanSnapshot, TaskCard } from "./types";

const emptySnapshot: KanbanSnapshot = { columns: [] };

export default function App() {
  const [snapshot, setSnapshot] = useState<KanbanSnapshot>(emptySnapshot);
  const [selectedTask, setSelectedTask] = useState<TaskCard | null>(null);
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

  return (
    <main className="app-shell">
      <header className="topbar">
        <div>
          <h1>Annotation Pipeline</h1>
          <p>{countCards(snapshot)} tasks across operational stages</p>
        </div>
        <div className="status-pill">{loading ? "Loading" : error ? "API error" : "Live snapshot"}</div>
      </header>

      {error ? <div className="notice">{error}</div> : null}
      <KanbanBoard snapshot={snapshot} selectedTaskId={selectedTask?.task_id ?? null} onSelectTask={setSelectedTask} />
      <TaskDrawer task={selectedTask} onClose={() => setSelectedTask(null)} />
    </main>
  );
}
