import type { RuntimeMonitorReport, RuntimeSnapshot } from "./types";

const queueOrder = [
  "pending",
  "annotating",
  "qc",
  "human_review",
  "accepted",
  "rejected",
  "blocked",
  "cancelled",
  "draft",
] as const;

export function runtimeHealthLabel(snapshot: RuntimeSnapshot): string {
  return snapshot.runtime_status.healthy ? "Healthy" : "Unhealthy";
}

export function monitorLabel(report: RuntimeMonitorReport | null): string {
  if (!report) return "Unknown";
  return report.ok ? "Clear" : "Action needed";
}

export function orderedQueueCounts(snapshot: RuntimeSnapshot): Array<{ key: string; value: number }> {
  return queueOrder.map((key) => ({ key, value: snapshot.queue_counts[key] }));
}

export function formatRuntimeDate(value: string | null): string {
  if (!value) return "missing";
  return new Date(value).toLocaleString();
}
