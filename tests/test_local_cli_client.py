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


def test_local_cli_profile_import_contract():
    profile = LLMProfile(
        name="codex",
        provider="local_cli",
        model="gpt-5.4-mini",
        cli_kind="codex",
        cli_binary="codex",
    )

    assert profile.cli_kind == "codex"
