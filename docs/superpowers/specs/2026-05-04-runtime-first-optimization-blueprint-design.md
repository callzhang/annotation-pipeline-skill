# Runtime-First Optimization Blueprint Design

## Purpose

This spec defines the next functional optimization blueprint for `annotation-pipeline-skill`.
It compares the current local implementation against the proven operational behavior in
`/home/derek/Projects/memory-ner/annotation manager/` and turns the reusable lessons into
a four-phase development roadmap.

The goal is not to port the old `memory-ner` implementation. The old manager uses
Streamlit, Redis, Dramatiq, systemd units, and NER-specific validators. This project
should keep a reusable local-first core, a TypeScript dashboard, task-type-agnostic
domain models, and replaceable provider/runtime backends.

## Reference Lessons From `memory-ner`

The important lessons from `memory-ner/annotation manager` are operational, not UI-specific:

- Project isolation matters. Switching projects must not mix task roots, settings, runtime state, or merge outputs.
- Business task state and runtime state must be separate. Task JSON is audit truth; runtime read models describe liveness, queues, leases, and capacity.
- A dashboard is only useful if it reads the same runtime truth as the scheduler and monitor.
- Runtime health needs heartbeat, cycle stats, active worker counts, stale detection, due retry tracking, and capacity enforcement.
- Provider failures are usually retry/fallback events, not immediate task rejection.
- QC feedback should be a discussion between annotator and QC, including partial agreement and rule-grounded disagreement.
- Deterministic validation should run before model QC and before merge/export whenever possible.
- Accepted labels are not enough for model training; the system must produce export manifests, validation summaries, and traceable training data packages.

## Current Local Baseline

The current feature branch already has useful foundations:

- Durable task, attempt, artifact, feedback, external outbox, and audit event models.
- File-system `FileStore`.
- JSONL task import with `pipeline_id` project grouping.
- TypeScript + React Kanban dashboard.
- Project selector in the dashboard, backed by `pipeline_id`.
- Config UI for annotation rules, annotators, LLM profiles, external task API, callbacks, and workflow YAML.
- Subagent provider profiles for OpenAI Responses API and local CLI providers such as Codex.
- Hardened Codex local CLI isolation using isolated `CODEX_HOME`, isolated `HOME`, `--ignore-user-config`, `--ephemeral`, and disabled apps/plugins.
- Feedback discussion records and consensus-based acceptance between annotator and QC.
- Event log and task detail drawer with source, attempts, artifacts, feedback, discussions, and consensus.

The main gap is runtime reliability. Current `run-cycle` is still a single local execution path. It does not yet provide a durable runtime read model, heartbeat, cycle stats, capacity accounting, retry drain monitoring, stale active run detection, or long-running local scheduler loop.

## Architecture

The optimized system should have four layers.

### Business Task State

Business task state remains in the core domain:

- `Task`
- `Attempt`
- `ArtifactRef`
- `FeedbackRecord`
- `FeedbackDiscussionEntry`
- `AuditEvent`
- `ExternalTaskRef`
- `OutboxRecord`

This layer answers: what is the task, what happened, why did it move, and what evidence was produced?

### Runtime State

Runtime state should be modeled separately from task state. It answers: is the scheduler alive, what is queued, what is active, what is stale, what retries are due, and whether capacity rules are being respected.

The initial local runtime can use files under `.annotation-pipeline/runtime/`. It should not require Redis, Docker, systemd, or Dramatiq.

### Coordinator Services

Coordinator services are the agent-facing operational layer. They should help an agent:

- Start and inspect local runtime cycles.
- Summarize project health.
- Detect stuck queues and stale work.
- Remind the user when Human Review is needed.
- Summarize feedback and rule changes.
- Report whether training data is ready for an algorithm engineer.

### Dashboard/API

The TypeScript dashboard should consume HTTP API read models. It should not scan task files directly and should not call providers.

The dashboard must eventually expose:

- Project selector.
- Kanban view.
- Runtime health.
- Queue/capacity summary.
- Task detail timeline.
- Feedback discussion and agreement state.
- Config editing.
- Event log.
- Export/training data readiness.

## Phase 1: Runtime Reliability

### Goal

Upgrade the local runtime from a one-shot task loop into a monitored local scheduler that can be run repeatedly, inspected, and tested against the monitoring behavior captured in `VERIFY_MANAGER_CYCLES_TEST_PLAN.md`.

### Features

Add a runtime snapshot at:

```text
.annotation-pipeline/runtime/runtime_snapshot.json
```

The snapshot should include:

- `generated_at`
- `runtime_status`
- `cycle_stats`
- `queue_counts`
- `active_runs`
- `capacity`
- `stale_tasks`
- `due_retries`
- project and pipeline summaries

Add local runtime commands:

```bash
annotation-pipeline runtime once --project-root ./demo-project
annotation-pipeline runtime run --project-root ./demo-project
annotation-pipeline runtime status --project-root ./demo-project
```

`once` runs one scheduling cycle. `run` loops locally with sleep intervals. `status` prints the runtime snapshot.

Add runtime config to YAML:

```yaml
runtime:
  max_concurrent_tasks: 4
  max_starts_per_cycle: 2
  stale_after_seconds: 600
  retry_delay_seconds: 3600
```

Phase 1 capacity enforcement is local and process-internal. It should be deterministic and testable, but it does not need distributed leases.

### Runtime Behavior

The local runtime should:

- Start pending work while respecting `max_concurrent_tasks` and `max_starts_per_cycle`.
- Record heartbeat and cycle stats.
- Mark active runs in the runtime snapshot.
- Detect stale active runs.
- Detect due retries from `next_retry_at` or a future structured retry plan.
- Keep due retry work visible until it drains.
- Expose runtime health through the API.

Phase 1 may detect stale active runs without automatically resolving every case. Automatic stale-run recovery can be added after the read model is reliable.

### API

Add:

```text
GET /api/runtime
GET /api/runtime/cycles
POST /api/runtime/run-once
```

`GET /api/runtime` returns the latest runtime snapshot. `POST /api/runtime/run-once` runs one local cycle and returns an action result plus the new snapshot.

### Verification

Phase 1 must turn the P0 scenarios in `VERIFY_MANAGER_CYCLES_TEST_PLAN.md` into tests:

- Runtime down.
- Missing heartbeat.
- Stale heartbeat.
- Stale active task.
- Due retry not draining.
- Capacity exceeded.
- Runnable backlog with no active progress.
- Annotated or QC-ready task not moving downstream when capacity exists.

## Phase 2: QC Feedback Depth

### Goal

Make QC feedback a structured, multi-turn quality loop between annotator and QC instead of a simple pass/fail outcome.

### Features

Extend feedback discussion into a per-feedback timeline with explicit resolution states:

- `agree`
- `partial_agree`
- `disagree`
- `resolved`
- `still_failing`
- `annotator_disagreement_accepted`
- `annotator_disagreement_rejected`

On QC failure, the next annotation prompt must include unresolved feedback and relevant discussion history.

Annotator update modes should be represented explicitly:

- Manual edit.
- Batch/code update.
- Model-assisted reannotation.

Add validator lint policy artifacts. QC can suggest policy changes, but those changes should not become active from one QC opinion alone. Policy activation should require configured agreement thresholds between QC and annotator.

Human Review should remain optional and should be triggered by configuration or unresolved disagreement, not by every QC failure.

### Verification

Tests should prove:

- A task can move through annotation, QC feedback, annotation retry, QC agreement, and accepted.
- Task detail shows every feedback turn, disputed point, and final resolution.
- Validator lint policy does not activate without required agreement.

## Phase 3: Operator Dashboard UX

### Goal

Turn the TypeScript dashboard into a practical project operations console.

### Features

Add a runtime health panel:

- Heartbeat age.
- Cycle age.
- Active runs.
- Capacity.
- Stale task count.
- Due retry count.
- Runtime errors.

Add queue and capacity panels:

- Counts by stage.
- Active vs capacity.
- Starts per cycle.
- Retry backlog.

Enhance task detail:

- Raw source payload.
- Annotation artifacts.
- QC artifacts.
- Feedback discussion timeline.
- Retry history.
- Provider diagnostics and token usage.

Improve event log:

- Project filter.
- Task filter.
- Status/stage filter.

Improve large-list behavior:

- Pagination or virtualization for large pending and accepted views.
- Stable project switching without reloading or rewriting tasks.

Add runtime config controls after Phase 1 API is stable.

### Verification

Tests should prove:

- Dashboard displays runtime health from `/api/runtime`.
- Project switching filters kanban, runtime, and event views consistently.
- Large accepted/pending sets do not require rendering every card at once.
- Users can inspect raw data, annotation output, QC feedback, retries, and provider usage without opening JSON files manually.

## Phase 4: Merge And Training Data Output

### Goal

Turn accepted tasks into traceable training data packages that an algorithm engineer can use directly.

### Features

Add merge/export gate after acceptance:

- Accepted means QC-consensus accepted.
- Exported means validation passed and training data was written.

Add `ExportManifest` artifacts containing:

- Export id.
- Project/pipeline id.
- Source files.
- Task ids included and excluded.
- Artifact ids.
- Annotation rules version or content hash.
- Schema/validator version.
- Validation summary.
- Known limitations.
- Output paths.

Support JSONL training data output as the first sink.

Support callback/submit outbox:

- Status callback.
- Submit accepted/exported result.
- Retry and dead-letter state.

Add coordinator readiness report:

- Accepted count.
- Exported count.
- Open feedback count.
- Human Review count.
- Validation blockers.
- Export path.
- Recommended next action.

### Verification

Tests should prove:

- Accepted tasks are not exported if merge/export validation fails.
- Export manifest records included/excluded tasks and validation summaries.
- Callback failures enter outbox retry/dead-letter without losing internal state.
- A complete local project can produce a traceable JSONL training data package.

## Cross-Cutting Data Model Additions

Add or extend these models over the four phases:

- `RuntimeSnapshot`
- `RuntimeCycleStats`
- `ActiveRun`
- `RetryPlan`
- `FeedbackDiscussionEntry` resolution fields
- `ValidatorLintPolicyArtifact`
- `ExportManifest`

These models should be serializable to JSON and should have unit tests for save/load behavior.

## Error Taxonomy

Use structured error categories:

- `provider_limit`
- `provider_unavailable`
- `timeout`
- `validation_failed`
- `qc_feedback_open`
- `human_review_required`
- `runtime_stale`
- `capacity_exceeded`
- `external_callback_failed`
- `export_validation_failed`

Provider and timeout failures should normally create retry plans, not immediate task rejection.

Validation and QC failures should carry feedback back to annotation.

Human Review should be a configurable gate.

Export failures should not change accepted business state. They should create failed export records or manifests.

Outbox failures should enter retry or dead-letter state without losing internal task state.

## Testing Strategy

Each phase should include:

- Unit tests for models, state transitions, and snapshot builders.
- Service tests for runtime cycles, feedback resolution, and export manifests.
- API tests for read models and action results.
- Frontend tests for pure helpers and critical rendering states.
- Integration smoke tests over a temporary project root.

The Phase 1 integration smoke path should be:

```text
init -> create-tasks -> runtime once -> inspect /api/runtime and /api/kanban
```

Later phases extend this path with:

```text
QC feedback -> annotation retry -> agreement -> accepted -> export -> readiness report
```

## Delivery Plan

Implement as four separate development plans:

1. Runtime reliability.
2. QC feedback depth.
3. Operator dashboard UX.
4. Merge and training data output.

Each phase must be independently testable and mergeable. Phase 1 is the foundation and should be implemented first because later QC, dashboard, and export behavior depend on reliable runtime truth.

## Non-Goals

The blueprint does not require:

- Streamlit.
- Redis.
- Dramatiq.
- Docker.
- systemd.
- Multi-machine scheduling.
- NER-specific validators in core.
- Reintroducing product-only placeholder runtimes or providers. Scripted doubles are still acceptable in tests.

Those can exist as optional adapters or external deployments later, but the open-source skill core should remain local-first and task-type agnostic.
