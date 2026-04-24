from __future__ import annotations

import asyncio
import json
import os
import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Mapping

from annotation_pipeline_skill.llm.client import LLMGenerateRequest, LLMGenerateResult
from annotation_pipeline_skill.llm.profiles import LLMProfile

_SAFE_ENV_KEYS = {
    "PATH",
    "HOME",
    "SHELL",
    "TMPDIR",
    "TEMP",
    "TMP",
    "LANG",
    "LC_ALL",
    "CODEX_HOME",
    "ANNOTATION_CODEX_HOME_ROOT",
}


class LocalCLIExecutionError(RuntimeError):
    def __init__(self, message: str, diagnostics: dict[str, Any]):
        super().__init__(message)
        self.diagnostics = diagnostics


def codex_shell_environment(env: Mapping[str, str] = os.environ) -> dict[str, str]:
    safe: dict[str, str] = {}
    for key, value in env.items():
        if key in _SAFE_ENV_KEYS or key.endswith("_CONNECTOR_API_KEY") or key == "CONNECTOR_API_KEY":
            safe[key] = value
    return safe


def build_codex_command(
    *,
    binary: str,
    prompt: str,
    developer_instructions: str | None,
    continuity_handle: str | None,
    model: str,
    reasoning_effort: str | None,
) -> tuple[list[str], Path]:
    prompt_file = Path(tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False).name)
    full_prompt = prompt
    if developer_instructions:
        full_prompt = f"{developer_instructions}\n\n{prompt}"
    prompt_file.write_text(full_prompt, encoding="utf-8")

    command = [binary, "exec"]
    if continuity_handle:
        command.append("resume")
    command.extend(["--dangerously-bypass-approvals-and-sandbox", "--skip-git-repo-check", "--json", "--model", model])
    if reasoning_effort:
        command.extend(["--config", f'model_reasoning_effort="{reasoning_effort}"'])
    if continuity_handle:
        command.append(continuity_handle)
    command.append(prompt_file.read_text(encoding="utf-8"))
    return command, prompt_file


@contextmanager
def isolated_codex_home(
    env: Mapping[str, str],
    *,
    model: str,
    reasoning_effort: str | None,
    continuity_handle: str | None,
) -> Iterator[tuple[dict[str, str], Path]]:
    source_home = Path(env.get("CODEX_HOME") or Path(env.get("HOME", "~")).expanduser() / ".codex")
    runtime_root = Path(env.get("ANNOTATION_CODEX_HOME_ROOT") or Path.cwd() / ".annotation-pipeline-codex-homes")
    runtime_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="annotation-codex-home-", dir=runtime_root) as temp_dir:
        isolated_home = Path(temp_dir)
        for filename in ("auth.json", "config.toml", "credentials.json"):
            source_file = source_home / filename
            if source_file.exists():
                shutil.copy2(source_file, isolated_home / filename)

        _write_isolated_codex_config(
            isolated_home / "config.toml",
            model=model,
            reasoning_effort=reasoning_effort,
        )

        isolated_env = codex_shell_environment(env)
        isolated_env["CODEX_HOME"] = str(isolated_home)
        isolated_env.pop("CODEX_THREAD_ID", None)
        if continuity_handle:
            isolated_env["CODEX_RESUME_THREAD_ID"] = continuity_handle
        yield isolated_env, isolated_home


def parse_codex_json_events(
    lines: list[str],
    *,
    provider: str,
    model: str,
) -> LLMGenerateResult:
    thread_id: str | None = None
    final_text_parts: list[str] = []
    raw_events: list[dict[str, Any]] = []

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        try:
            event = json.loads(stripped)
        except json.JSONDecodeError:
            final_text_parts.append(stripped)
            continue
        if not isinstance(event, dict):
            continue
        raw_events.append(event)
        event_type = event.get("type")
        if event_type in {"thread.started", "thread.resumed"} and isinstance(event.get("thread_id"), str):
            thread_id = event["thread_id"]
        item = event.get("item")
        if event_type == "item.completed" and isinstance(item, dict):
            text = item.get("text") or item.get("content")
            if item.get("type") in {"agent_message", "message"} and isinstance(text, str):
                final_text_parts.append(text)
        message = event.get("message")
        if event_type in {"agent_message", "message"} and isinstance(message, str):
            final_text_parts.append(message)

    return LLMGenerateResult(
        runtime="local_cli",
        provider=provider,
        model=model,
        continuity_handle=thread_id,
        final_text="\n".join(final_text_parts),
        usage=None,
        raw_response=raw_events,
        diagnostics={"line_count": len(lines), "event_count": len(raw_events)},
    )


class LocalCLIClient:
    def __init__(self, profile: LLMProfile):
        self.profile = profile

    async def generate(self, request: LLMGenerateRequest) -> LLMGenerateResult:
        if self.profile.cli_kind != "codex":
            raise ValueError(f"unsupported local cli kind: {self.profile.cli_kind}")
        command, prompt_file = build_codex_command(
            binary=self.profile.cli_binary or "codex",
            prompt=request.prompt or _messages_to_prompt(request.input_items),
            developer_instructions=request.instructions,
            continuity_handle=request.continuity_handle,
            model=self.profile.model,
            reasoning_effort=self.profile.reasoning_effort,
        )
        try:
            with isolated_codex_home(
                {**os.environ, **request.env},
                model=self.profile.model,
                reasoning_effort=self.profile.reasoning_effort,
                continuity_handle=request.continuity_handle,
            ) as (env, _home):
                process = await asyncio.create_subprocess_exec(
                    *command,
                    cwd=str(request.cwd) if request.cwd else None,
                    env=env,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(),
                    timeout=self.profile.timeout_seconds,
                )
            lines = stdout.decode("utf-8", errors="replace").splitlines()
            result = parse_codex_json_events(lines, provider=self.profile.name, model=self.profile.model)
            diagnostics = dict(result.diagnostics or {})
            diagnostics["returncode"] = process.returncode
            if stderr:
                diagnostics["stderr"] = stderr.decode("utf-8", errors="replace")[-4000:]
            if process.returncode != 0:
                raise LocalCLIExecutionError("local CLI provider failed", diagnostics)
            return LLMGenerateResult(
                runtime=result.runtime,
                provider=result.provider,
                model=result.model,
                continuity_handle=result.continuity_handle,
                final_text=result.final_text,
                usage=result.usage,
                raw_response=result.raw_response,
                diagnostics=diagnostics,
            )
        finally:
            prompt_file.unlink(missing_ok=True)


def _write_isolated_codex_config(path: Path, *, model: str, reasoning_effort: str | None) -> None:
    lines = []
    lines.append(f'model = "{model}"')
    if reasoning_effort:
        lines.append(f'model_reasoning_effort = "{reasoning_effort}"')
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _messages_to_prompt(input_items: list[dict[str, Any]]) -> str:
    return "\n".join(str(item.get("content", item)) for item in input_items)
