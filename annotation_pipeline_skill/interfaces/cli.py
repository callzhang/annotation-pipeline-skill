from __future__ import annotations

import argparse
import json
import time
from collections.abc import Iterable
from dataclasses import dataclass, replace
from pathlib import Path

from annotation_pipeline_skill.config.loader import (
    ConfigValidationError,
    _read_yaml,
    build_project_config_from_data,
    load_project_config,
    load_runtime_config,
    validate_project_config,
)
from annotation_pipeline_skill.config.models import ProjectConfig
from annotation_pipeline_skill.core.runtime import RuntimeConfig
from annotation_pipeline_skill.core.models import Task
from annotation_pipeline_skill.core.states import TaskStatus
from annotation_pipeline_skill.core.transitions import transition_task
from annotation_pipeline_skill.interfaces.api import serve_dashboard_api
from annotation_pipeline_skill.llm.local_cli import LocalCLIClient
from annotation_pipeline_skill.llm.openai_compatible import OpenAICompatibleClient
from annotation_pipeline_skill.llm.openai_responses import OpenAIResponsesClient
from annotation_pipeline_skill.llm.profiles import ProfileValidationError, load_llm_registry
from annotation_pipeline_skill.runtime.local_scheduler import LocalRuntimeScheduler
from annotation_pipeline_skill.runtime.snapshot import build_runtime_snapshot
from annotation_pipeline_skill.store.file_store import FileStore


@dataclass(frozen=True)
class RuntimeCliContext:
    project_root: Path
    config: ProjectConfig
    store: FileStore
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
    model: claude-sonnet-4-5
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
  deepseek_default:
    provider: openai_compatible
    provider_flavor: deepseek
    model: deepseek-chat
    api_key_env: DEEPSEEK_API_KEY
    base_url: https://api.deepseek.com
    timeout_seconds: 300
  glm_default:
    provider: openai_compatible
    provider_flavor: glm
    model: glm-4.5
    api_key_env: ZHIPUAI_API_KEY
    base_url: https://open.bigmodel.cn/api/paas/v4
    timeout_seconds: 300
  minimax_default:
    provider: openai_compatible
    provider_flavor: minimax
    model: MiniMax-M1
    api_key_env: MINIMAX_API_KEY
    base_url: https://api.minimax.io/v1
    timeout_seconds: 300
targets:
  annotation: local_codex
  qc: openai_default
  coordinator: local_codex
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
    create_parser.set_defaults(handler=handle_create_tasks)

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

    serve_parser = subparsers.add_parser("serve")
    serve_parser.add_argument("--project-root", type=Path, default=Path.cwd())
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", type=int, default=8765)
    serve_parser.set_defaults(handler=handle_serve)

    return parser


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
        "runtime",
        "snapshots",
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
    required_dirs = ("tasks", "events", "feedback", "feedback_discussions", "attempts", "artifacts", "outbox")
    config_root = args.project_root / ".annotation-pipeline"
    return 0 if all((config_root / name).is_dir() for name in required_dirs) else 1


def handle_create_tasks(args: argparse.Namespace) -> int:
    if args.batch_size <= 0:
        raise ValueError("--batch-size must be > 0")
    store = FileStore(args.project_root / ".annotation-pipeline")
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
            metadata=batch_metadata(batch),
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


def batch_metadata(batch: list[dict]) -> dict:
    sources = sorted({str(row.get("source") or row.get("source_dataset") or "") for row in batch if row.get("source") or row.get("source_dataset")})
    metadata = {
        "row_count": len(batch),
        "qc_policy": {
            "mode": "all_rows",
            "required_correct_rows": len(batch),
            "feedback_loop": "annotator_may_accept_or_dispute_qc_items",
        },
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
    _build_runtime_scheduler(context, runtime_config).run_once(stage_target=args.stage_target)
    return 0


def handle_runtime_once(args: argparse.Namespace) -> int:
    context = _runtime_context(args.project_root)
    snapshot = _build_runtime_scheduler(context).run_once(stage_target=args.stage_target)
    print(json.dumps(snapshot.to_dict(), sort_keys=True, indent=2))
    return 0


def handle_runtime_status(args: argparse.Namespace) -> int:
    runtime_config = load_runtime_config(args.project_root)
    store = FileStore(args.project_root / ".annotation-pipeline")
    snapshot = store.load_runtime_snapshot() or build_runtime_snapshot(store, runtime_config)
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


def handle_serve(args: argparse.Namespace) -> int:
    context = _runtime_context(args.project_root)
    scheduler = _build_runtime_scheduler(context)
    serve_dashboard_api(
        context.store,
        host=args.host,
        port=args.port,
        runtime_once=scheduler.run_once,
        runtime_config=context.config.runtime,
    )
    return 0


def _runtime_context(project_root: Path) -> RuntimeCliContext:
    project_root = Path(project_root)
    config_root = project_root / ".annotation-pipeline"
    annotators_data = _read_yaml(config_root / "annotators.yaml")
    external_data = _read_yaml(config_root / "external_tasks.yaml")
    callbacks_data = _read_yaml(config_root / "callbacks.yaml")
    workflow_data = _read_yaml(config_root / "workflow.yaml")
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
        store=FileStore(config_root),
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
