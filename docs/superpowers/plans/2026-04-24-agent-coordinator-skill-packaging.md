# Agent Coordinator Skill Packaging Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Package this repository as an installable agent skill that helps an agent start, configure, monitor, and coordinate an LLM-managed annotation project for algorithm engineers who need training data for model training.

**Architecture:** Add a root `SKILL.md` as the agent-facing contract, keep operational behavior in the existing `annotation-pipeline` CLI, and add coordinator-oriented reporting commands that summarize project health, Human Review needs, feedback, rule updates, long-tail issues, and training-data readiness. Store coordination artifacts as structured files under `.annotation-pipeline/coordination/` so agents can inspect and update them deterministically. Document active learning and RL workflow management as future platform directions, not MVP promises.

**Tech Stack:** Codex skill markdown contract, Python 3.11+, argparse CLI, filesystem JSON/YAML state, pytest.

---

## Scope

Update: the subagent runtime slice now adds `llm_profiles.yaml`, OpenAI Responses API profiles, local LLM CLI profiles, `provider doctor`, `provider targets`, and `run-cycle --runtime subagent`. Coordinator packaging should build on those provider targets instead of the older placeholder `providers.yaml`-only routing model.

This plan packages the existing project as an agent skill and adds the smallest coordinator layer needed for another agent to operate it responsibly. It does not implement real provider model calls, real external HTTP integrations, production auth, frontend configuration editing, active learning loops, or RL training loops.

The intended user persona is an algorithm engineer. The skill must guide the agent toward producing training datasets for model training, not just running annotation jobs. Later cycles can evolve this into an active learning and RL workflow management platform.

## File Structure

- Create `SKILL.md`: installable skill instructions and trigger guidance for agents.
- Create `docs/agent-operator-guide.md`: longer operational guide for agent-mediated annotation projects.
- Create `docs/algorithm-engineer-user-story.md`: user story centered on training-data delivery to algorithm engineers.
- Create `annotation_pipeline_skill/services/coordinator.py`: coordinator report, Human Review reminders, feedback summaries, and rule update records.
- Create `annotation_pipeline_skill/core/coordination.py`: dataclasses for `RuleUpdateRecord`, `LongTailIssue`, and `CoordinatorReport`.
- Modify `annotation_pipeline_skill/interfaces/cli.py`: add coordinator commands.
- Modify `README.md`: correct current implementation state and link the skill docs.
- Modify `.gitignore`: ignore local installed skill test outputs if needed.
- Create `tests/test_coordinator_service.py`: unit tests for coordinator summaries and records.
- Create `tests/test_coordinator_cli.py`: CLI tests for coordinator commands.
- Create `tests/test_skill_packaging.py`: static tests for root `SKILL.md` requirements.

## Task 1: Agent-Facing Skill Contract

**Files:**
- Create: `SKILL.md`
- Create: `tests/test_skill_packaging.py`

- [ ] **Step 1: Write failing skill packaging tests**

Create `tests/test_skill_packaging.py`:

```python
from pathlib import Path


def test_skill_md_exists_and_names_operator_role():
    text = Path("SKILL.md").read_text(encoding="utf-8")

    assert "Annotation Pipeline Operator" in text
    assert "algorithm engineer" in text
    assert "Human Review" in text
    assert "annotation-pipeline doctor" in text


def test_skill_md_contains_required_agent_workflows():
    text = Path("SKILL.md").read_text(encoding="utf-8")

    for phrase in [
        "Start a project",
        "Configure a project",
        "Monitor a project",
        "Coordinate feedback",
        "Escalate Human Review",
        "Deliver training data",
        "active learning",
        "RL",
    ]:
        assert phrase in text
```

- [ ] **Step 2: Run the test to verify it fails**

Run:

```bash
UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy uv run pytest tests/test_skill_packaging.py -v
```

Expected: FAIL with `FileNotFoundError` because `SKILL.md` does not exist.

- [ ] **Step 3: Create `SKILL.md`**

Create `SKILL.md`:

```markdown
---
name: annotation-pipeline-skill
description: Use when an agent needs to start, configure, monitor, or coordinate an LLM-managed annotation project that produces training data for an algorithm engineer.
---

# Annotation Pipeline Operator

Use this skill when the user wants an agent to operate an annotation project whose output must be directly usable as training data by an algorithm engineer.

The agent acts as the coordinator between the user, LLM annotators, QC feedback, Human Review, rule updates, long-tail issues, algorithm feedback, and final training data delivery.

## When To Use

- The user wants to start a new annotation pipeline.
- The user wants to configure providers, stage routes, annotators, or Human Review policy.
- The user wants to monitor annotation progress and identify stuck or risky work.
- The user wants to Coordinate feedback from QC or Human Review back into annotation rules.
- The user wants to Escalate Human Review for uncertain or high-risk items.
- The user wants to collect algorithm feedback and produce training data for model training.
- The user wants to prepare a future active learning or RL data loop.

## Start a project

1. Run `annotation-pipeline init --project-root <project>`.
2. Ask the user for the raw source file or external task source.
3. Run `annotation-pipeline doctor --project-root <project>`.
4. Create tasks with `annotation-pipeline create-tasks --project-root <project> --source <jsonl> --pipeline-id <id>`.

## Configure a project

Edit YAML under `<project>/.annotation-pipeline/`:

- `providers.yaml`
- `stage_routes.yaml`
- `annotators.yaml`
- `external_tasks.yaml`

Run `annotation-pipeline doctor --project-root <project>` after every config change.

## Monitor a project

Run:

```bash
annotation-pipeline coordinator report --project-root <project>
```

Use the report to summarize progress, blocked work, Human Review demand, feedback categories, and training-data readiness for the user.

## Coordinate feedback

When QC, Human Review, or the algorithm engineer identifies a data issue:

1. Record it with `annotation-pipeline coordinator record-rule-update`.
2. Link it to the affected task or feedback category.
3. Tell the user whether the issue should be handled as bulk code repair, annotator rerun, manual annotation, or rejection.
4. Re-run the local cycle only after `doctor` passes.

## Escalate Human Review

If tasks are in `human_review`, summarize why they need review and ask the user for the decision. Do not silently accept Human Review tasks.

## Deliver training data

Before telling the user training data is ready:

1. Run `annotation-pipeline doctor --project-root <project>`.
2. Run `annotation-pipeline coordinator report --project-root <project>`.
3. Confirm there are no blocked tasks, no unresolved Human Review tasks, and no blocking feedback.
4. Report accepted and merged counts separately.
5. Explain known long-tail limitations that may affect downstream model training.

## Future active learning and RL workflows

This skill can collect the state needed for future active learning and RL workflow management: model feedback, long-tail categories, Human Review decisions, rule updates, and training-data readiness. Do not claim active learning or RL automation is implemented until those loops exist in code.

## Boundaries

- Do not use Streamlit.
- Do not edit provider secrets directly; use secret references such as `env:PROVIDER_API_KEY`.
- Do not route tasks with keyword or regex shortcuts.
- Do not claim production provider execution unless a provider client has actually run.
```

- [ ] **Step 4: Run the test to verify it passes**

Run:

```bash
UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy uv run pytest tests/test_skill_packaging.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

Run:

```bash
git add SKILL.md tests/test_skill_packaging.py
git commit -m "docs: add agent skill contract"
```

## Task 2: Coordinator Domain Models

**Files:**
- Create: `annotation_pipeline_skill/core/coordination.py`
- Test: `tests/test_coordinator_service.py`

- [ ] **Step 1: Write failing coordinator model tests**

Create `tests/test_coordinator_service.py`:

```python
from annotation_pipeline_skill.core.coordination import LongTailIssue, RuleUpdateRecord


def test_rule_update_record_round_trips_to_dict():
    record = RuleUpdateRecord.new(
        rule_id="rule-1",
        source="algorithm_feedback",
        summary="Entity boundaries need to include suffix tokens",
        affected_task_ids=["task-1"],
        action="update_annotation_guideline",
    )

    loaded = RuleUpdateRecord.from_dict(record.to_dict())

    assert loaded == record
    assert loaded.source == "algorithm_feedback"


def test_long_tail_issue_round_trips_to_dict():
    issue = LongTailIssue.new(
        issue_id="issue-1",
        category="rare_format",
        summary="OCR rows with rotated text need manual review",
        task_ids=["task-7"],
        recommended_action="manual_annotation",
    )

    loaded = LongTailIssue.from_dict(issue.to_dict())

    assert loaded == issue
    assert loaded.recommended_action == "manual_annotation"
```

- [ ] **Step 2: Run the test to verify it fails**

Run:

```bash
UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy uv run pytest tests/test_coordinator_service.py -v
```

Expected: FAIL with `ModuleNotFoundError` for `annotation_pipeline_skill.core.coordination`.

- [ ] **Step 3: Implement coordinator dataclasses**

Create `annotation_pipeline_skill/core/coordination.py`:

```python
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def dt_to_str(value: datetime) -> str:
    return value.isoformat()


def dt_from_str(value: str) -> datetime:
    return datetime.fromisoformat(value)


@dataclass(frozen=True)
class RuleUpdateRecord:
    rule_id: str
    source: str
    summary: str
    affected_task_ids: list[str]
    action: str
    created_at: datetime
    metadata: dict = field(default_factory=dict)

    @classmethod
    def new(
        cls,
        rule_id: str,
        source: str,
        summary: str,
        affected_task_ids: list[str],
        action: str,
        metadata: dict | None = None,
    ) -> "RuleUpdateRecord":
        return cls(rule_id, source, summary, affected_task_ids, action, utc_now(), metadata or {})

    def to_dict(self) -> dict:
        return {
            "rule_id": self.rule_id,
            "source": self.source,
            "summary": self.summary,
            "affected_task_ids": self.affected_task_ids,
            "action": self.action,
            "created_at": dt_to_str(self.created_at),
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "RuleUpdateRecord":
        return cls(
            rule_id=data["rule_id"],
            source=data["source"],
            summary=data["summary"],
            affected_task_ids=list(data.get("affected_task_ids", [])),
            action=data["action"],
            created_at=dt_from_str(data["created_at"]),
            metadata=dict(data.get("metadata", {})),
        )


@dataclass(frozen=True)
class LongTailIssue:
    issue_id: str
    category: str
    summary: str
    task_ids: list[str]
    recommended_action: str
    created_at: datetime
    resolved: bool = False
    metadata: dict = field(default_factory=dict)

    @classmethod
    def new(
        cls,
        issue_id: str,
        category: str,
        summary: str,
        task_ids: list[str],
        recommended_action: str,
        metadata: dict | None = None,
    ) -> "LongTailIssue":
        return cls(issue_id, category, summary, task_ids, recommended_action, utc_now(), False, metadata or {})

    def to_dict(self) -> dict:
        return {
            "issue_id": self.issue_id,
            "category": self.category,
            "summary": self.summary,
            "task_ids": self.task_ids,
            "recommended_action": self.recommended_action,
            "created_at": dt_to_str(self.created_at),
            "resolved": self.resolved,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "LongTailIssue":
        return cls(
            issue_id=data["issue_id"],
            category=data["category"],
            summary=data["summary"],
            task_ids=list(data.get("task_ids", [])),
            recommended_action=data["recommended_action"],
            created_at=dt_from_str(data["created_at"]),
            resolved=bool(data.get("resolved", False)),
            metadata=dict(data.get("metadata", {})),
        )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run:

```bash
UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy uv run pytest tests/test_coordinator_service.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

Run:

```bash
git add annotation_pipeline_skill/core/coordination.py tests/test_coordinator_service.py
git commit -m "feat: add coordinator domain records"
```

## Task 3: Coordinator Service Report And Records

**Files:**
- Create: `annotation_pipeline_skill/services/coordinator.py`
- Modify: `tests/test_coordinator_service.py`

- [ ] **Step 1: Add failing coordinator service tests**

Append to `tests/test_coordinator_service.py`:

```python
from annotation_pipeline_skill.core.models import FeedbackRecord, Task
from annotation_pipeline_skill.core.states import FeedbackSeverity, FeedbackSource, TaskStatus
from annotation_pipeline_skill.services.coordinator import CoordinatorService
from annotation_pipeline_skill.store.file_store import FileStore


def test_coordinator_report_counts_human_review_feedback_and_training_data_readiness(tmp_path):
    store = FileStore(tmp_path)
    accepted = Task.new(task_id="accepted-1", pipeline_id="pipe", source_ref={"kind": "jsonl"})
    accepted.status = TaskStatus.ACCEPTED
    review = Task.new(task_id="review-1", pipeline_id="pipe", source_ref={"kind": "jsonl"})
    review.status = TaskStatus.HUMAN_REVIEW
    store.save_task(accepted)
    store.save_task(review)
    store.append_feedback(
        FeedbackRecord.new(
            task_id="review-1",
            attempt_id="attempt-1",
            source_stage=FeedbackSource.QC,
            severity=FeedbackSeverity.WARNING,
            category="ambiguous_boundary",
            message="Boundary requires review",
            target={"field": "entities"},
            suggested_action="manual_annotation",
            created_by="qc",
        )
    )

    report = CoordinatorService(store).build_report()

    assert report["counts_by_status"]["accepted"] == 1
    assert report["human_review_task_ids"] == ["review-1"]
    assert report["feedback_by_category"] == {"ambiguous_boundary": 1}
    assert report["training_data_readiness"]["accepted"] == 1
    assert report["training_data_readiness"]["ready_for_model_training"] is False


def test_coordinator_records_rule_updates_and_long_tail_issues(tmp_path):
    service = CoordinatorService(FileStore(tmp_path))

    rule = service.record_rule_update(
        source="human_review",
        summary="Boxes must include visible object edge",
        affected_task_ids=["task-1"],
        action="update_annotation_guideline",
    )
    issue = service.record_long_tail_issue(
        category="small_object",
        summary="Objects below 8px need manual annotation",
        task_ids=["task-1"],
        recommended_action="manual_annotation",
    )

    assert service.list_rule_updates() == [rule]
    assert service.list_long_tail_issues() == [issue]
```

- [ ] **Step 2: Run the test to verify it fails**

Run:

```bash
UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy uv run pytest tests/test_coordinator_service.py -v
```

Expected: FAIL because `annotation_pipeline_skill.services.coordinator` is missing.

- [ ] **Step 3: Implement coordinator service**

Create `annotation_pipeline_skill/services/coordinator.py`:

```python
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from uuid import uuid4

from annotation_pipeline_skill.core.coordination import LongTailIssue, RuleUpdateRecord
from annotation_pipeline_skill.core.states import FeedbackSeverity, TaskStatus
from annotation_pipeline_skill.store.file_store import FileStore


class CoordinatorService:
    def __init__(self, store: FileStore):
        self.store = store
        self.coordination_dir = store.root / "coordination"
        self.coordination_dir.mkdir(parents=True, exist_ok=True)
        self.rule_updates_path = self.coordination_dir / "rule_updates.jsonl"
        self.long_tail_path = self.coordination_dir / "long_tail_issues.jsonl"

    def build_report(self) -> dict:
        tasks = self.store.list_tasks()
        counts_by_status = Counter(task.status.value for task in tasks)
        human_review_task_ids = sorted(task.task_id for task in tasks if task.status is TaskStatus.HUMAN_REVIEW)
        blocked_task_ids = sorted(task.task_id for task in tasks if task.status is TaskStatus.BLOCKED)
        feedback_records = [record for task in tasks for record in self.store.list_feedback(task.task_id)]
        feedback_by_category = Counter(record.category for record in feedback_records)
        blocking_feedback = [
            record.feedback_id
            for record in feedback_records
            if record.severity is FeedbackSeverity.BLOCKING
        ]
        accepted = counts_by_status.get(TaskStatus.ACCEPTED.value, 0)
        merged = counts_by_status.get(TaskStatus.MERGED.value, 0)
        return {
            "counts_by_status": dict(sorted(counts_by_status.items())),
            "human_review_task_ids": human_review_task_ids,
            "blocked_task_ids": blocked_task_ids,
            "feedback_by_category": dict(sorted(feedback_by_category.items())),
            "blocking_feedback_ids": sorted(blocking_feedback),
            "rule_update_count": len(self.list_rule_updates()),
            "long_tail_issue_count": len(self.list_long_tail_issues()),
            "training_data_readiness": {
                "accepted": accepted,
                "merged": merged,
                "ready_for_model_training": bool((accepted or merged) and not human_review_task_ids and not blocked_task_ids and not blocking_feedback),
            },
        }

    def record_rule_update(
        self,
        source: str,
        summary: str,
        affected_task_ids: list[str],
        action: str,
    ) -> RuleUpdateRecord:
        record = RuleUpdateRecord.new(
            rule_id=f"rule-{uuid4().hex}",
            source=source,
            summary=summary,
            affected_task_ids=affected_task_ids,
            action=action,
        )
        self._append_jsonl(self.rule_updates_path, record.to_dict())
        return record

    def list_rule_updates(self) -> list[RuleUpdateRecord]:
        return self._read_jsonl(self.rule_updates_path, RuleUpdateRecord.from_dict)

    def record_long_tail_issue(
        self,
        category: str,
        summary: str,
        task_ids: list[str],
        recommended_action: str,
    ) -> LongTailIssue:
        issue = LongTailIssue.new(
            issue_id=f"issue-{uuid4().hex}",
            category=category,
            summary=summary,
            task_ids=task_ids,
            recommended_action=recommended_action,
        )
        self._append_jsonl(self.long_tail_path, issue.to_dict())
        return issue

    def list_long_tail_issues(self) -> list[LongTailIssue]:
        return self._read_jsonl(self.long_tail_path, LongTailIssue.from_dict)

    def _append_jsonl(self, path: Path, data: dict) -> None:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(data, sort_keys=True) + "\n")

    def _read_jsonl(self, path: Path, factory):
        if not path.exists():
            return []
        return [
            factory(json.loads(line))
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run:

```bash
UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy uv run pytest tests/test_coordinator_service.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

Run:

```bash
git add annotation_pipeline_skill/services/coordinator.py tests/test_coordinator_service.py
git commit -m "feat: add coordinator reporting service"
```

## Task 4: Coordinator CLI Commands

**Files:**
- Modify: `annotation_pipeline_skill/interfaces/cli.py`
- Create: `tests/test_coordinator_cli.py`

- [ ] **Step 1: Write failing CLI tests**

Create `tests/test_coordinator_cli.py`:

```python
import json

from annotation_pipeline_skill.core.models import Task
from annotation_pipeline_skill.core.states import TaskStatus
from annotation_pipeline_skill.interfaces.cli import main
from annotation_pipeline_skill.store.file_store import FileStore


def test_coordinator_report_cli_outputs_json(tmp_path, capsys):
    main(["init", "--project-root", str(tmp_path)])
    store = FileStore(tmp_path / ".annotation-pipeline")
    task = Task.new(task_id="task-1", pipeline_id="pipe", source_ref={"kind": "jsonl"})
    task.status = TaskStatus.HUMAN_REVIEW
    store.save_task(task)

    exit_code = main(["coordinator", "report", "--project-root", str(tmp_path)])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["human_review_task_ids"] == ["task-1"]


def test_coordinator_record_rule_update_cli_persists_record(tmp_path):
    main(["init", "--project-root", str(tmp_path)])

    exit_code = main(
        [
            "coordinator",
            "record-rule-update",
            "--project-root",
            str(tmp_path),
            "--source",
            "algorithm_feedback",
            "--summary",
            "Treat short aliases as entity mentions",
            "--affected-task-id",
            "task-1",
            "--action",
            "update_annotation_guideline",
        ]
    )

    report_code = main(["coordinator", "report", "--project-root", str(tmp_path)])
    assert exit_code == 0
    assert report_code == 0
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:

```bash
UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy uv run pytest tests/test_coordinator_cli.py -v
```

Expected: FAIL because the CLI has no `coordinator` command.

- [ ] **Step 3: Implement coordinator CLI commands**

Modify `annotation_pipeline_skill/interfaces/cli.py`:

```python
import json
from annotation_pipeline_skill.services.coordinator import CoordinatorService
```

Add subcommands under `build_parser()`:

```python
    coordinator_parser = subparsers.add_parser("coordinator")
    coordinator_subparsers = coordinator_parser.add_subparsers(required=True)

    report_parser = coordinator_subparsers.add_parser("report")
    report_parser.add_argument("--project-root", type=Path, default=Path.cwd())
    report_parser.set_defaults(handler=handle_coordinator_report)

    rule_parser = coordinator_subparsers.add_parser("record-rule-update")
    rule_parser.add_argument("--project-root", type=Path, default=Path.cwd())
    rule_parser.add_argument("--source", required=True)
    rule_parser.add_argument("--summary", required=True)
    rule_parser.add_argument("--affected-task-id", action="append", default=[])
    rule_parser.add_argument("--action", required=True)
    rule_parser.set_defaults(handler=handle_record_rule_update)

    issue_parser = coordinator_subparsers.add_parser("record-long-tail-issue")
    issue_parser.add_argument("--project-root", type=Path, default=Path.cwd())
    issue_parser.add_argument("--category", required=True)
    issue_parser.add_argument("--summary", required=True)
    issue_parser.add_argument("--task-id", action="append", default=[])
    issue_parser.add_argument("--recommended-action", required=True)
    issue_parser.set_defaults(handler=handle_record_long_tail_issue)
```

Add handlers:

```python
def coordinator_service(project_root: Path) -> CoordinatorService:
    return CoordinatorService(FileStore(project_root / ".annotation-pipeline"))


def handle_coordinator_report(args: argparse.Namespace) -> int:
    print(json.dumps(coordinator_service(args.project_root).build_report(), sort_keys=True))
    return 0


def handle_record_rule_update(args: argparse.Namespace) -> int:
    coordinator_service(args.project_root).record_rule_update(
        source=args.source,
        summary=args.summary,
        affected_task_ids=args.affected_task_id,
        action=args.action,
    )
    return 0


def handle_record_long_tail_issue(args: argparse.Namespace) -> int:
    coordinator_service(args.project_root).record_long_tail_issue(
        category=args.category,
        summary=args.summary,
        task_ids=args.task_id,
        recommended_action=args.recommended_action,
    )
    return 0
```

- [ ] **Step 4: Run the tests to verify they pass**

Run:

```bash
UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy uv run pytest tests/test_coordinator_cli.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

Run:

```bash
git add annotation_pipeline_skill/interfaces/cli.py tests/test_coordinator_cli.py
git commit -m "feat: add coordinator cli commands"
```

## Task 5: Agent Operator Documentation

**Files:**
- Create: `docs/agent-operator-guide.md`
- Create: `docs/algorithm-engineer-user-story.md`
- Modify: `README.md`

- [ ] **Step 1: Write failing documentation tests**

Append to `tests/test_skill_packaging.py`:

```python
def test_agent_operator_docs_explain_algorithm_engineer_coordination():
    guide = Path("docs/agent-operator-guide.md").read_text(encoding="utf-8")
    story = Path("docs/algorithm-engineer-user-story.md").read_text(encoding="utf-8")
    readme = Path("README.md").read_text(encoding="utf-8")

    assert "algorithm engineer" in guide
    assert "Human Review" in guide
    assert "long-tail" in guide
    assert "active learning" in guide
    assert "RL" in guide
    assert "training data" in story
    assert "model training" in story
    assert "SKILL.md" in readme
    assert "Vite + React + TypeScript frontend" in readme
```

- [ ] **Step 2: Run the documentation tests to verify they fail**

Run:

```bash
UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy uv run pytest tests/test_skill_packaging.py -v
```

Expected: FAIL because the new docs do not exist and README is stale.

- [ ] **Step 3: Create `docs/agent-operator-guide.md`**

Create concise sections:

```markdown
# Agent Operator Guide

This guide is for agents using `annotation-pipeline-skill` to coordinate annotation projects that produce training data for an algorithm engineer.

## Operator Role

The agent is responsible for keeping the annotation project moving and for turning QC, Human Review, algorithm feedback, and model-training readiness signals into concrete rule updates or task actions.

## Operating Loop

1. Run `annotation-pipeline doctor`.
2. Run `annotation-pipeline coordinator report`.
3. Summarize blocked tasks, Human Review tasks, feedback categories, and training-data readiness.
4. Ask the user only for decisions that require domain judgment.
5. Record rule updates or long-tail issues before rerunning cycles.
6. Deliver accepted and merged training-data counts separately.

## Human Review

Human Review tasks require explicit user attention. The agent should explain why the task needs review and ask for accept, repair, reject, or more rules.

## Long-tail Issues

Long-tail issues are cases where the current rules or annotators do not cover a meaningful pattern. Record them with `annotation-pipeline coordinator record-long-tail-issue`.

## Algorithm Feedback

When the algorithm engineer reports that training data is not usable for model training, record a rule update and connect it to affected task ids or feedback categories.

## Future Active Learning And RL

The MVP records feedback, long-tail issues, and rule updates so later cycles can add active learning queues and RL feedback loops. Do not describe those loops as automated until implemented.
```

- [ ] **Step 4: Create `docs/algorithm-engineer-user-story.md`**

Create concise sections:

```markdown
# Algorithm Engineer User Story

As an algorithm engineer, I want an agent to run and coordinate an annotation project so that I receive training data for model training with clear provenance, review status, feedback history, and known limitations.

## Success Criteria

- I can see how many tasks are accepted and merged.
- I can see which tasks need Human Review.
- I can provide algorithm feedback and have it recorded as a rule update.
- I can inspect long-tail issues before using the dataset.
- I do not receive training data marked ready while blocking feedback or Human Review remains unresolved.
- The workflow can later evolve toward active learning and RL process management without changing the core audit trail.
```

- [ ] **Step 5: Update `README.md`**

Make these concrete edits:

- Replace “Not implemented yet: Vite + React + TypeScript frontend.” with “Implemented: Vite + React + TypeScript frontend.”
- Add a “Skill Usage” section linking `SKILL.md`, `docs/agent-operator-guide.md`, and `docs/algorithm-engineer-user-story.md`.
- Add coordinator command examples:

```bash
annotation-pipeline coordinator report --project-root ./demo-project
annotation-pipeline coordinator record-rule-update --project-root ./demo-project --source algorithm_feedback --summary "..." --affected-task-id task-1 --action update_annotation_guideline
annotation-pipeline coordinator record-long-tail-issue --project-root ./demo-project --category rare_case --summary "..." --task-id task-1 --recommended-action manual_annotation
```

- [ ] **Step 6: Run the documentation tests to verify they pass**

Run:

```bash
UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy uv run pytest tests/test_skill_packaging.py -v
```

Expected: PASS.

- [ ] **Step 7: Commit**

Run:

```bash
git add README.md docs/agent-operator-guide.md docs/algorithm-engineer-user-story.md tests/test_skill_packaging.py
git commit -m "docs: add agent operator guidance"
```

## Task 6: End-to-End Skill Verification

**Files:**
- Modify: `README.md` if verification reveals a command mismatch.

- [ ] **Step 1: Run backend tests**

Run:

```bash
UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy uv run pytest -v
```

Expected: all tests pass.

- [ ] **Step 2: Run frontend tests and build**

Run:

```bash
cd web && npm test -- --run
cd web && npm run build
```

Expected: frontend tests pass and Vite builds successfully.

- [ ] **Step 3: Verify console entrypoint**

Run:

```bash
UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy uv run --with-editable . annotation-pipeline --help
```

Expected output contains:

```text
{init,doctor,create-tasks,run-cycle,serve,coordinator}
```

- [ ] **Step 4: Verify coordinator workflow on a temp project**

Run:

```bash
PROJECT_ROOT=$(mktemp -d /tmp/annotation-coordinator-XXXXXX)
INPUT_FILE="$PROJECT_ROOT/input.jsonl"
printf '%s\n' '{"text":"alpha","modality":"text","annotation_types":["entity_span"]}' > "$INPUT_FILE"
UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy uv run --with-editable . annotation-pipeline init --project-root "$PROJECT_ROOT"
UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy uv run --with-editable . annotation-pipeline create-tasks --project-root "$PROJECT_ROOT" --source "$INPUT_FILE" --pipeline-id coord
UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy uv run --with-editable . annotation-pipeline run-cycle --project-root "$PROJECT_ROOT"
UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy uv run --with-editable . annotation-pipeline coordinator record-rule-update --project-root "$PROJECT_ROOT" --source algorithm_feedback --summary "Entity boundaries need review" --affected-task-id coord-000001 --action update_annotation_guideline
UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy uv run --with-editable . annotation-pipeline coordinator report --project-root "$PROJECT_ROOT"
```

Expected JSON contains:

```json
"rule_update_count": 1
```

and:

```json
"training_data_readiness"
```

- [ ] **Step 5: Commit any verification doc fixes**

If Step 3 or Step 4 reveals a README mismatch, edit README and commit:

```bash
git add README.md
git commit -m "docs: align coordinator verification commands"
```

If no mismatch exists, do not create an empty commit.

- [ ] **Step 6: Push**

Run:

```bash
git push
```

Expected: local `main` is pushed to `origin/main`.

## Self-Review

- Spec coverage: this plan packages the repository as an agent skill, adds agent-facing coordinator workflows, supports Human Review reminders, records algorithm feedback as rule updates, records long-tail issues, reports training-data readiness for algorithm engineers, and documents active learning/RL as future workflow-management directions.
- Placeholder scan: no unresolved placeholder markers remain.
- Type consistency: `RuleUpdateRecord`, `LongTailIssue`, `CoordinatorService`, and CLI command names match across tests, code snippets, and docs.
