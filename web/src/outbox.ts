import type { OutboxRecord, OutboxSummary } from "./types";

export function outboxFacts(summary: OutboxSummary): Array<{ label: string; value: number }> {
  return [
    { label: "Pending", value: summary.counts.pending },
    { label: "Sent", value: summary.counts.sent },
    { label: "Dead letters", value: summary.counts.dead_letter },
  ];
}

export function outboxRecordTitle(record: OutboxRecord): string {
  return `${record.kind} · ${record.status}`;
}
