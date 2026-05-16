# Changelog

## 2026-05-16

- BREAKING (HR routing): the runtime no longer escalates to HUMAN_REVIEW on
  any "couldn't resolve" outcome. HR is now reserved strictly for genuine
  arbiter uncertainty — verdicts with `confidence` label `tentative` or
  `unsure` (i.e. `arb["unresolved"] > 0`). All other non-terminal arbiter
  outcomes (codex subprocess error, missing `corrected_annotation`, JSON
  parse fail, non-verbatim correction, unknown verdict value) are
  classified as **mechanical failures**: the task stays in `arbitrating`
  status for re-pickup, and the next worker cycle re-runs the arbiter on
  the same annotation (the annotation didn't change — there's no point
  re-running the annotator).
- New `arb["mechanical_fail"]` counter in `_arbitrate_and_apply` outcome.
  The "qc-wins-but-no-fix" case used to bump `unresolved` (forcing HR);
  now bumps `mechanical_fail` (allowing retry).
- `SubagentRuntime.ARBITER_MECHANICAL_RETRY_CAP = 3`. The runtime tracks
  consecutive mechanical retries per task in `task.metadata.arbiter_mechanical_retries`.
  When the counter reaches 3, the task is forced to HR with a clear reason.
  Counter is persistent across restarts.
- Provider fallback: `_generate_async` now wraps the target client and
  retries via the `fallback` target on rate-limit errors
  (`openai.RateLimitError`, `status_code == 429`, or message strings
  matching "rate limit"/"429"/"too many requests"). No circuit breaker;
  try-first semantics.
- Codex CLI invocation gains `--ignore-rules` and `--config enabled_tools=[]`
  to suppress user-installed rule files and tool-use, keeping arbiter calls
  pure-JSON and faster.
- Worker bail reset: when an `annotating` worker raises (rate-limit etc.),
  the `finally` block resets the task to `pending` instead of leaving
  `annotating` for the smart-resume path. Eliminates a tight loop that
  produced ~700 spurious audit events/min during MiniMax 429 storms.
- Audit script `scripts/audit_verbatim_accepted.py` to scan ACCEPTED tasks
  for verbatim violations (5% audit found ~11% violations) and route
  them back to ARBITRATING under the new arbiter / verbatim guard.
- HR drawer banner: drop redundant "Routed to human review." fallback line.
- `docs/RUNTIME_DESIGN.md` removed; content merged into
  `TECHNICAL_ARCHITECTURE.md` §6 (state machine), §10 (runtime), §11
  (execution model), §12 (error model), §13 (config) — these sections
  now describe the actual implementation rather than the original
  aspirational design.

## 2026-05-11

- Auto-escalate tasks to HUMAN_REVIEW after `RuntimeConfig.max_qc_rounds` (default 3) QC rejections, replacing the silent infinite-loop hazard. Triggered by counting `FeedbackRecord(source_stage=QC)` per task; configurable via `runtime.max_qc_rounds` in `workflow.yaml`.
- JSON Schema gate on all writes that produce annotation ground truth:
  - Annotator subagent output is parsed and validated against `task.source_ref.payload.annotation_guidance.output_schema`. Failures record a BLOCKING `FeedbackRecord(category="schema_invalid", source_stage=VALIDATION)` and return the task to PENDING. Tasks without an `output_schema` are passed through unchanged.
  - Human review correction (new endpoint `POST /api/tasks/<id>/human_review_correction` and CLI `apl human-review correct ...`) validates the submitted answer against the same schema. Failures return 400 with structured error list. Human-side writes require an `output_schema` and fail loudly with `missing_schema` if absent.
- New `human_review_answer` artifact kind. Export service prefers it over `annotation_result` when both exist; exported training rows include `human_authored: bool`.
- New dependency: `jsonschema>=4.0`.

## 2026-05-10

- BREAKING: replaced JSON/JSONL `FileStore` with `SqliteStore` (single
  `db.sqlite` per workspace, WAL mode, per-thread connections). Indexed
  queries on `(pipeline_id, status, created_at)` replace full-directory
  scans for hot paths in `coordinator_service`, `readiness_service`,
  `export_service`, `outbox_dispatch_service`, and `subagent_cycle`.
- New CLI: `db init`, `db status`, `db backup`, `db dump-json`.
- Migration: run
  `PYTHONPATH=. python scripts/migrate_filestore_to_sqlite.py
  --src <old-root> --dst <new-root>` once; the script archives the
  source tree to `backups/genesis-YYYYMMDD/` for recovery.
- Atomic runtime lease acquisition via `UNIQUE(task_id, stage)` constraint
  (replaces filesystem `open("x")` trick).
- `RuntimeLease`, `OutboxRecord` dispatcher, and task scheduler now use
  indexed SQL queries instead of in-memory filtering.
- Runtime monitoring (`heartbeat.json`, `cycle_stats.jsonl`,
  `runtime_snapshot.json`) remains file-based.
- `FileStore` retained at `store/file_store.py` solely for the migration
  script; will be removed in a future release.

## v0.1.0 - 2026-05-05

Initial local-first release for an agent-operated annotation pipeline skill.

### Added

- Installable `SKILL.md` for algorithm-engineer annotation projects.
- Python package and `annotation-pipeline` CLI.
- File-backed task store with tasks, attempts, artifacts, audit events, feedback, feedback discussions, outbox records, exports, runtime snapshots, provider config, and Coordinator records.
- JSONL task ingestion, external HTTP task pull, status/submit outbox, readiness reports, and training-data export.
- Configurable provider profiles for OpenAI Responses API, OpenAI-compatible APIs, Codex CLI, and Claude CLI.
- Monitored local runtime for annotation, deterministic validation, QC, retry/heartbeat/capacity reporting, and feedback-driven reruns.
- Optional Human Review after QC with `accept`, `reject`, and `request_changes`.
- Consensus-based annotator/QC feedback discussions.
- React/Vite dashboard with Kanban, Runtime, Readiness, Outbox, Providers, Coordinator, Configuration, Event Log, task details, and image bounding-box preview support.
- Clean agent handoff verification through `scripts/verify_agent_handoff.sh`.
- Real provider smoke scripts for Codex and DeepSeek.
- Memory-ner truth evaluation through `scripts/verify_memory_ner_truth_eval.sh`.
- Memory-ner accepted-state E2E through `scripts/verify_memory_ner_accepted_e2e.sh`.
- Memory-ner dashboard UI acceptance verification through `scripts/verify_memory_ner_ui_acceptance.sh`.
- Active learning/RL workflow design document for the next implementation phase.
- Runtime QC parsing for model responses wrapped in JSON markdown fences.
- Per-task QC sampling policy with `--qc-sample-count`, `--qc-sample-ratio`, and external source QC settings.
- Dashboard editing for task QC policies with audit events.
- File-backed runtime leases, missing snapshot failure reporting, operator-stage Kanban read model, strict QC parse-error handling, provider failure taxonomy, and indexed dashboard summaries.
- Read-only annotation manager v2 import that creates new QC-stage review tasks from old accepted/merged `.annotated.jsonl` outputs without mutating the source project.

### Known Limits

- The core is local-first and file-backed; it does not include a distributed scheduler.
- Real multimodal rendering is limited to image bounding-box preview artifacts.
- Active learning/RL workflow support is designed but not implemented in v0.1.0.
- GitHub repository metadata must be configured outside the codebase when GitHub CLI authentication is unavailable.
