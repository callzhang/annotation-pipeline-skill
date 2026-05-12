import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { cardSubtitle } from "../kanban";
import { previewArtifacts, previewBoxes, previewImageSource, previewTitle } from "../preview";
import {
  DRAWER_DEFAULT_WIDTH,
  clampDrawerWidth,
  loadDrawerWidth,
  saveDrawerWidth,
} from "../drawer_state";
import { JsonViewer } from "./JsonViewer";
import { PerRowView } from "./PerRowView";
import type { TaskCard, TaskDetail, TaskDetailArtifact } from "../types";
import type { ReactNode } from "react";

interface TaskDrawerProps {
  task: TaskCard | null;
  detail: TaskDetail | null;
  loading: boolean;
  saving: boolean;
  error: string | null;
  onSubmitFeedbackDiscussion: (payload: Record<string, unknown>) => Promise<void>;
  onSubmitHumanReviewDecision: (payload: Record<string, unknown>) => Promise<void>;
  onClose: () => void;
}

export function TaskDrawer({
  task,
  detail,
  loading,
  saving,
  error,
  onSubmitFeedbackDiscussion,
  onSubmitHumanReviewDecision,
  onClose,
}: TaskDrawerProps) {
  const [width, setWidth] = useState<number>(DRAWER_DEFAULT_WIDTH);
  const dragStateRef = useRef<{ startX: number; startWidth: number } | null>(null);

  useEffect(() => {
    if (typeof window === "undefined") return;
    setWidth(loadDrawerWidth(window.localStorage ?? null, window.innerWidth));
  }, []);

  useEffect(() => {
    if (!task) return;
    if (typeof window === "undefined") return;
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [task, onClose]);

  const onResizeMouseDown = useCallback(
    (event: React.MouseEvent<HTMLDivElement>) => {
      event.preventDefault();
      if (typeof window === "undefined") return;
      dragStateRef.current = { startX: event.clientX, startWidth: width };
      function onMouseMove(ev: MouseEvent) {
        const state = dragStateRef.current;
        if (!state) return;
        const delta = state.startX - ev.clientX;
        const next = clampDrawerWidth(state.startWidth + delta, window.innerWidth);
        setWidth(next);
      }
      function onMouseUp() {
        dragStateRef.current = null;
        window.removeEventListener("mousemove", onMouseMove);
        window.removeEventListener("mouseup", onMouseUp);
      }
      window.addEventListener("mousemove", onMouseMove);
      window.addEventListener("mouseup", onMouseUp);
    },
    [width],
  );

  // Persist width whenever it changes (covers drag-end and programmatic updates).
  useEffect(() => {
    if (typeof window === "undefined") return;
    saveDrawerWidth(window.localStorage ?? null, width);
  }, [width]);

  if (!task) return null;

  const annotationArtifacts = detail?.artifacts.filter((artifact) => artifact.kind === "annotation_result") ?? [];
  const previewEvidence = detail ? previewArtifacts(detail.artifacts) : [];

  return (
    <>
      <div className="task-drawer-backdrop" onClick={onClose} aria-hidden="true" />
      <aside className="task-drawer" aria-label="Task detail" style={{ width }}>
        <div
          className="task-drawer-resize-handle"
          onMouseDown={onResizeMouseDown}
          role="separator"
          aria-orientation="vertical"
          aria-label="Resize task drawer"
        />
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
        {detail?.task.document_version_id ? (
          <div>
            <dt>Guideline Version</dt>
            <dd><span className="agreement-pill">{detail.task.document_version_id}</span></dd>
          </div>
        ) : null}
      </dl>

      {loading ? <div className="drawer-state">Loading task detail</div> : null}
      {error ? <div className="drawer-error">{error}</div> : null}

      {detail ? (
        <div className="detail-sections">
          <PerRowView sourceRef={detail.task.source_ref} artifacts={detail.artifacts} />

          <DetailSection title="Raw Source">
            <JsonViewer value={detail.task.source_ref} />
          </DetailSection>

          {detail.task.metadata.qc_policy ? (
            <DetailSection title="QC Policy (legacy task override)">
              <JsonViewer value={detail.task.metadata.qc_policy} />
              <p className="empty-detail">
                This task carries a per-task QC policy override. Going forward, QC policy is
                project-level &mdash; see the Configuration tab &rarr; workflow.yaml
                (<code>runtime.qc_sample_mode</code> / <code>runtime.qc_sample_ratio</code> /
                <code>runtime.qc_sample_count</code>).
              </p>
            </DetailSection>
          ) : null}

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
                  <JsonViewer value={artifact.payload} />
                </div>
              ))
            )}
          </DetailSection>

          {previewEvidence.length > 0 ? (
            <DetailSection title="Preview Evidence">
              <div className="preview-stack">
                {previewEvidence.map((artifact) => (
                  <PreviewArtifact key={artifact.artifact_id} artifact={artifact} />
                ))}
              </div>
            </DetailSection>
          ) : null}

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

          {detail.task.status === "human_review" ? (
            <DetailSection title="Human Review Decision">
              <HumanReviewDecisionForm saving={saving} onSubmit={onSubmitHumanReviewDecision} />
            </DetailSection>
          ) : null}
        </div>
      ) : null}
      </aside>
    </>
  );
}

function PreviewArtifact({ artifact }: { artifact: TaskDetailArtifact }) {
  const imageSource = previewImageSource(artifact);
  const boxes = previewBoxes(artifact);
  return (
    <div className="preview-panel">
      <div className="artifact-title">
        <span>{previewTitle(artifact)}</span>
        <span>{boxes.length} boxes</span>
      </div>
      {imageSource ? (
        <div className="image-preview-frame">
          <img alt="" src={imageSource} />
          {boxes.map((box, index) => (
            <span
              className="bbox-overlay"
              key={`${box.label}-${index}`}
              style={{
                left: `${box.left}%`,
                top: `${box.top}%`,
                width: `${box.width}%`,
                height: `${box.height}%`,
              }}
              title={`${box.label}${box.score === null ? "" : ` ${box.score}`}`}
            >
              <span>{box.label}</span>
            </span>
          ))}
        </div>
      ) : null}
      {boxes.length > 0 ? (
        <div className="bbox-list">
          {boxes.map((box, index) => (
            <span key={`${box.label}-${index}`}>
              {box.label}{box.score === null ? "" : ` ${box.score.toFixed(2)}`}
            </span>
          ))}
        </div>
      ) : null}
      <JsonViewer value={artifact.payload} />
    </div>
  );
}

function HumanReviewDecisionForm({
  saving,
  onSubmit,
}: {
  saving: boolean;
  onSubmit: (payload: Record<string, unknown>) => Promise<void>;
}) {
  const [action, setAction] = useState("request_changes");
  const [correctionMode, setCorrectionMode] = useState("manual_annotation");
  const [feedback, setFeedback] = useState("");

  async function submit() {
    await onSubmit({
      action,
      correction_mode: correctionMode,
      feedback,
      actor: "algorithm-engineer",
    });
    setFeedback("");
  }

  return (
    <div className="human-review-form">
      <div className="segmented-row" aria-label="Human Review action">
        <button
          className={action === "request_changes" ? "segment selected" : "segment"}
          type="button"
          onClick={() => setAction("request_changes")}
        >
          Request Changes
        </button>
        <button
          className={action === "accept" ? "segment selected" : "segment"}
          type="button"
          onClick={() => setAction("accept")}
        >
          Accept
        </button>
        <button
          className={action === "reject" ? "segment selected" : "segment"}
          type="button"
          onClick={() => setAction("reject")}
        >
          Reject
        </button>
      </div>
      <select value={correctionMode} onChange={(event) => setCorrectionMode(event.target.value)}>
        <option value="manual_annotation">Manual annotation</option>
        <option value="batch_code_update">Batch code update</option>
      </select>
      <textarea
        placeholder="Decision feedback for the annotator, QC agent, or project record."
        value={feedback}
        onChange={(event) => setFeedback(event.target.value)}
      />
      <button className="primary-button" type="button" disabled={saving || !feedback.trim()} onClick={submit}>
        {saving ? "Saving" : "Submit Decision"}
      </button>
    </div>
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
      <JsonViewer value={value} />
    </details>
  );
}

