import { useMemo, useState } from "react";
import { cardSubtitle } from "../kanban";
import type { TaskCard, TaskDetail } from "../types";
import type { ReactNode } from "react";

interface TaskDrawerProps {
  task: TaskCard | null;
  detail: TaskDetail | null;
  loading: boolean;
  saving: boolean;
  error: string | null;
  onSubmitFeedbackDiscussion: (payload: Record<string, unknown>) => Promise<void>;
  onClose: () => void;
}

export function TaskDrawer({
  task,
  detail,
  loading,
  saving,
  error,
  onSubmitFeedbackDiscussion,
  onClose,
}: TaskDrawerProps) {
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

          <DetailSection title={`Feedback Agreement (${detail.feedback.length})`}>
            {detail.feedback.length === 0 ? (
              <p className="empty-detail">No QC or Human Review feedback recorded.</p>
            ) : (
              <>
                <ConsensusSummary detail={detail} />
                {detail.feedback.map((item) => (
                  <FeedbackAgreementCard
                    key={String(item.feedback_id)}
                    feedback={item}
                    discussions={detail.feedback_discussions.filter(
                      (entry) => entry.feedback_id === item.feedback_id,
                    )}
                    saving={saving}
                    onSubmit={onSubmitFeedbackDiscussion}
                  />
                ))}
              </>
            )}
          </DetailSection>
        </div>
      ) : null}
    </aside>
  );
}

function ConsensusSummary({ detail }: { detail: TaskDetail }) {
  const consensus = detail.feedback_consensus;
  return (
    <div className={consensus.can_accept_by_consensus ? "consensus-box accepted" : "consensus-box"}>
      <strong>{consensus.consensus_feedback}/{consensus.total_feedback} feedback items agreed</strong>
      <span>
        {consensus.can_accept_by_consensus
          ? "Annotator and QC have reached agreement; QC can pass."
          : "Open feedback still needs annotator/QC agreement."}
      </span>
    </div>
  );
}

function FeedbackAgreementCard({
  feedback,
  discussions,
  saving,
  onSubmit,
}: {
  feedback: Record<string, unknown>;
  discussions: Array<Record<string, unknown>>;
  saving: boolean;
  onSubmit: (payload: Record<string, unknown>) => Promise<void>;
}) {
  const [role, setRole] = useState("annotator");
  const [stance, setStance] = useState("partial_agree");
  const [message, setMessage] = useState("");
  const [consensus, setConsensus] = useState(false);
  const consensusReached = useMemo(() => discussions.some((entry) => entry.consensus === true), [discussions]);

  async function submit() {
    await onSubmit({
      feedback_id: feedback.feedback_id,
      role,
      stance,
      message,
      consensus,
      created_by: role,
    });
    setMessage("");
    setConsensus(false);
  }

  return (
    <div className="feedback-card">
      <div className="feedback-card-header">
        <div>
          <strong>{String(feedback.severity)} · {String(feedback.category)}</strong>
          <p>{String(feedback.message)}</p>
        </div>
        <span className={consensusReached ? "agreement-pill accepted" : "agreement-pill"}>
          {consensusReached ? "Agreed" : "Open"}
        </span>
      </div>

      {discussions.length > 0 ? (
        <div className="discussion-stack">
          {discussions.map((entry) => (
            <TimelineItem
              key={String(entry.entry_id)}
              title={`${String(entry.role)} · ${String(entry.stance)}${entry.consensus ? " · consensus" : ""}`}
              meta={String(entry.message)}
              value={entry}
            />
          ))}
        </div>
      ) : (
        <p className="empty-detail">No annotator/QC discussion yet.</p>
      )}

      <div className="agreement-form">
        <select value={role} onChange={(event) => setRole(event.target.value)}>
          <option value="annotator">Annotator</option>
          <option value="qc">QC</option>
          <option value="coordinator">Coordinator</option>
        </select>
        <select value={stance} onChange={(event) => setStance(event.target.value)}>
          <option value="agree">Agree</option>
          <option value="partial_agree">Partially agree</option>
          <option value="disagree">Disagree</option>
          <option value="proposal">Proposal</option>
        </select>
        <textarea
          placeholder="Record the annotator/QC opinion, agreed points, or final resolution."
          value={message}
          onChange={(event) => setMessage(event.target.value)}
        />
        <label className="checkbox-row">
          <input checked={consensus} type="checkbox" onChange={(event) => setConsensus(event.target.checked)} />
          Mark as consensus between annotator and QC
        </label>
        <button className="primary-button" type="button" disabled={saving || !message.trim()} onClick={submit}>
          {saving ? "Saving" : "Add Discussion"}
        </button>
      </div>
    </div>
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
