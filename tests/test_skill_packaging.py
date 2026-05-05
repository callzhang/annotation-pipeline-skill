from pathlib import Path

import yaml


def _read_skill_parts() -> tuple[dict, str]:
    text = Path("SKILL.md").read_text(encoding="utf-8")
    assert text.startswith("---\n")
    _, frontmatter, body = text.split("---", 2)
    return yaml.safe_load(frontmatter), body


def test_skill_metadata_is_discoverable_for_agent_installation():
    metadata, body = _read_skill_parts()

    assert metadata["name"] == "annotation-pipeline-skill"
    assert metadata["description"].startswith("Use when")
    assert "algorithm engineer" in metadata["description"]
    assert len(metadata["description"]) < 500
    assert "annotation-pipeline init" in body
    assert "annotation-pipeline doctor" in body
    assert "annotation-pipeline serve" in body


def test_skill_docs_explain_subagent_provider_configuration():
    _, text = _read_skill_parts()
    readme = Path("README.md").read_text(encoding="utf-8")

    assert "subagent" in text
    assert "OpenAI Responses API" in text
    assert "local LLM CLI" in text
    assert "llm_profiles.yaml" in text
    assert "annotation-pipeline provider doctor" in readme
    assert "annotation-pipeline run-cycle --runtime subagent" in readme


def test_skill_docs_include_agent_quickstart_and_handoff_gate():
    _, skill = _read_skill_parts()
    readme = Path("README.md").read_text(encoding="utf-8")
    guide = Path("docs/agent-operator-guide.md").read_text(encoding="utf-8")

    assert "Agent Quickstart" in skill
    assert "Handoff Checklist" in skill
    assert "Agent Quickstart" in readme
    assert "Failure Recovery" in readme
    assert "verify_agent_handoff.sh" in readme
    assert "verify_agent_handoff.sh" in guide
    assert "Handoff Checklist" in guide


def test_skill_packaging_has_local_verification_script():
    script = Path("scripts/verify_skill_installability.sh")
    assert script.exists()
    assert script.stat().st_mode & 0o111
    text = script.read_text(encoding="utf-8")

    assert "annotation-pipeline --help" in text
    assert "annotation-pipeline init" in text
    assert "annotation-pipeline doctor" in text
    assert "annotation-pipeline provider doctor" in text


def test_skill_packaging_has_agent_handoff_verification_script():
    script = Path("scripts/verify_agent_handoff.sh")
    assert script.exists()
    assert script.stat().st_mode & 0o111
    text = script.read_text(encoding="utf-8")

    assert "CODEX_HOME" in text
    assert "skills/annotation-pipeline-skill" in text
    assert "annotation-pipeline" in text
    assert "/api/coordinator?project=handoff" in text
    assert "export training-data" in text


def test_active_learning_rl_design_is_documented():
    spec = Path("docs/superpowers/specs/2026-05-05-active-learning-rl-workflow-design.md")
    assert spec.exists()
    text = spec.read_text(encoding="utf-8")

    assert "SelectionCandidate" in text
    assert "ModelFeedbackRecord" in text
    assert "DatasetVersion" in text
    assert "annotation-pipeline learning" in text
    assert "RL path" in text
