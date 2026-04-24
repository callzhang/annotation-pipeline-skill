from annotation_pipeline_skill.config.models import AnnotatorConfig


class AnnotatorSelectionError(ValueError):
    pass


def select_annotator(
    annotators: dict[str, AnnotatorConfig],
    modality: str,
    annotation_types: list[str],
) -> AnnotatorConfig:
    required_types = set(annotation_types)
    for annotator_id in sorted(annotators):
        annotator = annotators[annotator_id]
        if not annotator.enabled:
            continue
        if modality not in annotator.modalities:
            continue
        if not required_types.issubset(set(annotator.annotation_types)):
            continue
        return annotator
    raise AnnotatorSelectionError(f"no enabled annotator supports modality={modality} annotation_types={annotation_types}")
