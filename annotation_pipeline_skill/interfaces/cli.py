from __future__ import annotations

import argparse
import json
from pathlib import Path

from annotation_pipeline_skill.config.loader import ConfigValidationError, load_project_config
from annotation_pipeline_skill.core.models import Task
from annotation_pipeline_skill.core.states import TaskStatus
from annotation_pipeline_skill.core.transitions import transition_task
from annotation_pipeline_skill.interfaces.api import serve_dashboard_api
from annotation_pipeline_skill.llm.local_cli import LocalCLIClient
from annotation_pipeline_skill.llm.openai_responses import OpenAIResponsesClient
from annotation_pipeline_skill.llm.profiles import ProfileValidationError, load_llm_registry
from annotation_pipeline_skill.runtime.subagent_cycle import SubagentRuntime
from annotation_pipeline_skill.store.file_store import FileStore


CONFIG_FILES: dict[str, str] = {
    "workflow.yaml": """stages:
  annotation:
    target: annotation
  qc:
    target: qc
  repair:
    target: repair
human_review:
  required: false
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
  openai_default:
    provider: openai_responses
    model: gpt-5.4-mini
    api_key_env: OPENAI_API_KEY
    base_url: https://api.openai.com/v1
    reasoning_effort: medium
    timeout_seconds: 300
targets:
  annotation: local_codex
  qc: openai_default
  repair: local_codex
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
    create_parser.set_defaults(handler=handle_create_tasks)

    cycle_parser = subparsers.add_parser("run-cycle")
    cycle_parser.add_argument("--project-root", type=Path, default=Path.cwd())
    cycle_parser.add_argument("--limit", type=int, default=None)
    cycle_parser.add_argument("--runtime", choices=("subagent",), default="subagent")
    cycle_parser.add_argument("--stage-target", default="annotation")
    cycle_parser.set_defaults(handler=handle_run_cycle)

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
    for name in ("tasks", "events", "feedback", "attempts", "artifacts", "outbox", "runtime", "snapshots"):
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
    required_dirs = ("tasks", "events", "feedback", "attempts", "artifacts", "outbox")
    config_root = args.project_root / ".annotation-pipeline"
    return 0 if all((config_root / name).is_dir() for name in required_dirs) else 1


def handle_create_tasks(args: argparse.Namespace) -> int:
    store = FileStore(args.project_root / ".annotation-pipeline")
    rows = [
        json.loads(line)
        for line in args.source.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    for index, row in enumerate(rows, start=1):
        annotation_types = row.get("annotation_types", ["entity_span"])
        task = Task.new(
            task_id=f"{args.pipeline_id}-{index:06d}",
            pipeline_id=args.pipeline_id,
            source_ref={"kind": "jsonl", "path": str(args.source), "line": index, "payload": row},
            modality=row.get("modality", "text"),
            annotation_requirements={"annotation_types": annotation_types},
        )
        event = transition_task(
            task,
            TaskStatus.READY,
            actor="cli",
            reason="created from jsonl source",
            stage="prepare",
        )
        store.save_task(task)
        store.append_event(event)
    return 0


def handle_run_cycle(args: argparse.Namespace) -> int:
    load_project_config(args.project_root)
    store = FileStore(args.project_root / ".annotation-pipeline")
    registry = load_llm_registry(args.project_root / ".annotation-pipeline" / "llm_profiles.yaml")
    runtime = SubagentRuntime(store=store, client_factory=lambda target: _build_llm_client(registry.resolve(target)))
    runtime.run_once(stage_target=args.stage_target, limit=args.limit)
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
    payload = {
        target: {
            "profile": registry.resolve(target).name,
            "provider": registry.resolve(target).provider,
            "model": registry.resolve(target).model,
        }
        for target in sorted(registry.targets)
    }
    print(json.dumps(payload, sort_keys=True, indent=2))
    return 0


def handle_serve(args: argparse.Namespace) -> int:
    serve_dashboard_api(FileStore(args.project_root / ".annotation-pipeline"), host=args.host, port=args.port)
    return 0


def _build_llm_client(profile):
    if profile.provider == "openai_responses":
        return OpenAIResponsesClient(profile)
    if profile.provider == "local_cli":
        return LocalCLIClient(profile)
    raise ProfileValidationError(f"unsupported provider: {profile.provider}")


if __name__ == "__main__":
    console_main()
