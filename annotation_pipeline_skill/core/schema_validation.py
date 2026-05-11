from __future__ import annotations

from typing import Any

from jsonschema import Draft202012Validator

from annotation_pipeline_skill.core.models import Task


class SchemaValidationError(ValueError):
    def __init__(self, message: str, errors: list[dict]):
        super().__init__(message)
        self.errors = errors


def load_output_schema(task: Task) -> dict | None:
    payload = task.source_ref.get("payload") if isinstance(task.source_ref, dict) else None
    if not isinstance(payload, dict):
        return None
    guidance = payload.get("annotation_guidance")
    if not isinstance(guidance, dict):
        return None
    schema = guidance.get("output_schema")
    return schema if isinstance(schema, dict) else None


def validate_payload_against_task_schema(task: Task, payload: Any) -> None:
    schema = load_output_schema(task)
    if schema is None:
        raise SchemaValidationError(
            "task has no output_schema",
            [{"kind": "missing_schema", "message": "task has no output_schema"}],
        )
    validator = Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(payload), key=lambda e: list(e.absolute_path))
    if errors:
        raise SchemaValidationError(
            f"schema validation failed with {len(errors)} error(s)",
            [
                {
                    "kind": "schema_error",
                    "path": "/".join(str(p) for p in err.absolute_path),
                    "message": err.message,
                }
                for err in errors
            ],
        )
