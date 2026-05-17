"""Integration tests for prior verifier wiring across the runtime."""
from __future__ import annotations

import asyncio
import json

import pytest

from annotation_pipeline_skill.core.models import ArtifactRef, Task
from annotation_pipeline_skill.core.states import FeedbackSource, TaskStatus
from annotation_pipeline_skill.llm.client import LLMGenerateResult
from annotation_pipeline_skill.runtime.subagent_cycle import SubagentRuntime
from annotation_pipeline_skill.services.entity_statistics_service import (
    EntityStatisticsService,
)
from annotation_pipeline_skill.store.sqlite_store import SqliteStore


def _seed_prior(store, *, project_id, span, type_to_count):
    svc = EntityStatisticsService(store)
    for typ, n in type_to_count.items():
        for _ in range(n):
            svc.increment(project_id=project_id, span=span, entity_type=typ)


def _make_task(task_id, *, input_text, project_id="p"):
    return Task.new(
        task_id=task_id,
        pipeline_id=project_id,
        source_ref={
            "kind": "jsonl",
            "payload": {
                "text": input_text,
                "rows": [{"row_index": 0, "input": input_text}],
                "annotation_guidance": {"output_schema": {"type": "object"}},
            },
        },
    )


class _RecorderClient:
    def __init__(self, qc_passed: bool, annotation: dict):
        self.qc_passed = qc_passed
        self.annotation = annotation

    async def generate(self, request):
        if "qc subagent" in request.instructions.lower():
            final = json.dumps({
                "passed": self.qc_passed,
                "message": "ok" if self.qc_passed else "issues",
                "failures": [] if self.qc_passed else [{"category": "x", "message": "bad", "confidence": "certain"}],
            })
        else:
            final = json.dumps(self.annotation)
        return LLMGenerateResult(
            final_text=final, raw_response={}, usage={}, diagnostics={},
            runtime="stub", provider="stub", model="stub", continuity_handle=None,
        )


def test_qc_pass_with_prior_agree_accepts_and_increments_stats(tmp_path):
    store = SqliteStore.open(tmp_path)
    project = "p"
    _seed_prior(store, project_id=project, span="Apple",
                type_to_count={"organization": 9, "project": 1})

    annotation = {
        "rows": [{
            "row_index": 0,
            "output": {"entities": {"organization": ["Apple"]}},
        }]
    }
    task = _make_task("t-agree", input_text="Apple is a company")
    task.status = TaskStatus.PENDING
    store.save_task(task)

    runtime = SubagentRuntime(
        store=store,
        client_factory=lambda _t: _RecorderClient(qc_passed=True, annotation=annotation),
    )
    asyncio.run(runtime.run_task_async(store.load_task("t-agree")))

    after = store.load_task("t-agree")
    assert after.status is TaskStatus.ACCEPTED
    svc = EntityStatisticsService(store)
    # Original 9+1 from seed plus 1 increment from this acceptance.
    assert svc.distribution(project_id=project, span="Apple") == {
        "organization": 10, "project": 1,
    }


def test_qc_pass_with_prior_divergent_routes_to_arbitrating(tmp_path):
    store = SqliteStore.open(tmp_path)
    project = "p"
    _seed_prior(store, project_id=project, span="Apple",
                type_to_count={"organization": 10})

    annotation = {
        "rows": [{
            "row_index": 0,
            "output": {"entities": {"technology": ["Apple"]}},
        }]
    }
    task = _make_task("t-divergent", input_text="Apple is mentioned")
    task.status = TaskStatus.PENDING
    store.save_task(task)

    runtime = SubagentRuntime(
        store=store,
        client_factory=lambda _t: _RecorderClient(qc_passed=True, annotation=annotation),
    )
    asyncio.run(runtime.run_task_async(store.load_task("t-divergent")))

    after = store.load_task("t-divergent")
    assert after.status is TaskStatus.ARBITRATING
    fbs = store.list_feedback("t-divergent")
    assert any(
        f.source_stage is FeedbackSource.VALIDATION and f.category == "prior_disagreement"
        for f in fbs
    )


def test_qc_pass_with_cold_start_accepts(tmp_path):
    store = SqliteStore.open(tmp_path)
    project = "p"
    # 5 samples — below MIN_PRIOR_SAMPLES (10) → cold_start
    _seed_prior(store, project_id=project, span="Apple",
                type_to_count={"organization": 5})

    annotation = {
        "rows": [{
            "row_index": 0,
            "output": {"entities": {"technology": ["Apple"]}},
        }]
    }
    task = _make_task("t-cold", input_text="Apple is referenced")
    task.status = TaskStatus.PENDING
    store.save_task(task)

    runtime = SubagentRuntime(
        store=store,
        client_factory=lambda _t: _RecorderClient(qc_passed=True, annotation=annotation),
    )
    asyncio.run(runtime.run_task_async(store.load_task("t-cold")))

    after = store.load_task("t-cold")
    assert after.status is TaskStatus.ACCEPTED
    svc = EntityStatisticsService(store)
    assert svc.distribution(project_id=project, span="Apple") == {
        "organization": 5, "technology": 1,
    }
