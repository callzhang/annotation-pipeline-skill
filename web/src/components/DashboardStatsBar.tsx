import { useEffect, useState } from "react";
import { fetchDashboardStats, type DashboardStats } from "../api";

interface DashboardStatsBarProps {
  projectId: string | null;
  storeKey: string | null;
}

interface Stat {
  label: string;
  value: number | string;
  tone: "neutral" | "warning" | "critical" | "success";
  hint?: string;
}

export function DashboardStatsBar({ projectId, storeKey }: DashboardStatsBarProps) {
  const [stats, setStats] = useState<DashboardStats | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;

    async function refresh() {
      try {
        const next = await fetchDashboardStats(projectId, storeKey);
        if (!active) return;
        setStats(next);
        setError(null);
      } catch (reason: unknown) {
        if (!active) return;
        setError(reason instanceof Error ? reason.message : "Unable to load stats");
      }
    }

    refresh();
    const timer = setInterval(refresh, 5000);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, [projectId, storeKey]);

  if (!stats && !error) {
    return <div className="dashboard-stats-bar dashboard-stats-bar-loading">Loading stats…</div>;
  }
  if (!stats) {
    return <div className="dashboard-stats-bar dashboard-stats-bar-error">{error}</div>;
  }

  const counts = stats.status_counts;
  const window = stats.throughput_window_minutes || 1;
  const perMin = (stage: string): string => {
    const raw = stats.throughput_per_window[stage] ?? 0;
    const rate = raw / window;
    return rate >= 10 ? rate.toFixed(0) : rate.toFixed(1);
  };
  const hint = `attempts/min, averaged over last ${window} min`;
  const items: Stat[] = [
    { label: "Pending", value: counts.pending ?? 0, tone: "neutral" },
    { label: "Annotating", value: counts.annotating ?? 0, tone: "neutral" },
    { label: "QC", value: counts.qc ?? 0, tone: "neutral" },
    { label: "Arbitrating", value: counts.arbitrating ?? 0, tone: "neutral" },
    { label: "Human Review", value: counts.human_review ?? 0, tone: "warning" },
    { label: "Blocked", value: counts.blocked ?? 0, tone: "critical" },
    { label: "Accepted", value: counts.accepted ?? 0, tone: "success" },
    { label: "Open feedback", value: stats.open_feedback_count, tone: "warning" },
    { label: "Annotation/min", value: perMin("annotation"), tone: "neutral", hint },
    { label: "QC/min", value: perMin("qc"), tone: "neutral", hint },
    { label: "Arbitration/min", value: perMin("arbitration"), tone: "neutral", hint },
  ];

  return (
    <div className="dashboard-stats-bar">
      {items.map((stat) => (
        <div className={`dashboard-stat ${stat.tone}`} key={stat.label} title={stat.hint}>
          <span>{stat.label}</span>
          <strong>{stat.value}</strong>
        </div>
      ))}
    </div>
  );
}
