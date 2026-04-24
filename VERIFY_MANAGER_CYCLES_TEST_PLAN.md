# Verify Manager Cycles Test Case Plan

## Purpose

This document converts the business monitoring behavior from `verify_manager_cycles.py` into a reusable test case plan for the open-source `annotation-pipeline-skill` project.

The source script is not just a health check. It monitors whether an annotation pipeline is actually making progress across repeated scheduler cycles. The test plan below treats that as the product requirement:

- The runtime must be alive.
- The heartbeat and cycle stats must be fresh.
- Runnable work must eventually start.
- Active workers must correspond to real running processes or leases.
- Retries must drain when capacity exists.
- Annotated tasks must move downstream.
- Scheduler dispatch must respect concurrency and per-cycle limits.


## Scope

### In Scope

- Unit tests for sample validation logic.
- Unit tests for sample collection from dashboard, task store, heartbeat, cycle stats, and queue summary.
- Integration tests for scheduler progress across multiple cycles.
- Regression tests for false positives and false negatives discovered in the current `memory-ner` manager.
- Contract tests for future runtime backends.

### Out Of Scope

- Provider quality testing.
- Annotation schema correctness.
- End-to-end model invocation accuracy.
- UI rendering tests beyond whether dashboard state feeds monitoring correctly.


## Source Behavior Summary

The current monitor collects repeated `Sample` snapshots and validates them through `validate_samples`.

Each sample contains:

- Runtime health: `runtime_ok`, `runtime_pid`, `heartbeat_at`.
- Business counts: task counts by status.
- Worker truth: actual live annotation, QC, and merge workers.
- Dashboard/runtime overlay counts.
- Stale active task detection.
- Runnable annotated task lists.
- Due and delayed retry counts.
- Queue runtime summary.
- Recent scheduler cycle stats.
- Latest task update and log timestamps.

The validator checks:

- At least two samples exist.
- Runtime services are active.
- Heartbeat is present and fresh.
- Active tasks are not stale.
- Dead or mismatched workers are reported.
- Runnable backlog does not sit with zero actual live workers.
- Due retries do not sit with zero actual live workers.
- Some progress is observed across samples.
- Due retries either decrease or rotate task ids.
- Annotated tasks advance when capacity exists.
- Scheduler cycle stats are present, complete, and fresh.
- Dispatch count does not exceed available capacity.


## Test Fixtures

### Sample Builder

Create a reusable `sample_factory` fixture with defaults for a healthy pipeline:

- `runtime_ok=True`
- `heartbeat_at=taken_at - 2s`
- `counts={"pending": 0, "annotated": 0, "accepted": 0, "qc_failed": 0}`
- `max_concurrent=10`
- `max_starts_per_cycle=5`
- `actual_live_annotation=0`
- `actual_live_qc=0`
- `actual_live_merge=0`
- `stale_active=0`
- `due_retry_count=0`
- `running_state_mismatch=0`
- fresh `recent_cycle_stats`
- monotonic `newest_updated_at` and `newest_log_at`

Tests should override only the fields relevant to the scenario.

### Temporary Task Store

Use a temporary filesystem store that can create:

- task JSON files
- event JSON files
- dashboard snapshot
- heartbeat file
- cycle stats file

The store must support task states:

- `pending`
- `annotating`
- `annotated`
- `qc_in_progress`
- `accepted`
- `qc_failed`
- `merged`

### Runtime Stub

Use a runtime stub instead of real system processes:

- `pid_alive=True`
- `pid_alive=False`
- `unit_active=True`
- `unit_active=False`
- `lease_alive=True`
- `lease_expired=True`

The open-source implementation should prefer lease/runtime records over direct `/proc` and `systemctl` checks, but tests should preserve the behavior contract.


## Unit Test Cases For `validate_samples`

### VMC-001: Requires At Least Two Samples

Setup:

- Pass one healthy sample.

Expected:

- Failure contains `need at least two samples`.

Priority: P0


### VMC-002: Runtime Down Is A Failure

Setup:

- Two samples.
- First sample has `runtime_ok=False`.

Expected:

- Failure contains `scheduler runtime services are not active`.

Priority: P0


### VMC-003: Missing Heartbeat Is A Failure

Setup:

- Sample has `heartbeat_at=None`.

Expected:

- Failure contains `scheduler runtime heartbeat missing`.

Priority: P0


### VMC-004: Stale Heartbeat Is A Failure

Setup:

- `heartbeat_at` older than `max(interval_seconds * 2, 120)`.

Expected:

- Failure contains `heartbeat stale`.

Priority: P0


### VMC-005: Stale Active Tasks Are Reported

Setup:

- `stale_active=2`
- `stale_task_ids=("task_a", "task_b")`

Expected:

- Failure contains `stale active tasks detected`.

Priority: P0


### VMC-006: Running State Mismatch Includes Task Preview

Setup:

- `running_state_mismatch=2`
- `running_state_mismatch_ids=("task_a", "task_b")`

Expected:

- Failure contains `dead or mismatched workers detected`.
- Failure includes `task_a` or `task_b`.

Priority: P0


### VMC-007: Runnable Backlog With Zero Actual Workers Fails

Setup:

- `counts={"pending": 5}`
- `actual_live_annotation=0`
- `actual_live_qc=0`
- `actual_live_merge=0`

Expected:

- Failure contains `runnable backlog exists but no live workers`.

Business reason:

- The scheduler must not report healthy progress when work is ready but nothing is executing.

Priority: P0


### VMC-008: Dashboard Fallback Must Not Mask Zero Actual Workers

Setup:

- `actual_live_*` totals are zero.
- `live_annotation=4`
- `live_source="cycle_fallback"`
- runnable backlog exists.

Expected:

- Failure still contains `runnable backlog exists but no live workers`.

Business reason:

- Derived dashboard counts must not hide real worker absence.

Priority: P0


### VMC-009: Due Retries With Zero Actual Workers Fail

Setup:

- `due_retry_count=5`
- actual live worker total is zero.

Expected:

- Failure contains `retries are already due and no workers are running`.

Priority: P0


### VMC-010: No Progress Across Samples Fails

Setup:

- Two samples have identical counts.
- Heartbeat does not advance.
- `newest_updated_at` does not advance.
- `newest_log_at` does not advance.

Expected:

- Failure contains `no task-flow progress observed`.

Priority: P0


### VMC-011: Heartbeat Advance Counts As Progress

Setup:

- Counts unchanged.
- `curr.heartbeat_at > prev.heartbeat_at`.

Expected:

- No `no task-flow progress observed` failure.

Priority: P1


### VMC-012: Task Count Change Counts As Progress

Setup:

- `prev.counts != curr.counts`.
- Other progress markers unchanged.

Expected:

- No `no task-flow progress observed` failure.

Priority: P1


### VMC-013: Task Updated Timestamp Counts As Progress

Setup:

- Counts unchanged.
- Heartbeat unchanged.
- `curr.newest_updated_at > prev.newest_updated_at`.

Expected:

- No `no task-flow progress observed` failure.

Priority: P1


### VMC-014: Task Log Timestamp Counts As Progress

Setup:

- Counts unchanged.
- Heartbeat unchanged.
- `curr.newest_log_at > prev.newest_log_at`.

Expected:

- No `no task-flow progress observed` failure.

Priority: P1


### VMC-015: Repeated Stale Task Across Consecutive Samples Fails

Setup:

- `prev.stale_task_ids=("task_a",)`
- `curr.stale_task_ids=("task_a",)`

Expected:

- Failure contains `repeated stale tasks across consecutive samples`.

Priority: P0


### VMC-016: Due Retry Count Decrease Counts As Progress

Setup:

- `prev.due_retry_count=10`
- `curr.due_retry_count=6`
- available capacity exists.

Expected:

- No `due retries remained stuck` failure.

Priority: P0


### VMC-017: Due Retry Id Rotation Counts As Progress

Setup:

- Retry count unchanged.
- Retry task id set changes.

Expected:

- No `due retries remained stuck` failure.

Business reason:

- The exact number of due retries can remain stable while the scheduler drains old retries and new retries become due.

Priority: P0


### VMC-018: Due Retries Stuck With Available Capacity Fail

Setup:

- Last sample has `due_retry_count > 0`.
- Retry count unchanged.
- Retry ids unchanged.
- At least one sample has `actual_live_total < max_concurrent`.

Expected:

- Failure contains `due retries remained stuck`.

Priority: P0


### VMC-019: Due Retries At Full Capacity Do Not Fail Stuck Check

Setup:

- Retry count unchanged.
- Retry ids unchanged.
- Every sample has `actual_live_total >= max_concurrent`.

Expected:

- No `due retries remained stuck` failure.

Business reason:

- The scheduler should not be blamed for not draining retries while all capacity is in use.

Priority: P1


### VMC-020: Annotated Task Advances During Window

Setup:

- First sample has `runnable_annotated_task_ids=("task_a",)`.
- Final sample removes `task_a`.
- Capacity exists.

Expected:

- No `annotated tasks did not advance` failure.

Priority: P0


### VMC-021: Annotated Task Stuck With Capacity Fails

Setup:

- Same annotated task remains runnable in all samples.
- Every sample has `actual_live_total < max_concurrent`.

Expected:

- Failure contains `annotated tasks remained stuck across the full monitoring window`.

Priority: P0


### VMC-022: Annotated Task With Delayed Retry Is Exempt

Setup:

- Task remains `annotated`.
- Task has `next_retry_at` in the future in the task store.
- Capacity exists.

Expected:

- No persistent annotated stuck failure for that task.

Priority: P1


### VMC-023: Annotated Tasks Present But None Advance Fails

Setup:

- At least one sample has runnable annotated tasks.
- No sample-to-sample difference removes annotated ids.
- Persistent set may be empty because ids rotate.

Expected:

- Failure contains `annotated tasks were present but none advanced between consecutive samples`.

Priority: P1


### VMC-024: Missing Cycle Stats Fails

Setup:

- `recent_cycle_stats=()`

Expected:

- Failure contains `no scheduler cycle stats available`.

Priority: P0


### VMC-025: Latest Cycle Without Completed Timestamp Fails

Setup:

- Latest cycle has `started_at`.
- Latest cycle lacks `completed_at`.

Expected:

- Failure contains `latest scheduler cycle has not completed`.

Priority: P0


### VMC-026: Stale Cycle Stats Fail

Setup:

- Latest cycle completed earlier than `max(interval_seconds * 2, 180)`.

Expected:

- Failure contains `scheduler cycle stats stale`.

Priority: P0


### VMC-027: Dispatch Capacity Overflow Fails

Setup:

- Latest cycle has:
  - `started_total=5`
  - `max_concurrent_tasks=10`
  - `live_before_dispatch=9`
  - `available_slots_before_dispatch=1`
  - `max_starts_per_cycle=5`

Expected:

- Failure contains `exceeds available dispatch capacity 1`.

Priority: P0


### VMC-028: Dispatch At Capacity Limit Passes

Setup:

- Latest cycle has `started_total == min(max_starts_per_cycle, available_slots)`.

Expected:

- No dispatch capacity overflow failure.

Priority: P1


## Unit Test Cases For Sample Collection

### VMC-101: Collects Counts From Dashboard Snapshot

Setup:

- Dashboard snapshot has `counts`.
- Task store has different raw counts.

Expected:

- Sample uses dashboard counts.

Priority: P1


### VMC-102: Falls Back To Task Store Counts When Dashboard Missing

Setup:

- No dashboard snapshot.
- Task store has tasks in multiple states.

Expected:

- Sample counts are built from task files.

Priority: P1


### VMC-103: Refreshes Runtime Overlay Before Reading Dashboard Workers

Setup:

- Dashboard live counts are stale.
- Runtime overlay refresh returns updated worker counts.

Expected:

- Sample uses refreshed values.

Priority: P1


### VMC-104: Detects Runnable Annotated Tasks

Setup:

- Task status is `annotated`.
- No `queued_for_start`, no `next_retry_at`, no `active_worker`.

Expected:

- Task id appears in `runnable_annotated_task_ids`.

Priority: P0


### VMC-105: Queued Annotated Task Is Not Runnable

Setup:

- Task status is `annotated`.
- `queued_for_start=True`.

Expected:

- Task id appears in `annotated_task_ids`.
- Task id does not appear in `runnable_annotated_task_ids`.

Priority: P1


### VMC-106: Delayed Retry Annotated Task Is Not Runnable

Setup:

- Task status is `annotated`.
- `next_retry_at` in the future.

Expected:

- Task id does not appear in `runnable_annotated_task_ids`.
- `delayed_future_retry_count` increments.

Priority: P1


### VMC-107: Due Retry Count Captures Retry Ids

Setup:

- Task has `next_retry_at <= now`.

Expected:

- `due_retry_count` increments.
- Task id appears in `due_retry_task_ids`.

Priority: P0


### VMC-108: Active Worker With Live PID Counts As Live Worker

Setup:

- Task status is `annotating`.
- `active_worker.pid` maps to alive runtime stub.

Expected:

- `actual_live_annotation` increments.

Priority: P0


### VMC-109: Active Worker With Dead PID Becomes Stale After Age Threshold

Setup:

- Task status is `annotating`.
- `active_worker.pid` is dead.
- `updated_at` older than stale threshold.
- No completion event exists.

Expected:

- `stale_active` increments.
- `running_state_mismatch` increments.

Priority: P0


### VMC-110: Completion Event Suppresses Stale Active Classification

Setup:

- Task has dead active worker.
- Pending event exists for the task:
  - `annotation_completed`
  - or `annotation_failed`
  - or `qc_passed`
  - or `qc_failed_threshold`
  - or `qc_worker_failed`

Expected:

- Task is not counted as stale.

Business reason:

- The worker may have already finished and emitted an event that the manager has not drained yet.

Priority: P1


### VMC-111: Accepted Task With Merge Active Worker Counts As Merge Worker

Setup:

- Task status is `accepted`.
- `active_worker.type == "merge"`.
- Worker is alive.

Expected:

- `actual_live_merge` increments.

Priority: P1


### VMC-112: Queue Runtime Summary Error Is Captured, Not Raised

Setup:

- Queue summary provider raises an exception.

Expected:

- `queue_runtime={}`
- `queue_runtime_error` contains the exception text.
- Collection still returns a sample.

Priority: P1


### VMC-113: Cycle Fallback Counts Are Used Only When Live Counts Are Zero

Setup:

- Dashboard/task live counts total zero.
- Fresh cycle stats include `live_counts`.

Expected:

- `live_source="cycle_fallback"`.
- `live_*` are filled from cycle stats.
- `actual_live_*` remain zero.

Priority: P0


### VMC-114: Cycle Fallback Is Ignored When Cycle Stats Are Stale

Setup:

- Live counts total zero.
- Cycle stats completed more than 120 seconds ago.

Expected:

- `live_source="dashboard"`.
- `live_*` remain zero.

Priority: P1


## Integration Test Cases

### VMC-201: Healthy Pipeline Makes Progress Across Cycles

Setup:

- Create pending tasks.
- Start local runtime.
- Run monitor with at least three samples.
- Runtime advances at least one task.

Expected:

- Monitor exits successfully.
- No validation failures.

Priority: P0


### VMC-202: Scheduler Down Is Detected

Setup:

- Task backlog exists.
- Runtime heartbeat missing or unhealthy.

Expected:

- Monitor fails with runtime and heartbeat failures.

Priority: P0


### VMC-203: Worker Crash Is Detected As Stale Active

Setup:

- Task has active worker record.
- Worker process or lease disappears.
- No completion event arrives.

Expected:

- Monitor reports stale active task and running mismatch.

Priority: P0


### VMC-204: Worker Completion Event Prevents False Stale Alarm

Setup:

- Worker exits.
- Completion event exists before manager drains it.

Expected:

- Monitor does not report stale active for that task.

Priority: P1


### VMC-205: Due Retries Drain Under Available Capacity

Setup:

- Multiple tasks have due retry timestamps.
- Runtime has free capacity.

Expected:

- Retry count decreases or retry id set changes across samples.
- Monitor does not report stuck due retries.

Priority: P0


### VMC-206: Annotated Tasks Are Dispatched To QC

Setup:

- Multiple `annotated` tasks exist.
- Runtime has free capacity.

Expected:

- Some annotated ids disappear from `runnable_annotated_task_ids`.
- Monitor does not report annotated tasks stuck.

Priority: P0


### VMC-207: Concurrency Limit Is Enforced

Setup:

- More runnable tasks than capacity.
- `max_concurrent_tasks=N`.

Expected:

- Cycle stats never show `started_total` greater than available slots.
- Monitor does not report dispatch capacity overflow.

Priority: P0


### VMC-208: Dispatch Overflow Is Detected From Corrupt Cycle Stats

Setup:

- Inject cycle stats with `started_total > min(max_starts_per_cycle, available_slots)`.

Expected:

- Monitor reports capacity overflow.

Priority: P1


## Contract Tests For Open-Source Skill

### RuntimeHealth Contract

Every runtime backend must provide:

- `runtime_ok`
- fresh heartbeat timestamp
- live execution count by stage
- stale or expired lease detection

Contract tests:

- Healthy backend returns fresh heartbeat.
- Stopped backend returns unhealthy status.
- Expired lease appears as running mismatch or stale active.


### Dashboard Snapshot Contract

Every dashboard snapshot provider must provide:

- generated timestamp
- counts by task status
- task list with ids, statuses, retry metadata, and active run metadata
- live worker counts or a way to refresh overlays

Contract tests:

- Missing snapshot falls back to store scan.
- Snapshot counts are used when present.
- Runtime overlay refresh updates live counts.
- Snapshot worker counts expose their source, such as `runtime`, `snapshot`, or `cycle_fallback`.
- Snapshot includes provider route summary for each active task without exposing provider secrets.


### Task Store Contract

Every task store backend must support:

- scanning tasks by status
- loading `next_retry_at`
- loading active worker metadata
- loading recent logs
- locating pending events by task id

Contract tests:

- Store can identify runnable backlog.
- Store can identify due and delayed retries.
- Store can identify stale active candidates.


## Unit Test Cases For Dashboard And Settings

### UI-001: Dashboard Read Model Includes Runtime Health

Setup:

- Runtime health returns `runtime_ok=True`, heartbeat timestamp, backend name, and live counts by stage.
- Dashboard service builds a snapshot.

Expected:

- Snapshot contains health status, heartbeat, backend name, and live worker counts.
- Live counts are grouped by annotation, validation, QC, repair, and merge when present.

Priority: P0


### UI-002: Dashboard Runtime Overlay Overrides Stale Snapshot Workers

Setup:

- Cached dashboard snapshot says live workers are non-zero.
- Runtime overlay refresh reports zero actual live workers.

Expected:

- Dashboard read model reports zero actual live workers.
- Worker count source indicates runtime overlay.

Business reason:

- The UI must not hide a dead runtime behind stale cached counts.

Priority: P0


### UI-003: Task Detail Includes Attempts, Events, Artifacts, Feedback, Provider Route, And External Ref

Setup:

- Task has two attempts, audit events, artifact refs, feedback records, selected provider route, and `ExternalTaskRef`.

Expected:

- `GET /tasks/<task_id>` or dashboard detail payload includes all of those fields.
- Secret refs are omitted or redacted.

Priority: P0


### UI-004: Filters Preserve Correct Task Counts

Setup:

- Tasks exist across multiple sources and statuses.
- Apply source filter, status filter, and task id query.

Expected:

- Dashboard service returns filtered tasks.
- Counts match the filtered task set.
- TypeScript Kanban board preserves stage columns after filtering.

Priority: P1


### UI-004A: Kanban Uses Operational Stage Columns

Setup:

- Dashboard payload includes tasks in ready, annotating, validating, QC, human review, repair, accepted, rejected, and merged states.

Expected:

- Board renders columns: Ready, Annotating, Validating, QC, Human Review, Repair, Accepted, Rejected, Merged.
- Each task appears in exactly one column.
- Empty columns remain visible.

Priority: P0


### UI-005: Settings Save Validates Scheduler Limits

Setup:

- Submit scheduler settings with `max_concurrent_tasks`, `max_starts_per_cycle`, and `auto_dispatch_pending_tasks`.

Expected:

- Valid settings persist.
- Invalid limits are rejected.
- A settings audit event is written.

Priority: P0


### UI-006: Settings Validation Covers Stage Provider Routes

Setup:

- Provider registry contains two enabled providers with model and effort options.
- Validate stage routes for annotation, QC, repair, and merge.

Expected:

- Routes using known provider/model/effort combinations pass validation.
- Unknown provider ids, disabled providers, unsupported models, or unsupported efforts return validation errors.
- Provider tokens are not written to dashboard snapshots.
- UI does not write provider, stage route, or annotator YAML.

Priority: P0


### UI-007: Settings Changes Do Not Rewrite Active Runs

Setup:

- Task has active `ExecutionRecord` using provider A.
- Save settings changing annotation primary provider to provider B.

Expected:

- Active execution keeps provider A.
- New dispatch decisions use provider B.
- Audit event records the settings change.

Priority: P0


### UI-008: Task Control Actions Go Through Application Service

Setup:

- Invoke start, stop, retry, approve, reject, and merge through API handlers.

Expected:

- Each action calls the corresponding application service method.
- Each successful action returns an audit event id.
- Invalid transitions fail without mutating task state.

Priority: P0


### UI-009: TypeScript API Client Uses Typed Dashboard Payloads

Setup:

- Mock `GET /dashboard`, `GET /tasks/<task_id>`, `GET /settings`, and `GET /providers` responses.
- Render the dashboard through the TypeScript frontend test harness.

Expected:

- API client parses responses into explicit TypeScript types.
- Components do not consume untyped raw JSON directly.
- Missing optional fields render safe empty states without crashing.

Priority: P0


### UI-010: TypeScript Dashboard Never Reads Local Task Store

Setup:

- Run frontend tests with only mocked HTTP API responses.
- Do not mount any local task store path.

Expected:

- Dashboard renders from API mocks only.
- No frontend module imports filesystem APIs or project-local task paths.

Priority: P0


### UI-011: Frontend Control Actions Handle Loading, Success, And Failure

Setup:

- Mock task control actions for start, stop, retry, approve, reject, and merge.
- Return success with audit event id for one case.
- Return validation error for another case.

Expected:

- Button enters loading state while the request is pending.
- Duplicate submissions are prevented.
- Success path refreshes dashboard or task detail.
- Error path shows the backend validation message and leaves local state consistent.

Priority: P0


### UI-012: Provider Secret References Are Redacted In Frontend State

Setup:

- Mock provider settings with `secret_ref`.
- Mock a malformed backend response that accidentally includes a secret-like value.

Expected:

- UI displays only safe secret reference status.
- Frontend logs, component state snapshots, and local storage do not contain secret values.
- Test fails if provider token fields are rendered.

Priority: P0


### UI-013: Feedback Panel Shows Repair Decision Controls

Setup:

- Task detail payload contains open feedback records with suggested actions.
- User selects `bulk_code_repair`, `annotator_rerun`, or `manual_annotation`.

Expected:

- UI displays feedback code, severity, location, message, suggested action, and current status.
- Repair decision update calls the backend control API.
- Successful update returns an audit event id and refreshes task detail.
- Invalid decision is rejected without changing local UI state.

Priority: P0


### UI-014: Multimodal Preview Shows Bounding Box Overlay

Setup:

- Task detail payload contains `image_source`, `image_bbox_annotation`, and `image_bbox_preview` artifact refs.
- Preview metadata includes image dimensions, box coordinates, labels, and confidence.

Expected:

- TypeScript dashboard renders the preview image with bounding boxes.
- Box labels and confidence are visible or available in the detail panel.
- Missing preview artifact shows a render action instead of crashing.

Priority: P0


### UI-015: Human Review Shows Preview Evidence After QC

Setup:

- Task has passed QC and policy routes it to Human Review.
- Task detail contains annotation artifact, QC artifact, and preview artifact.

Expected:

- Human Review panel shows QC result and preview evidence.
- Reviewer can choose accept, reject, or request repair.
- Successful decision calls backend API and returns audit event id.

Priority: P0


### UI-016: Annotator Selector Shows Capability Match

Setup:

- Task manifest requires `modality=image` and `annotation_type=bounding_box`.
- Annotator registry has one image bbox annotator, one text extractor, and one human fallback queue.

Expected:

- UI shows the image bbox annotator as selectable.
- UI does not offer incompatible text-only annotator for this task.
- UI shows fallback human queue and recent quality metrics when present.

Priority: P1


## Unit Test Cases For Annotator Capabilities And Multimodal Artifacts

### AN-001: Annotator Selector Matches Structured Capability

Setup:

- Task manifest declares `modality=image`, `annotation_type=bounding_box`, and input artifact kind `image_source`.
- Registry contains compatible and incompatible annotator profiles.

Expected:

- Selector chooses the compatible annotator.
- Selection reason records matched modality, annotation type, and artifact kinds.
- No semantic keyword matching is used.

Priority: P0


### AN-002: Disabled Annotator Falls Back To Human Queue

Setup:

- Primary image bbox annotator is disabled.
- Fallback human image bbox queue is enabled.

Expected:

- Selector returns fallback annotator.
- Audit event records primary unavailable and fallback selected.

Priority: P0


### AN-003: VC Detection Annotator Produces Bounding Box Artifact

Setup:

- Image task is assigned to VC detection annotator.
- External tool adapter returns boxes, labels, confidence scores, and image dimensions.

Expected:

- Annotation artifact kind is `image_bbox_annotation`.
- Coordinates, labels, confidence, model/tool id, and source image ref are preserved in metadata or payload.
- Attempt records annotator id and external tool id.

Priority: P0


### AN-004: Image Bounding Box Renderer Produces Preview Artifact

Setup:

- Source image artifact and bbox annotation artifact exist.

Expected:

- Preview renderer writes `image_bbox_preview`.
- Preview artifact references both source and annotation artifacts.
- Re-rendering preview does not change task business status.

Priority: P0


### AN-005: Human Review Decision Runs After QC

Setup:

- Human Review policy is enabled for image bbox task.
- Task has passed QC and has preview artifact.

Expected:

- Task enters `human_review` after QC.
- Accept decision moves task to `accepted`.
- Request repair decision creates or updates feedback records and moves task to `repair_needed`.
- Reject decision moves task to `rejected`.

Priority: P0


### AN-007: Pipeline Policy Can Force Human Review

Setup:

- Pipeline human review policy is set to force review.
- QC passes with no risk flags.

Expected:

- Task enters `human_review`.
- Human Review reason records pipeline policy.

Priority: P0


### AN-008: QC Risk Can Route Task To Human Review

Setup:

- Pipeline policy does not force review.
- QC passes but returns structured risk signal such as low confidence or multimodal evidence review required.

Expected:

- Task enters `human_review`.
- Human Review reason records QC policy risk.
- No task text keyword routing is used.

Priority: P0


### AN-009: QC Pass Without Review Policy Enters Accepted

Setup:

- Pipeline policy does not force review.
- QC passes and returns no risk signal.

Expected:

- Task transitions directly to `accepted`.
- Human Review column remains empty for that task.

Priority: P0


### AN-006: Multimodal Artifact Contract Supports Video And Point Cloud Extensions

Setup:

- Create artifact refs for `video_frame_annotation` and `point_cloud_annotation`.

Expected:

- Artifact metadata can represent frame index, timestamp, track id, coordinate frame, 3D box, and instance id.
- Dashboard read model can include preview renderer id without knowing modality-specific internals.
- Core task state does not need video- or point-cloud-specific fields.

Priority: P1


## Unit Test Cases For Feedback Records And Repair Decisions

### FB-001: Validation Failure Creates Feedback Record

Setup:

- Validator returns structured issues with code, severity, source line, output line, and artifact ref.

Expected:

- `FeedbackService` appends one `FeedbackRecord` per issue.
- Records preserve location, source, severity, code, message, and artifact refs.
- Task transition to `repair_needed` references the created feedback ids.

Priority: P0


### FB-002: QC Failure Creates Feedback Record

Setup:

- QC artifact contains failed samples and reviewer rationale.

Expected:

- Feedback records are generated from QC failures.
- Each record links back to the QC artifact and attempt id.
- Suggested action is populated from QC policy or repair strategy.

Priority: P0


### FB-003: Feedback Bundle Is Available To Annotator Rerun

Setup:

- Task has open feedback records from validation and QC.
- Repair decision is `annotator_rerun`.

Expected:

- Prompt builder receives the open feedback records.
- Generated annotation repair prompt includes compact feedback bundle.
- Feedback bundle includes failure location, reason, and expected change.

Priority: P0


### FB-004: Repair Strategy Selects Bulk Code Repair For Deterministic Issues

Setup:

- Feedback records contain deterministic format or normalization issues.

Expected:

- `RepairStrategy` returns `bulk_code_repair`.
- Repair service writes a repair artifact.
- Task returns to `validating` after repair artifact is applied.

Priority: P0


### FB-005: Repair Strategy Selects Manual Annotation For Ambiguous Feedback

Setup:

- Feedback records contain rule conflict or human judgment requirement.

Expected:

- `RepairStrategy` returns `manual_annotation`.
- Task becomes `blocked` or enters human review queue.
- Dashboard surfaces the reason and feedback ids.

Priority: P0


### FB-006: Operator Override Of Repair Decision Is Audited

Setup:

- Repair strategy suggests `annotator_rerun`.
- Operator changes decision to `manual_annotation` in dashboard.

Expected:

- Feedback record stores the override decision.
- Audit event records actor, previous decision, new decision, reason, and feedback ids.
- Active runs are not mutated.

Priority: P0


## Unit Test Cases For Provider Routing

### PR-001: Stage Router Selects Primary Route When Available

Setup:

- Stage route has primary and fallback providers.
- Primary provider is enabled and not paused.

Expected:

- Router returns primary provider, model, effort, and route role `primary`.
- Audit decision reason says primary route selected.

Priority: P0


### PR-002: Stage Router Selects Fallback When Primary Is Paused

Setup:

- Primary route has `pause_until` in the future.
- Fallback provider is enabled.

Expected:

- Router returns fallback provider, model, effort, and route role `fallback`.
- Decision reason includes pause reason.

Priority: P0


### PR-003: Bound Session Can Block Provider Fallback

Setup:

- Task has a bound provider/session for annotation or QC.
- Current provider has a retryable provider error.
- Route policy marks the stage as session-bound.

Expected:

- Router keeps the task on the bound provider.
- Retry is scheduled instead of switching provider.
- Audit event explains that session binding blocked fallback.

Priority: P0


### PR-004: Provider Failure Schedules Fallback Retry When Allowed

Setup:

- Provider error kind is rate limit, provider unavailable, or timeout.
- Task is not session-bound.
- Fallback provider is enabled and not paused.

Expected:

- Task route switches to fallback for the next attempt.
- `next_retry_at` uses the configured fallback delay.
- Attempt error records provider id and model.

Priority: P0


### PR-005: Both Providers Unavailable Goes To Delayed Retry

Setup:

- Primary provider is paused or failing.
- Fallback provider is disabled, paused, or missing.

Expected:

- Task is not dispatched.
- Retry is scheduled or task becomes blocked according to policy.
- Dashboard surfaces the route blockage reason.

Priority: P0


## Unit Test Cases For External Task API Integration

MVP external task integration uses pull + status callback + submit result. Webhook ingestion is out of scope for MVP.

### EXT-001: Pull External Task Creates Internal Task With External Ref

Setup:

- External adapter returns one task envelope with external id, payload, and idempotency key.

Expected:

- `ExternalTaskService` creates an internal task.
- Task contains `ExternalTaskRef`.
- Audit event records external import.

Priority: P0


### EXT-002: Duplicate External Task Pull Is Idempotent

Setup:

- Same external id and idempotency key are pulled twice.

Expected:

- Only one internal task exists.
- Second pull returns existing task id or no-op result.
- No duplicate artifacts are created.

Priority: P0


### EXT-003: Stage Transition Enqueues External Status Update

Setup:

- Task with `ExternalTaskRef` transitions from ready to annotating, then validating.

Expected:

- External status outbox receives ordered status records.
- Each outbox item includes external id, internal task id, stage, status, and idempotency key.

Priority: P0


### EXT-003A: Status Callback Is Sent From Outbox

Setup:

- Stage transition creates external status outbox item.
- Drain outbox with adapter returning success.

Expected:

- Adapter `post_status` is called with external ref, stage, status, and idempotency key.
- Outbox item is marked sent.
- Audit event records external status sync.

Priority: P0


### EXT-004: Accepted Task Submits Result To External API

Setup:

- Task reaches accepted or merged.
- Accepted artifact exists.

Expected:

- External adapter `submit_result` is called with the accepted artifact summary or payload.
- Task audit event records submit success.
- `ExternalTaskRef.last_status_posted` updates.

Priority: P0


### EXT-004A: Submit Result Is Sent From Outbox

Setup:

- Accepted task creates submit result outbox item.
- Drain outbox with adapter returning success.

Expected:

- Adapter `submit_result` is called.
- Outbox item is marked sent.
- `ExternalTaskRef.last_status_posted` or submission metadata updates.

Priority: P0


### EXT-005: Rejected Task Submits Failure Reason

Setup:

- Task reaches rejected with structured error and QC/validation reason.

Expected:

- External adapter receives failure status and reason.
- Failure reason is structured and traceable to attempt/event ids.

Priority: P0


### EXT-006: External API Failure Retains Outbox Item

Setup:

- External adapter raises a retryable HTTP or network error while posting status.

Expected:

- Internal task state remains committed.
- Outbox item remains pending with incremented attempt count.
- Monitor can report pending external outbox work.

Priority: P0


### EXT-007: External API Permanent Failure Goes To Dead Letter

Setup:

- Outbox item exceeds max attempts or adapter returns permanent rejection.

Expected:

- Outbox item moves to dead letter.
- Dashboard surfaces dead-letter count and affected task ids.
- Task audit event records the external sync failure.

Priority: P1


## Regression Tests Already Present In `memory-ner`

The current project already covers these behaviors:

- cycle fallback live counts do not mask zero actual workers
- decreasing due retry count counts as progress
- stuck due retries without full utilization fail
- rotating due retry ids counts as progress

These should be ported into the open-source skill test suite as P0 regression tests.


## Core Annotation Manager Test Logic

The monitoring tests above should be paired with tests extracted from the core `annotation manager` implementation. These cases are not tied to the current repository layout; they define the behavior the open-source skill should preserve.


## Unit Test Cases For Task State And Persistence

### AM-001: Task Save Writes Canonical JSON And Backup

Source behavior:

- `save_task` writes the task JSON.
- It also writes a backup next to the task file.

Setup:

- Create a task payload with `task_json_file`.
- Save it through the task store.

Expected:

- Canonical task JSON exists.
- Backup file exists.
- Reloaded payload matches the saved payload.

Priority: P0


### AM-002: Task Load Restores From Backup When Canonical JSON Is Corrupt

Source behavior:

- `load_task` retries canonical reads, then restores from backup when possible.

Setup:

- Write invalid or empty canonical task JSON.
- Write valid backup JSON.

Expected:

- Load returns the backup payload.
- Canonical task JSON is restored from backup content.

Priority: P0


### AM-003: Transition Rejects Invalid Status

Source behavior:

- `transition_task` only accepts known statuses.

Setup:

- Call transition with a status outside the state machine.

Expected:

- Raises a validation error.
- Task JSON remains unchanged.

Priority: P0


### AM-004: Transition Appends History And Log Entries

Source behavior:

- A successful transition updates `status`, `assignee`, `updated_at`, `history`, and `logs`.

Setup:

- Task starts as `pending`.
- Transition to `annotating` with actor, note, and assignee.

Expected:

- Status is `annotating`.
- Assignee is updated.
- A history event records old and new statuses.
- A log entry records transition details.

Priority: P0


### AM-005: Event Write Produces Structured Pending Event

Source behavior:

- `write_event` creates an event JSON under the task root events directory.

Setup:

- Write an event with custom payload.

Expected:

- Event file exists.
- Payload contains `type`, `task_id`, `timestamp`, `pid`, and custom fields.

Priority: P0


### AM-006: Malformed Pending Event Is Ignored During Read

Source behavior:

- Pending event reads skip malformed JSON instead of failing the scheduler.

Setup:

- Create one valid event and one malformed event.

Expected:

- Read returns only the valid event.
- No exception is raised.

Priority: P1


## Unit Test Cases For Routing And Provider Selection

### AM-101: Default Worker Settings Are Stable

Source behavior:

- Annotation, QC, repair, and merge each have a configured primary route.
- Stages that use model providers can also have fallback route, model, effort, and fallback delay.

Setup:

- Load default settings for `annotation`, `qc`, `repair`, and `merge`.

Expected:

- Defaults match the documented stage route matrix.
- Every provider id referenced by a route exists in the provider registry.

Priority: P0


### AM-102: Invalid User Model Setting Is Rejected

Source behavior:

- Stage route settings validate provider id, model, and effort values against registry options.

Setup:

- Settings contain unknown provider, model, or effort values.

Expected:

- Settings save fails.
- Existing route remains unchanged.
- Validation error identifies the invalid field.

Priority: P0


### AM-103: Configured Stage Route Resolves Provider Client

Source behavior:

- Provider routing is explicit through provider ids, not inferred from model name.

Setup:

- Register two providers with distinct ids and model lists.
- Configure annotation route to provider A and QC route to provider B.

Expected:

- Annotation route resolves provider A.
- QC route resolves provider B.
- Unknown model names are rejected instead of used for implicit provider inference.

Priority: P0


### AM-104: Stage Override Configures Primary And Fallback Pair

Source behavior:

- A task or operator override can set a stage primary route and fallback route.

Setup:

- Empty task payload.
- Force annotation primary provider to `provider_b`.
- Configure `provider_a` as fallback.

Expected:

- `annotation_provider_id` and `annotation_primary_provider_id` are `provider_b`.
- `annotation_fallback_provider_id` is `provider_a`.
- QC route is still populated from settings.

Priority: P1


### AM-105: Bound Annotation Session Blocks Provider Fallback

Source behavior:

- Annotation retries stay provider-bound once a session exists.
- QC does not block fallback the same way.

Setup:

- Task has `annotation_session_id` and matching `annotation_session_provider_id`.
- Task has `qc_session_id` and matching `qc_session_provider_id`.

Expected:

- Annotation reports session-bound fallback blocking.
- QC does not report fallback blocking.

Priority: P0


### AM-106: Session Bound To Different Provider Is Not Reused

Source behavior:

- A session only counts as bound when its stored provider matches the current worker provider.

Setup:

- Task has `annotation_session_id`.
- `annotation_session_provider_id="provider_a"`.
- Current `annotation_provider_id="provider_b"`.

Expected:

- Worker does not treat the old session as reusable.

Priority: P1


## Unit Test Cases For Worker Isolation And Runtime Records

### AM-201: Worker CLI Homes Are Isolated Per Task And Worker

Source behavior:

- Worker credential/config homes are isolated per task, stage, and provider.

Setup:

- Call home creation for task `task_a` and worker `annotation`.
- Call home creation for task `task_a` and worker `qc`.
- Call home creation for task `task_a` and two different provider ids.

Expected:

- Paths are different per worker.
- Paths are different per provider.
- Provider runtime config uses minimal permissions and does not inherit global agent/plugin state.
- Secret values are injected by reference and are not copied into task state.

Priority: P0


### AM-202: Active Worker Is Cleared Only For Matching Expected PID

Source behavior:

- `clear_active_worker` respects `expected_pid`.

Setup:

- Task has active worker pid `100`.
- Clear with `expected_pid=200`.

Expected:

- Active worker remains.
- No worker finished log is appended.

Priority: P0


### AM-203: Active Annotation Worker Release Also Releases Annotation Slot

Source behavior:

- Clearing an annotation worker releases its annotation slot.

Setup:

- Task has active annotation worker pid.
- Matching annotation slot exists.
- Clear active worker with matching pid.

Expected:

- Active worker is removed.
- Slot is released.
- Worker finished log is appended.

Priority: P0


### AM-204: Runtime Repair Clears Dead Active Worker And Restores Expected Business Status

Source behavior:

- Runtime repair normalizes tasks where status and active worker disagree.

Setup:

- Task is `annotating` with a dead active worker.

Expected:

- Active worker is removed.
- Task returns to a runnable status, usually `pending`.
- Repair log explains the transition.

Priority: P0


### AM-205: Queue And Retry Markers Are Removed When Worker Dispatch Starts

Source behavior:

- Dispatch clears queue markers and retry markers once a worker is actually launched.

Setup:

- Task has `queued_for_start`, `queued_worker`, `queued_at`, `next_retry_at`, and `scheduled_retry_worker`.
- Dispatch worker with runtime stub.

Expected:

- Queue and retry marker fields are removed.
- Active worker record is present.

Priority: P0


## Unit Test Cases For Queue-Style Scheduling

### AM-301: Stage Candidate Search Includes Ready Status And Explicit Queue Marker

Source behavior:

- Annotation candidates include `pending` or `queued_worker=annotation`.
- QC candidates include `annotated` or `queued_worker=qc`.
- Merge candidates include `accepted` or `queued_worker=merge`.

Setup:

- Create tasks covering each status and queued marker.

Expected:

- Candidate discovery includes the right files for each stage.

Priority: P0


### AM-302: Task With Active Worker Is Not Ready For Stage

Source behavior:

- `_task_text_ready_for_stage` and `_task_ready_for_stage` reject tasks with active workers.

Setup:

- Task has `status=pending` and `active_worker`.

Expected:

- It is not enqueued for annotation.

Priority: P0


### AM-303: Recently Enqueued Task Is Not Enqueued Again

Source behavior:

- A task with `scheduler_enqueued_stage` and recent `scheduler_enqueued_at` is suppressed for 120 seconds.

Setup:

- Task is ready for annotation.
- It has `scheduler_enqueued_stage="annotation"` and fresh timestamp.

Expected:

- Bootstrap does not enqueue it again.

Priority: P0


### AM-304: Explicitly Queued Work Has Priority Over Ordinary Ready Work

Source behavior:

- `_stage_priority` gives queued-for-stage tasks priority.

Setup:

- Two tasks are ready.
- One has `queued_for_start=True` and matching `queued_worker`.

Expected:

- Queued task is sent before ordinary ready task.

Priority: P1


### AM-305: Bootstrap Stage Respects Limit

Source behavior:

- `bootstrap_stage(..., limit=N)` enqueues at most `N` tasks.

Setup:

- More ready tasks than limit.

Expected:

- Result reports `enqueued=N`.
- Sent task id list has length `N`.

Priority: P0


### AM-306: Retryable Dispatch Failure Requeues Same Stage With Delay

Source behavior:

- Provider limit, provider unavailable, and timeout errors are retryable.

Setup:

- Dispatch raises a classified retryable error.
- No alternate route applies.

Expected:

- Same stage is requeued with retry delay.
- Task is not marked permanently failed.

Priority: P0


### AM-307: Retryable Dispatch Failure Uses Fallback Route When Available

Source behavior:

- If the current route has an alternate agent and the worker is not session-bound, scheduler persists fallback route and requeues immediately.

Setup:

- Annotation dispatch fails with provider limit.
- Route has alternate agent.
- No bound annotation session exists.

Expected:

- Task route changes to alternate provider.
- Requeue delay is zero.

Priority: P0


### AM-308: Bound Session Avoids Fallback And Delays Retry

Source behavior:

- Bound annotation sessions requeue same provider after delay.

Setup:

- Annotation dispatch fails.
- Task has bound annotation session.

Expected:

- Fallback route is not persisted.
- Same stage is requeued with retry delay.

Priority: P0


## Unit Test Cases For Annotation Worker Helpers

### AM-401: Provider Annotation Command Is Hardened

Source behavior:

- Provider worker command uses isolated execution and disables unrelated plugin/app surfaces when the provider client supports those options.

Setup:

- Run annotation pass with provider client `provider_a` using a mocked subprocess runner.

Expected:

- Command uses the provider client's isolated execution flags.
- Command disables optional plugin/app surfaces when supported.
- Command sets repository working directory and reasoning effort.

Priority: P0


### AM-402: Provider Annotation Resume Uses Session Id

Source behavior:

- If `session_id` exists and the provider client supports resume, command uses the provider-specific resume mechanism.

Setup:

- Run annotation pass with `provider_a` and `session_id="session-1"`.

Expected:

- Command uses the provider-specific resume option.
- Command includes `session-1`.

Priority: P1


### AM-403: Provider Annotation Command Is Stateless Unless Session Id Is Supplied

Source behavior:

- Provider command uses no session persistence by default when the provider client supports stateless execution.
- If session id exists, command includes the provider-specific resume option.

Setup:

- Run annotation pass with a resumable provider with and without session id.

Expected:

- Base command disables session persistence when supported.
- Resume flag appears only when session id is supplied.

Priority: P1


### AM-404: Retry Guidance Aggregates Prior Validation And QC Failures

Source behavior:

- Retry guidance reads archived validation files, current validation, QC failures, and rule suggestions.

Setup:

- Task has one archived attempt with validation issues and QC failures.
- Task has current QC failure file.

Expected:

- Guidance text includes attempt validation issues.
- Guidance text includes current QC failures.
- Guidance text includes rule suggestions.

Priority: P0


### AM-405: Manifest Poison Detection Requires High Mismatch Ratio

Source behavior:

- Manifest poison detection requires at least `minimum_issues` and at least 90 percent SHA mismatch issues.

Setup:

- Payload with too few issues.
- Payload with many mixed issues below 90 percent.
- Payload with enough SHA mismatch issues.

Expected:

- Only the high-volume SHA mismatch payload returns true.

Priority: P0


### AM-406: Context Overflow Detection Covers Provider Error Variants

Source behavior:

- Combined stdout/stderr is checked for context overflow phrases.

Setup:

- One result contains `context_length_exceeded`.
- One result contains `exceeds the context window`.
- One unrelated error.

Expected:

- First two return true.
- Unrelated error returns false.

Priority: P1


### AM-407: Annotation Worker Rejects Unsupported Model For Selected Agent

Source behavior:

- Provider models are validated against each provider registry entry.

Setup:

- Annotation provider is `provider_a` with a model supported only by `provider_b`.
- Annotation provider is `provider_b` with a model supported only by `provider_a`.

Expected:

- Worker exits or raises before provider invocation.

Priority: P0


## Unit Test Cases For QC Worker Logic

### AM-501: Malformed Annotation JSONL Produces QC Failure Artifact

Source behavior:

- QC worker catches JSON parse failure and writes a structured QC failure.

Setup:

- Task output file contains malformed JSONL.

Expected:

- QC JSON exists.
- `threshold_met=false`.
- Failure row id is `output_parse_error`.
- Event `qc_failed_threshold` is emitted.

Priority: P0


### AM-502: QC Sampling Is Stable For Seed

Source behavior:

- QC sample indices use `random.Random(qc_seed)`.

Setup:

- Output has more rows than sample size.
- Run sample selection twice with the same seed.

Expected:

- Same sampled output row indices.

Priority: P0


### AM-503: QC Sample Preserves Source Line From Manifest

Source behavior:

- QC sample rows include `source_line_at_dispatch` when manifest records exist.

Setup:

- Output row index maps to manifest record with source line.

Expected:

- Sample row contains the source line.

Priority: P0


### AM-504: Schema Precheck Failure Blocks Semantic QC

Source behavior:

- If schema failures exceed threshold, QC writes deterministic failure artifacts before model QC.

Setup:

- QC sample has enough invalid rows to exceed `max_allowed_failures`.

Expected:

- QC JSON verdict references schema precheck.
- No provider QC call is made.
- Event `qc_failed_threshold` is emitted.

Priority: P0


### AM-505: QC Runs Statelessly And Clears Stored QC Session

Source behavior:

- QC worker clears `qc_session_id` and `qc_session_agent`.

Setup:

- Task has QC session fields populated.
- Start QC worker.

Expected:

- Session fields become empty before QC provider call.

Priority: P0


### AM-506: Sampled Row Id Prefers Source Line Over Output Row Index

Source behavior:

- `sampled_row_id` uses `source_line_at_dispatch` first, then `_output_row_index`, then `unknown`.

Setup:

- Row with both fields.
- Row with only output row index.
- Row with neither.

Expected:

- Returns source line for first row.
- Returns output row index for second row.
- Returns `unknown` for third row.

Priority: P1


## Unit Test Cases For Merge Feedback Logic

### AM-601: QC Feedback Collection Includes Archived And Current Attempts

Source behavior:

- `collect_qc_feedback` reads archived attempt QC payloads, current QC payload, suggestion files, and retry context.

Setup:

- Task has archived attempts and current QC files.

Expected:

- Feedback bundle includes all attempts.
- `all_failures` is flattened.
- `all_rule_optimization_suggestions` is flattened.
- Retry context is included when present.

Priority: P0


### AM-602: Missing Or Invalid QC Payload Is Skipped

Source behavior:

- Missing or invalid QC JSON does not crash feedback collection.

Setup:

- One archived attempt has invalid QC JSON.
- Another has valid QC JSON.

Expected:

- Valid payload is included.
- Invalid payload is skipped.

Priority: P1


### AM-603: Source Rule Section Upsert Replaces Existing Source Block

Source behavior:

- `upsert_source_section` updates a marker-delimited source block when present.

Setup:

- Source rules file contains an existing block for the same source.

Expected:

- Old block is replaced.
- Other source blocks remain unchanged.

Priority: P0


### AM-604: Source Rule Section Upsert Appends Missing Source Block

Source behavior:

- If no block exists, the source-specific section is appended.

Setup:

- Source rules file has no block for source.

Expected:

- New marker-delimited block is appended.

Priority: P1


### AM-605: Merge Feedback Prompt Contains Required Evidence Inputs

Source behavior:

- Prompt includes base guide, existing source rules, and compact QC feedback bundle.

Setup:

- Build prompt from fake task, fake rules, and feedback bundle.

Expected:

- Prompt contains source id.
- Prompt contains base guide text.
- Prompt contains existing source rules.
- Prompt contains serialized QC failures.
- Prompt requests JSON-only output.

Priority: P0


## Unit Test Cases For Runtime Status And Queue Summary

### AM-701: Runtime Status Reports Healthy Only When Runtime And Worker Services Are Active

Source behavior:

- Runtime status combines scheduler runtime health, worker backend health, heartbeat, and staleness.

Setup:

- Stub service checks for active/inactive combinations.

Expected:

- Healthy only when required services and heartbeat are valid.

Priority: P0


### AM-702: Runtime Status Marks Stale Heartbeat Unhealthy

Setup:

- Heartbeat timestamp is older than threshold.

Expected:

- Runtime status has `healthy=false`.
- Status payload includes stale heartbeat detail.

Priority: P0


### AM-703: Cycle Stats Loader Ignores Malformed Stats File

Setup:

- Cycle stats file contains malformed JSON.

Expected:

- Loader returns an empty list or safe default.
- No exception escapes to monitor caller.

Priority: P1


### AM-704: Queue Summary Reports Backend Dependency Failure Clearly

Source behavior:

- Queue summary raises an actionable dependency error when an optional queue backend dependency is missing.

Setup:

- Optional queue backend dependency import is unavailable.

Expected:

- Error message points to the queue backend requirements and keeps dashboard collection alive.

Priority: P1


### AM-705: Queue Summary Includes Queued Totals By Stage

Setup:

- Runtime backend has queued annotation, QC, and merge items.

Expected:

- Summary contains total queued count and stage-level counts.

Priority: P1


## Gaps To Cover First

Highest priority missing tests:

- Missing heartbeat and stale heartbeat.
- Running state mismatch with task id preview.
- Annotated tasks stuck with available capacity.
- Delayed retry exemption for annotated tasks.
- Missing, incomplete, and stale cycle stats.
- Dispatch overflow against available capacity.
- Sample collection from task store when dashboard is missing.
- Active worker stale detection with and without completion events.
- Task JSON backup restore.
- Routing fallback with and without bound annotation sessions.
- Bootstrap duplicate-enqueue suppression.
- Malformed annotation JSONL QC artifact generation.
- Deterministic QC schema precheck before provider QC.
- Merge feedback collection from archived and current QC attempts.


## Recommended Test File Layout

```text
tests/
  test_monitor_validate_samples.py
  test_monitor_collect_sample.py
  test_task_store_and_events.py
  test_task_state_machine.py
  test_worker_routing.py
  test_worker_runtime_records.py
  test_scheduler_bootstrap.py
  test_annotation_worker_helpers.py
  test_qc_worker_logic.py
  test_merge_feedback_logic.py
  test_runtime_status.py
  test_monitor_integration_local_runtime.py
  test_runtime_health_contract.py
  test_dashboard_snapshot_contract.py
  fixtures/
    sample_factory.py
    task_store_factory.py
```


## Acceptance Criteria

The monitoring test suite is acceptable when:

- All P0 unit tests are implemented.
- The local runtime integration test can pass without Redis, Docker, or systemd.
- Every monitor failure message is covered by at least one direct test.
- Every false-positive exemption has a regression test.
- Runtime, dashboard, and task store contracts can be reused by future backends.
