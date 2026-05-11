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


def test_load_llm_registry_resolves_five_provider_categories(tmp_path: Path):
    profiles_path = tmp_path / "llm_profiles.yaml"
    profiles_path.write_text(
        """
profiles:
  local_codex:
    provider: local_cli
    cli_kind: codex
    cli_binary: codex
    model: gpt-5.4-mini
  local_claude:
    provider: local_cli
    cli_kind: claude
    cli_binary: claude
    model: claude-sonnet-4-5
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
targets:
  annotation: local_codex
  qc: deepseek_default
  human_review: local_claude
  coordinator: glm_default
  model_assist: minimax_default
""",
        encoding="utf-8",
    )

    registry = load_llm_registry(profiles_path)

    assert registry.resolve("annotation").cli_kind == "codex"
    assert registry.resolve("human_review").cli_kind == "claude"
    assert registry.resolve("qc").provider == "openai_compatible"
    assert registry.resolve("qc").provider_flavor == "deepseek"
    assert registry.resolve("coordinator").provider_flavor == "glm"
    assert registry.resolve("model_assist").provider_flavor == "minimax"


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


def test_load_llm_registry_rejects_missing_openai_compatible_flavor(tmp_path: Path):
    profiles_path = tmp_path / "llm_profiles.yaml"
    profiles_path.write_text(
        """
profiles:
  broken:
    provider: openai_compatible
    model: deepseek-chat
    api_key_env: DEEPSEEK_API_KEY
    base_url: https://api.deepseek.com
targets:
  annotation: broken
""",
        encoding="utf-8",
    )

    with pytest.raises(ProfileValidationError, match="provider_flavor"):
        load_llm_registry(profiles_path)


def test_reasoning_kwargs_only_apply_to_reasoning_models():
    assert reasoning_kwargs("gpt-5.4-mini", "high") == {"reasoning": {"effort": "high"}}
    assert reasoning_kwargs("gemma4-e4b-it", "high") == {}
    assert reasoning_kwargs("gpt-5.4-mini", "none") == {}


def test_api_key_env_accepts_string_value(tmp_path: Path):
    import os
    profiles_path = tmp_path / "llm_profiles.yaml"
    profiles_path.write_text(
        """
profiles:
  ds:
    provider: openai_compatible
    provider_flavor: deepseek
    model: deepseek-chat
    api_key_env: DEEPSEEK_API_KEY
    base_url: https://api.deepseek.com
targets:
  annotation: ds
""",
        encoding="utf-8",
    )
    registry = load_llm_registry(profiles_path)
    profile = registry.resolve("annotation")
    assert profile.resolve_api_key({"DEEPSEEK_API_KEY": "k"}) == "k"


def test_api_key_env_accepts_list_and_falls_back_to_first_non_empty(tmp_path: Path):
    profiles_path = tmp_path / "llm_profiles.yaml"
    profiles_path.write_text(
        """
profiles:
  glm_coding:
    provider: openai_compatible
    provider_flavor: glm
    model: glm-4.6
    api_key_env:
      - GLM_CODING_API_KEY
      - BIGMODEL_MCP_API_KEY
    base_url: https://open.bigmodel.cn/api/coding/paas/v4
targets:
  annotation: glm_coding
""",
        encoding="utf-8",
    )
    registry = load_llm_registry(profiles_path)
    profile = registry.resolve("annotation")

    # First env var present
    assert profile.resolve_api_key({"GLM_CODING_API_KEY": "primary"}) == "primary"
    # First env var missing, second present
    assert profile.resolve_api_key({"BIGMODEL_MCP_API_KEY": "fallback"}) == "fallback"
    # First present but empty string, second present
    assert profile.resolve_api_key({"GLM_CODING_API_KEY": "", "BIGMODEL_MCP_API_KEY": "fallback"}) == "fallback"
    # Both missing
    assert profile.resolve_api_key({}) == ""


def test_api_key_env_list_validation_rejects_empty_list_and_non_strings(tmp_path: Path):
    profiles_path = tmp_path / "llm_profiles.yaml"
    profiles_path.write_text(
        """
profiles:
  bad:
    provider: openai_compatible
    provider_flavor: glm
    model: glm-4.6
    api_key_env: []
    base_url: https://x.example
targets:
  annotation: bad
""",
        encoding="utf-8",
    )
    with pytest.raises(ProfileValidationError):
        load_llm_registry(profiles_path)
