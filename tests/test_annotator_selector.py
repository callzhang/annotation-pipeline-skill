import pytest

from annotation_pipeline_skill.config.models import AnnotatorConfig
from annotation_pipeline_skill.services.annotator_selector import AnnotatorSelectionError, select_annotator


def test_select_annotator_matches_modality_and_annotation_types():
    annotators = {
        "text": AnnotatorConfig(
            annotator_id="text",
            display_name="Text",
            modalities=["text"],
            annotation_types=["entity_span"],
            input_artifact_kinds=["raw_slice"],
            output_artifact_kinds=["annotation_result"],
        ),
        "vision": AnnotatorConfig(
            annotator_id="vision",
            display_name="Vision",
            modalities=["image"],
            annotation_types=["bounding_box", "segmentation"],
            input_artifact_kinds=["raw_slice"],
            output_artifact_kinds=["annotation_result", "image_bbox_preview"],
        ),
    }

    selected = select_annotator(
        annotators,
        modality="image",
        annotation_types=["bounding_box"],
    )

    assert selected.annotator_id == "vision"


def test_select_annotator_ignores_disabled_profiles():
    annotators = {
        "disabled": AnnotatorConfig(
            annotator_id="disabled",
            display_name="Disabled",
            modalities=["image"],
            annotation_types=["bounding_box"],
            input_artifact_kinds=["raw_slice"],
            output_artifact_kinds=["annotation_result"],
            enabled=False,
        )
    }

    with pytest.raises(AnnotatorSelectionError):
        select_annotator(annotators, modality="image", annotation_types=["bounding_box"])
