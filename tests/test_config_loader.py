import pytest

from annotation_pipeline_skill.config.loader import ConfigValidationError, load_project_config


def write_config(root):
    config_root = root / ".annotation-pipeline"
    config_root.mkdir()
    (config_root / "providers.yaml").write_text(
        """
providers:
  general_llm:
    kind: openai_compatible
    models: [general-large]
    default_model: general-large
    secret_ref: env:GENERAL_LLM_API_KEY
""",
        encoding="utf-8",
    )
    (config_root / "stage_routes.yaml").write_text(
        """
stage_routes:
  annotation:
    primary_provider: general_llm
    primary_model: general-large
    primary_effort: medium
human_review:
  required: false
""",
        encoding="utf-8",
    )
    (config_root / "annotators.yaml").write_text(
        """
annotators:
  text_annotator:
    display_name: Text Annotator
    modalities: [text]
    annotation_types: [entity_span]
    input_artifact_kinds: [raw_slice]
    output_artifact_kinds: [annotation_result]
    provider_route_id: annotation
    enabled: true
""",
        encoding="utf-8",
    )
    (config_root / "external_tasks.yaml").write_text(
        """
external_tasks:
  default:
    enabled: false
""",
        encoding="utf-8",
    )


def test_load_project_config_reads_yaml_files(tmp_path):
    write_config(tmp_path)

    config = load_project_config(tmp_path)

    assert config.providers["general_llm"].default_model == "general-large"
    assert config.stage_routes["annotation"].primary_provider == "general_llm"
    assert config.annotators["text_annotator"].modalities == ["text"]
    assert config.human_review_required is False


def test_load_project_config_rejects_missing_route_provider(tmp_path):
    write_config(tmp_path)
    route_file = tmp_path / ".annotation-pipeline" / "stage_routes.yaml"
    route_file.write_text(
        """
stage_routes:
  annotation:
    primary_provider: missing_provider
    primary_model: general-large
    primary_effort: medium
""",
        encoding="utf-8",
    )

    with pytest.raises(ConfigValidationError, match="missing_provider"):
        load_project_config(tmp_path)
