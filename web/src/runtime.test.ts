import { describe, expect, it } from "vitest";
import { monitorLabel, orderedQueueCounts, runtimeHealthLabel } from "./runtime";
import type { RuntimeMonitorReport, RuntimeSnapshot } from "./types";

const snapshot: RuntimeSnapshot = {
  generated_at: "2026-05-05T00:00:00+00:00",
  runtime_status: {
    healthy: false,
    heartbeat_at: null,
    heartbeat_age_seconds: null,
    active: false,
    errors: ["heartbeat_missing"],
  },
  queue_counts: {
    draft: 0,
    pending: 2,
    annotating: 0,
    qc: 0,
    human_review: 0,
    accepted: 1,
    rejected: 0,
    blocked: 0,
    cancelled: 0,
  },
  active_runs: [],
  capacity: {
    max_concurrent_tasks: 4,
    active_count: 0,
    available_slots: 4,
  },
  stale_tasks: [],
  due_retries: [],
  project_summaries: [],
};

describe("runtime helpers", () => {
  it("formats runtime health", () => {
    expect(runtimeHealthLabel(snapshot)).toBe("Unhealthy");
  });

  it("orders queue counts for compact display", () => {
    expect(orderedQueueCounts(snapshot).map((item) => item.key)).toEqual([
      "pending",
      "annotating",
      "qc",
      "human_review",
      "accepted",
      "rejected",
      "blocked",
      "cancelled",
      "draft",
    ]);
  });

  it("formats monitor report state", () => {
    const report: RuntimeMonitorReport = {
      ok: false,
      failures: ["runtime_unhealthy"],
      details: { runtime_unhealthy: { errors: ["heartbeat_missing"] } },
    };

    expect(monitorLabel(report)).toBe("Action needed");
  });
});
