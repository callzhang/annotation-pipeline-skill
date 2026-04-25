import { cardSubtitle } from "../kanban";
import type { TaskCard, TaskDetail } from "../types";
import type { ReactNode } from "react";

interface TaskDrawerProps {
  task: TaskCard | null;
  detail: TaskDetail | null;
  loading: boolean;
  error: string | null;
  onClose: () => void;
}

export function TaskDrawer({ task, detail, loading, error, onClose }: TaskDrawerProps) {
  if (!task) return null;

  const annotationArtifacts = detail?.artifacts.filter((artifact) => artifact.kind === "annotation_result") ?? [];

  return (
    <aside className="task-drawer" aria-label="Task detail">
      <div className="drawer-header">
        <div>
          <h2>{task.task_id}</h2>
          <p>{cardSubtitle(task)}</p>
        </div>
        <button className="icon-button" type="button" aria-label="Close task detail" onClick={onClose}>
          ×
        </button>
      </div>

      <dl className="detail-grid">
        <div>
          <dt>Status</dt>
          <dd>{task.status}</dd>
        </div>
        <div>
          <dt>Annotator</dt>
          <dd>{task.selected_annotator_id ?? "unassigned"}</dd>
        </div>
        <div>
          <dt>Latest Attempt</dt>
          <dd>{task.latest_attempt_status ?? "none"}</dd>
        </div>
        <div>
          <dt>Feedback</dt>
          <dd>{task.feedback_count}</dd>
        </div>
        <div>
          <dt>Retry</dt>
          <dd>{task.retry_pending ? "pending" : "none"}</dd>
        </div>
        <div>
          <dt>External Sync</dt>
          <dd>{task.external_sync_pending ? "pending" : "clear"}</dd>
        </div>
      </dl>

      {loading ? <div className="drawer-state">Loading task detail</div> : null}
      {error ? <div className="drawer-error">{error}</div> : null}

      {detail ? (
        <div className="detail-sections">
          <DetailSection title="Raw Source">
            <JsonBlock value={detail.task.source_ref} />
          </DetailSection>

          <DetailSection title="Annotation Content">
            {annotationArtifacts.length === 0 ? (
              <p className="empty-detail">No annotation artifacts recorded.</p>
            ) : (
              annotationArtifacts.map((artifact) => (
                <div className="artifact-panel" key={artifact.artifact_id}>
                  <div className="artifact-title">
                    <span>{artifact.kind}</span>
                    <span>{artifact.metadata.provider ? String(artifact.metadata.provider) : artifact.content_type}</span>
                  </div>
                  <JsonBlock value={artifact.payload} />
                </div>
              ))
            )}
          </DetailSection>

          <DetailSection title={`Attempts (${detail.attempts.length})`}>
            {detail.attempts.map((attempt) => (
              <TimelineItem
                key={String(attempt.attempt_id)}
                title={`#${String(attempt.index)} ${String(attempt.stage)} · ${String(attempt.status)}`}
                meta={`${String(attempt.provider_id ?? "provider unknown")} · ${String(attempt.model ?? "model unknown")}`}
                value={attempt}
              />
            ))}
          </DetailSection>

          <DetailSection title={`Round Changes (${detail.events.length})`}>
            {detail.events.map((event) => (
              <TimelineItem
                key={String(event.event_id)}
                title={`${String(event.previous_status)} → ${String(event.next_status)}`}
                meta={`${String(event.stage)} · ${String(event.reason)}`}
                value={event}
              />
            ))}
          </DetailSection>

          <DetailSection title={`Feedback (${detail.feedback.length})`}>
            {detail.feedback.length === 0 ? (
              <p className="empty-detail">No QC or Human Review feedback recorded.</p>
            ) : (
              detail.feedback.map((item) => (
                <TimelineItem
                  key={String(item.feedback_id)}
                  title={`${String(item.severity)} · ${String(item.category)}`}
                  meta={String(item.message)}
                  value={item}
                />
              ))
            )}
          </DetailSection>
        </div>
      ) : null}
    </aside>
  );
}

function DetailSection({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="detail-section">
      <h3>{title}</h3>
      {children}
    </section>
  );
}

function TimelineItem({ title, meta, value }: { title: string; meta: string; value: unknown }) {
  return (
    <details className="timeline-item">
      <summary>
        <span>{title}</span>
        <small>{meta}</small>
      </summary>
      <JsonBlock value={value} />
    </details>
  );
}

function JsonBlock({ value }: { value: unknown }) {
  return <pre className="json-block">{JSON.stringify(value, null, 2)}</pre>;
}
