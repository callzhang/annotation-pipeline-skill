import type { ReadinessReport } from "./types";

const actionLabels: Record<string, string> = {
  complete_human_review: "Complete Human Review",
  resolve_feedback: "Resolve feedback",
  repair_export_blockers: "Repair export blockers",
  run_annotation_runtime: "Run annotation runtime",
  export_training_data: "Export training data",
  drain_external_outbox: "Drain external outbox",
  inspect_dead_letter_outbox: "Inspect dead-letter outbox",
  deliver_training_data: "Deliver training data",
  inspect_project_state: "Inspect project state",
};

export function readinessTitle(report: ReadinessReport): string {
  return report.ready_for_training ? "Training data ready" : actionLabels[report.recommended_next_action] ?? "Inspect project state";
}

export function readinessFacts(report: ReadinessReport): Array<{ label: string; value: number | string }> {
  return [
    { label: "Accepted", value: report.accepted_count },
    { label: "Exported", value: report.exported_count },
    { label: "Exportable", value: report.exportable_count },
    { label: "Open feedback", value: report.open_feedback_count },
    { label: "Human Review", value: report.human_review_count },
    { label: "Blockers", value: report.validation_blockers.length },
    { label: "Outbox", value: report.pending_outbox_count },
    { label: "Dead letters", value: report.dead_letter_outbox_count },
  ];
}
