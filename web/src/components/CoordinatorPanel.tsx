import { FormEvent, useEffect, useMemo, useState } from "react";
import {
  fetchCoordinatorReport,
  postCoordinatorLongTailIssue,
  postCoordinatorRuleUpdate,
} from "../api";
import { buildCoordinatorViewModel } from "../coordinator";
import type { CoordinatorReport } from "../types";

interface CoordinatorPanelProps {
  projectId: string | null;
  storeKey: string | null;
}

interface RuleUpdateFormState {
  source: string;
  summary: string;
  action: string;
  taskIds: string;
}

interface LongTailIssueFormState {
  category: string;
  summary: string;
  recommendedAction: string;
  severity: string;
  taskIds: string;
}

const emptyRuleUpdateForm: RuleUpdateFormState = {
  source: "",
  summary: "",
  action: "",
  taskIds: "",
};

const emptyLongTailIssueForm: LongTailIssueFormState = {
  category: "",
  summary: "",
  recommendedAction: "",
  severity: "warning",
  taskIds: "",
};

export function CoordinatorPanel({ projectId, storeKey }: CoordinatorPanelProps) {
  const [report, setReport] = useState<CoordinatorReport | null>(null);
  const [loading, setLoading] = useState(false);
  const [savingRuleUpdate, setSavingRuleUpdate] = useState(false);
  const [savingIssue, setSavingIssue] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [ruleUpdateForm, setRuleUpdateForm] = useState<RuleUpdateFormState>(emptyRuleUpdateForm);
  const [longTailIssueForm, setLongTailIssueForm] = useState<LongTailIssueFormState>(emptyLongTailIssueForm);

  useEffect(() => {
    if (!projectId) {
      setReport(null);
      setLoading(false);
      setError(null);
      setMessage(null);
      return;
    }

    let active = true;
    setLoading(true);
    fetchCoordinatorReport(projectId, storeKey)
      .then((nextReport) => {
        if (!active) return;
        setReport(nextReport);
        setError(null);
      })
      .catch((reason: unknown) => {
        if (!active) return;
        setReport(null);
        setError(reason instanceof Error ? reason.message : "Unable to load coordinator report");
      })
      .finally(() => {
        if (active) setLoading(false);
      });

    return () => {
      active = false;
    };
  }, [projectId, storeKey]);

  const viewModel = useMemo(() => (report ? buildCoordinatorViewModel(report) : null), [report]);
  const formsDisabled = !projectId || savingRuleUpdate || savingIssue;

  async function refreshReport() {
    if (!projectId) return;
    const nextReport = await fetchCoordinatorReport(projectId, storeKey);
    setReport(nextReport);
  }

  async function submitRuleUpdate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!projectId) {
      setError("Select a project before recording a rule update.");
      return;
    }

    setSavingRuleUpdate(true);
    setMessage(null);
    setError(null);
    try {
      await postCoordinatorRuleUpdate({
        project_id: projectId,
        source: ruleUpdateForm.source.trim(),
        summary: ruleUpdateForm.summary.trim(),
        action: ruleUpdateForm.action.trim(),
        created_by: "coordinator-agent",
        task_ids: parseTaskIds(ruleUpdateForm.taskIds),
      }, storeKey);
      setRuleUpdateForm(emptyRuleUpdateForm);
      await refreshReport();
      setMessage("Rule update recorded");
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : "Unable to record rule update");
    } finally {
      setSavingRuleUpdate(false);
    }
  }

  async function submitLongTailIssue(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!projectId) {
      setError("Select a project before recording a long-tail issue.");
      return;
    }

    setSavingIssue(true);
    setMessage(null);
    setError(null);
    try {
      await postCoordinatorLongTailIssue({
        project_id: projectId,
        category: longTailIssueForm.category.trim(),
        summary: longTailIssueForm.summary.trim(),
        recommended_action: longTailIssueForm.recommendedAction.trim(),
        severity: longTailIssueForm.severity,
        created_by: "coordinator-agent",
        task_ids: parseTaskIds(longTailIssueForm.taskIds),
      }, storeKey);
      setLongTailIssueForm(emptyLongTailIssueForm);
      await refreshReport();
      setMessage("Long-tail issue recorded");
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : "Unable to record long-tail issue");
    } finally {
      setSavingIssue(false);
    }
  }

  if (!projectId) {
    return (
      <section className="coordinator-panel" aria-label="Coordinator">
        <div className="runtime-header">
          <div>
            <h2>Coordinator</h2>
            <p>Select one project to view guidance and record coordinator decisions.</p>
          </div>
        </div>
        <div className="notice compact">No project selected. Coordinator records require a project.</div>
      </section>
    );
  }

  if (loading) return <section className="work-panel">Loading coordinator</section>;
  if (!report || !viewModel) {
    return <section className="work-panel notice compact">{error ?? "Coordinator report unavailable"}</section>;
  }

  return (
    <section className="coordinator-panel" aria-label="Coordinator">
      <div className="runtime-header">
        <div>
          <h2>Coordinator</h2>
          <p>
            {report.project_id ?? projectId} · generated {viewModel.generatedAtLabel}
          </p>
        </div>
      </div>

      {message ? <div className="notice compact success">{message}</div> : null}
      {error ? <div className="notice compact error">{error}</div> : null}

      {viewModel.emptyState ? (
        <div className="coordinator-empty">
          <h3>{viewModel.emptyState.title}</h3>
          <p>{viewModel.emptyState.detail}</p>
        </div>
      ) : null}

      <div className="coordinator-stats" aria-label="Coordinator overview">
        {viewModel.overviewStats.map((stat) => (
          <div className={`coordinator-stat ${stat.tone}`} key={stat.label}>
            <span>{stat.label}</span>
            <strong>{stat.value}</strong>
          </div>
        ))}
      </div>

      <div className="coordinator-layout">
        <section className="coordinator-section">
          <div className="coordinator-section-header">
            <h3>Recommended Actions</h3>
          </div>
          {viewModel.actionRows.length === 0 ? (
            <p className="runtime-muted">No recommended actions.</p>
          ) : (
            <div className="coordinator-row-list">
              {viewModel.actionRows.map((action) => (
                <div className={`coordinator-action ${action.severity}`} key={action.id}>
                  <span>{action.label}</span>
                  <small>{action.severity}</small>
                </div>
              ))}
            </div>
          )}
        </section>

        <section className="coordinator-section">
          <div className="coordinator-section-header">
            <h3>Provider Diagnostics</h3>
          </div>
          {viewModel.providerCards.length === 0 ? (
            <p className="runtime-muted">No provider diagnostics.</p>
          ) : (
            <div className="coordinator-row-list">
              {viewModel.providerCards.map((provider) => (
                <details className={`coordinator-diagnostic ${provider.severity}`} key={provider.id}>
                  <summary>
                    <span>{provider.label}</span>
                    <small>{provider.status}</small>
                  </summary>
                  <p>{provider.message}</p>
                  <dl className="runtime-facts compact">
                    <div>
                      <dt>Targets</dt>
                      <dd>{provider.targetStages.length > 0 ? provider.targetStages.join(", ") : "Unassigned"}</dd>
                    </div>
                  </dl>
                  <div className="coordinator-checks">
                    {provider.checks.map((check) => (
                      <div className={`coordinator-check ${check.status}`} key={check.id}>
                        <span>{check.id}</span>
                        <strong>{check.status}</strong>
                        <p>{check.message}</p>
                      </div>
                    ))}
                  </div>
                </details>
              ))}
            </div>
          )}
        </section>

        <section className="coordinator-section">
          <div className="coordinator-section-header">
            <h3>Rule Updates</h3>
          </div>
          <form className="coordinator-form" onSubmit={submitRuleUpdate}>
            <TextField
              label="Source"
              value={ruleUpdateForm.source}
              disabled={formsDisabled}
              required
              onChange={(value) => setRuleUpdateForm((current) => ({ ...current, source: value }))}
            />
            <TextField
              label="Action"
              value={ruleUpdateForm.action}
              disabled={formsDisabled}
              required
              onChange={(value) => setRuleUpdateForm((current) => ({ ...current, action: value }))}
            />
            <TextField
              label="Task IDs"
              value={ruleUpdateForm.taskIds}
              disabled={formsDisabled}
              placeholder="task-1, task-2"
              onChange={(value) => setRuleUpdateForm((current) => ({ ...current, taskIds: value }))}
            />
            <TextAreaField
              label="Summary"
              value={ruleUpdateForm.summary}
              disabled={formsDisabled}
              required
              onChange={(value) => setRuleUpdateForm((current) => ({ ...current, summary: value }))}
            />
            <button className="primary-button" type="submit" disabled={formsDisabled}>
              {savingRuleUpdate ? "Recording" : "Record rule update"}
            </button>
          </form>
          {viewModel.ruleUpdateRows.length === 0 ? (
            <p className="runtime-muted">No rule updates recorded.</p>
          ) : (
            <div className="coordinator-table">
              {viewModel.ruleUpdateRows.map((row) => (
                <div className="coordinator-record-row" key={row.id}>
                  <strong>{row.summary}</strong>
                  <span>{row.sourceLabel}</span>
                  <span>{row.actionLabel}</span>
                  <span>{row.statusLabel}</span>
                  <small>
                    {row.taskCount} tasks · {row.createdAtLabel} · {row.createdBy}
                  </small>
                </div>
              ))}
            </div>
          )}
        </section>

        <section className="coordinator-section">
          <div className="coordinator-section-header">
            <h3>Long-Tail Issues</h3>
          </div>
          <form className="coordinator-form" onSubmit={submitLongTailIssue}>
            <TextField
              label="Category"
              value={longTailIssueForm.category}
              disabled={formsDisabled}
              required
              onChange={(value) => setLongTailIssueForm((current) => ({ ...current, category: value }))}
            />
            <TextField
              label="Recommended action"
              value={longTailIssueForm.recommendedAction}
              disabled={formsDisabled}
              required
              onChange={(value) => setLongTailIssueForm((current) => ({ ...current, recommendedAction: value }))}
            />
            <label>
              <span>Severity</span>
              <select
                value={longTailIssueForm.severity}
                disabled={formsDisabled}
                onChange={(event) => setLongTailIssueForm((current) => ({ ...current, severity: event.target.value }))}
              >
                <option value="critical">Critical</option>
                <option value="warning">Warning</option>
                <option value="info">Info</option>
              </select>
            </label>
            <TextField
              label="Task IDs"
              value={longTailIssueForm.taskIds}
              disabled={formsDisabled}
              placeholder="task-1, task-2"
              onChange={(value) => setLongTailIssueForm((current) => ({ ...current, taskIds: value }))}
            />
            <TextAreaField
              label="Summary"
              value={longTailIssueForm.summary}
              disabled={formsDisabled}
              required
              onChange={(value) => setLongTailIssueForm((current) => ({ ...current, summary: value }))}
            />
            <button className="primary-button" type="submit" disabled={formsDisabled}>
              {savingIssue ? "Recording" : "Record long-tail issue"}
            </button>
          </form>
          {viewModel.longTailIssueRows.length === 0 ? (
            <p className="runtime-muted">No long-tail issues recorded.</p>
          ) : (
            <div className="coordinator-table">
              {viewModel.longTailIssueRows.map((row) => (
                <div className={`coordinator-record-row ${row.severity}`} key={row.id}>
                  <strong>{row.summary}</strong>
                  <span>{row.categoryLabel}</span>
                  <span>{row.recommendedActionLabel}</span>
                  <span>{row.statusLabel}</span>
                  <small>
                    {row.taskCount} tasks · {row.createdAtLabel} · {row.createdBy}
                  </small>
                </div>
              ))}
            </div>
          )}
        </section>
      </div>
    </section>
  );
}

function TextField(props: {
  label: string;
  value: string;
  disabled?: boolean;
  placeholder?: string;
  required?: boolean;
  onChange: (value: string) => void;
}) {
  return (
    <label>
      <span>{props.label}</span>
      <input
        value={props.value}
        disabled={props.disabled}
        placeholder={props.placeholder}
        required={props.required}
        onChange={(event) => props.onChange(event.target.value)}
      />
    </label>
  );
}

function TextAreaField(props: {
  label: string;
  value: string;
  disabled?: boolean;
  required?: boolean;
  onChange: (value: string) => void;
}) {
  return (
    <label className="wide">
      <span>{props.label}</span>
      <textarea
        value={props.value}
        disabled={props.disabled}
        required={props.required}
        onChange={(event) => props.onChange(event.target.value)}
      />
    </label>
  );
}

function parseTaskIds(value: string): string[] {
  return value
    .split(",")
    .map((taskId) => taskId.trim())
    .filter(Boolean);
}
