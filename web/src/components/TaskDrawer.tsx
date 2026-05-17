import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { cardSubtitle } from "../kanban";
import { previewArtifacts, previewBoxes, previewImageSource, previewTitle } from "../preview";
import {
  DRAWER_DEFAULT_WIDTH,
  clampDrawerWidth,
  loadDrawerWidth,
  saveDrawerWidth,
} from "../drawer_state";
import { AnnotationView } from "./AnnotationView";
import { JsonViewer } from "./JsonViewer";
import { PerRowView } from "./PerRowView";
import type { TaskCard, TaskDetail, TaskDetailArtifact } from "../types";
import type { ReactNode } from "react";
import {
  declareConvention,
  fetchConventions,
  resolveConventionDispute,
  type EntityConvention,
} from "../api";

interface TaskDrawerProps {
  task: TaskCard | null;
  detail: TaskDetail | null;
  loading: boolean;
  saving: boolean;
  error: string | null;
  onSubmitHumanReviewDecision: (payload: Record<string, unknown>) => Promise<void>;
  onClose: () => void;
}

export function TaskDrawer({
  task,
  detail,
  loading,
  saving,
  error,
  onSubmitHumanReviewDecision,
  onClose,
}: TaskDrawerProps) {
  const [width, setWidth] = useState<number>(DRAWER_DEFAULT_WIDTH);
  const [drawerTab, setDrawerTab] = useState<"raw" | "annotation" | "discussions" | "logs">("annotation");
  const [annotationFormat, setAnnotationFormat] = useState<"structured" | "json">("structured");
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
        <>
          {detail.task.status === "human_review" ? (
            <>
              <HumanReviewReasonBanner events={detail.events} />
              <EntityConventionForm
                projectId={detail.task.pipeline_id}
                taskId={detail.task.task_id}
                sourceRef={detail.task.source_ref}
              />
            </>
          ) : null}
          <div className="drawer-tabs" role="tablist">
            {(["annotation", "raw", "discussions", "logs"] as const).map((tab) => (
              <button
                key={tab}
                role="tab"
                aria-selected={drawerTab === tab}
                className={drawerTab === tab ? "drawer-tab selected" : "drawer-tab"}
                type="button"
                onClick={() => setDrawerTab(tab)}
              >
                {tab === "raw" ? "Raw Data" : tab === "annotation" ? "Annotation" : tab === "discussions" ? "Discussions" : "Logs"}
              </button>
            ))}
          </div>

          <div className="detail-sections">
            {drawerTab === "raw" ? (
              <>
                <PerRowView sourceRef={detail.task.source_ref} artifacts={detail.artifacts} />
                <DetailSection title="Raw Source">
                  <JsonViewer value={detail.task.source_ref} />
                </DetailSection>
              </>
            ) : null}

            {drawerTab === "annotation" ? (
              <>
                {annotationArtifacts.length === 0 ? (
                  <p className="empty-detail">No annotation artifacts recorded.</p>
                ) : (
                  <>
                    <div className="annotation-format-toggle">
                      <button
                        type="button"
                        className={annotationFormat === "structured" ? "segment selected" : "segment"}
                        onClick={() => setAnnotationFormat("structured")}
                      >
                        Structured
                      </button>
                      <button
                        type="button"
                        className={annotationFormat === "json" ? "segment selected" : "segment"}
                        onClick={() => setAnnotationFormat("json")}
                      >
                        JSON
                      </button>
                    </div>
                    {annotationFormat === "structured" ? (
                      <div className="artifact-panel">
                        <div className="artifact-title">
                          <span>Latest</span>
                          <span>
                            {annotationArtifacts[annotationArtifacts.length - 1].metadata.provider
                              ? String(annotationArtifacts[annotationArtifacts.length - 1].metadata.provider)
                              : annotationArtifacts[annotationArtifacts.length - 1].content_type}
                          </span>
                        </div>
                        <AnnotationView
                          artifacts={annotationArtifacts}
                          sourceRef={detail.task.source_ref}
                        />
                      </div>
                    ) : (
                      annotationArtifacts.map((artifact, index) => {
                        const isLatest = index === annotationArtifacts.length - 1;
                        const label = artifact.metadata.provider
                          ? String(artifact.metadata.provider)
                          : artifact.content_type;
                        return isLatest ? (
                          <div className="artifact-panel" key={artifact.artifact_id}>
                            <div className="artifact-title">
                              <span>Latest</span>
                              <span>{label}</span>
                            </div>
                            <JsonViewer value={artifact.payload} />
                          </div>
                        ) : (
                          <details className="artifact-panel artifact-collapsed" key={artifact.artifact_id}>
                            <summary className="artifact-title">
                              <span>#{index + 1}</span>
                              <span>{label}</span>
                            </summary>
                            <JsonViewer value={artifact.payload} />
                          </details>
                        );
                      })
                    )}
                  </>
                )}
                {previewEvidence.length > 0 ? (
                  <DetailSection title="Preview Evidence">
                    <div className="preview-stack">
                      {previewEvidence.map((artifact) => (
                        <PreviewArtifact key={artifact.artifact_id} artifact={artifact} />
                      ))}
                    </div>
                  </DetailSection>
                ) : null}
              </>
            ) : null}

            {drawerTab === "discussions" ? (
              <>
                <DetailSection title={`Feedback (${detail.feedback.length})`}>
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
              </>
            ) : null}

            {drawerTab === "logs" ? (
              <>
                <DetailSection title={`Attempts (${detail.attempts.length})`}>
                  {detail.attempts.length === 0 ? (
                    <p className="empty-detail">No attempts recorded.</p>
                  ) : (
                    detail.attempts.map((attempt) => (
                      <TimelineItem
                        key={String(attempt.attempt_id)}
                        title={`#${String(attempt.index)} ${String(attempt.stage)} · ${String(attempt.status)}`}
                        meta={`${String(attempt.provider_id ?? "provider unknown")} · ${String(attempt.model ?? "model unknown")}`}
                        value={attempt}
                      />
                    ))
                  )}
                </DetailSection>
                <DetailSection title={`Round Changes (${detail.events.length})`}>
                  {detail.events.length === 0 ? (
                    <p className="empty-detail">No round changes recorded.</p>
                  ) : (
                    detail.events.map((event) => (
                      <TimelineItem
                        key={String(event.event_id)}
                        title={`${String(event.previous_status)} → ${String(event.next_status)}`}
                        meta={`${String(event.stage)} · ${String(event.reason)}`}
                        value={event}
                      />
                    ))
                  )}
                </DetailSection>
              </>
            ) : null}
          </div>
        </>
      ) : null}
      </aside>
    </>
  );
}

function HumanReviewReasonBanner({ events }: { events: Array<Record<string, unknown>> }) {
  // Find the most recent transition that landed in human_review.
  let entry: Record<string, unknown> | null = null;
  for (let i = events.length - 1; i >= 0; i--) {
    const e = events[i];
    if (e?.next_status === "human_review") {
      entry = e;
      break;
    }
  }
  if (!entry) return null;

  const reason = String(entry.reason ?? "");
  const meta = (entry.metadata && typeof entry.metadata === "object")
    ? (entry.metadata as Record<string, unknown>)
    : {};
  const arbiterRan = meta.arbiter_ran === true;
  const arbiterUnresolved = typeof meta.arbiter_unresolved === "number" ? meta.arbiter_unresolved : 0;
  const roundCount = typeof meta.round_count === "number" ? meta.round_count : null;
  const maxRounds = typeof meta.max_qc_rounds === "number" ? meta.max_qc_rounds : null;
  const autoEscalated = meta.auto_escalated === true;

  let detail: string | null = null;
  let tone: "warning" | "critical" = "warning";
  if (autoEscalated && !arbiterRan) {
    tone = "critical";
    detail =
      "Arbiter was skipped because the annotator never posted a rebuttal " +
      "(no discussion_replies emitted). The retry loop ran out without " +
      "anyone disputing QC's complaints.";
  } else if (autoEscalated && arbiterRan && arbiterUnresolved > 0) {
    detail = `Arbiter ran but ${arbiterUnresolved} disputes remained unresolved after the retry loop exhausted.`;
  } else if (autoEscalated) {
    detail = "Auto-escalated after the retry loop exhausted.";
  }

  return (
    <div className={`hr-reason-banner ${tone}`}>
      <strong>Why this is in Human Review</strong>
      <p className="hr-reason-quote">{reason}</p>
      {detail ? <p className="hr-reason-detail">{detail}</p> : null}
      {roundCount !== null && maxRounds !== null ? (
        <p className="hr-reason-meta">
          Rounds: {roundCount} / {maxRounds} · Arbiter ran: {arbiterRan ? "yes" : "no"}
          {arbiterRan ? ` · unresolved: ${arbiterUnresolved}` : ""}
        </p>
      ) : null}
    </div>
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

const ROLE_LABELS: Record<string, string> = {
  annotator: "Annotator",
  qc: "QC Reviewer",
  coordinator: "Coordinator",
};

const STANCE_LABELS: Record<string, string> = {
  agree: "Agree",
  partial_agree: "Partially agree",
  disagree: "Disagree",
  proposal: "Proposal",
};

const STANCE_COLORS: Record<string, string> = {
  agree: "stance-agree",
  partial_agree: "stance-partial",
  disagree: "stance-disagree",
  proposal: "stance-proposal",
};

const SOURCE_LABELS: Record<string, string> = {
  qc: "QC Agent",
  annotation: "Annotation Agent",
  human_review: "Human Reviewer",
};

function ConsensusSummary({ detail }: { detail: TaskDetail }) {
  const c = detail.feedback_consensus;
  return (
    <div className={c.can_accept_by_consensus ? "consensus-box accepted" : "consensus-box"}>
      <strong>
        {c.can_accept_by_consensus ? "All feedback resolved" : `${c.consensus_feedback} of ${c.total_feedback} items resolved`}
      </strong>
      <span>
        {c.can_accept_by_consensus
          ? "Annotator and QC reached agreement on all items — task can pass QC."
          : "Some feedback still needs a response from the annotator or QC reviewer."}
      </span>
    </div>
  );
}

function FeedbackAgreementCard({
  feedback,
  discussions,
}: {
  feedback: Record<string, unknown>;
  discussions: Array<Record<string, unknown>>;
}) {
  const consensusReached = useMemo(() => discussions.some((entry) => entry.consensus === true), [discussions]);

  const sourceLabel = SOURCE_LABELS[String(feedback.source_stage ?? "")] ?? "QC Agent";
  const severityClass = String(feedback.severity) === "critical" ? "severity-critical"
    : String(feedback.severity) === "warning" ? "severity-warning" : "severity-info";

  return (
    <div className="feedback-card">
      <div className="feedback-issue">
        <div className="feedback-issue-meta">
          <span className="feedback-from">{sourceLabel}</span>
          <span className={`feedback-severity ${severityClass}`}>{String(feedback.severity)}</span>
          <span className="feedback-category">{String(feedback.category)}</span>
          <span className={consensusReached ? "agreement-pill accepted" : "agreement-pill"}>
            {consensusReached ? "Resolved" : "Open"}
          </span>
        </div>
        <p className="feedback-message">{String(feedback.message)}</p>
      </div>

      {discussions.length === 0 ? (
        <p className="discussion-empty">No responses yet.</p>
      ) : (
        <div className="discussion-thread">
          {discussions.map((entry) => (
            <div key={String(entry.entry_id)} className="discussion-message">
              <div className="discussion-message-meta">
                <span className="discussion-role">{ROLE_LABELS[String(entry.role)] ?? String(entry.role)}</span>
                <span className={`discussion-stance ${STANCE_COLORS[String(entry.stance)] ?? ""}`}>
                  {STANCE_LABELS[String(entry.stance)] ?? String(entry.stance)}
                </span>
                {entry.consensus ? <span className="discussion-consensus-badge">✓ Consensus</span> : null}
              </div>
              <p className="discussion-message-body">{String(entry.message)}</p>
            </div>
          ))}
        </div>
      )}
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


const ENTITY_TYPES = [
  "person", "organization", "project", "document", "time",
  "number", "event", "location", "technology", "entity",
] as const;

function EntityConventionForm({
  projectId,
  taskId,
  sourceRef,
}: {
  projectId: string;
  taskId: string;
  sourceRef: unknown;
}) {
  const [conventions, setConventions] = useState<EntityConvention[]>([]);
  const [span, setSpan] = useState("");
  const [entityType, setEntityType] = useState<string>(ENTITY_TYPES[1]);
  const [notes, setNotes] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  const reload = useCallback(async () => {
    try {
      const list = await fetchConventions(projectId);
      setConventions(list);
    } catch (e) {
      // silent — listing is best-effort
    }
  }, [projectId]);

  useEffect(() => {
    void reload();
  }, [reload]);

  // Suggest spans found in the task's input rows so the operator can click.
  const inputSpansHint = useMemo(() => {
    const payload =
      typeof sourceRef === "object" && sourceRef !== null
        ? (sourceRef as { payload?: { rows?: Array<{ input?: string }> } }).payload
        : undefined;
    const rows = payload?.rows ?? [];
    return rows
      .map((r) => (typeof r.input === "string" ? r.input : ""))
      .filter(Boolean)
      .join(" • ")
      .slice(0, 400);
  }, [sourceRef]);

  const onSubmit = useCallback(
    async (e: React.FormEvent) => {
      e.preventDefault();
      const trimmed = span.trim();
      if (!trimmed) {
        setError("Span is required");
        return;
      }
      setBusy(true);
      setError(null);
      setMessage(null);
      try {
        const conv = await declareConvention({
          project_id: projectId,
          span: trimmed,
          entity_type: entityType,
          task_id: taskId,
          notes: notes.trim() || undefined,
          actor: "operator",
        });
        setMessage(
          conv.status === "disputed"
            ? `Recorded — now disputed (${conv.evidence_count} evidence, conflicting types in history)`
            : `Recorded "${trimmed}" → ${entityType} (evidence_count=${conv.evidence_count})`,
        );
        setSpan("");
        setNotes("");
        await reload();
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      } finally {
        setBusy(false);
      }
    },
    [span, entityType, projectId, taskId, notes, reload],
  );

  const onResolveDispute = useCallback(
    async (convId: string, type: string) => {
      setBusy(true);
      setError(null);
      try {
        await resolveConventionDispute(convId, type, null, "operator");
        await reload();
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      } finally {
        setBusy(false);
      }
    },
    [reload],
  );

  const disputed = conventions.filter((c) => c.status === "disputed");
  const active = conventions.filter((c) => c.status === "active");

  return (
    <section className="entity-convention-box">
      <h3>Entity Conventions for this project</h3>
      <p className="hint">
        Establish a per-project type for an ambiguous entity span. Future tasks whose input contains
        this span will see the convention injected into annotator/QC/arbiter prompts.
      </p>
      <form onSubmit={onSubmit} className="convention-form">
        <input
          type="text"
          placeholder="span (e.g. Gmail, Apple)"
          value={span}
          onChange={(e) => setSpan(e.target.value)}
          disabled={busy}
        />
        <select value={entityType} onChange={(e) => setEntityType(e.target.value)} disabled={busy}>
          {ENTITY_TYPES.map((t) => (
            <option key={t} value={t}>
              {t}
            </option>
          ))}
        </select>
        <input
          type="text"
          placeholder="notes (optional)"
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
          disabled={busy}
        />
        <button type="submit" disabled={busy || !span.trim()}>
          {busy ? "Saving..." : "Declare convention"}
        </button>
      </form>
      {error ? <p className="convention-error">{error}</p> : null}
      {message ? <p className="convention-ok">{message}</p> : null}
      {inputSpansHint ? (
        <p className="convention-hint-source">Input spans: {inputSpansHint}{inputSpansHint.length >= 400 ? "…" : ""}</p>
      ) : null}
      {disputed.length > 0 ? (
        <div className="convention-disputed">
          <strong>Disputed conventions ({disputed.length})</strong>
          <ul>
            {disputed.map((c) => {
              const proposed = Array.from(
                new Set(c.proposals.map((p) => String((p as { type?: unknown }).type ?? ""))),
              ).filter(Boolean);
              return (
                <li key={c.convention_id}>
                  <code>{c.span}</code> — proposed:{" "}
                  {proposed.map((t) => (
                    <button
                      key={t}
                      type="button"
                      className="convention-resolve-btn"
                      onClick={() => onResolveDispute(c.convention_id, t)}
                      disabled={busy}
                    >
                      keep {t}
                    </button>
                  ))}
                </li>
              );
            })}
          </ul>
        </div>
      ) : null}
      {active.length > 0 ? (
        <details className="convention-list">
          <summary>Active conventions ({active.length})</summary>
          <ul>
            {active.map((c) => (
              <li key={c.convention_id}>
                <code>{c.span}</code> → <em>{c.entity_type}</em>{" "}
                <small>
                  (×{c.evidence_count}, {c.created_by})
                </small>
              </li>
            ))}
          </ul>
        </details>
      ) : null}
    </section>
  );
}
