# Agent Handoff

This repository is intended to become an open-source `annotation-pipeline-skill`.

Before making implementation changes, read these documents in order:

1. `PRODUCT_DESIGN.md`
2. `TECHNICAL_ARCHITECTURE.md`
3. `VERIFY_MANAGER_CYCLES_TEST_PLAN.md`

## Project Intent

The goal is to build a reusable annotation pipeline skill inspired by the product and operational lessons from `memory-ner`'s `annotation manager`.

Do not port the old implementation wholesale. Use it as prior art for product behavior and test logic only.

## Core Product Requirements

The skill should support:

- Task slicing from raw sources.
- A durable task state model with attempts, events, artifacts, and audit history.
- Multi-stage flow: prepare, annotate, validate, QC, repair, accept or reject, merge.
- Deterministic validation before model-based QC wherever possible.
- Runtime monitoring that detects stuck queues, stale workers, stale heartbeats, retry drain failures, and capacity violations.
- Pluggable adapters for dataset input, prompt building, validation, QC policy, repair strategy, merge sink, provider client, and runtime backend.

## First Implementation Phase

Start with a minimal local implementation:

- Python package skeleton.
- File-system task store.
- Local subprocess runtime or in-process fake runtime for tests.
- Core domain models and state transitions.
- Test fixtures for task store, runtime, and monitor samples.
- Unit tests from the P0 cases in `VERIFY_MANAGER_CYCLES_TEST_PLAN.md`.

Avoid Redis, Docker, systemd, Streamlit, and provider-specific CLIs in the first phase.

## Suggested Initial File Layout

```text
annotation_pipeline_skill/
  core/
  services/
  store/
  runtime/
  plugins/
  interfaces/
tests/
  fixtures/
```

## Important Design Boundaries

- Framework core must stay task-type agnostic.
- NER, Schema V3, `memory-ner` directory paths, and by-source truth merging belong in adapters or examples, not core.
- Provider clients should be replaceable. Do not hard-code Codex or Claude into the core domain model.
- Runtime state should be modeled separately from business task state.
- Every task transition should produce an audit event.

## Test Priorities

Implement tests before or alongside code. Start with:

- Task JSON save/load and backup restore.
- Valid and invalid state transitions.
- Event append/read behavior.
- Runtime health and heartbeat freshness.
- Stale active worker detection.
- Retry drain progress.
- Annotated task downstream progress.
- Dispatch capacity enforcement.
- Routing fallback behavior with and without bound sessions.

## Source Reference

The prior implementation lives at:

```text
/home/derek/Projects/memory-ner/annotation manager/
```

Use it only for behavior research. Keep new code independent and minimal.
