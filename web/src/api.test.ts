import { afterEach, describe, expect, it, vi } from "vitest";
import {
  fetchCoordinatorReport,
  fetchOutboxSummary,
  postHumanReviewDecision,
  saveTaskQcPolicy,
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

  it("updates task QC policy", async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce({
      ok: true,
      json: () =>
        Promise.resolve({
          task: {
            task_id: "task-1",
            metadata: {
              qc_policy: { mode: "sample_ratio", sample_ratio: 0.2, sample_count: 2 },
            },
          },
        }),
    });
    vi.stubGlobal("fetch", fetchMock);

    const detail = await saveTaskQcPolicy("task-1", {
      mode: "sample_ratio",
      sample_ratio: 0.2,
      actor: "algorithm-engineer",
    });

    expect(fetchMock).toHaveBeenCalledWith("/api/tasks/task-1/qc-policy", {
      method: "PUT",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        mode: "sample_ratio",
        sample_ratio: 0.2,
        actor: "algorithm-engineer",
      }),
    });
    expect(detail.task.metadata.qc_policy).toEqual({ mode: "sample_ratio", sample_ratio: 0.2, sample_count: 2 });
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

});
