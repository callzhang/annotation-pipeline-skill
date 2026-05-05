import os
from pathlib import Path

from annotation_pipeline_skill.llm.local_cli import (
    build_claude_command,
    build_codex_command,
    codex_shell_environment,
    isolated_codex_home,
    parse_claude_stream_events,
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

    assert command[:3] == ["codex", "exec", "resume"]
    assert "--json" in command
    assert "--ignore-user-config" in command
    assert "--ephemeral" in command
    assert command[command.index("--disable") + 1] == "apps"
    assert command[command.index("--disable", command.index("--disable") + 1) + 1] == "plugins"
    assert "--model" in command
    assert "gpt-5.4-mini" in command
    assert "--developer-message" not in command
    assert command[-2:] == ["thread-1", prompt_file.read_text(encoding="utf-8")]
    assert "Return JSON" in prompt_file.read_text(encoding="utf-8")
    assert "Annotate this" in prompt_file.read_text(encoding="utf-8")
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
        assert isolated_env["HOME"] == str(isolated_home)
        assert "CODEX_THREAD_ID" not in isolated_env
        assert "OPENAI_API_KEY" not in isolated_env
        assert (isolated_home / "auth.json").exists()
        config = (isolated_home / "config.toml").read_text(encoding="utf-8")
        assert 'model = "gpt-5.4-mini"' in config
        assert 'model_reasoning_effort = "none"' in config
        assert "[plugins." not in config


def test_isolated_codex_home_does_not_copy_user_tui_state(tmp_path: Path):
    source_home = tmp_path / "source"
    source_home.mkdir()
    (source_home / "config.toml").write_text(
        'model = "gpt-5.4"\n[tui]\nmodel_availability_nux = "gpt-5.4-mini"\n',
        encoding="utf-8",
    )

    with isolated_codex_home(
        {"CODEX_HOME": str(source_home), "ANNOTATION_CODEX_HOME_ROOT": str(tmp_path / "runtime")},
        model="gpt-5.4-mini",
        reasoning_effort="none",
        continuity_handle=None,
    ) as (_isolated_env, isolated_home):
        config = (isolated_home / "config.toml").read_text(encoding="utf-8")
        assert "[tui]" not in config
        assert "model_availability_nux" not in config


def test_isolated_codex_home_can_use_non_tmp_runtime_root(tmp_path: Path):
    source_home = tmp_path / "source"
    runtime_root = tmp_path / "runtime"
    source_home.mkdir()

    with isolated_codex_home(
        {"CODEX_HOME": str(source_home), "ANNOTATION_CODEX_HOME_ROOT": str(runtime_root)},
        model="gpt-5.4-mini",
        reasoning_effort="none",
        continuity_handle=None,
    ) as (isolated_env, isolated_home):
        assert isolated_home.is_relative_to(runtime_root)
        assert isolated_env["CODEX_HOME"] == str(isolated_home)


def test_parse_codex_json_events_extracts_thread_and_final_text():
    result = parse_codex_json_events(
        [
            '{"type":"thread.started","thread_id":"thread-1"}',
            '{"type":"item.completed","item":{"type":"agent_message","text":"final answer"}}',
            '{"type":"turn.completed","usage":{"input_tokens":11,"output_tokens":2}}',
        ],
        provider="local_cli",
        model="gpt-5.4-mini",
    )

    assert result.continuity_handle == "thread-1"
    assert result.final_text == "final answer"
    assert result.usage == {"input_tokens": 11, "output_tokens": 2}


def test_build_claude_command_uses_stream_json_and_stdin_prompt():
    command = build_claude_command(
        binary="claude",
        model="claude-sonnet-4-5",
        permission_mode="dontAsk",
    )

    assert command[:2] == ["claude", "-p"]
    assert "--no-session-persistence" in command
    assert "--output-format" in command
    assert "stream-json" in command
    assert command[command.index("--model") + 1] == "claude-sonnet-4-5"
    assert command[command.index("--permission-mode") + 1] == "dontAsk"
    assert command[-1] == "-"


def test_parse_claude_stream_events_extracts_text_and_usage():
    result = parse_claude_stream_events(
        [
            '{"type":"system","session_id":"session-1"}',
            '{"type":"assistant","message":{"content":[{"type":"text","text":"final answer"}]}}',
            '{"type":"result","usage":{"input_tokens":5,"output_tokens":2}}',
        ],
        provider="local_cli",
        model="claude-sonnet-4-5",
    )

    assert result.continuity_handle == "session-1"
    assert result.final_text == "final answer"
    assert result.usage == {"input_tokens": 5, "output_tokens": 2}
    assert result.raw_response[1]["type"] == "assistant"


def test_local_cli_profile_import_contract():
    profile = LLMProfile(
        name="codex",
        provider="local_cli",
        model="gpt-5.4-mini",
        cli_kind="codex",
        cli_binary="codex",
    )

    assert profile.cli_kind == "codex"
