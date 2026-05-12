import { describe, expect, it } from "vitest";
import {
  buildCoordinatorViewModel,
  classifyProviderDiagnostic,
  coordinatorEmptyState,
  formatActionLabel,
  formatProviderLabel,
  formatTimestampRecency,
} from "./coordinator";
import type { CoordinatorReport } from "./types";

const now = new Date("2026-05-05T12:00:00Z");

const report: CoordinatorReport = {
  project_id: "pipe",
  generated_at: "2026-05-05T11:59:00Z",
  task_count: 9,
  status_counts: {
    pending: 3,
    accepted: 2,
    human_review: 1,
    blocked: 1,
  },
  human_review_task_ids: ["task-hr"],
  blocked_task_ids: ["task-blocked"],
  open_feedback_count: 2,
  open_feedback_ids: ["fb-1", "fb-2"],
  feedback_by_category: { schema: 1, instruction: 1 },
  blocking_feedback_count: 1,
  outbox_counts: { pending: 1, sent: 4, dead_letter: 0 },
  readiness: null,
  provider_diagnostics: {
    config_valid: false,
    error: "missing provider target",
    targets: { annotation: "local_codex", qc: "review_llm" },
    diagnostics: {
      local_codex: {
        status: "ok",
        checks: [{ id: "cli_binary_found", status: "ok", message: "codex is available" }],
      },
      review_llm: {
        status: "error",
        checks: [{ id: "api_key", status: "error", message: "REVIEW_API_KEY is not set" }],
      },
    },
  },
  recommended_actions: ["complete_human_review", "drain_external_outbox"],
};

describe("coordinator helpers", () => {
  it("formats provider, recency, and action labels for dashboard copy", () => {
    expect(formatProviderLabel("review_llm")).toBe("Review LLM");
    expect(formatTimestampRecency("2026-05-05T11:58:00Z", now)).toBe("2m ago");
    expect(formatActionLabel("complete_human_review")).toBe("Complete Human Review");
    expect(formatActionLabel("remind_user_to_complete_human_review")).toBe("Complete Human Review");
    expect(formatActionLabel("resolve_annotator_qc_feedback")).toBe("Resolve annotator/QC feedback");
  });

  it("projects non-empty coordinator reports into compact dashboard sections", () => {
    const view = buildCoordinatorViewModel(report, now);

    expect(view.overviewStats).toEqual([
      { label: "Pending", value: 3, tone: "neutral" },
      { label: "Accepted", value: 2, tone: "success" },
      { label: "Human Review", value: 1, tone: "warning" },
      { label: "Blocked", value: 1, tone: "critical" },
      { label: "Open feedback", value: 2, tone: "warning" },
      { label: "Outbox pending", value: 1, tone: "warning" },
    ]);
    expect(view.providerCards.map((card) => [card.id, card.label, card.severity])).toEqual([
      ["provider_config", "Provider configuration", "critical"],
      ["review_llm", "Review LLM", "critical"],
      ["local_codex", "Local Codex", "ok"],
    ]);
    expect(view.actionRows.map((row) => row.label)).toEqual(["Complete Human Review", "Drain external outbox"]);
  });

  it("orders provider diagnostics by severity before label", () => {
    const view = buildCoordinatorViewModel(report, now);
    expect(classifyProviderDiagnostic(report.provider_diagnostics.diagnostics.review_llm)).toBe("critical");
    expect(view.providerCards.map((card) => card.id).slice(0, 2)).toEqual(["provider_config", "review_llm"]);
  });

  it("surfaces top-level provider configuration errors even without per-provider diagnostics", () => {
    const configErrorReport: CoordinatorReport = {
      ...report,
      provider_diagnostics: {
        config_valid: false,
        error: "Provider target for annotation is missing",
        diagnostics: {},
      },
    };

    const view = buildCoordinatorViewModel(configErrorReport, now);

    expect(view.providerCards).toEqual([
      expect.objectContaining({
        id: "provider_config",
        label: "Provider configuration",
        severity: "critical",
        status: "error",
        message: "Provider target for annotation is missing",
      }),
    ]);
  });

  it("returns empty-state messages when no coordinator activity exists", () => {
    const emptyReport: CoordinatorReport = {
      ...report,
      task_count: 0,
      status_counts: {},
      human_review_task_ids: [],
      blocked_task_ids: [],
      open_feedback_count: 0,
      open_feedback_ids: [],
      feedback_by_category: {},
      blocking_feedback_count: 0,
      outbox_counts: { pending: 0, sent: 0, dead_letter: 0 },
      provider_diagnostics: { config_valid: true, diagnostics: {} },
      recommended_actions: [],
    };

    const view = buildCoordinatorViewModel(emptyReport, now);

    expect(coordinatorEmptyState(emptyReport)).toEqual({
      title: "No coordinator activity yet",
      detail: "Coordinator guidance will appear after tasks, feedback, or provider checks exist.",
    });
    expect(view.emptyState?.title).toBe("No coordinator activity yet");
    expect(view.providerCards).toEqual([]);
    expect(view.actionRows).toEqual([]);
  });
});
