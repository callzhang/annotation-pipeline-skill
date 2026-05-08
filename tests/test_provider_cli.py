import json

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


def test_provider_targets_exposes_ui_relevant_profile_fields(tmp_path, capsys):
    main(["init", "--project-root", str(tmp_path)])

    assert main(["provider", "targets", "--project-root", str(tmp_path)]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["annotation"] == {
        "base_url": "https://api.deepseek.com",
        "cli_kind": None,
        "model": "deepseek-v4-flash",
        "profile": "deepseek_flash",
        "provider": "openai_compatible",
        "provider_flavor": "deepseek",
    }
    assert payload["qc"] == {
        "base_url": "https://api.deepseek.com",
        "cli_kind": None,
        "model": "deepseek-v4-flash",
        "profile": "deepseek_flash",
        "provider": "openai_compatible",
        "provider_flavor": "deepseek",
    }
