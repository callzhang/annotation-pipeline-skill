import json

import pytest

from annotation_pipeline_skill.core.models import Task
from annotation_pipeline_skill.core.schema_validation import (
    SchemaValidationError,
    load_output_schema,
    load_project_output_schema,
    resolve_output_schema,
    validate_payload_against_task_schema,
)
from annotation_pipeline_skill.store.sqlite_store import SqliteStore


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
    validate_payload_against_task_schema(task, {"entities": []}, store=None)


def test_validate_raises_schema_validation_error_on_invalid_payload():
    schema = {
        "type": "object",
        "required": ["entities"],
        "properties": {"entities": {"type": "array"}},
    }
    task = _task_with_schema(schema)
    with pytest.raises(SchemaValidationError) as exc:
        validate_payload_against_task_schema(task, {"wrong_field": []}, store=None)
    assert exc.value.errors
    joined = " ".join(str(e).lower() for e in exc.value.errors)
    assert "entities" in joined or "required" in joined


def test_validate_raises_missing_schema_when_task_has_no_output_schema():
    task = _task_with_schema(None)
    with pytest.raises(SchemaValidationError) as exc:
        validate_payload_against_task_schema(task, {"anything": True}, store=None)
    assert exc.value.errors == [{"kind": "missing_schema", "message": "task has no output_schema"}]


def _write_project_schema(root, schema):
    root.mkdir(parents=True, exist_ok=True)
    (root / "output_schema.json").write_text(json.dumps(schema), encoding="utf-8")


def test_load_project_output_schema_reads_file(tmp_path):
    schema = {"type": "object", "required": ["rows"]}
    _write_project_schema(tmp_path, schema)
    assert load_project_output_schema(tmp_path) == schema


def test_load_project_output_schema_returns_none_when_missing(tmp_path):
    assert load_project_output_schema(tmp_path) is None


def test_resolve_output_schema_prefers_task_inline_over_project(tmp_path):
    inline = {"type": "object", "required": ["a"]}
    project = {"type": "object", "required": ["b"]}
    store = SqliteStore.open(tmp_path)
    _write_project_schema(store.root, project)
    task = _task_with_schema(inline)
    assert resolve_output_schema(task, store) == inline


def test_resolve_output_schema_falls_back_to_project_when_task_missing(tmp_path):
    project = {"type": "object", "required": ["b"]}
    store = SqliteStore.open(tmp_path)
    _write_project_schema(store.root, project)
    task = _task_with_schema(None)
    assert resolve_output_schema(task, store) == project


def test_resolve_output_schema_none_when_neither_available(tmp_path):
    store = SqliteStore.open(tmp_path)
    task = _task_with_schema(None)
    assert resolve_output_schema(task, store) is None


def test_resolve_output_schema_none_when_store_none_and_no_inline():
    task = _task_with_schema(None)
    assert resolve_output_schema(task, None) is None


def test_validate_uses_project_schema_when_store_provided(tmp_path):
    project = {
        "type": "object",
        "required": ["entities"],
        "properties": {"entities": {"type": "array"}},
    }
    store = SqliteStore.open(tmp_path)
    _write_project_schema(store.root, project)
    task = _task_with_schema(None)
    # Passes against project schema
    validate_payload_against_task_schema(task, {"entities": []}, store=store)
    # Fails against project schema
    with pytest.raises(SchemaValidationError):
        validate_payload_against_task_schema(task, {"wrong": True}, store=store)
