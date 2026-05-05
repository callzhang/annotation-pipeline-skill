# Active Learning And RL Workflow Design

## Goal

Extend `annotation-pipeline-skill` from an annotation operator into a workflow coordinator for model-improvement loops. The first implementation should help an algorithm engineer decide what to label next and what to do with model feedback after training, without turning the local skill into a training platform.

The produced data remains training data for the user's model. Active learning and RL support should manage selection, feedback, and audit records around that data.

## User Story

An algorithm engineer installs the skill, connects raw task sources and provider profiles, runs annotation/QC/Human Review, exports accepted training data, trains or evaluates a model outside the skill, then returns model feedback to the Coordinator. The skill records which examples were uncertain, failed, or high-value, updates project rules or long-tail issues, and creates the next Pending annotation batch.

## Scope For The First Active-Learning Slice

Implement a local, file-backed loop with three records:

- `SelectionCandidate`: an unlabeled or weakly labeled item proposed for annotation.
- `ModelFeedbackRecord`: model evaluation or training feedback attached to tasks, exports, or source examples.
- `DatasetVersion`: an export snapshot used for a training/evaluation run.

The first slice should not run training jobs, tune RL policies, or require a distributed scheduler. External training systems can call the CLI/API to post feedback and pull the next candidates.

## Data Model

Store records under `.annotation-pipeline/learning/`.

```text
.annotation-pipeline/learning/
  candidates.jsonl
  model_feedback.jsonl
  dataset_versions.jsonl
```

`SelectionCandidate` fields:

- `candidate_id`
- `project_id`
- `source_ref`
- `modality`
- `payload`
- `score`
- `score_kind`: `uncertainty`, `disagreement`, `error_cluster`, `user_priority`, or `coverage_gap`
- `reason`
- `status`: `proposed`, `selected`, `task_created`, `dismissed`
- `created_at`
- `metadata`

`ModelFeedbackRecord` fields:

- `feedback_id`
- `project_id`
- `dataset_version_id`
- `task_ids`
- `source_refs`
- `metric_name`
- `metric_value`
- `failure_category`
- `summary`
- `recommended_action`
- `created_at`
- `metadata`

`DatasetVersion` fields:

- `dataset_version_id`
- `project_id`
- `export_id`
- `training_data_path`
- `manifest_path`
- `task_ids`
- `annotation_rules_hash`
- `created_at`
- `metadata`

## Workflow

1. Export accepted data with `annotation-pipeline export training-data`.
2. Register the export as a dataset version.
3. External training/evaluation runs outside the skill.
4. Post model feedback into the skill through CLI/API.
5. Coordinator converts feedback into rule updates, long-tail issues, or selection candidates.
6. Operator reviews candidates in the dashboard and creates the next Pending tasks.
7. Runtime annotates/QCs those tasks through the existing flow.

## CLI/API Surface

Minimal CLI:

```bash
annotation-pipeline learning dataset-version create \
  --project-root <project> \
  --project-id <project-id> \
  --export-id <export-id>

annotation-pipeline learning feedback add \
  --project-root <project> \
  --project-id <project-id> \
  --dataset-version-id <version-id> \
  --metric-name eval_f1 \
  --metric-value 0.82 \
  --failure-category boundary_error \
  --summary "Product boundaries regressed on short titles." \
  --recommended-action "Select more short-title product examples."

annotation-pipeline learning candidates import \
  --project-root <project> \
  --project-id <project-id> \
  --source candidates.jsonl

annotation-pipeline learning candidates create-tasks \
  --project-root <project> \
  --project-id <project-id> \
  --limit 50
```

Minimal API:

- `GET /api/learning?project=<project-id>`
- `POST /api/learning/model-feedback`
- `POST /api/learning/candidates`
- `POST /api/learning/candidates/create-tasks`

## Dashboard

Add a `Learning` tab after Coordinator. It should show:

- Dataset versions and linked export manifests.
- Model feedback grouped by metric and failure category.
- Candidate queues by score kind and status.
- A create-tasks action for selected candidates.
- Links back to Coordinator rule updates and long-tail issues.

The dashboard should stay operational and compact. It should not include charts unless they directly support candidate selection or handoff decisions.

## RL Path

The RL path starts as feedback and preference management, not policy training.

Represent RL-relevant records as `ModelFeedbackRecord` entries with:

- `metric_name`: `reward_score`, `preference_win_rate`, or `policy_regression`
- `failure_category`: specific policy behavior, reward hacking pattern, or preference conflict
- `recommended_action`: `add_preference_examples`, `revise_reward_guidance`, `send_to_human_review`, or `create_candidate_batch`

This keeps RL work inside the same audit and Coordinator model until there is a concrete need for a separate policy-training adapter.

## Error Handling

- Invalid feedback payloads fail validation and do not mutate learning records.
- Candidate import is idempotent by `candidate_id` when provided, otherwise by stable source reference hash.
- Creating tasks from candidates must append audit events and mark selected candidates as `task_created`.
- Dataset version creation fails if the export manifest is missing.
- No model feedback should automatically change accepted training data. It can only create candidates, Coordinator records, or task feedback.

## Verification

First implementation should add:

- Unit tests for append/read/idempotency of learning records.
- CLI tests for dataset version creation, model feedback add, candidate import, and candidate-to-task creation.
- API tests for project-scoped learning report and POST endpoints.
- Frontend tests for learning view helpers.
- One script `scripts/verify_learning_loop.sh` that exports a tiny dataset, registers the dataset version, posts model feedback, imports candidates, creates Pending tasks, and verifies Coordinator report linkage.

## Out Of Scope

- Running model training jobs.
- Hosting an active-learning service.
- Training reward models or policies.
- Automatic rule changes without user/operator review.
- Replacing the existing annotation/QC/Human Review flow.
