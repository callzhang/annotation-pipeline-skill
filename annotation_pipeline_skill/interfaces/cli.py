from __future__ import annotations

import argparse
import hashlib
import json
import time
from collections.abc import Iterable
from dataclasses import dataclass, replace
from pathlib import Path

from annotation_pipeline_skill.config.loader import (
    ConfigValidationError,
    read_yaml,
    build_project_config_from_data,
    load_project_config,
    load_runtime_config,
    validate_project_config,
)
from annotation_pipeline_skill.config.models import ProjectConfig
from annotation_pipeline_skill.core.models import ArtifactRef, Attempt, Task, utc_now
from annotation_pipeline_skill.core.qc_policy import build_qc_policy, validate_qc_sample_options
from annotation_pipeline_skill.core.runtime import RuntimeConfig
from annotation_pipeline_skill.core.states import AttemptStatus, TaskStatus
from annotation_pipeline_skill.core.transitions import transition_task
from annotation_pipeline_skill.interfaces.api import serve_dashboard_api
from annotation_pipeline_skill.llm.local_cli import LocalCLIClient
from annotation_pipeline_skill.llm.openai_compatible import OpenAICompatibleClient
from annotation_pipeline_skill.llm.openai_responses import OpenAIResponsesClient
from annotation_pipeline_skill.llm.profiles import ProfileValidationError, load_llm_registry
from annotation_pipeline_skill.runtime.local_scheduler import LocalRuntimeScheduler
from annotation_pipeline_skill.runtime.snapshot import build_runtime_snapshot
from annotation_pipeline_skill.services.coordinator_service import CoordinatorService
from annotation_pipeline_skill.services.external_task_service import ExternalTaskService
from annotation_pipeline_skill.services.export_service import TrainingDataExportService
from annotation_pipeline_skill.services.human_review_service import HumanReviewService
from annotation_pipeline_skill.services.outbox_dispatch_service import OutboxDispatchService, build_outbox_summary
from annotation_pipeline_skill.services.readiness_service import build_readiness_report
from annotation_pipeline_skill.store.sqlite_store import SqliteStore


@dataclass(frozen=True)
class RuntimeCliContext:
    project_root: Path
    config: ProjectConfig
    store: SqliteStore
    registry: object


CONFIG_FILES: dict[str, str] = {
    "workflow.yaml": """stages:
  annotation:
    target: annotation
  qc:
    target: qc
human_review:
  required: false
runtime:
  max_concurrent_tasks: 4
  max_starts_per_cycle: 2
  stale_after_seconds: 600
  retry_delay_seconds: 3600
  loop_interval_seconds: 5
""",
    "annotators.yaml": """annotators:
  text_annotator:
    display_name: Text Annotator
    modalities: [text]
    annotation_types: [entity_span, classification, relation, structured_json]
    input_artifact_kinds: [raw_slice]
    output_artifact_kinds: [annotation_result]
    provider_target: annotation
    enabled: true
  image_bbox_annotator:
    display_name: Image Bounding Box Annotator
    modalities: [image]
    annotation_types: [bounding_box, segmentation]
    input_artifact_kinds: [raw_slice]
    output_artifact_kinds: [annotation_result, image_bbox_preview]
    provider_target: annotation
    preview_renderer_id: image_bbox_preview
    enabled: true
""",
    "annotation_rules.yaml": """rules:
  - id: entity_span_defaults
    applies_to: [entity_span]
    instruction: Label person, organization, location, date, product, and event mentions with exact text spans.
    examples: []
""",
    "external_tasks.yaml": """external_tasks:
  default:
    enabled: false
    system_id: external
    pull_url: null
    auth_secret_env: null
    qc_sample_count: null
    qc_sample_ratio: null
""",
    "callbacks.yaml": """callbacks:
  status:
    enabled: false
    url: null
    secret_env: null
  submit:
    enabled: false
    url: null
    secret_env: null
""",
    "llm_profiles.yaml": """profiles:
  local_codex:
    provider: local_cli
    cli_kind: codex
    cli_binary: codex
    model: gpt-5.4-mini
    reasoning_effort: none
    timeout_seconds: 900
    no_progress_timeout_seconds: 30
  local_claude:
    provider: local_cli
    cli_kind: claude
    cli_binary: claude
    model: claude-sonnet-4-6
    permission_mode: dontAsk
    timeout_seconds: 900
    no_progress_timeout_seconds: 30
  openai_default:
    provider: openai_responses
    model: gpt-5.4-mini
    api_key_env: OPENAI_API_KEY
    base_url: https://api.openai.com/v1
    reasoning_effort: medium
    timeout_seconds: 300
  deepseek_flash:
    provider: openai_compatible
    provider_flavor: deepseek
    model: deepseek-v4-flash
    api_key_env: DEEPSEEK_API_KEY
    base_url: https://api.deepseek.com
    timeout_seconds: 120
  deepseek_pro:
    provider: openai_compatible
    provider_flavor: deepseek
    model: deepseek-v4-pro
    api_key_env: DEEPSEEK_API_KEY
    base_url: https://api.deepseek.com
    timeout_seconds: 300
  glm_flash:
    provider: openai_compatible
    provider_flavor: glm
    model: glm-4-flash
    api_key_env: BIGMODEL_MCP_API_KEY
    base_url: https://open.bigmodel.cn/api/paas/v4
    timeout_seconds: 120
  glm_air:
    provider: openai_compatible
    provider_flavor: glm
    model: glm-4.5-air
    api_key_env: BIGMODEL_MCP_API_KEY
    base_url: https://open.bigmodel.cn/api/coding/paas/v4
    timeout_seconds: 300
  minimax_default:
    provider: openai_compatible
    provider_flavor: minimax
    model: MiniMax-M2.7
    api_key_env: MINIMAX_API_KEY
    base_url: https://api.minimaxi.com/v1
    timeout_seconds: 300
targets:
  annotation: deepseek_flash
  qc: deepseek_flash
  coordinator: deepseek_flash
limits:
  local_cli_global_concurrency: 4
""",
}


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.handler(args)


def console_main() -> None:
    raise SystemExit(main())


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="annotation-pipeline")
    subparsers = parser.add_subparsers(required=True)

    init_parser = subparsers.add_parser("init")
    init_parser.add_argument("--project-root", type=Path, default=Path.cwd())
    init_parser.set_defaults(handler=handle_init)

    doctor_parser = subparsers.add_parser("doctor")
    doctor_parser.add_argument("--project-root", type=Path, default=Path.cwd())
    doctor_parser.set_defaults(handler=handle_doctor)

    create_parser = subparsers.add_parser("create-tasks")
    create_parser.add_argument("--project-root", type=Path, default=Path.cwd())
    create_parser.add_argument("--source", type=Path, required=True)
    create_parser.add_argument("--pipeline-id", required=True)
    create_parser.add_argument("--batch-size", type=int, default=1)
    create_parser.add_argument("--annotation-type", action="append", dest="annotation_types")
    create_parser.add_argument("--modality", default="text")
    create_parser.add_argument("--task-prefix")
    create_parser.add_argument("--group-by", action="append", default=[])
    create_parser.add_argument("--qc-sample-count", type=int)
    create_parser.add_argument("--qc-sample-ratio", type=float)
    create_parser.add_argument("--document-version-id")
    create_parser.set_defaults(handler=handle_create_tasks)

    document_parser = subparsers.add_parser("document")
    document_subparsers = document_parser.add_subparsers(required=True)

    doc_create = document_subparsers.add_parser("create")
    doc_create.add_argument("--project-root", type=Path, default=Path.cwd())
    doc_create.add_argument("--title", required=True)
    doc_create.add_argument("--description", default="")
    doc_create.add_argument("--created-by", default="operator")
    doc_create.set_defaults(handler=handle_document_create)

    doc_list = document_subparsers.add_parser("list")
    doc_list.add_argument("--project-root", type=Path, default=Path.cwd())
    doc_list.set_defaults(handler=handle_document_list)

    doc_version_parser = document_subparsers.add_parser("version")
    doc_version_subparsers = doc_version_parser.add_subparsers(required=True)

    doc_version_add = doc_version_subparsers.add_parser("add")
    doc_version_add.add_argument("--project-root", type=Path, default=Path.cwd())
    doc_version_add.add_argument("--document-id", required=True)
    doc_version_add.add_argument("--version", required=True)
    doc_version_add.add_argument("--content-file", type=Path, required=True)
    doc_version_add.add_argument("--changelog", default="")
    doc_version_add.add_argument("--created-by", default="operator")
    doc_version_add.set_defaults(handler=handle_document_version_add)

    doc_version_list = doc_version_subparsers.add_parser("list")
    doc_version_list.add_argument("--project-root", type=Path, default=Path.cwd())
    doc_version_list.add_argument("--document-id", required=True)
    doc_version_list.set_defaults(handler=handle_document_version_list)

    doc_version_show = doc_version_subparsers.add_parser("show")
    doc_version_show.add_argument("--project-root", type=Path, default=Path.cwd())
    doc_version_show.add_argument("--version-id", required=True)
    doc_version_show.set_defaults(handler=handle_document_version_show)

    import_parser = subparsers.add_parser("import")
    import_subparsers = import_parser.add_subparsers(required=True)

    annotation_manager_v2 = import_subparsers.add_parser("annotation-manager-v2")
    annotation_manager_v2.add_argument("--project-root", type=Path, default=Path.cwd())
    annotation_manager_v2.add_argument("--source-task-root", type=Path, required=True)
    annotation_manager_v2.add_argument("--pipeline-id", required=True)
    annotation_manager_v2.add_argument("--task-prefix")
    annotation_manager_v2.add_argument("--status", action="append", choices=("accepted", "merged"))
    annotation_manager_v2.add_argument("--limit", type=int)
    annotation_manager_v2.add_argument("--qc-sample-count", type=int)
    annotation_manager_v2.add_argument("--qc-sample-ratio", type=float)
    annotation_manager_v2.set_defaults(handler=handle_import_annotation_manager_v2)

    cycle_parser = subparsers.add_parser("run-cycle")
    cycle_parser.add_argument("--project-root", type=Path, default=Path.cwd())
    cycle_parser.add_argument("--limit", type=int, default=None)
    cycle_parser.add_argument("--runtime", choices=("subagent",), default="subagent")
    cycle_parser.add_argument("--stage-target", default="annotation")
    cycle_parser.set_defaults(handler=handle_run_cycle)

    runtime_parser = subparsers.add_parser("runtime")
    runtime_subparsers = runtime_parser.add_subparsers(required=True)

    runtime_once = runtime_subparsers.add_parser("once")
    runtime_once.add_argument("--project-root", type=Path, default=Path.cwd())
    runtime_once.add_argument("--stage-target", default="annotation")
    runtime_once.set_defaults(handler=handle_runtime_once)

    runtime_run = runtime_subparsers.add_parser("run")
    runtime_run.add_argument("--project-root", type=Path, default=Path.cwd())
    runtime_run.add_argument("--stage-target", default="annotation")
    runtime_run.add_argument("--max-cycles", type=int, default=None)
    runtime_run.set_defaults(handler=handle_runtime_run)

    runtime_status = runtime_subparsers.add_parser("status")
    runtime_status.add_argument("--project-root", type=Path, default=Path.cwd())
    runtime_status.set_defaults(handler=handle_runtime_status)

    provider_parser = subparsers.add_parser("provider")
    provider_subparsers = provider_parser.add_subparsers(required=True)

    provider_doctor = provider_subparsers.add_parser("doctor")
    provider_doctor.add_argument("--project-root", type=Path, default=Path.cwd())
    provider_doctor.set_defaults(handler=handle_provider_doctor)

    provider_targets = provider_subparsers.add_parser("targets")
    provider_targets.add_argument("--project-root", type=Path, default=Path.cwd())
    provider_targets.set_defaults(handler=handle_provider_targets)

    export_parser = subparsers.add_parser("export")
    export_subparsers = export_parser.add_subparsers(required=True)

    training_data = export_subparsers.add_parser("training-data")
    training_data.add_argument("--project-root", type=Path, default=Path.cwd())
    training_data.add_argument("--project-id", required=True)
    training_data.add_argument("--output-dir", type=Path)
    training_data.add_argument("--export-id")
    training_data.add_argument("--enqueue-external-submit", action="store_true")
    training_data.set_defaults(handler=handle_export_training_data)

    report_parser = subparsers.add_parser("report")
    report_subparsers = report_parser.add_subparsers(required=True)

    readiness = report_subparsers.add_parser("readiness")
    readiness.add_argument("--project-root", type=Path, default=Path.cwd())
    readiness.add_argument("--project-id", required=True)
    readiness.set_defaults(handler=handle_report_readiness)

    outbox_parser = subparsers.add_parser("outbox")
    outbox_subparsers = outbox_parser.add_subparsers(required=True)

    outbox_status = outbox_subparsers.add_parser("status")
    outbox_status.add_argument("--project-root", type=Path, default=Path.cwd())
    outbox_status.set_defaults(handler=handle_outbox_status)

    outbox_drain = outbox_subparsers.add_parser("drain")
    outbox_drain.add_argument("--project-root", type=Path, default=Path.cwd())
    outbox_drain.add_argument("--max-items", type=int, default=10)
    outbox_drain.add_argument("--max-attempts", type=int, default=3)
    outbox_drain.add_argument("--retry-delay-seconds", type=int, default=60)
    outbox_drain.set_defaults(handler=handle_outbox_drain)

    human_review_parser = subparsers.add_parser("human-review")
    human_review_subparsers = human_review_parser.add_subparsers(required=True)

    human_review_decide = human_review_subparsers.add_parser("decide")
    human_review_decide.add_argument("--project-root", type=Path, default=Path.cwd())
    human_review_decide.add_argument("--task-id", required=True)
    human_review_decide.add_argument("--action", choices=("accept", "reject", "request_changes"), required=True)
    human_review_decide.add_argument("--actor", required=True)
    human_review_decide.add_argument("--feedback", required=True)
    human_review_decide.add_argument(
        "--correction-mode",
        choices=("manual_annotation", "batch_code_update"),
        default="manual_annotation",
    )
    human_review_decide.set_defaults(handler=handle_human_review_decide)

    human_review_correct = human_review_subparsers.add_parser(
        "correct", help="submit a schema-validated correction for a task in HUMAN_REVIEW"
    )
    human_review_correct.add_argument("--root", required=True)
    human_review_correct.add_argument("--task", required=True)
    human_review_correct.add_argument(
        "--answer-file", required=True, help="path to a JSON file containing the corrected answer"
    )
    human_review_correct.add_argument("--actor", required=True)
    human_review_correct.add_argument("--note", default=None)
    human_review_correct.set_defaults(handler=handle_human_review_correct)

    coordinator_parser = subparsers.add_parser("coordinator")
    coordinator_subparsers = coordinator_parser.add_subparsers(required=True)

    coordinator_report = coordinator_subparsers.add_parser("report")
    coordinator_report.add_argument("--project-root", type=Path, default=Path.cwd())
    coordinator_report.add_argument("--project-id")
    coordinator_report.set_defaults(handler=handle_coordinator_report)

    coordinator_rule = coordinator_subparsers.add_parser("rule-update")
    coordinator_rule.add_argument("--project-root", type=Path, default=Path.cwd())
    coordinator_rule.add_argument("--project-id", required=True)
    coordinator_rule.add_argument("--source", required=True)
    coordinator_rule.add_argument("--summary", required=True)
    coordinator_rule.add_argument("--action", required=True)
    coordinator_rule.add_argument("--created-by", default="coordinator-agent")
    coordinator_rule.add_argument("--task-id", action="append", default=[])
    coordinator_rule.set_defaults(handler=handle_coordinator_rule_update)

    coordinator_issue = coordinator_subparsers.add_parser("long-tail-issue")
    coordinator_issue.add_argument("--project-root", type=Path, default=Path.cwd())
    coordinator_issue.add_argument("--project-id", required=True)
    coordinator_issue.add_argument("--category", required=True)
    coordinator_issue.add_argument("--summary", required=True)
    coordinator_issue.add_argument("--recommended-action", required=True)
    coordinator_issue.add_argument("--severity", default="medium")
    coordinator_issue.add_argument("--created-by", default="coordinator-agent")
    coordinator_issue.add_argument("--task-id", action="append", default=[])
    coordinator_issue.set_defaults(handler=handle_coordinator_long_tail_issue)

    task_parser = subparsers.add_parser("task")
    task_subparsers = task_parser.add_subparsers(required=True)

    task_unblock = task_subparsers.add_parser("unblock")
    task_unblock.add_argument("--project-root", type=Path, default=Path.cwd())
    task_unblock.add_argument("--task-id", required=True)
    task_unblock.add_argument("--actor", default="operator")
    task_unblock.add_argument("--reason", default="manually unblocked")
    task_unblock.set_defaults(handler=handle_task_unblock)

    external_parser = subparsers.add_parser("external")
    external_subparsers = external_parser.add_subparsers(required=True)

    external_pull = external_subparsers.add_parser("pull")
    external_pull.add_argument("--project-root", type=Path, default=Path.cwd())
    external_pull.add_argument("--project-id", required=True)
    external_pull.add_argument("--source-id", default="default")
    external_pull.add_argument("--limit", type=int, default=100)
    external_pull.set_defaults(handler=handle_external_pull)

    serve_parser = subparsers.add_parser("serve")
    serve_parser.add_argument("--workspace", type=Path, default=Path.cwd())
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", type=int, default=8765)
    serve_parser.set_defaults(handler=handle_serve)

    _register_db_commands(subparsers)

    return parser


def _register_db_commands(subparsers) -> None:
    db = subparsers.add_parser("db", help="database utilities")
    db_sub = db.add_subparsers(dest="db_command", required=True)

    p_init = db_sub.add_parser("init", help="initialize an empty SqliteStore at --root")
    p_init.add_argument("--root", required=True)
    p_init.set_defaults(handler=_cmd_db_init)

    p_status = db_sub.add_parser("status", help="print row counts")
    p_status.add_argument("--root", required=True)
    p_status.set_defaults(handler=_cmd_db_status)

    p_backup = db_sub.add_parser("backup", help="snapshot db.sqlite + prune")
    p_backup.add_argument("--root", required=True)
    p_backup.add_argument("--hourly-keep", type=int, default=24)
    p_backup.add_argument("--daily-keep", type=int, default=30)
    p_backup.set_defaults(handler=_cmd_db_backup)

    p_dump = db_sub.add_parser("dump-json", help="export DB to JSON tree")
    p_dump.add_argument("--root", required=True)
    p_dump.add_argument("--out", required=True)
    p_dump.set_defaults(handler=_cmd_db_dump_json)


def _cmd_db_init(args: argparse.Namespace) -> int:
    from annotation_pipeline_skill.store.sqlite_store import SqliteStore
    SqliteStore.open(args.root).close()
    return 0


def _cmd_db_status(args: argparse.Namespace) -> int:
    from annotation_pipeline_skill.store.sqlite_store import SqliteStore
    store = SqliteStore.open(args.root)
    print(f"tasks: {len(store.list_tasks())}")
    print(f"outbox: {len(store.list_outbox())}")
    print(f"documents: {len(store.list_documents())}")
    print(f"exports: {len(store.list_export_manifests())}")
    print(f"active_runs: {len(store.list_active_runs())}")
    print(f"leases: {len(store.list_runtime_leases())}")
    store.close()
    return 0


def _cmd_db_backup(args: argparse.Namespace) -> int:
    from annotation_pipeline_skill.store.backup import prune_snapshots, snapshot
    root = Path(args.root)
    out = snapshot(root / "db.sqlite", root / "backups")
    deleted = prune_snapshots(
        root / "backups",
        hourly_keep=args.hourly_keep,
        daily_keep=args.daily_keep,
    )
    print(f"created: {out}")
    print(f"pruned: {len(deleted)}")
    return 0


def _cmd_db_dump_json(args: argparse.Namespace) -> int:
    from annotation_pipeline_skill.store.dump import dump_to_json
    from annotation_pipeline_skill.store.sqlite_store import SqliteStore
    store = SqliteStore.open(args.root)
    dump_to_json(store, Path(args.out))
    store.close()
    return 0


def handle_init(args: argparse.Namespace) -> int:
    config_root = args.project_root / ".annotation-pipeline"
    for name in (
        "tasks",
        "events",
        "feedback",
        "feedback_discussions",
        "attempts",
        "artifacts",
        "outbox",
        "exports",
        "runtime",
        "snapshots",
        "coordination",
        "documents",
        "document_versions",
    ):
        (config_root / name).mkdir(parents=True, exist_ok=True)
    for filename, content in CONFIG_FILES.items():
        path = config_root / filename
        if not path.exists():
            path.write_text(content, encoding="utf-8")
    return 0


def handle_doctor(args: argparse.Namespace) -> int:
    try:
        load_project_config(args.project_root)
    except ConfigValidationError:
        return 1
    required_dirs = (
        "tasks",
        "events",
        "feedback",
        "feedback_discussions",
        "attempts",
        "artifacts",
        "outbox",
        "exports",
        "coordination",
    )
    config_root = args.project_root / ".annotation-pipeline"
    return 0 if all((config_root / name).is_dir() for name in required_dirs) else 1


def handle_create_tasks(args: argparse.Namespace) -> int:
    if args.batch_size <= 0:
        raise ValueError("--batch-size must be > 0")
    validate_qc_sample_options(args.qc_sample_count, args.qc_sample_ratio)
    store = SqliteStore.open(args.project_root / ".annotation-pipeline")
    rows = read_jsonl(args.source)
    task_prefix = args.task_prefix or args.pipeline_id
    batches = build_batches(rows, batch_size=args.batch_size, group_by=args.group_by)
    for index, batch in enumerate(batches, start=1):
        annotation_types = args.annotation_types or batch_annotation_types(batch)
        source_payload = batch[0] if args.batch_size == 1 else {"rows": batch}
        task = Task.new(
            task_id=f"{task_prefix}-{index:06d}",
            pipeline_id=args.pipeline_id,
            source_ref={
                "kind": "jsonl",
                "path": str(args.source),
                "line_start": ((index - 1) * args.batch_size) + 1,
                "line_end": ((index - 1) * args.batch_size) + len(batch),
                "row_count": len(batch),
                "payload": source_payload,
            },
            modality=batch_modality(batch, args.modality),
            annotation_requirements={"annotation_types": annotation_types},
            metadata=batch_metadata(
                batch,
                qc_sample_count=args.qc_sample_count,
                qc_sample_ratio=args.qc_sample_ratio,
            ),
            document_version_id=getattr(args, "document_version_id", None),
        )
        event = transition_task(
            task,
            TaskStatus.PENDING,
            actor="cli",
            reason="created from jsonl source",
            stage="prepare",
        )
        store.save_task(task)
        store.append_event(event)
    return 0


def handle_import_annotation_manager_v2(args: argparse.Namespace) -> int:
    validate_qc_sample_options(args.qc_sample_count, args.qc_sample_ratio)
    store = SqliteStore.open(args.project_root / ".annotation-pipeline")
    statuses = set(args.status or ["accepted", "merged"])
    task_prefix = args.task_prefix or args.pipeline_id
    imported = 0
    skipped = 0
    for source_task_file in sorted(args.source_task_root.rglob("*.task.json")):
        if args.limit is not None and imported >= args.limit:
            break
        source_task = json.loads(source_task_file.read_text(encoding="utf-8"))
        if source_task.get("status") not in statuses:
            skipped += 1
            continue
        output_file = _annotation_manager_v2_output_file(source_task, source_task_file)
        if output_file is None:
            skipped += 1
            continue
        annotated_rows = _read_annotation_manager_v2_rows(output_file)
        if not annotated_rows:
            skipped += 1
            continue
        imported += 1
        _save_annotation_manager_v2_task(
            store=store,
            pipeline_id=args.pipeline_id,
            task_id=f"{task_prefix}-{imported:06d}",
            source_task=source_task,
            source_task_file=source_task_file,
            output_file=output_file,
            annotated_rows=annotated_rows,
            qc_sample_count=args.qc_sample_count,
            qc_sample_ratio=args.qc_sample_ratio,
        )
    print(json.dumps({"imported": imported, "skipped": skipped, "pipeline_id": args.pipeline_id}, sort_keys=True, indent=2))
    return 0


def _annotation_manager_v2_output_file(source_task: dict, source_task_file: Path) -> Path | None:
    raw_output_file = source_task.get("output_file")
    if not isinstance(raw_output_file, str) or not raw_output_file.strip():
        return None
    output_file = Path(raw_output_file)
    if not output_file.is_absolute():
        output_file = source_task_file.parent / output_file
    if not output_file.is_file():
        return None
    return output_file


def _read_annotation_manager_v2_rows(output_file: Path) -> list[dict]:
    rows = []
    for line_no, line in enumerate(output_file.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        row = json.loads(line)
        text = row.get("input") or row.get("text")
        output = row.get("output")
        if isinstance(text, str) and isinstance(output, dict):
            rows.append(
                {
                    "source_row_index": line_no,
                    "text": text,
                    "source_dataset": row.get("source_dataset"),
                    "source_path": row.get("source_path"),
                    "output": output,
                }
            )
    return rows


def _save_annotation_manager_v2_task(
    *,
    store: SqliteStore,
    pipeline_id: str,
    task_id: str,
    source_task: dict,
    source_task_file: Path,
    output_file: Path,
    annotated_rows: list[dict],
    qc_sample_count: int | None,
    qc_sample_ratio: float | None,
) -> None:
    attempt_id = f"{task_id}-attempt-1"
    source_rows = [
        {
            "source_row_index": row["source_row_index"],
            "text": row["text"],
            "source_dataset": row.get("source_dataset"),
            "source_path": row.get("source_path"),
        }
        for row in annotated_rows
    ]
    task = Task.new(
        task_id=task_id,
        pipeline_id=pipeline_id,
        source_ref={
            "kind": "annotation_manager_v2",
            "task_file": str(source_task_file),
            "output_file": str(output_file),
            "row_count": len(source_rows),
            "payload": {"rows": source_rows},
        },
        modality="text",
        annotation_requirements={"annotation_types": ["entity_span", "structured_json"]},
        metadata={
            "row_count": len(source_rows),
            "source_task_id": source_task.get("task_id"),
            "source_task_status": source_task.get("status"),
            "runtime_next_stage": "qc",
            "qc_policy": build_qc_policy(
                row_count=len(source_rows),
                qc_sample_count=qc_sample_count,
                qc_sample_ratio=qc_sample_ratio,
            ),
        },
    )
    prepare_event = transition_task(
        task,
        TaskStatus.PENDING,
        actor="cli",
        reason="imported annotation manager v2 task",
        stage="prepare",
        metadata={"source_task_id": source_task.get("task_id"), "source_task_status": source_task.get("status")},
    )
    annotating_event = transition_task(
        task,
        TaskStatus.ANNOTATING,
        actor="cli",
        reason="attached annotation manager v2 annotation artifact",
        stage="annotation",
        attempt_id=attempt_id,
    )
    validating_event = transition_task(
        task,
        TaskStatus.VALIDATING,
        actor="cli",
        reason="imported annotation artifact ready for qc",
        stage="validation",
        attempt_id=attempt_id,
    )
    qc_event = transition_task(
        task,
        TaskStatus.QC,
        actor="cli",
        reason="queued imported annotation for qc",
        stage="qc",
        attempt_id=attempt_id,
    )
    task.current_attempt = 1
    store.save_task(task)
    for event in (prepare_event, annotating_event, validating_event, qc_event):
        store.append_event(event)

    relative_path = f"artifact_payloads/{task_id}/{attempt_id}_annotation_result.json"
    payload_path = store.root / relative_path
    payload_path.parent.mkdir(parents=True, exist_ok=True)
    imported_annotation = {
        "rows": [
            {
                "source_row_index": row["source_row_index"],
                "text": row["text"],
                "output": row["output"],
            }
            for row in annotated_rows
        ]
    }
    payload_path.write_text(
        json.dumps(
            {
                "task_id": task_id,
                "text": json.dumps(imported_annotation, ensure_ascii=False, sort_keys=True),
                "imported_annotation": imported_annotation,
                "raw_response": {
                    "source": "annotation_manager_v2",
                    "source_task_id": source_task.get("task_id"),
                    "source_task_status": source_task.get("status"),
                    "task_file": str(source_task_file),
                    "output_file": str(output_file),
                },
                "usage": {},
                "diagnostics": {"imported": True, "source": "annotation_manager_v2"},
            },
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    artifact = ArtifactRef.new(
        task_id=task_id,
        kind="annotation_result",
        path=relative_path,
        content_type="application/json",
        metadata={
            "runtime": "import",
            "provider": "annotation_manager_v2",
            "model": None,
            "diagnostics": {"imported": True, "source": "annotation_manager_v2"},
        },
    )
    store.append_artifact(artifact)
    store.append_attempt(
        Attempt(
            attempt_id=attempt_id,
            task_id=task_id,
            index=1,
            stage="annotation",
            status=AttemptStatus.SUCCEEDED,
            started_at=utc_now(),
            finished_at=utc_now(),
            provider_id="annotation_manager_v2",
            model=None,
            route_role="import",
            summary=f"Imported {len(annotated_rows)} annotation manager v2 rows for QC.",
            artifacts=[artifact],
        )
    )


def handle_document_create(args: argparse.Namespace) -> int:
    from annotation_pipeline_skill.core.models import AnnotationDocument
    store = SqliteStore.open(args.project_root / ".annotation-pipeline")
    doc = AnnotationDocument.new(
        title=args.title,
        description=args.description,
        created_by=args.created_by,
    )
    store.save_document(doc)
    print(json.dumps(doc.to_dict(), sort_keys=True, indent=2))
    return 0


def handle_document_list(args: argparse.Namespace) -> int:
    store = SqliteStore.open(args.project_root / ".annotation-pipeline")
    docs = store.list_documents()
    print(json.dumps({"documents": [doc.to_dict() for doc in docs]}, sort_keys=True, indent=2))
    return 0


def handle_document_version_add(args: argparse.Namespace) -> int:
    from annotation_pipeline_skill.core.models import AnnotationDocumentVersion
    store = SqliteStore.open(args.project_root / ".annotation-pipeline")
    content = args.content_file.read_text(encoding="utf-8")
    ver = AnnotationDocumentVersion.new(
        document_id=args.document_id,
        version=args.version,
        content=content,
        changelog=args.changelog,
        created_by=args.created_by,
    )
    store.save_document_version(ver)
    print(json.dumps(ver.to_dict(), sort_keys=True, indent=2))
    return 0


def handle_document_version_list(args: argparse.Namespace) -> int:
    store = SqliteStore.open(args.project_root / ".annotation-pipeline")
    versions = store.list_document_versions(args.document_id)
    print(json.dumps({"versions": [v.to_dict() for v in versions]}, sort_keys=True, indent=2))
    return 0


def handle_document_version_show(args: argparse.Namespace) -> int:
    store = SqliteStore.open(args.project_root / ".annotation-pipeline")
    ver = store.load_document_version(args.version_id)
    print(json.dumps(ver.to_dict(), sort_keys=True, indent=2))
    return 0


def read_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        payload = json.loads(line)
        if not isinstance(payload, dict):
            raise ValueError(f"{path}:{line_no} must be a JSON object")
        rows.append(payload)
    return rows


def chunked(rows: list[dict], size: int) -> Iterable[list[dict]]:
    for index in range(0, len(rows), size):
        yield rows[index : index + size]


def build_batches(rows: list[dict], *, batch_size: int, group_by: list[str]) -> list[list[dict]]:
    if not group_by:
        return list(chunked(rows, batch_size))
    buckets: dict[tuple[str, ...], list[dict]] = {}
    order: list[tuple[str, ...]] = []
    for row in rows:
        key = tuple(str(row.get(field) or "") for field in group_by)
        if key not in buckets:
            buckets[key] = []
            order.append(key)
        buckets[key].append(row)
    batches: list[list[dict]] = []
    for key in order:
        batches.extend(chunked(buckets[key], batch_size))
    return batches


def batch_annotation_types(batch: list[dict]) -> list[str]:
    for row in batch:
        values = row.get("annotation_types")
        if isinstance(values, list) and all(isinstance(item, str) for item in values):
            return values
    return ["entity_span"]


def batch_modality(batch: list[dict], default: str) -> str:
    for row in batch:
        value = row.get("modality")
        if isinstance(value, str) and value:
            return value
    return default


def batch_metadata(
    batch: list[dict],
    *,
    qc_sample_count: int | None = None,
    qc_sample_ratio: float | None = None,
) -> dict:
    sources = sorted({str(row.get("source") or row.get("source_dataset") or "") for row in batch if row.get("source") or row.get("source_dataset")})
    metadata = {
        "row_count": len(batch),
        "qc_policy": build_qc_policy(
            row_count=len(batch),
            qc_sample_count=qc_sample_count,
            qc_sample_ratio=qc_sample_ratio,
        ),
    }
    if sources:
        metadata["sources"] = sources
    return metadata


def handle_run_cycle(args: argparse.Namespace) -> int:
    context = _runtime_context(args.project_root)
    runtime_config = context.config.runtime
    if args.limit is not None:
        runtime_config = replace(
            runtime_config,
            max_starts_per_cycle=min(runtime_config.max_starts_per_cycle, args.limit),
        )
    snapshot = _build_runtime_scheduler(context, runtime_config).run_once(stage_target=args.stage_target)
    print(json.dumps(snapshot.to_dict(), sort_keys=True, indent=2))
    return 0


def handle_runtime_once(args: argparse.Namespace) -> int:
    context = _runtime_context(args.project_root)
    snapshot = _build_runtime_scheduler(context).run_once(stage_target=args.stage_target)
    print(json.dumps(snapshot.to_dict(), sort_keys=True, indent=2))
    return 0


def handle_runtime_status(args: argparse.Namespace) -> int:
    runtime_config = load_runtime_config(args.project_root)
    store = SqliteStore.open(args.project_root / ".annotation-pipeline")
    snapshot = store.load_runtime_snapshot()
    if snapshot is None:
        snapshot = build_runtime_snapshot(store, runtime_config)
        snapshot = replace(
            snapshot,
            runtime_status=replace(
                snapshot.runtime_status,
                healthy=False,
                active=False,
                errors=sorted(set([*snapshot.runtime_status.errors, "runtime_snapshot_missing"])),
            ),
        )
    print(json.dumps(snapshot.to_dict(), sort_keys=True, indent=2))
    return 0


def handle_runtime_run(args: argparse.Namespace) -> int:
    context = _runtime_context(args.project_root)
    scheduler = _build_runtime_scheduler(context)
    cycles = 0
    while args.max_cycles is None or cycles < args.max_cycles:
        snapshot = scheduler.run_once(stage_target=args.stage_target)
        print(json.dumps(snapshot.to_dict(), sort_keys=True, indent=2))
        cycles += 1
        if args.max_cycles is None or cycles < args.max_cycles:
            time.sleep(context.config.runtime.loop_interval_seconds)
    return 0


def handle_provider_doctor(args: argparse.Namespace) -> int:
    try:
        load_llm_registry(args.project_root / ".annotation-pipeline" / "llm_profiles.yaml")
    except (OSError, ProfileValidationError):
        return 1
    return 0


def handle_provider_targets(args: argparse.Namespace) -> int:
    try:
        registry = load_llm_registry(args.project_root / ".annotation-pipeline" / "llm_profiles.yaml")
    except (OSError, ProfileValidationError):
        return 1
    payload = {}
    for target in sorted(registry.targets):
        profile = registry.resolve(target)
        payload[target] = {
            "profile": profile.name,
            "provider": profile.provider,
            "provider_flavor": profile.provider_flavor,
            "cli_kind": profile.cli_kind,
            "model": profile.model,
            "base_url": profile.base_url,
        }
    print(json.dumps(payload, sort_keys=True, indent=2))
    return 0


def handle_export_training_data(args: argparse.Namespace) -> int:
    store = SqliteStore.open(args.project_root / ".annotation-pipeline")
    export_id = args.export_id
    output_dir = args.output_dir or store.root / "exports" / (export_id or args.project_id)
    manifest = TrainingDataExportService(store).export_jsonl(
        project_id=args.project_id,
        output_dir=output_dir,
        export_id=export_id,
        enqueue_external_submit=args.enqueue_external_submit,
    )
    print(json.dumps(manifest.to_dict(), sort_keys=True, indent=2))
    return 0


def handle_report_readiness(args: argparse.Namespace) -> int:
    store = SqliteStore.open(args.project_root / ".annotation-pipeline")
    print(json.dumps(build_readiness_report(store, args.project_id), sort_keys=True, indent=2))
    return 0


def handle_outbox_status(args: argparse.Namespace) -> int:
    store = SqliteStore.open(args.project_root / ".annotation-pipeline")
    print(json.dumps(build_outbox_summary(store), sort_keys=True, indent=2))
    return 0


def handle_outbox_drain(args: argparse.Namespace) -> int:
    config_root = args.project_root / ".annotation-pipeline"
    callbacks_data = read_yaml(config_root / "callbacks.yaml")
    store = SqliteStore.open(config_root)
    result = OutboxDispatchService(
        store,
        callbacks=callbacks_data.get("callbacks", {}),
    ).drain(
        max_items=args.max_items,
        max_attempts=args.max_attempts,
        retry_delay_seconds=args.retry_delay_seconds,
    )
    print(json.dumps({"result": result, "outbox": build_outbox_summary(store)}, sort_keys=True, indent=2))
    return 0


def handle_human_review_decide(args: argparse.Namespace) -> int:
    store = SqliteStore.open(args.project_root / ".annotation-pipeline")
    result = HumanReviewService(store).decide(
        task_id=args.task_id,
        action=args.action,
        actor=args.actor,
        feedback=args.feedback,
        correction_mode=args.correction_mode,
    )
    print(json.dumps(result.to_dict(), sort_keys=True, indent=2))
    return 0


def handle_human_review_correct(args: argparse.Namespace) -> int:
    from annotation_pipeline_skill.core.schema_validation import SchemaValidationError

    answer = json.loads(Path(args.answer_file).read_text(encoding="utf-8"))
    store = SqliteStore.open(args.root)
    try:
        result = HumanReviewService(store).submit_correction(
            task_id=args.task,
            answer=answer,
            actor=args.actor,
            note=args.note,
        )
    except SchemaValidationError as exc:
        print("schema validation failed:")
        for err in exc.errors:
            print(f"  - {err}")
        store.close()
        return 2
    print(f"task {result.task.task_id} accepted (artifact {result.artifact.artifact_id})")
    store.close()
    return 0


def handle_coordinator_report(args: argparse.Namespace) -> int:
    store = SqliteStore.open(args.project_root / ".annotation-pipeline")
    report = CoordinatorService(store).build_report(project_id=args.project_id)
    print(json.dumps(report, sort_keys=True, indent=2))
    return 0


def handle_coordinator_rule_update(args: argparse.Namespace) -> int:
    store = SqliteStore.open(args.project_root / ".annotation-pipeline")
    record = CoordinatorService(store).record_rule_update(
        project_id=args.project_id,
        source=args.source,
        summary=args.summary,
        action=args.action,
        created_by=args.created_by,
        task_ids=args.task_id,
    )
    print(json.dumps(record, sort_keys=True, indent=2))
    return 0


def handle_coordinator_long_tail_issue(args: argparse.Namespace) -> int:
    store = SqliteStore.open(args.project_root / ".annotation-pipeline")
    record = CoordinatorService(store).record_long_tail_issue(
        project_id=args.project_id,
        category=args.category,
        summary=args.summary,
        recommended_action=args.recommended_action,
        severity=args.severity,
        created_by=args.created_by,
        task_ids=args.task_id,
    )
    print(json.dumps(record, sort_keys=True, indent=2))
    return 0


def handle_task_unblock(args: argparse.Namespace) -> int:
    store = SqliteStore.open(args.project_root / ".annotation-pipeline")
    task = store.load_task(args.task_id)
    event = transition_task(
        task,
        TaskStatus.PENDING,
        actor=args.actor,
        reason=args.reason,
        stage="unblock",
    )
    store.save_task(task)
    store.append_event(event)
    print(json.dumps({"task": task.to_dict(), "event": event.to_dict()}, sort_keys=True, indent=2))
    return 0


def handle_external_pull(args: argparse.Namespace) -> int:
    config_root = args.project_root / ".annotation-pipeline"
    external_data = read_yaml(config_root / "external_tasks.yaml").get("external_tasks", {})
    store = SqliteStore.open(config_root)
    result = ExternalTaskService(store).pull_http_tasks(
        pipeline_id=args.project_id,
        source_id=args.source_id,
        config=dict(external_data[args.source_id]),
        limit=args.limit,
    )
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0


def discover_project_stores(workspace: Path) -> dict[str, Path]:
    workspace = Path(workspace).resolve()
    result: dict[str, Path] = {}
    seen: set[Path] = set()

    def _add(project_root: Path) -> None:
        resolved = project_root.resolve()
        if resolved in seen:
            return
        seen.add(resolved)
        key = hashlib.sha256(str(resolved).encode()).hexdigest()[:12]
        result[key] = resolved

    if (workspace / ".annotation-pipeline").is_dir():
        _add(workspace)

    base_parts = len(workspace.parts)
    for item in workspace.rglob(".annotation-pipeline"):
        depth = len(item.parts) - base_parts
        if depth > 4:
            continue
        if item.is_dir():
            _add(item.parent)

    return result


def handle_serve(args: argparse.Namespace) -> int:
    stores_map = discover_project_stores(args.workspace)
    if not stores_map:
        print(json.dumps({"error": "no_projects_found", "workspace": str(args.workspace)}))
        return 1
    stores = {key: SqliteStore.open(path / ".annotation-pipeline") for key, path in stores_map.items()}
    default_key = next(iter(stores))
    default_store = stores[default_key]
    runtime_once = None
    runtime_config = None
    try:
        registry = load_llm_registry(default_store.root / "llm_profiles.yaml")
        default_project_root = stores_map[default_key]
        config_root = default_project_root / ".annotation-pipeline"
        annotators_data = read_yaml(config_root / "annotators.yaml")
        external_data = read_yaml(config_root / "external_tasks.yaml")
        callbacks_data = read_yaml(config_root / "callbacks.yaml")
        workflow_data = read_yaml(config_root / "workflow.yaml")
        config = build_project_config_from_data(
            annotators_data=annotators_data,
            external_data=external_data,
            callbacks_data=callbacks_data,
            workflow_data=workflow_data,
        )
        context = RuntimeCliContext(
            project_root=default_project_root,
            config=config,
            store=default_store,
            registry=registry,
        )
        scheduler = _build_runtime_scheduler(context)
        runtime_once = scheduler.run_once
        runtime_config = config.runtime
    except Exception:
        pass
    serve_dashboard_api(
        default_store,
        host=args.host,
        port=args.port,
        stores=stores,
        default_store_key=default_key,
        runtime_once=runtime_once,
        runtime_config=runtime_config,
    )
    return 0


def _runtime_context(project_root: Path) -> RuntimeCliContext:
    project_root = Path(project_root)
    config_root = project_root / ".annotation-pipeline"
    annotators_data = read_yaml(config_root / "annotators.yaml")
    external_data = read_yaml(config_root / "external_tasks.yaml")
    callbacks_data = read_yaml(config_root / "callbacks.yaml")
    workflow_data = read_yaml(config_root / "workflow.yaml")
    registry = load_llm_registry(config_root / "llm_profiles.yaml")
    config = build_project_config_from_data(
        annotators_data=annotators_data,
        external_data=external_data,
        callbacks_data=callbacks_data,
        workflow_data=workflow_data,
    )
    validate_project_config(config, config_root, llm_registry=registry)
    return RuntimeCliContext(
        project_root=project_root,
        config=config,
        store=SqliteStore.open(config_root),
        registry=registry,
    )


def _build_runtime_scheduler(
    context: RuntimeCliContext,
    config: RuntimeConfig | None = None,
) -> LocalRuntimeScheduler:
    return LocalRuntimeScheduler(
        store=context.store,
        client_factory=lambda target: _build_llm_client(context.registry.resolve(target)),
        config=config or context.config.runtime,
    )


def _build_llm_client(profile):
    if profile.provider == "openai_responses":
        return OpenAIResponsesClient(profile)
    if profile.provider == "openai_compatible":
        return OpenAICompatibleClient(profile)
    if profile.provider == "local_cli":
        return LocalCLIClient(profile)
    raise ProfileValidationError(f"unsupported provider: {profile.provider}")


if __name__ == "__main__":
    console_main()
