import { afterEach, describe, expect, it, vi } from "vitest";
import {
  fetchCoordinatorReport,
  fetchOutboxSummary,
  postCoordinatorLongTailIssue,
  postCoordinatorRuleUpdate,
  postHumanReviewDecision,
} from "./api";

describe("dashboard API client", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("posts Human Review decisions and reloads task detail", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve({ task: { status: "annotating" } }),
      })
      .mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve({ task: { task_id: "task-1", status: "annotating" } }),
      });
    vi.stubGlobal("fetch", fetchMock);

    const detail = await postHumanReviewDecision("task-1", {
      action: "request_changes",
      correction_mode: "batch_code_update",
      feedback: "Apply the new rule.",
      actor: "algorithm-engineer",
    });

    expect(fetchMock).toHaveBeenNthCalledWith(1, "/api/tasks/task-1/human-review", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        action: "request_changes",
        correction_mode: "batch_code_update",
        feedback: "Apply the new rule.",
        actor: "algorithm-engineer",
      }),
    });
    expect(fetchMock).toHaveBeenNthCalledWith(2, "/api/tasks/task-1");
    expect(detail.task.status).toBe("annotating");
  });

  it("fetches project-scoped outbox summaries", async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve({ counts: { pending: 1, sent: 0, dead_letter: 0 }, records: [] }),
    });
    vi.stubGlobal("fetch", fetchMock);

    const summary = await fetchOutboxSummary("pipe");

    expect(fetchMock).toHaveBeenCalledWith("/api/outbox?project=pipe");
    expect(summary.counts.pending).toBe(1);
  });

  it("fetches project-scoped coordinator reports", async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce({
      ok: true,
      json: () =>
        Promise.resolve({
          project_id: "pipe",
          task_count: 2,
          recommended_actions: ["resolve_annotator_qc_feedback"],
        }),
    });
    vi.stubGlobal("fetch", fetchMock);

    const report = await fetchCoordinatorReport("pipe");

    expect(fetchMock).toHaveBeenCalledWith("/api/coordinator?project=pipe");
    expect(report.project_id).toBe("pipe");
    expect(report.recommended_actions).toEqual(["resolve_annotator_qc_feedback"]);
  });

  it("posts coordinator records", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce({ ok: true, json: () => Promise.resolve({ record_id: "rule-1", project_id: "pipe" }) })
      .mockResolvedValueOnce({ ok: true, json: () => Promise.resolve({ issue_id: "issue-1", project_id: "pipe" }) });
    vi.stubGlobal("fetch", fetchMock);

    await postCoordinatorRuleUpdate({
      project_id: "pipe",
      source: "qc",
      summary: "Boundary examples are missing.",
      action: "Update annotation_rules.yaml.",
      created_by: "coordinator-agent",
      task_ids: ["task-1"],
    });
    await postCoordinatorLongTailIssue({
      project_id: "pipe",
      category: "ambiguous_abbreviation",
      summary: "Abbreviations need guidance.",
      recommended_action: "Ask the algorithm engineer for a rule.",
      severity: "medium",
      created_by: "coordinator-agent",
      task_ids: ["task-1"],
    });

    expect(fetchMock).toHaveBeenNthCalledWith(
      1,
      "/api/coordinator/rule-updates",
      expect.objectContaining({ method: "POST" }),
    );
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      "/api/coordinator/long-tail-issues",
      expect.objectContaining({ method: "POST" }),
    );
  });
});
