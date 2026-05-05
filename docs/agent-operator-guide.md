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

The dashboard UI exposes the same configuration under the Configuration tab. Open `Subagent Providers` to edit `.annotation-pipeline/llm_profiles.yaml`, then save from the UI. This is the operator path for changing annotation, QC, coordinator, Human Review, or future model-assist provider targets without editing code.

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

Local Claude CLI profile:

```yaml
profiles:
  local_claude:
    provider: local_cli
    cli_kind: claude
    cli_binary: claude
    model: claude-sonnet-4-5
    permission_mode: dontAsk
```

OpenAI-compatible API profiles for DeepSeek, GLM, and MiniMax:

```yaml
profiles:
  deepseek_default:
    provider: openai_compatible
    provider_flavor: deepseek
    model: deepseek-chat
    api_key_env: DEEPSEEK_API_KEY
    base_url: https://api.deepseek.com

  glm_default:
    provider: openai_compatible
    provider_flavor: glm
    model: glm-4.5
    api_key_env: ZHIPUAI_API_KEY
    base_url: https://open.bigmodel.cn/api/paas/v4

  minimax_default:
    provider: openai_compatible
    provider_flavor: minimax
    model: MiniMax-M1
    api_key_env: MINIMAX_API_KEY
    base_url: https://api.minimax.io/v1
```

Switch QC to DeepSeek by editing targets:

```yaml
targets:
  annotation: local_codex
  qc: deepseek_default
  coordinator: local_codex
```

Provider selection is explicit. Do not rely on model-name prefixes to infer providers.

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
