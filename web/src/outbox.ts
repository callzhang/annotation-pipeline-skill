import type { OutboxRecord, OutboxSummary } from "./types";

export function outboxFacts(summary: OutboxSummary): Array<{ label: string; value: number; description: string }> {
  return [
    {
      label: "Pending",
      value: summary.counts.pending,
      description: "Records queued and waiting to be pushed to the external system. Retried automatically until sent or dead-lettered.",
    },
    {
      label: "Sent",
      value: summary.counts.sent,
      description: "Records successfully delivered to the external system.",
    },
    {
      label: "Dead Letters",
      value: summary.counts.dead_letter,
      description: "Records that exhausted all retries. Require manual investigation before delivery can resume.",
    },
  ];
}

export function outboxRecordTitle(record: OutboxRecord): string {
  return `${record.kind} · ${record.status}`;
}
