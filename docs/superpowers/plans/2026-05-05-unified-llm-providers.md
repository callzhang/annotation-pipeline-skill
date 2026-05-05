# Unified LLM Providers Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add explicit, UI-configurable support for Codex CLI, Claude CLI, DeepSeek API, GLM API, and MiniMax API provider profiles.

**Architecture:** Keep provider choice in `llm_profiles.yaml` and route through `LLMRegistry`. Add an OpenAI-compatible chat client for DeepSeek, GLM, and MiniMax while preserving the existing OpenAI Responses client and isolated local Codex execution. Extend the React UI with a structured Providers tab, while preserving raw YAML editing in the Configuration tab.

**Tech Stack:** Python dataclasses and PyYAML for profile loading, OpenAI Python SDK `AsyncOpenAI` for Responses and OpenAI-compatible chat completions, asyncio subprocesses for local CLIs, pytest for backend tests, Vite React TypeScript for UI configuration.

---

## File Structure

- Modify `annotation_pipeline_skill/llm/profiles.py`: add `openai_compatible`, `provider_flavor`, validation, and parsing.
- Create `annotation_pipeline_skill/llm/openai_compatible.py`: implement chat completion generation against explicit OpenAI-compatible base URLs.
- Modify `annotation_pipeline_skill/llm/local_cli.py`: support `cli_kind: claude` with command builder and stream parsing.
- Modify `annotation_pipeline_skill/interfaces/cli.py`: update default `llm_profiles.yaml`, provider factory, and provider target output.
- Modify `tests/test_llm_profiles.py`: cover five-provider registry and invalid `openai_compatible`.
- Create `tests/test_openai_compatible_client.py`: test request construction and result conversion.
- Modify `tests/test_local_cli_client.py`: test Claude command and stream parsing.
- Modify `tests/test_provider_cli.py`: assert target diagnostics include UI-relevant fields.
- Create `scripts/verify_runtime_codex_smoke.sh`: run a real local Codex one-task smoke with diagnostics.
- Modify `docs/agent-operator-guide.md`: document UI-configurable provider profiles and smoke verification.
- Create `annotation_pipeline_skill/services/provider_config_service.py`: provide structured provider snapshots, YAML writeback, and deterministic local doctor checks.
- Create `web/src/components/ProvidersPanel.tsx`: provide form controls for provider profiles and stage targets.
- Create `web/src/providers.ts`: share frontend provider helper logic.

### Task 1: Profile Schema

**Files:**
- Modify: `annotation_pipeline_skill/llm/profiles.py`
- Modify: `tests/test_llm_profiles.py`

- [ ] **Step 1: Write tests for five provider categories**

Add a test that writes `llm_profiles.yaml` with `local_codex`, `local_claude`, `deepseek_default`, `glm_default`, and `minimax_default`, then asserts targets resolve and API profiles expose `provider_flavor`.

- [ ] **Step 2: Add invalid profile test**

Add a test that an `openai_compatible` profile without `provider_flavor` raises `ProfileValidationError`.

- [ ] **Step 3: Run profile tests**

Run: `uv run pytest tests/test_llm_profiles.py -q`

Expected: tests fail because `openai_compatible` and `provider_flavor` are not supported yet.

- [ ] **Step 4: Implement schema**

Add `ProviderName = Literal["openai_responses", "openai_compatible", "local_cli"]`, `ProviderFlavor = Literal["deepseek", "glm", "minimax"]`, `provider_flavor` on `LLMProfile`, parsing, and validation.

- [ ] **Step 5: Re-run profile tests**

Run: `uv run pytest tests/test_llm_profiles.py -q`

Expected: all profile tests pass.

- [ ] **Step 6: Commit**

Run: `git add annotation_pipeline_skill/llm/profiles.py tests/test_llm_profiles.py && git commit -m "feat: extend llm provider profiles"`

### Task 2: OpenAI-Compatible Client

**Files:**
- Create: `annotation_pipeline_skill/llm/openai_compatible.py`
- Create: `tests/test_openai_compatible_client.py`
- Modify: `annotation_pipeline_skill/interfaces/cli.py`

- [ ] **Step 1: Write client test**

Create a fake async chat completion client. Assert `generate()` sends `model`, `messages`, `max_tokens` when requested, and returns final assistant text, usage, raw response, and diagnostics with `provider_flavor`.

- [ ] **Step 2: Run client test**

Run: `uv run pytest tests/test_openai_compatible_client.py -q`

Expected: import failure because the client module does not exist.

- [ ] **Step 3: Implement client**

Create `OpenAICompatibleClient` with `AsyncOpenAI(api_key=profile.resolve_api_key(), base_url=profile.base_url, max_retries=..., timeout=...)` and `client.chat.completions.create(...)`.

- [ ] **Step 4: Wire provider factory**

Update `_build_llm_client()` so `profile.provider == "openai_compatible"` returns `OpenAICompatibleClient(profile)`.

- [ ] **Step 5: Re-run tests**

Run: `uv run pytest tests/test_openai_compatible_client.py tests/test_llm_profiles.py -q`

Expected: tests pass.

- [ ] **Step 6: Commit**

Run: `git add annotation_pipeline_skill/llm/openai_compatible.py annotation_pipeline_skill/interfaces/cli.py tests/test_openai_compatible_client.py && git commit -m "feat: add openai compatible llm client"`

### Task 3: Claude Local CLI

**Files:**
- Modify: `annotation_pipeline_skill/llm/local_cli.py`
- Modify: `tests/test_local_cli_client.py`

- [ ] **Step 1: Write command and parser tests**

Add tests for `build_claude_command()` and `parse_claude_stream_events()`. The command should include `-p`, `--no-session-persistence`, `--output-format stream-json`, `--model`, and `--permission-mode`. The parser should extract text from simple JSON stream events and preserve raw events.

- [ ] **Step 2: Run local CLI tests**

Run: `uv run pytest tests/test_local_cli_client.py -q`

Expected: tests fail because Claude helpers are missing.

- [ ] **Step 3: Implement Claude helpers and generation branch**

Add a `cli_kind == "claude"` branch that builds the command, passes prompt through stdin, waits with `timeout_seconds`, parses stdout, attaches return code and stderr diagnostics, and raises `LocalCLIExecutionError` on non-zero return.

- [ ] **Step 4: Re-run local CLI tests**

Run: `uv run pytest tests/test_local_cli_client.py -q`

Expected: tests pass.

- [ ] **Step 5: Commit**

Run: `git add annotation_pipeline_skill/llm/local_cli.py tests/test_local_cli_client.py && git commit -m "feat: support claude local cli profiles"`

### Task 4: UI-Configurable Defaults and Diagnostics

**Files:**
- Modify: `annotation_pipeline_skill/interfaces/cli.py`
- Modify: `tests/test_provider_cli.py`
- Modify: `docs/agent-operator-guide.md`

- [ ] **Step 1: Write provider target test**

Extend provider CLI tests to initialize a project and assert `provider targets` includes `provider_flavor`, `cli_kind`, and `base_url` keys where applicable.

- [ ] **Step 2: Run provider CLI tests**

Run: `uv run pytest tests/test_provider_cli.py -q`

Expected: tests fail because target output is too sparse.

- [ ] **Step 3: Update default YAML and output**

Update `CONFIG_FILES["llm_profiles.yaml"]` to include editable examples for `local_codex`, `local_claude`, `openai_default`, `deepseek_default`, `glm_default`, and `minimax_default`. Update `handle_provider_targets()` output to print `profile`, `provider`, `provider_flavor`, `cli_kind`, `model`, and `base_url`.

- [ ] **Step 4: Update operator guide**

Document that the UI Configuration tab can edit `llm_profiles.yaml`, and show how to switch QC from OpenAI to DeepSeek by changing `targets.qc`.

- [ ] **Step 5: Re-run provider tests**

Run: `uv run pytest tests/test_provider_cli.py tests/test_cli.py -q`

Expected: tests pass.

- [ ] **Step 6: Commit**

Run: `git add annotation_pipeline_skill/interfaces/cli.py tests/test_provider_cli.py docs/agent-operator-guide.md && git commit -m "feat: expose configurable provider targets"`

### Task 5: Real Codex Smoke Verification

**Files:**
- Create: `scripts/verify_runtime_codex_smoke.sh`
- Modify: `docs/agent-operator-guide.md`

- [ ] **Step 1: Add smoke script**

Create a Bash script that checks `codex` is available, initializes a temp project, creates one JSONL task, runs `annotation-pipeline runtime once`, checks one task is accepted, and prints runtime/task diagnostics on failure.

- [ ] **Step 2: Run stable verification**

Run: `uv run pytest -q`

Expected: all tests pass.

- [ ] **Step 3: Run runtime scripts**

Run: `bash scripts/verify_runtime_progress.sh`

Expected: script prints accepted tasks and exits 0.

Run: `bash scripts/verify_runtime_e2e.sh`

Expected: script prints runtime/API verification and exits 0.

- [ ] **Step 4: Run real Codex smoke when available**

Run: `bash scripts/verify_runtime_codex_smoke.sh`

Expected: exits 0 if local Codex is installed and authenticated; otherwise exits non-zero with explicit diagnostics.

- [ ] **Step 5: Commit**

Run: `git add scripts/verify_runtime_codex_smoke.sh docs/agent-operator-guide.md && git commit -m "test: add real codex runtime smoke"`

### Task 6: Structured Provider UI Completion

**Files:**
- Create: `annotation_pipeline_skill/services/provider_config_service.py`
- Modify: `annotation_pipeline_skill/interfaces/api.py`
- Create: `tests/test_provider_config_api.py`
- Create: `web/src/providers.ts`
- Create: `web/src/providers.test.ts`
- Create: `web/src/components/ProvidersPanel.tsx`
- Modify: `web/src/App.tsx`
- Modify: `web/src/api.ts`
- Modify: `web/src/types.ts`
- Modify: `web/src/styles.css`

- [x] **Step 1: Write backend provider API tests**

Tests cover `GET /api/providers` returning profiles, targets, limits, and diagnostics, plus `PUT /api/providers` writing valid structured provider config back to `llm_profiles.yaml`.

- [x] **Step 2: Implement backend provider config service**

`build_provider_config_snapshot()` loads `llm_profiles.yaml`, serializes profiles, and runs local checks for CLI binaries and API key env vars. `save_provider_config()` validates structured payloads through `LLMRegistry` before writing YAML.

- [x] **Step 3: Write frontend helper tests**

Tests cover creating provider profiles, stripping diagnostics from save payloads, and formatting scan-friendly profile titles.

- [x] **Step 4: Implement Providers tab**

The Providers tab supports adding, editing, deleting provider profiles, changing stage targets, editing local CLI concurrency, saving to YAML, and refreshing provider doctor status.

- [x] **Step 5: Verify**

Run backend tests, frontend tests, frontend build, runtime progress verification, runtime e2e verification, and real Codex smoke.

## Self-Review

- Spec coverage: profile-driven five-provider support, UI YAML configurability, target diagnostics, no model-name auto-detection, and real Codex smoke are all mapped to tasks.
- Placeholder scan: no task uses unresolved TBD work.
- Type consistency: `provider_flavor`, `cli_kind`, `OpenAICompatibleClient`, and `LLMGenerateResult` names match current code conventions.
