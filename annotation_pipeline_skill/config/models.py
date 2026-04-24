from dataclasses import dataclass, field


@dataclass(frozen=True)
class ProviderConfig:
    provider_id: str
    kind: str
    models: list[str]
    default_model: str
    secret_ref: str | None = None


@dataclass(frozen=True)
class StageRoute:
    stage: str
    primary_provider: str
    primary_model: str
    primary_effort: str
    fallback_provider: str | None = None
    fallback_model: str | None = None
    fallback_effort: str | None = None


@dataclass(frozen=True)
class AnnotatorConfig:
    annotator_id: str
    display_name: str
    modalities: list[str]
    annotation_types: list[str]
    input_artifact_kinds: list[str]
    output_artifact_kinds: list[str]
    provider_route_id: str | None = None
    external_tool_id: str | None = None
    preview_renderer_id: str | None = None
    human_review_policy_id: str | None = None
    fallback_annotator_id: str | None = None
    enabled: bool = True
    metadata: dict = field(default_factory=dict)


@dataclass(frozen=True)
class ProjectConfig:
    providers: dict[str, ProviderConfig]
    stage_routes: dict[str, StageRoute]
    annotators: dict[str, AnnotatorConfig]
    external_tasks: dict
    human_review_required: bool
