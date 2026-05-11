import pytest

from annotation_pipeline_skill.core.models import Task
from annotation_pipeline_skill.core.schema_validation import (
    SchemaValidationError,
    load_output_schema,
    validate_payload_against_task_schema,
)


def _task_with_schema(schema):
    payload = {"text": "x"}
    if schema is not None:
        payload["annotation_guidance"] = {"output_schema": schema}
    return Task.new(
        task_id="t-1",
        pipeline_id="p",
        source_ref={"kind": "jsonl", "payload": payload},
    )


def test_load_output_schema_returns_schema_when_present():
    schema = {"type": "object", "required": ["entities"]}
    task = _task_with_schema(schema)
    assert load_output_schema(task) == schema


def test_load_output_schema_returns_none_when_absent():
    task = _task_with_schema(None)
    assert load_output_schema(task) is None


def test_validate_passes_when_payload_matches_schema():
    schema = {
        "type": "object",
        "required": ["entities"],
        "properties": {"entities": {"type": "array"}},
    }
    task = _task_with_schema(schema)
    validate_payload_against_task_schema(task, {"entities": []})


def test_validate_raises_schema_validation_error_on_invalid_payload():
    schema = {
        "type": "object",
        "required": ["entities"],
        "properties": {"entities": {"type": "array"}},
    }
    task = _task_with_schema(schema)
    with pytest.raises(SchemaValidationError) as exc:
        validate_payload_against_task_schema(task, {"wrong_field": []})
    assert exc.value.errors
    joined = " ".join(str(e).lower() for e in exc.value.errors)
    assert "entities" in joined or "required" in joined


def test_validate_raises_missing_schema_when_task_has_no_output_schema():
    task = _task_with_schema(None)
    with pytest.raises(SchemaValidationError) as exc:
        validate_payload_against_task_schema(task, {"anything": True})
    assert exc.value.errors == [{"kind": "missing_schema", "message": "task has no output_schema"}]
