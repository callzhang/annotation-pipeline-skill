from pathlib import Path


def test_skill_docs_explain_subagent_provider_configuration():
    text = Path("SKILL.md").read_text(encoding="utf-8")
    readme = Path("README.md").read_text(encoding="utf-8")

    assert "subagent" in text
    assert "OpenAI Responses API" in text
    assert "local LLM CLI" in text
    assert "llm_profiles.yaml" in text
    assert "annotation-pipeline provider doctor" in readme
    assert "annotation-pipeline run-cycle --runtime subagent" in readme
