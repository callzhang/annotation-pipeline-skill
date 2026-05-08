import { useEffect, useState } from "react";
import {
  fetchKanbanSnapshot,
  fetchProjects,
  fetchStores,
  fetchTaskDetail,
  postFeedbackDiscussion,
  postHumanReviewDecision,
  saveTaskQcPolicy,
} from "./api";
import { ConfigPanel } from "./components/ConfigPanel";
import { CoordinatorPanel } from "./components/CoordinatorPanel";
import { DocumentsPanel } from "./components/DocumentsPanel";
import { EventLogPanel } from "./components/EventLogPanel";
import { KanbanBoard } from "./components/KanbanBoard";
import { OutboxPanel } from "./components/OutboxPanel";
import { ProvidersPanel } from "./components/ProvidersPanel";
import { ReadinessPanel } from "./components/ReadinessPanel";
import { RuntimePanel } from "./components/RuntimePanel";
import { TaskDrawer } from "./components/TaskDrawer";
import { countCards } from "./kanban";
import type { KanbanSnapshot, ProjectSummary, StoreInfo, TaskCard, TaskDetail } from "./types";

const emptySnapshot: KanbanSnapshot = { project_id: null, columns: [] };
type ViewMode = "kanban" | "runtime" | "readiness" | "outbox" | "providers" | "coordinator" | "config" | "events" | "documents";

export default function App() {
  const [snapshot, setSnapshot] = useState<KanbanSnapshot>(emptySnapshot);
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [stores, setStores] = useState<StoreInfo[]>([]);
  const [selectedStoreKey, setSelectedStoreKey] = useState<string | null>(() => localStorage.getItem("storeKey"));
  const [selectedProjectId, setSelectedProjectId] = useState<string | null>(null);
  const [selectedTask, setSelectedTask] = useState<TaskCard | null>(null);
  const [selectedDetail, setSelectedDetail] = useState<TaskDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailSaving, setDetailSaving] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [viewMode, setViewMode] = useState<ViewMode>("kanban");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchStores()
      .then((snap) => {
        setStores(snap.stores);
        if (snap.stores.length > 0) {
          setSelectedStoreKey((prev) => {
            const valid = snap.stores.some((s) => s.key === prev);
            return valid ? prev : snap.stores[0].key;
          });
        }
      })
      .catch(() => {});
  }, []);

  useEffect(() => {
    let active = true;
    setLoading(true);

    Promise.all([fetchProjects(selectedStoreKey), fetchKanbanSnapshot(selectedProjectId, selectedStoreKey)])
      .then(([projectSnapshot, nextSnapshot]) => {
        if (!active) return;
        setProjects(projectSnapshot.projects);
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
  }, [selectedProjectId, selectedStoreKey]);

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
    fetchTaskDetail(selectedTask.task_id, selectedStoreKey)
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
  }, [selectedTask, selectedStoreKey]);

  function handleStoreChange(key: string) {
    setSelectedStoreKey(key || null);
    localStorage.setItem("storeKey", key);
    setSelectedTask(null);
  }

  async function submitFeedbackDiscussion(payload: Record<string, unknown>) {
    if (!selectedTask) return;
    setDetailSaving(true);
    setDetailError(null);
    try {
      const detail = await postFeedbackDiscussion(selectedTask.task_id, payload, selectedStoreKey);
      setSelectedDetail(detail);
      setSnapshot(await fetchKanbanSnapshot(selectedProjectId, selectedStoreKey));
    } catch (reason: unknown) {
      setDetailError(reason instanceof Error ? reason.message : "Unable to save feedback discussion");
    } finally {
      setDetailSaving(false);
    }
  }

  async function submitHumanReviewDecision(payload: Record<string, unknown>) {
    if (!selectedTask) return;
    setDetailSaving(true);
    setDetailError(null);
    try {
      const detail = await postHumanReviewDecision(selectedTask.task_id, payload, selectedStoreKey);
      setSelectedDetail(detail);
      setSnapshot(await fetchKanbanSnapshot(selectedProjectId, selectedStoreKey));
    } catch (reason: unknown) {
      setDetailError(reason instanceof Error ? reason.message : "Unable to save Human Review decision");
    } finally {
      setDetailSaving(false);
    }
  }

  async function submitTaskQcPolicy(payload: Record<string, unknown>) {
    if (!selectedTask) return;
    setDetailSaving(true);
    setDetailError(null);
    try {
      const detail = await saveTaskQcPolicy(selectedTask.task_id, payload, selectedStoreKey);
      setSelectedDetail(detail);
      setSnapshot(await fetchKanbanSnapshot(selectedProjectId, selectedStoreKey));
    } catch (reason: unknown) {
      setDetailError(reason instanceof Error ? reason.message : "Unable to save QC policy");
    } finally {
      setDetailSaving(false);
    }
  }

  return (
    <main className="app-shell">
      <header className="topbar">
        <div>
          <h1>Annotation Pipeline</h1>
          <p>{countCards(snapshot)} tasks across operational stages</p>
        </div>
        <div className="topbar-actions">
          {stores.length > 0 ? (
            <label className="project-selector">
              <span>Workspace</span>
              <select
                value={selectedStoreKey ?? ""}
                onChange={(event) => handleStoreChange(event.target.value)}
              >
                {stores.map((s) => (
                  <option key={s.key} value={s.key}>
                    {s.name}
                  </option>
                ))}
              </select>
            </label>
          ) : null}
          <label className="project-selector">
            <span>Project</span>
            <select
              value={selectedProjectId ?? ""}
              onChange={(event) => {
                setSelectedProjectId(event.target.value || null);
                setSelectedTask(null);
              }}
            >
              <option value="">All projects</option>
              {projects.map((project) => (
                <option key={project.project_id} value={project.project_id}>
                  {project.project_id} ({project.task_count})
                </option>
              ))}
            </select>
          </label>
          <div className="status-pill">{loading ? "Loading" : error ? "API error" : "Live snapshot"}</div>
        </div>
      </header>

      <nav className="view-tabs" aria-label="Dashboard views">
        <button className={viewMode === "kanban" ? "view-tab selected" : "view-tab"} type="button" onClick={() => setViewMode("kanban")}>
          Kanban
        </button>
        <button className={viewMode === "runtime" ? "view-tab selected" : "view-tab"} type="button" onClick={() => setViewMode("runtime")}>
          Runtime
        </button>
        <button className={viewMode === "readiness" ? "view-tab selected" : "view-tab"} type="button" onClick={() => setViewMode("readiness")}>
          Readiness
        </button>
        <button className={viewMode === "outbox" ? "view-tab selected" : "view-tab"} type="button" onClick={() => setViewMode("outbox")}>
          Outbox
        </button>
        <button className={viewMode === "providers" ? "view-tab selected" : "view-tab"} type="button" onClick={() => setViewMode("providers")}>
          Providers
        </button>
        <button
          className={viewMode === "coordinator" ? "view-tab selected" : "view-tab"}
          type="button"
          onClick={() => setViewMode("coordinator")}
        >
          Coordinator
        </button>
        <button className={viewMode === "config" ? "view-tab selected" : "view-tab"} type="button" onClick={() => setViewMode("config")}>
          Configuration
        </button>
        <button className={viewMode === "events" ? "view-tab selected" : "view-tab"} type="button" onClick={() => setViewMode("events")}>
          Event Log
        </button>
        <button className={viewMode === "documents" ? "view-tab selected" : "view-tab"} type="button" onClick={() => setViewMode("documents")}>
          Documents
        </button>
      </nav>

      {error ? <div className="notice">{error}</div> : null}
      {viewMode === "kanban" ? (
        <KanbanBoard snapshot={snapshot} selectedTaskId={selectedTask?.task_id ?? null} onSelectTask={setSelectedTask} />
      ) : null}
      {viewMode === "runtime" ? <RuntimePanel /> : null}
      {viewMode === "readiness" ? <ReadinessPanel projectId={selectedProjectId} /> : null}
      {viewMode === "outbox" ? <OutboxPanel projectId={selectedProjectId} /> : null}
      {viewMode === "providers" ? <ProvidersPanel /> : null}
      {viewMode === "coordinator" ? <CoordinatorPanel projectId={selectedProjectId} /> : null}
      {viewMode === "config" ? <ConfigPanel /> : null}
      {viewMode === "events" ? <EventLogPanel projectId={selectedProjectId} /> : null}
      {viewMode === "documents" ? <DocumentsPanel storeKey={selectedStoreKey} /> : null}
      <TaskDrawer
        task={selectedTask}
        detail={selectedDetail}
        loading={detailLoading}
        saving={detailSaving}
        error={detailError}
        onSubmitFeedbackDiscussion={submitFeedbackDiscussion}
        onSubmitHumanReviewDecision={submitHumanReviewDecision}
        onSaveQcPolicy={submitTaskQcPolicy}
        onClose={() => setSelectedTask(null)}
      />
    </main>
  );
}
