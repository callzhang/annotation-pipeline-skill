# Annotation Pipeline Skill Design Spec

## Status

This spec records the approved design direction for the first implementation cycle of `annotation-pipeline-skill`.

The project builds a reusable local-first annotation pipeline skill. It is inspired by `/home/derek/Projects/memory-ner/annotation manager/`, but the implementation must remain independent, task-type agnostic, and suitable for open-source reuse.

## Goals

- Provide a durable task model for batch annotation pipelines.
- Support a multi-stage flow: prepare, annotate, validate, QC, optional Human Review, repair, accept or reject, merge.
- Provide a Vite + React + TypeScript Kanban dashboard for operational control.
- Support external task APIs through pull, status callback, and submit result operations.
- Let users configure LLM providers, stage routes, and annotator capabilities without hard-coding provider-specific behavior into core logic.
- Preserve auditability through append-only events, attempts, artifacts, feedback records, and external API outbox records.
- Leave a clear extension path for multimodal annotation such as image bounding boxes, video tracks, and point-cloud boxes.

## Non-Goals

- Do not implement the dashboard in Streamlit.
- Do not use Redis, Docker, systemd, or provider-specific CLIs in the first implementation cycle.
- Do not build a full Label Studio replacement for dense manual annotation.
- Do not implement webhook ingestion in MVP.
- Do not let the UI write provider, stage-route, or annotator YAML in MVP.
- Do not hard-code semantic keyword matching for annotator selection, routing, task classification, or intent detection.
- Do not port the old `memory-ner` code wholesale.

## MVP Scope

### In Scope

- Python package skeleton under `annotation_pipeline_skill/`.
- File-system-backed task store.
- In-process fake runtime for deterministic tests.
- Local subprocess runtime contract, even if the first tests use the fake runtime.
- Core domain models and validated state transitions.
- Append-only audit events.
- Attempts and artifact references.
- Feedback records for QC and Human Review.
- External task reference and outbox records.
- YAML-backed configuration:
  - `providers.yaml`
  - `stage_routes.yaml`
  - `annotators.yaml`
  - `external_tasks.yaml`
- CLI commands for init, doctor, creating tasks, external pull, running cycles, and serving dashboard data.
- Minimal HTTP API for the dashboard.
- Vite + React + TypeScript dashboard with Kanban columns and detail drawer.
- UI read-only settings panels for provider routes and annotator capability matching, with validation and test-call actions.
- Text annotation core plus a preview artifact contract that can represent image bounding-box previews.

### Deferred

- UI editing and saving of YAML configuration.
- Webhook ingestion from external task systems.
- Real provider SDK integrations beyond a pluggable client contract and test/fake clients.
- Real VC detection model execution.
- Full video and point-cloud editing tools.
- Distributed queueing and multi-host worker coordination.
- Authentication and multi-user permissions.

## Recommended Tech Stack

- Backend/core: Python 3.11+.
- Tests: `pytest`.
- CLI: `typer` if dependency installation is acceptable; otherwise `argparse` for zero extra runtime dependency.
- Dashboard API: a small ASGI app if FastAPI/Starlette is introduced; otherwise a narrow standard-library HTTP API for MVP.
- Frontend: Vite + React + TypeScript.
- UI layout: Kanban board plus detail drawer, not a list-first interface.
- Styling: lightweight CSS modules or plain CSS in the Vite app for the first cycle.

The first implementation plan should prefer fewer dependencies unless they materially reduce complexity.

## User Story

As a data engineer, I install `annotation-pipeline-skill`, initialize a new project, configure task source and model providers, open a Kanban dashboard, run annotation cycles, review QC feedback, trigger repair when needed, and submit accepted results back to an external task system.

End-to-end flow:

1. Install the skill.
2. Run `annotation-pipeline init` in a project directory.
3. Configure data input, providers, stage routes, annotators, and optional external task API in YAML.
4. Run `annotation-pipeline doctor` to validate config, store paths, and dashboard readiness.
5. Create local tasks from JSONL or pull external tasks through an adapter.
6. Run the local scheduler or fake runtime cycle.
7. Open the React Kanban dashboard.
8. Inspect task cards by state, modality, selected annotator, provider route, feedback count, and retry state.
9. Open a task detail drawer to inspect attempts, artifacts, feedback, preview evidence, and external API sync state.
10. Let deterministic validation run before model QC.
11. Let QC either accept, reject, require repair, or route a QC-passed task to optional Human Review.
12. Let annotators use feedback to rerun in one of three ways: bulk code repair, annotator rerun, or manual annotation.
13. Merge accepted tasks and submit results through the external task outbox.

## Core Domain Model

### Task

`Task` is the durable business object. It is independent from process or worker state.

Required fields:

- `task_id`
- `pipeline_id`
- `source_ref`
- `external_ref`
- `modality`
- `annotation_requirements`
- `selected_annotator_id`
- `status`
- `current_attempt`
- `created_at`
- `updated_at`
- `active_run_id`
- `next_retry_at`
- `metadata`

### Attempt

`Attempt` records one execution of one stage.

Required fields:

- `attempt_id`
- `task_id`
- `index`
- `stage`
- `status`
- `started_at`
- `finished_at`
- `provider_id`
- `model`
- `effort`
- `route_role`
- `summary`
- `error`
- `artifacts`

### ArtifactRef

`ArtifactRef` points to data produced or consumed by a stage.

Required fields:

- `artifact_id`
- `task_id`
- `kind`
- `path`
- `content_type`
- `created_at`
- `metadata`

Artifact kinds should be explicit and structured, for example:

- `raw_slice`
- `annotation_result`
- `validation_report`
- `qc_report`
- `feedback_bundle`
- `image_bbox_preview`
- `merge_result`
- `external_submit_payload`

### FeedbackRecord

`FeedbackRecord` is append-only. It is the first-class object that connects QC/Human Review findings to annotator repair behavior.

Required fields:

- `feedback_id`
- `task_id`
- `attempt_id`
- `source_stage`: `validation`, `qc`, or `human_review`
- `severity`: `info`, `warning`, `error`, or `blocking`
- `category`
- `message`
- `target`
- `suggested_action`
- `created_at`
- `created_by`
- `metadata`

The repair service must build a compact feedback bundle for the next annotator attempt. The annotator can choose:

- `bulk_code_repair`
- `annotator_rerun`
- `manual_annotation`
- `reject`

### ExternalTaskRef

`ExternalTaskRef` links internal tasks to external task systems.

Required fields:

- `system_id`
- `external_task_id`
- `source_url`
- `idempotency_key`
- `last_status_posted`
- `last_status_posted_at`
- `submit_attempts`

External API calls must be mediated by an integration service and an outbox. Core task models may store references, but they must not contain HTTP client logic.

### AnnotatorProfile

`AnnotatorProfile` describes the capabilities of an annotator, model, service, or human-assisted adapter.

Required fields:

- `annotator_id`
- `display_name`
- `modalities`
- `annotation_types`
- `input_artifact_kinds`
- `output_artifact_kinds`
- `provider_route_id`
- `external_tool_id`
- `preview_renderer_id`
- `human_review_policy_id`
- `fallback_annotator_id`
- `enabled`
- `metadata`

Annotator selection must match structured task requirements against structured profile fields. It must not infer semantic meaning from free-text keywords.

## State Machine

MVP task states:

- `draft`
- `ready`
- `annotating`
- `validating`
- `qc`
- `human_review`
- `repair`
- `accepted`
- `rejected`
- `merged`
- `blocked`
- `cancelled`

Required transitions:

- `draft -> ready`
- `ready -> annotating`
- `annotating -> validating`
- `validating -> qc`
- `validating -> repair`
- `validating -> rejected`
- `qc -> accepted`
- `qc -> human_review`
- `qc -> repair`
- `qc -> rejected`
- `human_review -> accepted`
- `human_review -> repair`
- `human_review -> rejected`
- `repair -> annotating`
- `repair -> validating`
- `accepted -> merged`
- any active state may move to `blocked` through a runtime or configuration failure
- terminal states may not move without an explicit administrative transition

Every transition must write an audit event containing:

- previous state
- next state
- actor
- reason
- stage
- attempt id if applicable
- timestamp
- metadata

## Human Review

The optional human stage is named `Human Review`, not `Preview`.

Placement:

- Human Review occurs after QC.
- It only sees tasks that passed or conditionally passed QC.
- It is not part of validation or QC itself.

Activation:

- Pipeline config may require Human Review for all QC-passed tasks.
- QC policy may route an individual QC-passed task to Human Review when structured risk metadata requires it.
- If neither condition applies, QC pass transitions directly to `accepted`.

Human Review uses task details, feedback history, and preview artifacts as evidence. It can accept, reject, or request repair.

## Multimodal Extension Model

The framework core must support modality metadata without implementing every modality editor in MVP.

Initial modalities:

- `text`
- `image`
- `video`
- `point_cloud`

Initial annotation types:

- `entity_span`
- `classification`
- `relation`
- `structured_json`
- `bounding_box`
- `segmentation`
- `keypoint`
- `track`
- `box_3d`

Example future image flow:

1. A task declares `modality=image` and `annotation_types=["bounding_box"]`.
2. Annotator selection chooses a profile that supports image bounding boxes.
3. The selected annotator calls a VC detection model through its provider or external tool adapter.
4. The annotation result is saved as structured bounding-box data.
5. A `PreviewRenderer` creates an `image_bbox_preview` artifact with boxes rendered on the image.
6. QC and optional Human Review inspect the rendered evidence.
7. The preview artifact never decides task state by itself; policies and human decisions decide state.

## External Task API

MVP supports three external operations:

- Pull tasks.
- Post status changes.
- Submit accepted or merged results.

Webhook ingestion is deferred.

External API requirements:

- Pull operations must be idempotent by `system_id + external_task_id` or explicit `idempotency_key`.
- Status posts and result submissions must be written to an outbox before network execution.
- Outbox records must have retry count, last error, next retry time, and terminal dead-letter status.
- External status updates must be derived from internal task transitions, not from runtime worker state.
- Submit result must include enough artifact references or payload data for the external system to reconcile the task.

## Provider And Route Configuration

YAML is the canonical source of truth in MVP.

Files:

- `providers.yaml`: provider registry and secret references.
- `stage_routes.yaml`: stage-to-provider route selection.
- `annotators.yaml`: capability profiles and annotator-to-route bindings.

The UI may:

- display config
- validate references
- show whether a task's requirements match an annotator profile
- test provider availability
- test route resolution

The UI may not write these YAML files in MVP.

Provider secrets must be stored as references such as `env:OPENAI_API_KEY`. The project must not persist secret values.

## Dashboard UX

The dashboard is a Kanban interface.

Required columns:

- Ready
- Annotating
- Validating
- QC
- Human Review
- Repair
- Accepted
- Rejected
- Merged

Each task card should show:

- task id
- modality
- annotation type summary
- selected annotator
- status age
- latest attempt status
- feedback count
- retry or blocked indicator
- external sync indicator when present

The detail drawer should show:

- task metadata
- state transition history
- attempts
- artifact list
- feedback records
- compact feedback bundle for repair
- preview artifacts when available
- provider route used by the current or latest attempt
- external task reference and outbox status
- actions allowed for the current state

Settings views should be read-only in MVP, except for validation/test actions.

## Runtime And Monitoring

Runtime state is separate from task state.

The first implementation should support:

- in-process fake runtime for tests
- local runtime records for cycles, leases, and heartbeat
- monitor samples based on task counts, runtime heartbeat, active workers, retry queues, and scheduler cycle stats

Monitoring must detect:

- missing or stale heartbeat
- stale active tasks
- dead or mismatched workers
- runnable backlog with no workers
- due retries with no workers
- retry drain failures
- annotated or accepted tasks that stop progressing
- dispatch capacity violations

## Testing Requirements

Implementation should start with P0 tests from `VERIFY_MANAGER_CYCLES_TEST_PLAN.md` and add coverage for new product decisions.

Required test groups:

- Task JSON save/load and backup restore.
- Valid and invalid state transitions.
- Event append/read behavior.
- Attempt creation and artifact reference persistence.
- Feedback record append/read and compact feedback bundle construction.
- Human Review routing after QC.
- Annotator capability matching with structured fields.
- Preview artifact reference persistence for image bounding-box evidence.
- External task pull idempotency.
- External status outbox creation on transition.
- External submit outbox creation on accepted or merged tasks.
- Runtime heartbeat freshness.
- Stale active worker detection.
- Retry drain progress.
- Annotated task downstream progress.
- Dispatch capacity enforcement.
- Dashboard API snapshot shape.

## Open Questions For Later Cycles

- Whether the production dashboard API should use FastAPI, Starlette, or a minimal standard-library HTTP server.
- Whether future UI config editing should be implemented through form-backed YAML patches or a database-backed settings service.
- Which real provider clients should ship as maintained examples.
- Whether Human Review should later support reviewer assignment and reviewer-specific permissions.
- Which multimodal renderer should become the first real reference adapter after text core is stable.

These questions do not block the MVP because the MVP defines contracts and test doubles first.

## Spec Self-Review

- Placeholder scan: no unresolved placeholder markers remain.
- Internal consistency: Human Review is consistently optional, after QC, and separate from preview artifacts.
- Scope check: this is one implementation cycle focused on local-first core, contracts, tests, and a minimal Kanban dashboard.
- Ambiguity check: YAML remains canonical in MVP; UI reads, validates, and tests but does not write config.
