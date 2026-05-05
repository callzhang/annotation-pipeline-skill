# Unified LLM Providers Design

## Goal

Phase 4 adds a profile-driven LLM provider layer for annotation pipeline subagents. The system must support the five provider categories needed by the algorithm-engineer workflow:

- Codex CLI for local subagent annotation and coordination.
- Claude CLI for local subagent annotation and coordination.
- DeepSeek API through an OpenAI-compatible chat completion endpoint.
- GLM API through an OpenAI-compatible chat completion endpoint.
- MiniMax API through an OpenAI-compatible chat completion endpoint.

The implementation keeps the core annotation workflow provider-agnostic. Runtime scheduling, task state, feedback consensus, artifacts, and audit events must not depend on a specific vendor or CLI.

## Non-Goals

- Do not port `memory-ner/annotation manager/llm_provider.py` wholesale.
- Do not reintroduce model-name prefix detection such as `detect_provider(model)`.
- Do not add semantic keyword routing or regex-based provider selection.
- Do not require API keys during project initialization.
- Do not make Streamlit part of the UI stack.

## Provider Model

Provider selection is explicit in `.annotation-pipeline/llm_profiles.yaml`.

```yaml
profiles:
  local_codex:
    provider: local_cli
    cli_kind: codex
    cli_binary: codex
    model: gpt-5.4-mini
    reasoning_effort: none

  deepseek_default:
    provider: openai_compatible
    provider_flavor: deepseek
    model: deepseek-chat
    api_key_env: DEEPSEEK_API_KEY
    base_url: https://api.deepseek.com

targets:
  annotation: local_codex
  qc: deepseek_default
```

`provider` defines the client implementation. `provider_flavor` records which OpenAI-compatible service is being used for diagnostics, UI display, and future service-specific request tuning. It is not inferred from `model`.

## UI Configurability

The Vite React UI remains the primary operator UI. The Configuration tab must expose `llm_profiles.yaml` so users can configure:

- Provider profiles.
- API key environment variable names.
- API base URLs.
- Local CLI binary names.
- Stage target bindings for annotation, QC, and coordinator agents.
- Runtime limits such as local CLI concurrency.

The UI should not hide provider configuration behind code-only defaults. The initialized project config must include editable examples for all five provider categories so a user can switch targets by editing YAML in the UI and saving it.

Provider target display should include enough fields to confirm active configuration without opening YAML: profile, provider, provider flavor, CLI kind, model, and base URL when configured.

## Runtime Behavior

The scheduler continues to call a client factory by stage target. The factory resolves a target through `LLMRegistry`, then creates:

- `OpenAIResponsesClient` for `provider: openai_responses`.
- `OpenAICompatibleClient` for `provider: openai_compatible`.
- `LocalCLIClient` for `provider: local_cli`.

`LocalCLIClient` must support:

- `cli_kind: codex`, preserving isolated `CODEX_HOME`, `--ignore-user-config`, `--ephemeral`, disabled apps/plugins, JSON output, and no desktop thread leakage.
- `cli_kind: claude`, using stdin prompt execution with stream JSON output and no session persistence.

API clients should return `LLMGenerateResult` with final text, raw response, usage when available, and diagnostics that include provider flavor for OpenAI-compatible providers.

## Verification

Unit tests must cover:

- Loading a registry containing all five provider categories.
- Rejecting `openai_compatible` profiles without `provider_flavor`.
- OpenAI-compatible chat completion request construction and result parsing.
- Claude CLI command construction and JSON stream parsing.
- Provider target output showing UI-relevant fields.

Integration verification must cover:

- Existing mock Codex quality script.
- Runtime progress script.
- A real Codex smoke script that initializes a tiny project, creates one task, runs one runtime cycle with `local_codex`, and prints useful diagnostics if Codex is unavailable or the cycle fails.

The real Codex smoke is environment-dependent. It can fail because the local Codex CLI is missing or unauthenticated, but the failure must be explicit and debuggable.
