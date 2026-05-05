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
