from __future__ import annotations

import argparse
import json
from collections.abc import Iterable
from pathlib import Path

from annotation_pipeline_skill.config.loader import ConfigValidationError, load_project_config
from annotation_pipeline_skill.core.models import Task
from annotation_pipeline_skill.core.states import TaskStatus
from annotation_pipeline_skill.core.transitions import transition_task
from annotation_pipeline_skill.interfaces.api import serve_dashboard_api
from annotation_pipeline_skill.runtime.local_cycle import run_local_cycle
from annotation_pipeline_skill.services.merge_service import MergeService
from annotation_pipeline_skill.store.file_store import FileStore


CONFIG_FILES: dict[str, str] = {
    "providers.yaml": """providers:
  local_fake:
    kind: fake
    models: [fake-annotator]
    default_model: fake-annotator
    secret_ref: null
""",
    "stage_routes.yaml": """stage_routes:
  annotation:
    primary_provider: local_fake
    primary_model: fake-annotator
    primary_effort: medium
  qc:
    primary_provider: local_fake
    primary_model: fake-annotator
    primary_effort: medium
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
    provider_route_id: annotation
    enabled: true
  image_bbox_annotator:
    display_name: Image Bounding Box Annotator
    modalities: [image]
    annotation_types: [bounding_box, segmentation]
    input_artifact_kinds: [raw_slice]
    output_artifact_kinds: [annotation_result, image_bbox_preview]
    provider_route_id: annotation
    preview_renderer_id: image_bbox_preview
    enabled: true
""",
    "external_tasks.yaml": """external_tasks:
  default:
    enabled: false
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
    cycle_parser.add_argument("--auto-merge", action="store_true")
    cycle_parser.set_defaults(handler=handle_run_cycle)

    merge_parser = subparsers.add_parser("merge-accepted")
    merge_parser.add_argument("--project-root", type=Path, default=Path.cwd())
    merge_parser.add_argument("--limit", type=int, default=None)
    merge_parser.set_defaults(handler=handle_merge_accepted)

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
            TaskStatus.READY,
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
    config = load_project_config(args.project_root)
    store = FileStore(args.project_root / ".annotation-pipeline")
    run_local_cycle(store, config, limit=args.limit, auto_merge=args.auto_merge)
    return 0


def handle_merge_accepted(args: argparse.Namespace) -> int:
    store = FileStore(args.project_root / ".annotation-pipeline")
    MergeService(store).merge_accepted(limit=args.limit, actor="cli")
    return 0


def handle_serve(args: argparse.Namespace) -> int:
    serve_dashboard_api(FileStore(args.project_root / ".annotation-pipeline"), host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    console_main()
