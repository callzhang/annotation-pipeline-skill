from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path
from typing import Any

from annotation_pipeline_skill.core.models import ArtifactRef, ExportManifest, OutboxRecord, Task
from annotation_pipeline_skill.core.states import OutboxKind, TaskStatus
from annotation_pipeline_skill.store.sqlite_store import SqliteStore


REQUIRED_TRAINING_ROW_FIELDS = [
    "task_id",
    "pipeline_id",
    "source_ref",
    "modality",
    "annotation_requirements",
    "annotation",
    "annotation_artifact_id",
    "annotation_artifact_path",
]


class TrainingDataExportService:
    def __init__(self, store: SqliteStore):
        self.store = store

    def export_jsonl(
        self,
        *,
        project_id: str,
        output_dir: Path,
        export_id: str | None = None,
        enqueue_external_submit: bool = False,
    ) -> ExportManifest:
        export_id = export_id or "export-" + sha256(project_id.encode("utf-8")).hexdigest()[:12]
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / "training_data.jsonl"

        accepted_tasks = [
            task
            for task in self.store.list_tasks_by_pipeline(project_id)
            if task.status is TaskStatus.ACCEPTED
        ]
        rows: list[dict[str, Any]] = []
        included: list[str] = []
        excluded: list[dict[str, Any]] = []
        row_errors: list[dict[str, Any]] = []
        artifact_ids: list[str] = []
        source_files = sorted(
            {
                str(task.source_ref.get("path"))
                for task in accepted_tasks
                if isinstance(task.source_ref.get("path"), str)
            }
        )

        for task in accepted_tasks:
            annotation_artifact = self._latest_annotation_artifact(task)
            if annotation_artifact is None:
                excluded.append({"task_id": task.task_id, "reason": "missing_annotation_result"})
                continue
            annotation_payload = self._read_artifact_payload(annotation_artifact)
            if annotation_payload is None:
                excluded.append({"task_id": task.task_id, "reason": "missing_annotation_payload"})
                continue
            row = self._training_row(task, annotation_artifact, annotation_payload)
            validation_errors = self._validate_training_row(row)
            if validation_errors:
                row_errors.append({"task_id": task.task_id, "errors": validation_errors})
                excluded.append(
                    {
                        "task_id": task.task_id,
                        "reason": "invalid_training_row",
                        "errors": validation_errors,
                    }
                )
                continue
            rows.append(row)
            included.append(task.task_id)
            artifact_ids.append(annotation_artifact.artifact_id)
            if enqueue_external_submit and task.external_ref is not None:
                self._enqueue_submit(task, export_id=export_id, row=row)

        output_path.write_text(
            "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
            encoding="utf-8",
        )

        manifest = ExportManifest.new(
            project_id=project_id,
            output_paths=[self._relative_output_path(output_path)],
            task_ids_included=included,
            task_ids_excluded=excluded,
            artifact_ids=artifact_ids,
            source_files=source_files,
            annotation_rules_hash=self._annotation_rules_hash(),
            schema_version="jsonl-training-v2",
            validator_version="local-export-v2",
            validation_summary={
                "accepted_tasks": len(accepted_tasks),
                "included": len(included),
                "excluded": len(excluded),
                "required_fields": REQUIRED_TRAINING_ROW_FIELDS,
                "row_errors": row_errors,
                "errors": excluded,
            },
            known_limitations=["text-first JSONL sink; multimodal preview artifacts are referenced, not rendered"],
            export_id=export_id,
        )
        self.store.save_export_manifest(manifest)
        return manifest

    def _latest_annotation_artifact(self, task: Task) -> ArtifactRef | None:
        artifacts = [artifact for artifact in self.store.list_artifacts(task.task_id) if artifact.kind == "annotation_result"]
        if not artifacts:
            return None
        return artifacts[-1]

    def _read_artifact_payload(self, artifact: ArtifactRef) -> Any:
        path = self.store.root / artifact.path
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def _training_row(self, task: Task, artifact: ArtifactRef, artifact_payload: Any) -> dict[str, Any]:
        annotation = artifact_payload.get("text", artifact_payload) if isinstance(artifact_payload, dict) else artifact_payload
        return {
            "task_id": task.task_id,
            "pipeline_id": task.pipeline_id,
            "source_ref": task.source_ref,
            "modality": task.modality,
            "annotation_requirements": task.annotation_requirements,
            "annotation": annotation,
            "annotation_artifact_id": artifact.artifact_id,
            "annotation_artifact_path": artifact.path,
        }

    def _validate_training_row(self, row: dict[str, Any]) -> list[str]:
        errors = [
            f"missing_{field}"
            for field in REQUIRED_TRAINING_ROW_FIELDS
            if field not in row
        ]
        if not isinstance(row.get("task_id"), str) or not row.get("task_id"):
            errors.append("task_id_required")
        if not isinstance(row.get("pipeline_id"), str) or not row.get("pipeline_id"):
            errors.append("pipeline_id_required")
        if not isinstance(row.get("source_ref"), dict) or not row.get("source_ref"):
            errors.append("source_ref_required")
        if not isinstance(row.get("annotation_artifact_id"), str) or not row.get("annotation_artifact_id"):
            errors.append("annotation_artifact_id_required")
        if not isinstance(row.get("annotation_artifact_path"), str) or not row.get("annotation_artifact_path"):
            errors.append("annotation_artifact_path_required")

        annotation = row.get("annotation")
        if annotation in (None, "", [], {}):
            errors.append("annotation_required")
        elif isinstance(annotation, str):
            self._validate_annotation_json_string(annotation, errors)
        elif not isinstance(annotation, (dict, list)):
            errors.append("annotation_must_be_json_value")
        return errors

    def _validate_annotation_json_string(self, value: str, errors: list[str]) -> None:
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            errors.append("annotation_string_must_be_json")
            return
        if parsed in (None, "", [], {}):
            errors.append("annotation_json_must_not_be_empty")

    def _enqueue_submit(self, task: Task, *, export_id: str, row: dict[str, Any]) -> None:
        record = OutboxRecord.new(
            task_id=task.task_id,
            kind=OutboxKind.SUBMIT,
            payload={
                "task_id": task.task_id,
                "external_ref": task.external_ref.to_dict() if task.external_ref else None,
                "export_id": export_id,
                "result": row,
            },
        )
        self.store.save_outbox(record)

    def _annotation_rules_hash(self) -> str | None:
        rules_path = self.store.root / "annotation_rules.yaml"
        if not rules_path.exists():
            return None
        return sha256(rules_path.read_bytes()).hexdigest()

    def _relative_output_path(self, output_path: Path) -> str:
        try:
            return str(output_path.relative_to(self.store.root))
        except ValueError:
            return str(output_path)
