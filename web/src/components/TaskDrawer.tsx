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
import { PerRowView, extractOutputsByIndex } from "./PerRowView";
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
  const [drawerTab, setDrawerTab] = useState<"raw" | "annotation" | "discussions" | "logs" | "manual_review">("annotation");
  const [annotationFormat, setAnnotationFormat] = useState<"structured" | "json">("structured");
  const dragStateRef = useRef<{ startX: number; startWidth: number } | null>(null);

  // Default to the Manual Review tab whenever a task enters HR, so the
  // operator's quick-pick UI is the first thing they see.
  const hrStatus = detail?.task.status === "human_review";
  useEffect(() => {
    if (hrStatus) setDrawerTab("manual_review");
    else if (drawerTab === "manual_review") setDrawerTab("annotation");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hrStatus, detail?.task.task_id]);

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
            <HumanReviewReasonBanner events={detail.events} />
          ) : null}
          <div className="drawer-tabs" role="tablist">
            {(detail.task.status === "human_review"
              ? (["manual_review", "annotation", "raw", "discussions", "logs"] as const)
              : (["annotation", "raw", "discussions", "logs"] as const)
            ).map((tab) => (
              <button
                key={tab}
                role="tab"
                aria-selected={drawerTab === tab}
                className={drawerTab === tab ? "drawer-tab selected" : "drawer-tab"}
                type="button"
                onClick={() => setDrawerTab(tab)}
              >
                {tab === "raw"
                  ? "Raw Data"
                  : tab === "annotation"
                  ? "Annotation"
                  : tab === "discussions"
                  ? "Discussions"
                  : tab === "manual_review"
                  ? "Manual Review"
                  : "Logs"}
              </button>
            ))}
          </div>

          <div className="detail-sections">
            {drawerTab === "manual_review" ? (
              <ManualReviewTab
                projectId={detail.task.pipeline_id}
                taskId={detail.task.task_id}
                sourceRef={detail.task.source_ref}
                artifacts={detail.artifacts}
              />
            ) : null}

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

function ManualReviewTab({
  projectId,
  taskId,
  sourceRef,
  artifacts,
}: {
  projectId: string;
  taskId: string;
  sourceRef: unknown;
  artifacts: TaskDetailArtifact[];
}) {
  return (
    <div className="manual-review-tab">
      <EntityConventionForm
        projectId={projectId}
        taskId={taskId}
        sourceRef={sourceRef}
        artifacts={artifacts}
      />
    </div>
  );
}

// Find the first occurrence of `span` in `text`, return the surrounding
// window (±radius chars, truncated at the nearest sentence boundary if one
// lands inside the window, otherwise snapped to a word boundary). Returns
// null if the span isn't in the text. Handles English . ? ! and Chinese
// 。？！sentence terminators.
//
// Periods inside acronyms ("U.S.", "e.g.") are NOT treated as sentence
// boundaries — heuristic: a period preceded by a single uppercase letter
// or by another period/letter-period pattern is part of an abbreviation.
function spanContext(text: string, span: string, radius = 50): {
  before: string;
  match: string;
  after: string;
} | null {
  const idx = text.indexOf(span);
  if (idx === -1) return null;
  const spanEnd = idx + span.length;
  const initialStart = Math.max(0, idx - radius);
  const initialEnd = Math.min(text.length, spanEnd + radius);
  let start = initialStart;
  let end = initialEnd;

  // LEFT — find latest real sentence end inside the window. Falls back to
  // the nearest word boundary so we don't cut a word in half.
  const left = text.slice(initialStart, idx);
  const leftBoundary = lastSentenceBoundary(left);
  if (leftBoundary !== -1) {
    start = initialStart + leftBoundary;
  } else if (initialStart > 0) {
    // No sentence boundary in window — snap forward to the next word start
    // so the excerpt doesn't begin mid-word.
    const ws = left.search(/\s/);
    if (ws !== -1) start = initialStart + ws + 1;
  }

  // RIGHT — same, mirrored.
  const right = text.slice(spanEnd, initialEnd);
  const rightBoundary = firstSentenceBoundary(right);
  if (rightBoundary !== -1) {
    end = spanEnd + rightBoundary;
  } else if (initialEnd < text.length) {
    // Snap backward to the last whitespace so the excerpt doesn't end
    // mid-word.
    const lastWs = right.search(/\s\S*$/);
    if (lastWs > 0) end = spanEnd + lastWs;
  }

  return {
    before: text.slice(start, idx),
    match: text.slice(idx, spanEnd),
    after: text.slice(spanEnd, end),
  };
}

// Return the index in `text` immediately after the LAST sentence-end
// punctuation, or -1 if none. An abbreviation period ("U.S.", "Mr.",
// "e.g.") is NOT a sentence end — we filter it out by requiring that the
// character before the period is NOT a single uppercase letter on its own
// (preceded by another non-letter or start of string) or another period.
function lastSentenceBoundary(text: string): number {
  let result = -1;
  const re = /([.?!]+["')\]]?\s+|[。？！])/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(text)) !== null) {
    const punctStart = m.index;
    if (looksLikeAbbreviation(text, punctStart)) continue;
    result = punctStart + m[0].length;
  }
  return result;
}

function firstSentenceBoundary(text: string): number {
  const re = /([.?!]+["')\]]?\s+|[。？！])/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(text)) !== null) {
    if (looksLikeAbbreviation(text, m.index)) continue;
    return m.index + m[0].length;
  }
  return -1;
}

// A period at `idx` looks like an abbreviation when the chars right before
// it form one of: a single capital letter ("U" before "U."), another
// capital-period pair ("S" after "U." in "U.S."), or a known abbreviation
// stem like "Mr", "Mrs", "Dr", "Inc", "Co", "etc", "vs".
function looksLikeAbbreviation(text: string, periodIdx: number): boolean {
  if (text[periodIdx] !== ".") return false;
  // Check char immediately before the period.
  const prev = text[periodIdx - 1] ?? "";
  if (/[A-Z]/.test(prev)) {
    const before = text[periodIdx - 2] ?? "";
    // Sole uppercase letter ("A." at start of word) or "X.Y." chain.
    if (before === "" || /[\s.([{]/.test(before)) return true;
  }
  // Check 2-3 char stems just before the period.
  const stem = text.slice(Math.max(0, periodIdx - 4), periodIdx);
  if (/(?:^|\W)(?:Mr|Mrs|Dr|Inc|Co|etc|vs|e\.g|i\.e|cf)$/.test(stem)) return true;
  return false;
}

// Aggregate historical type proposals on an EntityConvention so the operator
// sees what past annotator / QC / arbiter submissions claimed for this span.
function summarizeProposals(
  convention: EntityConvention | undefined,
): { counts: Record<string, number>; total: number; label: string } {
  const counts: Record<string, number> = {};
  let total = 0;
  for (const p of convention?.proposals ?? []) {
    const t = String((p as { type?: unknown }).type ?? "").trim();
    if (!t) continue;
    counts[t] = (counts[t] ?? 0) + 1;
    total += 1;
  }
  const label = Object.entries(counts)
    .sort((a, b) => b[1] - a[1])
    .map(([t, n]) => `${t}×${n}`)
    .join(", ");
  return { counts, total, label };
}

function extractInputRows(sourceRef: unknown): Array<{ label: string | null; text: string }> {
  if (!sourceRef || typeof sourceRef !== "object") return [];
  const payload = (sourceRef as { payload?: unknown }).payload;
  if (!payload || typeof payload !== "object") return [];
  const rec = payload as Record<string, unknown>;
  if (typeof rec.text === "string" && rec.text.trim()) {
    return [{ label: null, text: rec.text }];
  }
  const rows = rec.rows;
  if (!Array.isArray(rows)) return [];
  const out: Array<{ label: string | null; text: string }> = [];
  for (const r of rows) {
    if (!r || typeof r !== "object") continue;
    const rr = r as Record<string, unknown>;
    let text: string | null = null;
    if (typeof rr.input === "string") text = rr.input;
    else if (rr.input && typeof rr.input === "object") {
      const inner = (rr.input as Record<string, unknown>).text;
      if (typeof inner === "string") text = inner;
    } else if (typeof rr.text === "string") text = rr.text;
    if (!text) continue;
    const id =
      (typeof rr.row_id === "string" && rr.row_id) ||
      (typeof rr.source_id === "string" && rr.source_id) ||
      (typeof rr.row_index === "number" ? `row ${rr.row_index}` : null);
    out.push({ label: id, text });
  }
  return out;
}

function EntityConventionForm({
  projectId,
  taskId,
  sourceRef,
  artifacts,
}: {
  projectId: string;
  taskId: string;
  sourceRef: unknown;
  artifacts: TaskDetailArtifact[];
}) {
  const inputRows = useMemo(() => extractInputRows(sourceRef), [sourceRef]);
  const [conventions, setConventions] = useState<EntityConvention[]>([]);
  const [span, setSpan] = useState("");
  const [entityType, setEntityType] = useState<string>(ENTITY_TYPES[1]);
  const [notes, setNotes] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [pendingPick, setPendingPick] = useState<string | null>(null);

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

  // Extract entity (span, current_type) pairs from this task's latest
  // annotation so the operator can declare conventions in one click.
  const quickPicks = useMemo(() => {
    const outputs = extractOutputsByIndex(artifacts);
    const seen = new Set<string>();
    const pairs: Array<{ span: string; currentType: string }> = [];
    for (const out of outputs.values()) {
      const entities = (out as { entities?: Record<string, unknown> }).entities;
      if (!entities || typeof entities !== "object") continue;
      for (const [type, spans] of Object.entries(entities)) {
        if (!Array.isArray(spans)) continue;
        for (const s of spans) {
          if (typeof s !== "string") continue;
          const key = `${s}|${type}`;
          if (seen.has(key)) continue;
          seen.add(key);
          pairs.push({ span: s, currentType: type });
        }
      }
    }
    return pairs;
  }, [artifacts]);

  // Index conventions by span so we can mark spans that already have one.
  const conventionBySpan = useMemo(() => {
    const map = new Map<string, EntityConvention>();
    for (const c of conventions) map.set(c.span, c);
    return map;
  }, [conventions]);

  const declarePick = useCallback(
    async (pickSpan: string, pickType: string) => {
      const key = `${pickSpan}|${pickType}`;
      setPendingPick(key);
      setBusy(true);
      setError(null);
      setMessage(null);
      try {
        const conv = await declareConvention({
          project_id: projectId,
          span: pickSpan,
          entity_type: pickType,
          task_id: taskId,
          actor: "operator",
        });
        setMessage(
          conv.status === "disputed"
            ? `Recorded "${pickSpan}" — now disputed (history conflicts)`
            : `Recorded "${pickSpan}" → ${pickType}`,
        );
        await reload();
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      } finally {
        setBusy(false);
        setPendingPick(null);
      }
    },
    [projectId, taskId, reload],
  );

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
      <p className="hint">
        Click an entity span below to declare its type as a project-wide convention.
        Future tasks will see it injected into annotator/QC/arbiter prompts.
      </p>
      {error ? <p className="convention-error">{error}</p> : null}
      {message ? <p className="convention-ok">{message}</p> : null}

      {quickPicks.length > 0 ? (
        <div className="convention-quick-picks">
          {quickPicks.map(({ span: pickSpan, currentType }) => {
            const existing = conventionBySpan.get(pickSpan);
            // First row whose input text contains this span — show its
            // surrounding sentence so the operator doesn't have to read the
            // whole task.
            let ctx: { before: string; match: string; after: string } | null = null;
            for (const r of inputRows) {
              ctx = spanContext(r.text, pickSpan);
              if (ctx) break;
            }
            // Build the history line: count of each proposed type across
            // past annotator/QC submissions for this span.
            const history = summarizeProposals(existing);
            return (
              <div className="convention-pick-card" key={`${pickSpan}|${currentType}`}>
                {ctx ? (
                  <p className="convention-pick-context">
                    {ctx.before ? <>…{ctx.before}</> : null}
                    <mark>{ctx.match}</mark>
                    {ctx.after ? <>{ctx.after}…</> : null}
                  </p>
                ) : null}
                <div className="convention-pick-row">
                  <code className="convention-pick-span">{pickSpan}</code>
                  <span className="convention-pick-sep">→</span>
                  {ENTITY_TYPES.map((t) => {
                    const isCurrent = t === currentType;
                    const isExisting = existing?.entity_type === t;
                    const key = `${pickSpan}|${t}`;
                    const pending = pendingPick === key;
                    const cls = [
                      "convention-pick-btn",
                      isCurrent ? "current" : "",
                      isExisting ? "established" : "",
                    ].filter(Boolean).join(" ");
                    const historyCount = history.counts[t] ?? 0;
                    return (
                      <button
                        type="button"
                        key={t}
                        className={cls}
                        disabled={busy}
                        title={
                          isExisting
                            ? `Convention already set: ${pickSpan} → ${t}`
                            : isCurrent
                            ? `Current annotation type — click to lock in as convention`
                            : `Declare ${pickSpan} → ${t}`
                        }
                        onClick={() => declarePick(pickSpan, t)}
                      >
                        {pending ? "…" : t}
                        {historyCount > 0 ? (
                          <span className="convention-pick-tally">×{historyCount}</span>
                        ) : null}
                      </button>
                    );
                  })}
                </div>
                {history.total > 0 ? (
                  <p className="convention-pick-history">
                    History: {history.label}
                    {existing?.status === "disputed" ? <span className="convention-pick-disputed-tag"> · disputed</span> : null}
                  </p>
                ) : null}
              </div>
            );
          })}
        </div>
      ) : (
        <p className="hint">No entities found in this task's annotation.</p>
      )}

      <details className="convention-manual">
        <summary>+ declare a span not in the annotation</summary>
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
      </details>
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
