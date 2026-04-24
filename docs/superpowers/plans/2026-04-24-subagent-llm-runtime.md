# Subagent LLM Runtime Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a configurable subagent runtime that can annotate, QC, repair, and coordinate tasks through either OpenAI Responses API or local LLM CLI providers such as Codex.

**Architecture:** Introduce a small unified LLM client layer inspired by `/home/derek/Projects/memory-connector/backend/app/core/unified_llm_client.py`, but scoped to annotation pipeline needs. Keep provider profile parsing, target resolution, reasoning kwargs, continuity handles, local CLI isolation, diagnostics, and structured output parsing. Route pipeline stages through subagent worker services that record attempts, artifacts, feedback, and audit events.

**Tech Stack:** Python 3.11+, PyYAML, Pydantic, OpenAI Python SDK, argparse CLI, pytest. Local CLI providers are invoked through subprocess and tested with fakes.

---

## Scope

This plan implements configurable LLM-backed subagents for the local pipeline. It does not implement a distributed queue, production auth, real external task API transport, active learning queues, or RL training loops.

Important source reference:

- Read `/home/derek/Projects/memory-connector/backend/app/core/unified_llm_client.py`.
- Reuse concepts, not secrets or project-specific paths.
- Do not copy `config/llm_profiles.json` values because it contains environment-specific credentials.

Key mechanisms to port in simplified form:

- provider profiles and target aliases
- `openai_responses` provider using `responses.create` and `responses.parse`
- `local_cli` provider using subprocess
- Codex exec command construction with JSON output
- isolated Codex home with auth/state restoration and secret stripping
- local CLI timeout and no-progress timeout
- continuity handle persistence
- diagnostics attached to attempts
- strict structured output validation with repair prompt

## File Structure

- Create `annotation_pipeline_skill/llm/__init__.py`: package marker.
- Create `annotation_pipeline_skill/llm/profiles.py`: profile dataclasses, YAML loader, target resolver, validation.
- Create `annotation_pipeline_skill/llm/client.py`: request/result dataclasses and client protocol.
- Create `annotation_pipeline_skill/llm/openai_responses.py`: OpenAI Responses API adapter.
- Create `annotation_pipeline_skill/llm/local_cli.py`: local CLI adapter, Codex command builder, diagnostics, isolated env.
- Create `annotation_pipeline_skill/llm/structured.py`: Pydantic structured parse and repair prompt helpers.
- Create `annotation_pipeline_skill/runtime/subagent_cycle.py`: stage runner that calls LLM clients and advances tasks.
- Modify `annotation_pipeline_skill/config/models.py`: add `llm_profiles` and `stage_llm_targets` metadata if needed.
- Modify `annotation_pipeline_skill/config/loader.py`: load `.annotation-pipeline/llm_profiles.yaml`.
- Modify `annotation_pipeline_skill/interfaces/cli.py`: add `provider doctor` and `run-cycle --runtime subagent`.
- Modify `annotation_pipeline_skill/interfaces/api.py`: expose provider diagnostics in task detail payload when available.
- Modify `README.md`, `SKILL.md`, and `docs/agent-operator-guide.md`: document subagent runtime and provider setup.
- Create `tests/test_llm_profiles.py`.
- Create `tests/test_openai_responses_client.py`.
- Create `tests/test_local_cli_client.py`.
- Create `tests/test_subagent_cycle.py`.
- Create `tests/test_provider_cli.py`.

## Task 1: LLM Profile Registry

**Files:**
- Create: `annotation_pipeline_skill/llm/__init__.py`
- Create: `annotation_pipeline_skill/llm/profiles.py`
- Modify: `annotation_pipeline_skill/config/loader.py`
- Test: `tests/test_llm_profiles.py`

- [ ] **Step 1: Write failing profile tests**

Create `tests/test_llm_profiles.py`:

```python
from pathlib import Path

import pytest

from annotation_pipeline_skill.llm.profiles import ProfileValidationError, load_llm_registry, reasoning_kwargs


def test_load_llm_registry_resolves_openai_and_local_cli_targets(tmp_path: Path):
    profiles_path = tmp_path / "llm_profiles.yaml"
    profiles_path.write_text(
        """
profiles:
  openai_primary:
    provider: openai_responses
    model: gpt-5.4-mini
    api_key_env: OPENAI_API_KEY
    base_url: https://api.openai.com/v1
    reasoning_effort: medium
    timeout_seconds: 120
  local_codex:
    provider: local_cli
    cli_kind: codex
    cli_binary: codex
    model: gpt-5.4-mini
    reasoning_effort: none
    concurrency_limit: 2
    timeout_seconds: 300
    no_progress_timeout_seconds: 30
targets:
  annotation: local_codex
  qc: openai_primary
limits:
  local_cli_global_concurrency: 4
""",
        encoding="utf-8",
    )

    registry = load_llm_registry(profiles_path)

    assert registry.resolve("annotation").name == "local_codex"
    assert registry.resolve("qc").provider == "openai_responses"
    assert registry.local_cli_global_concurrency == 4


def test_load_llm_registry_rejects_missing_openai_base_url(tmp_path: Path):
    profiles_path = tmp_path / "llm_profiles.yaml"
    profiles_path.write_text(
        """
profiles:
  broken:
    provider: openai_responses
    model: gpt-5.4-mini
    api_key_env: OPENAI_API_KEY
targets:
  annotation: broken
""",
        encoding="utf-8",
    )

    with pytest.raises(ProfileValidationError, match="base_url"):
        load_llm_registry(profiles_path)


def test_reasoning_kwargs_only_apply_to_reasoning_models():
    assert reasoning_kwargs("gpt-5.4-mini", "high") == {"reasoning": {"effort": "high"}}
    assert reasoning_kwargs("gemma4-e4b-it", "high") == {}
    assert reasoning_kwargs("gpt-5.4-mini", "none") == {}
```

- [ ] **Step 2: Run the test and verify it fails**

Run:

```bash
UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy uv run pytest tests/test_llm_profiles.py -v
```

Expected: FAIL because `annotation_pipeline_skill.llm.profiles` is missing.

- [ ] **Step 3: Implement profile registry**

Create dataclasses:

```python
@dataclass(frozen=True)
class LLMProfile:
    name: str
    provider: Literal["openai_responses", "local_cli"]
    model: str
    api_key: str | None = None
    api_key_env: str | None = None
    base_url: str | None = None
    reasoning_effort: str | None = None
    cli_kind: Literal["codex", "claude"] | None = None
    cli_binary: str | None = None
    permission_mode: str | None = None
    timeout_seconds: int | None = None
    max_retries: int | None = None
    concurrency_limit: int | None = None
    no_progress_timeout_seconds: int | None = None
```

Implement:

- `load_llm_registry(path: Path) -> LLMRegistry`
- `LLMRegistry.resolve(target: str) -> LLMProfile`
- `LLMProfile.resolve_api_key(env: Mapping[str, str] = os.environ) -> str`
- `reasoning_kwargs(model: str | None, effort: str | None) -> dict`
- strict validation:
  - `openai_responses` requires `model`, `base_url`, and `api_key` or `api_key_env`
  - `local_cli` requires `model`, `cli_kind`, and `cli_binary`
  - positive integers for timeout and limits

- [ ] **Step 4: Run the test and verify it passes**

Run:

```bash
UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy uv run pytest tests/test_llm_profiles.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

Run:

```bash
git add annotation_pipeline_skill/llm/__init__.py annotation_pipeline_skill/llm/profiles.py tests/test_llm_profiles.py pyproject.toml uv.lock
git commit -m "feat: add llm profile registry"
```

## Task 2: Unified Client Interfaces And OpenAI Responses Adapter

**Files:**
- Create: `annotation_pipeline_skill/llm/client.py`
- Create: `annotation_pipeline_skill/llm/openai_responses.py`
- Create: `annotation_pipeline_skill/llm/structured.py`
- Test: `tests/test_openai_responses_client.py`

- [ ] **Step 1: Write failing OpenAI Responses tests**

Create `tests/test_openai_responses_client.py`:

```python
from pydantic import BaseModel
import pytest

from annotation_pipeline_skill.llm.client import LLMGenerateRequest, LLMStructuredRequest
from annotation_pipeline_skill.llm.openai_responses import OpenAIResponsesClient
from annotation_pipeline_skill.llm.profiles import LLMProfile


class LabelPayload(BaseModel):
    label: str


@pytest.mark.asyncio
async def test_openai_responses_generate_forwards_previous_response_id(monkeypatch):
    captured = {}

    class FakeResponses:
        async def create(self, **kwargs):
            captured.update(kwargs)
            return {
                "id": "resp-1",
                "output_text": "done",
                "usage": {"input_tokens": 1, "output_tokens": 2},
                "output": [{"type": "message", "content": [{"type": "output_text", "text": "done"}]}],
            }

    class FakeClient:
        responses = FakeResponses()

    profile = LLMProfile(
        name="openai",
        provider="openai_responses",
        model="gpt-5.4-mini",
        api_key="key",
        base_url="https://api.example/v1",
    )
    client = OpenAIResponsesClient(profile, client=FakeClient())
    result = await client.generate(
        LLMGenerateRequest(
            instructions="annotate carefully",
            input_items=[{"role": "user", "content": "hello"}],
            reasoning={"effort": "medium"},
            continuity_handle="prev-1",
            max_output_tokens=100,
        )
    )

    assert captured["model"] == "gpt-5.4-mini"
    assert captured["instructions"] == "annotate carefully"
    assert captured["input"] == [{"role": "user", "content": "hello"}]
    assert captured["previous_response_id"] == "prev-1"
    assert captured["reasoning"] == {"effort": "medium"}
    assert result.final_text == "done"
    assert result.continuity_handle == "resp-1"


@pytest.mark.asyncio
async def test_openai_responses_parse_structured_uses_sdk_parse(monkeypatch):
    captured = {}

    class ParsedText:
        parsed = LabelPayload(label="positive")

    class ParsedMessage:
        type = "message"
        content = [ParsedText()]

    class FakeParsedResponse:
        id = "resp-structured"
        output = [ParsedMessage()]

        def model_dump(self, **kwargs):
            return {"id": self.id, "output": []}

    class FakeResponses:
        async def parse(self, **kwargs):
            captured.update(kwargs)
            return FakeParsedResponse()

    class FakeClient:
        responses = FakeResponses()

    profile = LLMProfile(
        name="openai",
        provider="openai_responses",
        model="gpt-5.4-mini",
        api_key="key",
        base_url="https://api.example/v1",
    )
    client = OpenAIResponsesClient(profile, client=FakeClient())
    result = await client.parse_structured(
        LLMStructuredRequest(
            messages=[{"role": "user", "content": "label this"}],
            text_format=LabelPayload,
            reasoning={"effort": "low"},
        )
    )

    assert captured["text_format"] is LabelPayload
    assert result.output_parsed.label == "positive"
```

- [ ] **Step 2: Run the test and verify it fails**

Run:

```bash
UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy uv run pytest tests/test_openai_responses_client.py -v
```

Expected: FAIL because unified LLM client modules are missing.

- [ ] **Step 3: Add dependencies**

Modify `pyproject.toml`:

```toml
dependencies = [
  "openai>=2.0",
  "pydantic>=2.0",
  "pyyaml>=6.0",
]
```

Run:

```bash
UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy uv sync
```

- [ ] **Step 4: Implement client dataclasses**

Create `annotation_pipeline_skill/llm/client.py` with:

```python
@dataclass(frozen=True)
class LLMGenerateRequest:
    instructions: str | None = None
    input_items: list[dict[str, Any]] = field(default_factory=list)
    prompt: str | None = None
    reasoning: dict[str, Any] = field(default_factory=dict)
    continuity_handle: str | None = None
    max_output_tokens: int | None = None
    cwd: Path | None = None
    env: dict[str, str] = field(default_factory=dict)

@dataclass(frozen=True)
class LLMGenerateResult:
    runtime: str
    provider: str
    model: str
    continuity_handle: str | None
    final_text: str
    usage: dict[str, Any] | None
    raw_response: dict[str, Any] | list[dict[str, Any]]
    diagnostics: dict[str, Any] | None = None

@dataclass(frozen=True)
class LLMStructuredRequest:
    messages: list[dict[str, Any]]
    text_format: type[BaseModel]
    reasoning: dict[str, Any] = field(default_factory=dict)
    continuity_handle: str | None = None
    cwd: Path | None = None
    env: dict[str, str] = field(default_factory=dict)

@dataclass(frozen=True)
class LLMStructuredResult:
    id: str | None
    output_parsed: BaseModel
    raw_response: dict[str, Any] | list[dict[str, Any]]
    diagnostics: dict[str, Any] | None = None
```

- [ ] **Step 5: Implement OpenAI Responses adapter**

Create `annotation_pipeline_skill/llm/openai_responses.py`:

- instantiate `AsyncOpenAI` from profile unless a fake client is injected
- `generate()` calls `client.responses.create`
- `parse_structured()` calls `client.responses.parse`
- include `previous_response_id` only when continuity handle exists
- include `reasoning` only when non-empty
- extract `output_text`, usage, response id
- convert SDK objects with `model_dump(mode="json", warnings="none")` when available

- [ ] **Step 6: Run the test and verify it passes**

Run:

```bash
UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy uv run pytest tests/test_openai_responses_client.py -v
```

Expected: PASS.

- [ ] **Step 7: Commit**

Run:

```bash
git add pyproject.toml uv.lock annotation_pipeline_skill/llm/client.py annotation_pipeline_skill/llm/openai_responses.py annotation_pipeline_skill/llm/structured.py tests/test_openai_responses_client.py
git commit -m "feat: add openai responses llm client"
```

## Task 3: Local CLI Client With Codex Isolation

**Files:**
- Create: `annotation_pipeline_skill/llm/local_cli.py`
- Test: `tests/test_local_cli_client.py`

- [ ] **Step 1: Write failing local CLI tests**

Create `tests/test_local_cli_client.py`:

```python
import os
from pathlib import Path

from annotation_pipeline_skill.llm.local_cli import (
    build_codex_command,
    codex_shell_environment,
    isolated_codex_home,
    parse_codex_json_events,
)
from annotation_pipeline_skill.llm.profiles import LLMProfile


def test_codex_shell_environment_allows_only_safe_keys():
    env = codex_shell_environment(
        {
            "PATH": "/usr/bin",
            "HOME": "/tmp/home",
            "SHELL": "/bin/bash",
            "OPENAI_API_KEY": "do-not-pass",
            "SECRET_TOKEN": "do-not-pass",
            "CONNECTOR_API_KEY": "connector-key",
        }
    )

    assert env == {
        "PATH": "/usr/bin",
        "HOME": "/tmp/home",
        "SHELL": "/bin/bash",
        "CONNECTOR_API_KEY": "connector-key",
    }


def test_build_codex_command_includes_json_resume_and_model():
    command, prompt_file = build_codex_command(
        binary="codex",
        prompt="Annotate this",
        developer_instructions="Return JSON",
        continuity_handle="thread-1",
        model="gpt-5.4-mini",
        reasoning_effort="none",
    )

    assert command[:5] == ["codex", "--dangerously-bypass-approvals-and-sandbox", "exec", "resume", "thread-1"]
    assert "--json" in command
    assert "--model" in command
    assert "gpt-5.4-mini" in command
    assert prompt_file.read_text(encoding="utf-8") == "Annotate this"
    prompt_file.unlink()


def test_isolated_codex_home_strips_desktop_context_and_preserves_auth(tmp_path: Path):
    source_home = tmp_path / "source"
    source_home.mkdir()
    (source_home / "auth.json").write_text('{"token":"demo"}', encoding="utf-8")
    (source_home / "config.toml").write_text('model = "gpt-5.4"\n[plugins."gmail"]\nenabled = true\n', encoding="utf-8")

    with isolated_codex_home(
        {
            "CODEX_HOME": str(source_home),
            "CODEX_THREAD_ID": "desktop-thread",
            "OPENAI_API_KEY": "strip-me",
            "PATH": os.environ.get("PATH", ""),
        },
        model="gpt-5.4-mini",
        reasoning_effort="none",
        continuity_handle=None,
    ) as (isolated_env, isolated_home):
        assert isolated_env["CODEX_HOME"] == str(isolated_home)
        assert "CODEX_THREAD_ID" not in isolated_env
        assert "OPENAI_API_KEY" not in isolated_env
        assert (isolated_home / "auth.json").exists()
        config = (isolated_home / "config.toml").read_text(encoding="utf-8")
        assert 'model = "gpt-5.4-mini"' in config
        assert 'model_reasoning_effort = "none"' in config
        assert "[plugins." not in config


def test_parse_codex_json_events_extracts_thread_and_final_text():
    result = parse_codex_json_events(
        [
            '{"type":"thread.started","thread_id":"thread-1"}',
            '{"type":"item.completed","item":{"type":"agent_message","text":"final answer"}}',
        ],
        provider="local_cli",
        model="gpt-5.4-mini",
    )

    assert result.continuity_handle == "thread-1"
    assert result.final_text == "final answer"
```

- [ ] **Step 2: Run the test and verify it fails**

Run:

```bash
UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy uv run pytest tests/test_local_cli_client.py -v
```

Expected: FAIL because `annotation_pipeline_skill.llm.local_cli` is missing.

- [ ] **Step 3: Implement local CLI primitives**

Implement:

- `codex_shell_environment(base_env)` with an allowlist: `PATH`, `HOME`, `TMPDIR`, `SHELL`, `CONNECTOR_API_KEY`
- `build_codex_command(...) -> tuple[list[str], Path]`
- `_sanitize_codex_config(raw_text, model, reasoning_effort)` stripping plugin sections and desktop defaults
- `isolated_codex_home(base_env, model, reasoning_effort, continuity_handle)` context manager
- `parse_codex_json_events(raw_lines, provider, model) -> LLMGenerateResult`
- keep `claude` as a future `cli_kind`, but implement only `codex` in this task

- [ ] **Step 4: Run the test and verify it passes**

Run:

```bash
UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy uv run pytest tests/test_local_cli_client.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

Run:

```bash
git add annotation_pipeline_skill/llm/local_cli.py tests/test_local_cli_client.py
git commit -m "feat: add local cli llm client primitives"
```

## Task 4: Subagent Stage Runner

**Files:**
- Create: `annotation_pipeline_skill/runtime/subagent_cycle.py`
- Modify: `annotation_pipeline_skill/interfaces/cli.py`
- Test: `tests/test_subagent_cycle.py`

- [ ] **Step 1: Write failing subagent runtime tests**

Create `tests/test_subagent_cycle.py`:

```python
from annotation_pipeline_skill.core.models import Task
from annotation_pipeline_skill.core.states import TaskStatus
from annotation_pipeline_skill.runtime.subagent_cycle import SubagentRuntime, SubagentRuntimeResult
from annotation_pipeline_skill.store.file_store import FileStore


class FakeLLMClient:
    async def generate(self, request):
        from annotation_pipeline_skill.llm.client import LLMGenerateResult

        return LLMGenerateResult(
            runtime="fake",
            provider="fake",
            model="fake-model",
            continuity_handle="thread-1",
            final_text='{"labels":[{"text":"alpha","type":"ENTITY"}]}',
            usage={"total_tokens": 10},
            raw_response={"id": "fake"},
            diagnostics={"queue_wait_ms": 0},
        )


def test_subagent_runtime_advances_ready_task_and_records_attempt(tmp_path):
    store = FileStore(tmp_path)
    task = Task.new(task_id="task-1", pipeline_id="pipe", source_ref={"kind": "jsonl", "payload": {"text": "alpha"}})
    task.status = TaskStatus.READY
    store.save_task(task)
    runtime = SubagentRuntime(store=store, client_factory=lambda target: FakeLLMClient())

    result = runtime.run_once(stage_target="annotation")

    loaded = store.load_task("task-1")
    attempts = store.list_attempts("task-1")
    artifacts = store.list_artifacts("task-1")
    assert isinstance(result, SubagentRuntimeResult)
    assert result.started == 1
    assert loaded.status is TaskStatus.ACCEPTED
    assert attempts[0].provider_id == "fake"
    assert attempts[0].model == "fake-model"
    assert artifacts[0].kind == "annotation_result"
    assert artifacts[0].metadata["continuity_handle"] == "thread-1"
```

- [ ] **Step 2: Run the test and verify it fails**

Run:

```bash
UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy uv run pytest tests/test_subagent_cycle.py -v
```

Expected: FAIL because `runtime.subagent_cycle` is missing.

- [ ] **Step 3: Implement `SubagentRuntime`**

Implementation requirements:

- select READY tasks
- create a stage prompt from `task.source_ref`, `annotation_requirements`, and feedback bundle
- call `client_factory(stage_target).generate(...)`
- record an `Attempt` with provider/model/summary/diagnostics
- save an `annotation_result` artifact containing response text metadata
- append audit events for `ANNOTATING`, `VALIDATING`, `QC`, and `ACCEPTED`
- leave Human Review policy integration to Task 5
- use `asyncio.run()` internally for the single async client call in synchronous CLI context

- [ ] **Step 4: Run the test and verify it passes**

Run:

```bash
UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy uv run pytest tests/test_subagent_cycle.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

Run:

```bash
git add annotation_pipeline_skill/runtime/subagent_cycle.py tests/test_subagent_cycle.py
git commit -m "feat: add subagent runtime cycle"
```

## Task 5: Provider CLI Doctor And Runtime Wiring

**Files:**
- Modify: `annotation_pipeline_skill/interfaces/cli.py`
- Modify: `annotation_pipeline_skill/interfaces/cli.py`
- Modify: `annotation_pipeline_skill/config/loader.py`
- Test: `tests/test_provider_cli.py`

- [ ] **Step 1: Write failing provider CLI tests**

Create `tests/test_provider_cli.py`:

```python
from annotation_pipeline_skill.interfaces.cli import main


def test_provider_doctor_validates_llm_profiles(tmp_path):
    main(["init", "--project-root", str(tmp_path)])
    profiles = tmp_path / ".annotation-pipeline" / "llm_profiles.yaml"
    profiles.write_text(
        """
profiles:
  local_codex:
    provider: local_cli
    cli_kind: codex
    cli_binary: codex
    model: gpt-5.4-mini
targets:
  annotation: local_codex
""",
        encoding="utf-8",
    )

    assert main(["provider", "doctor", "--project-root", str(tmp_path)]) == 0


def test_provider_doctor_rejects_invalid_llm_profiles(tmp_path):
    main(["init", "--project-root", str(tmp_path)])
    profiles = tmp_path / ".annotation-pipeline" / "llm_profiles.yaml"
    profiles.write_text(
        """
profiles:
  broken:
    provider: openai_responses
    model: gpt-5.4-mini
targets:
  annotation: broken
""",
        encoding="utf-8",
    )

    assert main(["provider", "doctor", "--project-root", str(tmp_path)]) == 1
```

- [ ] **Step 2: Run the test and verify it fails**

Run:

```bash
UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy uv run pytest tests/test_provider_cli.py -v
```

Expected: FAIL because the CLI has no `provider` command.

- [ ] **Step 3: Add default `llm_profiles.yaml` in `init`**

Default file:

```yaml
profiles:
  local_codex:
    provider: local_cli
    cli_kind: codex
    cli_binary: codex
    model: gpt-5.4-mini
    reasoning_effort: none
    timeout_seconds: 900
    no_progress_timeout_seconds: 30
  openai_default:
    provider: openai_responses
    model: gpt-5.4-mini
    api_key_env: OPENAI_API_KEY
    base_url: https://api.openai.com/v1
    reasoning_effort: medium
    timeout_seconds: 300
targets:
  annotation: local_codex
  qc: openai_default
  repair: local_codex
  coordinator: local_codex
limits:
  local_cli_global_concurrency: 4
```

- [ ] **Step 4: Add CLI commands**

Add:

- `annotation-pipeline provider doctor --project-root <project>`
- `annotation-pipeline provider targets --project-root <project>`
- `annotation-pipeline run-cycle --runtime subagent --project-root <project>`

Provider doctor validates profile config only; it does not require local CLI binaries to exist, because CI/dev tests should not depend on installed Codex.

- [ ] **Step 5: Run the provider CLI tests**

Run:

```bash
UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy uv run pytest tests/test_provider_cli.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

Run:

```bash
git add annotation_pipeline_skill/interfaces/cli.py annotation_pipeline_skill/config/loader.py tests/test_provider_cli.py
git commit -m "feat: add provider cli and subagent runtime wiring"
```

## Task 6: Skill And Operator Docs For Subagents

**Files:**
- Modify: `SKILL.md`
- Modify: `docs/agent-operator-guide.md`
- Modify: `docs/algorithm-engineer-user-story.md`
- Modify: `README.md`
- Modify: `docs/superpowers/plans/2026-04-24-agent-coordinator-skill-packaging.md`
- Test: `tests/test_skill_packaging.py`

- [ ] **Step 1: Add failing documentation tests**

Append to `tests/test_skill_packaging.py`:

```python
def test_skill_docs_explain_subagent_provider_configuration():
    text = Path("SKILL.md").read_text(encoding="utf-8")
    readme = Path("README.md").read_text(encoding="utf-8")

    assert "subagent" in text
    assert "OpenAI Responses API" in text
    assert "local LLM CLI" in text
    assert "llm_profiles.yaml" in text
    assert "annotation-pipeline provider doctor" in readme
    assert "annotation-pipeline run-cycle --runtime subagent" in readme
```

- [ ] **Step 2: Run the documentation tests and verify they fail**

Run:

```bash
UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy uv run pytest tests/test_skill_packaging.py -v
```

Expected: FAIL until docs are updated.

- [ ] **Step 3: Update `SKILL.md`**

Add a section:

```markdown
## Configure subagents

Use `.annotation-pipeline/llm_profiles.yaml` to configure stage subagents.

Supported runtimes:

- OpenAI Responses API through `provider: openai_responses`
- local LLM CLI through `provider: local_cli`, with `cli_kind: codex`

Run `annotation-pipeline provider doctor --project-root <project>` after edits.
Run `annotation-pipeline run-cycle --runtime subagent --project-root <project>` to use configured subagents.

Do not put raw secrets in skill docs. Prefer `api_key_env`.
```

- [ ] **Step 4: Update README and operator guide**

Document:

- `llm_profiles.yaml` profile format
- OpenAI Responses API example using `api_key_env`
- local Codex CLI example
- `provider doctor`
- `provider targets`
- `run-cycle --runtime subagent`
- subagent attempts record diagnostics and continuity handles

- [ ] **Step 5: Run documentation tests**

Run:

```bash
UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy uv run pytest tests/test_skill_packaging.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

Run:

```bash
git add SKILL.md README.md docs/agent-operator-guide.md docs/algorithm-engineer-user-story.md docs/superpowers/plans/2026-04-24-agent-coordinator-skill-packaging.md tests/test_skill_packaging.py
git commit -m "docs: explain subagent provider configuration"
```

## Task 7: End-To-End Verification

**Files:**
- Modify: `README.md` only if commands are mismatched.

- [ ] **Step 1: Run backend tests**

Run:

```bash
UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy uv run pytest -v
```

Expected: all tests pass.

- [ ] **Step 2: Run frontend tests and build**

Run:

```bash
cd web && npm test -- --run
cd web && npm run build
```

Expected: frontend tests pass and Vite builds successfully.

- [ ] **Step 3: Verify provider CLI**

Run:

```bash
PROJECT_ROOT=$(mktemp -d /tmp/annotation-subagent-XXXXXX)
UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy uv run --with-editable . annotation-pipeline init --project-root "$PROJECT_ROOT"
UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy uv run --with-editable . annotation-pipeline provider doctor --project-root "$PROJECT_ROOT"
UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy uv run --with-editable . annotation-pipeline provider targets --project-root "$PROJECT_ROOT"
```

Expected output includes configured targets:

```text
annotation
qc
repair
coordinator
```

- [ ] **Step 4: Verify fake subagent runtime through tests**

Run:

```bash
UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy uv run pytest tests/test_subagent_cycle.py -v
```

Expected: PASS.

- [ ] **Step 5: Push**

Run:

```bash
git push
```

Expected: local `main` pushed to `origin/main`.

## Self-Review

- Spec coverage: this plan adds configurable subagent execution, OpenAI Responses API support, local LLM CLI support, provider targets, diagnostics, continuity handles, and docs for algorithm-engineer training-data workflows.
- Placeholder scan: no unresolved placeholder markers remain.
- Type consistency: profile fields, CLI command names, runtime names, and test expectations match across all tasks.
