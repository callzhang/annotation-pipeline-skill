import type { ReadinessReport } from "./types";

const actionLabels: Record<string, string> = {
  complete_human_review: "Complete Human Review",
  resolve_feedback: "Resolve feedback",
  fix_export_blockers: "Fix export blockers",
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

export function readinessFacts(report: ReadinessReport): Array<{ label: string; value: number | string; description: string }> {
  return [
    {
      label: "Accepted",
      value: report.accepted_count,
      description: "Total rows across all ACCEPTED tasks — the pool eligible for export.",
    },
    {
      label: "Exported",
      value: report.exported_count,
      description: "Rows already included in at least one export manifest (training_data.jsonl).",
    },
    {
      label: "Pending Export",
      value: report.pending_export_count,
      description: "Accepted rows not yet in any export — run the export command to include them.",
    },
    {
      label: "Open Feedback",
      value: report.open_feedback_count,
      description: "Unresolved QC/HR feedback on active (non-terminal) tasks. Must reach zero before export is considered complete.",
    },
    {
      label: "Resolved Feedback",
      value: report.resolved_feedback_count,
      description: "Feedback items that reached annotator consensus (marked resolved in a discussion).",
    },
    {
      label: "Closed Feedback",
      value: report.closed_feedback_count,
      description: "Feedback on completed tasks (ACCEPTED/REJECTED) that was never explicitly resolved — no longer actionable.",
    },
    {
      label: "Human Review",
      value: report.human_review_count,
      description: "Tasks currently awaiting a human accept/reject decision in the HR stage.",
    },
    {
      label: "Export Blockers",
      value: report.validation_blockers.length,
      description: "Accepted tasks that cannot be exported: missing annotation file, or excluded by the last export due to invalid training row format.",
    },
    {
      label: "Outbox",
      value: report.pending_outbox_count,
      description: "Export records queued for delivery to an external system (e.g. a training platform webhook). Waiting to be sent.",
    },
    {
      label: "Dead Letters",
      value: report.dead_letter_outbox_count,
      description: "Outbox records that failed all retries and require manual intervention before delivery can continue.",
    },
  ];
}
