import { describe, expect, it } from "vitest";
import { readinessFacts, readinessTitle } from "./readiness";
import type { ReadinessReport } from "./types";

const baseReport: ReadinessReport = {
  project_id: "pipe",
  ready_for_training: false,
  accepted_count: 2,
  exported_count: 1,
  exportable_count: 1,
  open_feedback_count: 0,
  human_review_count: 0,
  validation_blockers: [],
  pending_outbox_count: 0,
  dead_letter_outbox_count: 0,
  latest_export: null,
  recommended_next_action: "export_training_data",
  next_command: "annotation-pipeline export training-data --project-id pipe",
};

describe("readiness helpers", () => {
  it("labels recommended action", () => {
    expect(readinessTitle(baseReport)).toBe("Export training data");
    expect(readinessTitle({ ...baseReport, ready_for_training: true })).toBe("Training data ready");
  });

  it("orders report facts for dashboard display", () => {
    expect(readinessFacts(baseReport).map((fact) => fact.label)).toEqual([
      "Accepted",
      "Exported",
      "Exportable",
      "Open feedback",
      "Human Review",
      "Blockers",
      "Outbox",
      "Dead letters",
    ]);
  });
});
