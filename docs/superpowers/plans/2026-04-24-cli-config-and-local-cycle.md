# CLI Config And Local Cycle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the skill usable from the command line with project initialization, YAML validation, JSONL task creation, annotator selection, a deterministic local cycle, and dashboard serving.

**Architecture:** Add a small config layer for YAML-backed provider, route, annotator, and external-task files. Keep capability matching in a structured `AnnotatorSelector` service. Add an argparse CLI that calls application services and the existing dashboard API. Add a fake local runtime cycle that advances ready tasks through the MVP state path with audit events, attempts, and artifacts.

**Tech Stack:** Python 3.11+, PyYAML, argparse, pytest, existing Vite dashboard.

---

## Scope

This plan completes the remaining local MVP path. It does not implement real provider calls, real external HTTP clients, real model QC quality, authentication, or UI config editing.

## Task 1: YAML Config Loading And Doctor Validation

**Files:**
- Create: `annotation_pipeline_skill/config/__init__.py`
- Create: `annotation_pipeline_skill/config/models.py`
- Create: `annotation_pipeline_skill/config/loader.py`
- Test: `tests/test_config_loader.py`

Steps:
- Write tests that create `.annotation-pipeline/providers.yaml`, `stage_routes.yaml`, `annotators.yaml`, and `external_tasks.yaml`.
- Verify config loads structured provider, stage-route, annotator, and external-task data.
- Verify doctor validation rejects a stage route that references a missing provider.
- Implement the loader and validation errors.
- Run `UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy uv run pytest tests/test_config_loader.py -v`.

## Task 2: Structured Annotator Selection

**Files:**
- Create: `annotation_pipeline_skill/services/annotator_selector.py`
- Test: `tests/test_annotator_selector.py`

Steps:
- Write tests that select an image bounding-box annotator by structured modality and annotation type.
- Write tests that disabled annotators are ignored.
- Implement exact structured matching without keyword or regex routing.
- Run `UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy uv run pytest tests/test_annotator_selector.py -v`.

## Task 3: CLI Init, Doctor, And JSONL Task Creation

**Files:**
- Create: `annotation_pipeline_skill/interfaces/cli.py`
- Modify: `pyproject.toml`
- Test: `tests/test_cli.py`

Steps:
- Write tests for `annotation-pipeline init --project-root <tmp>`.
- Verify init creates `.annotation-pipeline` subdirectories and YAML config files.
- Write tests for `doctor --project-root <tmp>` returning success after init.
- Write tests for `create-tasks --source input.jsonl --project-root <tmp> --pipeline-id demo`.
- Implement argparse CLI commands and console script entrypoint.
- Run `UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy uv run pytest tests/test_cli.py -v`.

## Task 4: Deterministic Local Cycle

**Files:**
- Create: `annotation_pipeline_skill/runtime/__init__.py`
- Create: `annotation_pipeline_skill/runtime/local_cycle.py`
- Modify: `annotation_pipeline_skill/interfaces/cli.py`
- Test: `tests/test_local_cycle.py`

Steps:
- Write tests that a ready task advances to accepted by default.
- Write tests that config with `human_review.required: true` routes QC-passed tasks to `human_review`.
- Verify transitions append audit events and an annotation attempt.
- Implement `run_local_cycle(store, config)` and CLI `run-cycle`.
- Run `UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy uv run pytest tests/test_local_cycle.py -v`.

## Task 5: Final Verification, Docs, Commit, Push

**Files:**
- Modify: `README.md`

Steps:
- Document `annotation-pipeline init`, `doctor`, `create-tasks`, `run-cycle`, and `serve`.
- Run backend tests: `UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy uv run pytest -v`.
- Run frontend tests: `cd web && npm test -- --run`.
- Run frontend build: `cd web && npm run build`.
- Commit with `git commit -m "feat: add cli config and local cycle"`.
- Push to `origin/main`.

## Self-Review

- Spec coverage: this plan covers the remaining MVP local path: YAML config, annotator capability matching, CLI commands, task creation, local runtime cycle, and dashboard serving.
- Placeholder scan: no unresolved placeholder markers remain.
- Type consistency: config model field names match the design spec and CLI command names match README examples.
