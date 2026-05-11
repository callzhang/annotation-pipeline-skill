import { useEffect, useMemo, useState } from "react";
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
import { useUrlState, type UrlState } from "./url_state";

const emptySnapshot: KanbanSnapshot = { project_id: null, columns: [] };
type ViewMode = "kanban" | "runtime" | "readiness" | "outbox" | "providers" | "coordinator" | "config" | "events" | "documents";

const urlDefaults: UrlState = { view: "kanban", store: null, project: null, task: null };

function findTaskInSnapshot(snapshot: KanbanSnapshot, taskId: string | null): TaskCard | null {
  if (!taskId) return null;
  for (const column of snapshot.columns) {
    for (const card of column.cards) {
      if (card.task_id === taskId) return card;
    }
  }
  return null;
}

export default function App() {
  const [snapshot, setSnapshot] = useState<KanbanSnapshot>(emptySnapshot);
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [stores, setStores] = useState<StoreInfo[]>([]);
  const [urlState, { setView, setStore, setProject, setTask }] = useUrlState(urlDefaults);
  const selectedStoreKey = urlState.store;
  const selectedProjectId = urlState.project;
  const selectedTaskId = urlState.task;
  const viewMode = urlState.view as ViewMode;

  const [selectedDetail, setSelectedDetail] = useState<TaskDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailSaving, setDetailSaving] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Derive the full TaskCard from the kanban snapshot. URL only holds task_id.
  const selectedTask = useMemo<TaskCard | null>(
    () => findTaskInSnapshot(snapshot, selectedTaskId),
    [snapshot, selectedTaskId],
  );

  useEffect(() => {
    fetchStores()
      .then((snap) => {
        setStores(snap.stores);
        if (snap.stores.length > 0) {
          const valid = snap.stores.some((s) => s.key === selectedStoreKey);
          if (!valid) {
            setStore(snap.stores[0].key);
          }
        }
      })
      .catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
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
        // Auto-select the only project when no explicit choice is made, so
        // single-project workspaces don't strand users on the "Select one project" empty state.
        if (!selectedProjectId && projectSnapshot.projects.length === 1) {
          setProject(projectSnapshot.projects[0].project_id);
        }
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
    if (!selectedTaskId) {
      setSelectedDetail(null);
      setDetailError(null);
      setDetailLoading(false);
      return;
    }

    let active = true;
    setDetailLoading(true);
    setDetailError(null);
    fetchTaskDetail(selectedTaskId, selectedStoreKey)
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
  }, [selectedTaskId, selectedStoreKey]);

  function handleStoreChange(key: string) {
    setStore(key || null);
    setTask(null);
  }

  function handleProjectChange(value: string) {
    setProject(value || null);
    setTask(null);
  }

  async function submitFeedbackDiscussion(payload: Record<string, unknown>) {
    if (!selectedTaskId) return;
    setDetailSaving(true);
    setDetailError(null);
    try {
      const detail = await postFeedbackDiscussion(selectedTaskId, payload, selectedStoreKey);
      setSelectedDetail(detail);
      setSnapshot(await fetchKanbanSnapshot(selectedProjectId, selectedStoreKey));
    } catch (reason: unknown) {
      setDetailError(reason instanceof Error ? reason.message : "Unable to save feedback discussion");
    } finally {
      setDetailSaving(false);
    }
  }

  async function submitHumanReviewDecision(payload: Record<string, unknown>) {
    if (!selectedTaskId) return;
    setDetailSaving(true);
    setDetailError(null);
    try {
      const detail = await postHumanReviewDecision(selectedTaskId, payload, selectedStoreKey);
      setSelectedDetail(detail);
      setSnapshot(await fetchKanbanSnapshot(selectedProjectId, selectedStoreKey));
    } catch (reason: unknown) {
      setDetailError(reason instanceof Error ? reason.message : "Unable to save Human Review decision");
    } finally {
      setDetailSaving(false);
    }
  }

  async function submitTaskQcPolicy(payload: Record<string, unknown>) {
    if (!selectedTaskId) return;
    setDetailSaving(true);
    setDetailError(null);
    try {
      const detail = await saveTaskQcPolicy(selectedTaskId, payload, selectedStoreKey);
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
              onChange={(event) => handleProjectChange(event.target.value)}
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
        <button className={viewMode === "kanban" ? "view-tab selected" : "view-tab"} type="button" onClick={() => setView("kanban")}>
          Kanban
        </button>
        <button className={viewMode === "runtime" ? "view-tab selected" : "view-tab"} type="button" onClick={() => setView("runtime")}>
          Runtime
        </button>
        <button className={viewMode === "readiness" ? "view-tab selected" : "view-tab"} type="button" onClick={() => setView("readiness")}>
          Readiness
        </button>
        <button className={viewMode === "outbox" ? "view-tab selected" : "view-tab"} type="button" onClick={() => setView("outbox")}>
          Outbox
        </button>
        <button className={viewMode === "providers" ? "view-tab selected" : "view-tab"} type="button" onClick={() => setView("providers")}>
          Providers
        </button>
        <button
          className={viewMode === "coordinator" ? "view-tab selected" : "view-tab"}
          type="button"
          onClick={() => setView("coordinator")}
        >
          Coordinator
        </button>
        <button className={viewMode === "config" ? "view-tab selected" : "view-tab"} type="button" onClick={() => setView("config")}>
          Configuration
        </button>
        <button className={viewMode === "events" ? "view-tab selected" : "view-tab"} type="button" onClick={() => setView("events")}>
          Event Log
        </button>
        <button className={viewMode === "documents" ? "view-tab selected" : "view-tab"} type="button" onClick={() => setView("documents")}>
          Documents
        </button>
      </nav>

      {error ? <div className="notice">{error}</div> : null}
      {viewMode === "kanban" ? (
        <KanbanBoard
          snapshot={snapshot}
          selectedTaskId={selectedTaskId}
          onSelectTask={(card) => setTask(card.task_id)}
        />
      ) : null}
      {viewMode === "runtime" ? <RuntimePanel storeKey={selectedStoreKey} /> : null}
      {viewMode === "readiness" ? <ReadinessPanel projectId={selectedProjectId} storeKey={selectedStoreKey} /> : null}
      {viewMode === "outbox" ? <OutboxPanel projectId={selectedProjectId} storeKey={selectedStoreKey} /> : null}
      {viewMode === "providers" ? <ProvidersPanel /> : null}
      {viewMode === "coordinator" ? <CoordinatorPanel projectId={selectedProjectId} storeKey={selectedStoreKey} /> : null}
      {viewMode === "config" ? <ConfigPanel storeKey={selectedStoreKey} /> : null}
      {viewMode === "events" ? <EventLogPanel projectId={selectedProjectId} storeKey={selectedStoreKey} /> : null}
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
        onClose={() => setTask(null)}
      />
    </main>
  );
}
