import type { CoordinatorReport, ProviderDiagnostic } from "./types";

export type CoordinatorSeverity = "critical" | "warning" | "info" | "ok";
export type CoordinatorTone = "critical" | "warning" | "success" | "neutral";

export interface CoordinatorStat {
  label: string;
  value: number;
  tone: CoordinatorTone;
}

export interface CoordinatorProviderCard {
  id: string;
  label: string;
  severity: CoordinatorSeverity;
  status: ProviderDiagnostic["status"] | "error";
  checks: ProviderDiagnostic["checks"];
  targetStages: string[];
  message: string;
}

export interface CoordinatorActionRow {
  id: string;
  label: string;
  severity: CoordinatorSeverity;
}

export interface CoordinatorEmptyState {
  title: string;
  detail: string;
}

export interface CoordinatorViewModel {
  generatedAtLabel: string;
  overviewStats: CoordinatorStat[];
  providerCards: CoordinatorProviderCard[];
  actionRows: CoordinatorActionRow[];
  emptyState: CoordinatorEmptyState | null;
}

const severityRank: Record<CoordinatorSeverity, number> = {
  critical: 0,
  warning: 1,
  info: 2,
  ok: 3,
};

const actionLabels: Record<string, string> = {
  remind_user_to_complete_human_review: "Complete Human Review",
  resolve_annotator_qc_feedback: "Resolve annotator/QC feedback",
  inspect_blocked_tasks: "Inspect blocked tasks",
  complete_human_review: "Complete Human Review",
  fix_export_blockers: "Fix export blockers",
  run_annotation_runtime: "Run annotation runtime",
  export_training_data: "Export training data",
  export_or_deliver_training_data: "Deliver training data",
  deliver_training_data: "Deliver training data",
  inspect_project_state: "Inspect project state",
  drain_external_outbox: "Drain external outbox",
  inspect_dead_letter_outbox: "Inspect dead-letter outbox",
};

export function classifyProviderDiagnostic(diagnostic: ProviderDiagnostic): CoordinatorSeverity {
  if (diagnostic.status === "error") {
    return "critical";
  }
  if (diagnostic.status === "warning") {
    return "warning";
  }
  return "ok";
}

export function formatProviderLabel(providerId: string): string {
  return providerId
    .split(/[_-]+/)
    .filter(Boolean)
    .map((part) => {
      const upper = part.toUpperCase();
      if (upper === "LLM" || upper === "CLI" || upper === "API" || upper === "QC") {
        return upper;
      }
      return `${part.charAt(0).toUpperCase()}${part.slice(1)}`;
    })
    .join(" ");
}

export function formatTimestampRecency(timestamp: string | null | undefined, now: Date = new Date()): string {
  if (!timestamp) {
    return "Unknown time";
  }

  const then = Date.parse(timestamp);
  if (!Number.isFinite(then)) {
    return "Unknown time";
  }

  const seconds = Math.max(0, Math.floor((now.getTime() - then) / 1000));
  if (seconds < 60) {
    return "Just now";
  }

  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) {
    return `${minutes}m ago`;
  }

  const hours = Math.floor(minutes / 60);
  if (hours < 48) {
    return `${hours}h ago`;
  }

  const days = Math.floor(hours / 24);
  if (days < 14) {
    return `${days}d ago`;
  }

  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
    timeZone: "UTC",
  }).format(new Date(then));
}

export function formatActionLabel(action: string): string {
  return actionLabels[action] ?? formatTitleLabel(action);
}

export function buildCoordinatorViewModel(report: CoordinatorReport, now: Date = new Date()): CoordinatorViewModel {
  return {
    generatedAtLabel: formatTimestampRecency(report.generated_at, now),
    overviewStats: buildOverviewStats(report),
    providerCards: buildProviderCards(report),
    actionRows: buildActionRows(report.recommended_actions),
    emptyState: coordinatorEmptyState(report),
  };
}

export function buildOverviewStats(report: CoordinatorReport): CoordinatorStat[] {
  return [
    { label: "Pending", value: report.status_counts.pending ?? 0, tone: "neutral" },
    { label: "Accepted", value: report.status_counts.accepted ?? 0, tone: "success" },
    { label: "Human Review", value: report.human_review_task_ids.length, tone: "warning" },
    { label: "Blocked", value: report.blocked_task_ids.length, tone: "critical" },
    { label: "Open feedback", value: report.open_feedback_count, tone: "warning" },
    { label: "Outbox pending", value: report.outbox_counts.pending, tone: "warning" },
  ];
}

export function buildProviderCards(report: CoordinatorReport): CoordinatorProviderCard[] {
  const targets = report.provider_diagnostics.targets ?? {};
  const configErrorCard =
    !report.provider_diagnostics.config_valid && report.provider_diagnostics.error
      ? [
          {
            id: "provider_config",
            label: "Provider configuration",
            severity: "critical" as const,
            status: "error" as const,
            checks: [
              {
                id: "provider_config",
                status: "error" as const,
                message: report.provider_diagnostics.error,
              },
            ],
            targetStages: [],
            message: report.provider_diagnostics.error,
          },
        ]
      : [];

  const diagnosticCards = Object.entries(report.provider_diagnostics.diagnostics)
    .map(([id, diagnostic]) => {
      const targetStages = Object.entries(targets)
        .filter(([, target]) => target === id)
        .map(([stage]) => formatTitleLabel(stage));

      return {
        id,
        label: formatProviderLabel(id),
        severity: classifyProviderDiagnostic(diagnostic),
        status: diagnostic.status,
        checks: diagnostic.checks,
        targetStages,
        message: diagnostic.checks.find((check) => check.status !== "ok")?.message ?? "Provider checks passed",
      };
    })
    .sort(compareProviderCards);

  return [...configErrorCard, ...diagnosticCards].sort(compareProviderCards);
}

export function buildActionRows(actions: string[]): CoordinatorActionRow[] {
  return actions.map((action) => ({
    id: action,
    label: formatActionLabel(action),
    severity: action === "complete_human_review" || action === "inspect_dead_letter_outbox" ? "warning" : "info",
  }));
}

export function coordinatorEmptyState(report: CoordinatorReport): CoordinatorEmptyState | null {
  const hasActivity =
    report.task_count > 0 ||
    report.open_feedback_count > 0 ||
    report.blocking_feedback_count > 0 ||
    report.outbox_counts.pending > 0 ||
    report.outbox_counts.dead_letter > 0 ||
    Object.keys(report.provider_diagnostics.diagnostics).length > 0 ||
    report.recommended_actions.length > 0;

  if (hasActivity) {
    return null;
  }

  return {
    title: "No coordinator activity yet",
    detail: "Coordinator guidance will appear after tasks, feedback, or provider checks exist.",
  };
}

function compareProviderCards(left: CoordinatorProviderCard, right: CoordinatorProviderCard): number {
  const bySeverity = severityRank[left.severity] - severityRank[right.severity];
  if (bySeverity !== 0) {
    return bySeverity;
  }
  return left.label.localeCompare(right.label);
}

function formatTitleLabel(value: string): string {
  const words = value.split(/[_-]+/).filter(Boolean);
  if (words.length === 0) {
    return value;
  }

  return words
    .map((word, index) => {
      const upper = word.toUpperCase();
      if (upper === "LLM" || upper === "CLI" || upper === "API" || upper === "QC") {
        return upper;
      }
      if (index > 0) {
        return word.toLowerCase();
      }
      return `${word.charAt(0).toUpperCase()}${word.slice(1).toLowerCase()}`;
    })
    .join(" ");
}
