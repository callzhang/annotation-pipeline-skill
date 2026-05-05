# Agent Operator Guide

This guide is for the agent running an annotation project on behalf of an algorithm engineer.

## Setup

Initialize the project:

```bash
annotation-pipeline init --project-root ./demo-project
```

This creates `.annotation-pipeline/llm_profiles.yaml` with default stage targets:

- `annotation`
- `qc`
- `coordinator`

Validate provider configuration:

```bash
annotation-pipeline provider doctor --project-root ./demo-project
annotation-pipeline provider targets --project-root ./demo-project
```

## Provider Profiles

OpenAI Responses API profile:

```yaml
profiles:
  openai_default:
    provider: openai_responses
    model: gpt-5.4-mini
    api_key_env: OPENAI_API_KEY
    base_url: https://api.openai.com/v1
    reasoning_effort: medium
```

Local Codex CLI profile:

```yaml
profiles:
  local_codex:
    provider: local_cli
    cli_kind: codex
    cli_binary: codex
    model: gpt-5.4-mini
    reasoning_effort: none
```

The local CLI adapter uses an isolated Codex home, strips desktop session context, preserves auth/config needed to run, and records continuity handles from JSON events.

## Run A Cycle

Use configured subagents:

```bash
annotation-pipeline run-cycle --runtime subagent --project-root ./demo-project
```

Subagent attempts record provider, model, artifact metadata, diagnostics, and continuity handles. Treat those records as the audit trail for debugging quality and provider behavior.

## Runtime Operations

Use `annotation-pipeline runtime status --project-root <project>` before starting work. A healthy project has a fresh heartbeat, no stale active runs, and capacity that is not exceeded.

Use `annotation-pipeline runtime once --project-root <project>` for one monitored cycle. Use `annotation-pipeline runtime run --project-root <project>` when the agent should keep the local project moving.

If runtime status shows stale tasks or due retries that are not draining, inspect task detail and event logs before changing annotation rules or provider config.

## Operating Loop

1. Pull or create tasks.
2. Select annotators by task modality and annotation type.
3. Run annotation and QC stages.
4. Let annotator and QC exchange feedback, including partial agreement.
5. Record consensus when both sides agree on the final resolution.
6. Notify the user when unresolved items need Human Review.
7. Re-run annotation with feedback when consensus requires label updates, then submit accepted training data.

For multimodal projects, keep the core task model generic and add adapters/renderers for images, video, point clouds, or model-specific previews such as bounding boxes from a VC detection model.
