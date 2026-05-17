import { describe, expect, it } from "vitest";
import { outboxFacts, outboxRecordTitle } from "./outbox";
import type { OutboxSummary } from "./types";

const summary: OutboxSummary = {
  counts: { pending: 2, sent: 3, dead_letter: 1 },
  records: [
    {
      record_id: "outbox-1",
      task_id: "task-1",
      kind: "submit",
      payload: {},
      status: "dead_letter",
      retry_count: 3,
      created_at: "2026-05-05T00:00:00Z",
      next_retry_at: null,
      last_error: "http 400",
    },
  ],
};

describe("outbox helpers", () => {
  it("orders outbox counts for operator scanning", () => {
    expect(outboxFacts(summary).map((f) => ({ label: f.label, value: f.value }))).toEqual([
      { label: "Pending", value: 2 },
      { label: "Sent", value: 3 },
      { label: "Dead Letters", value: 1 },
    ]);
  });

  it("formats record titles with kind and status", () => {
    expect(outboxRecordTitle(summary.records[0])).toBe("submit · dead_letter");
  });
});
