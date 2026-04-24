from __future__ import annotations

from pathlib import Path

import yaml

from annotation_pipeline_skill.config.models import (
    AnnotatorConfig,
    ProjectConfig,
    ProviderConfig,
    StageRoute,
)


class ConfigValidationError(ValueError):
    pass


def load_project_config(project_root: Path | str) -> ProjectConfig:
    config_root = Path(project_root) / ".annotation-pipeline"
    providers_data = _read_yaml(config_root / "providers.yaml")
    routes_data = _read_yaml(config_root / "stage_routes.yaml")
    annotators_data = _read_yaml(config_root / "annotators.yaml")
    external_data = _read_yaml(config_root / "external_tasks.yaml")

    providers = _load_providers(providers_data.get("providers", {}))
    stage_routes = _load_stage_routes(routes_data.get("stage_routes", {}))
    annotators = _load_annotators(annotators_data.get("annotators", {}))
    human_review_required = bool(routes_data.get("human_review", {}).get("required", False))
    config = ProjectConfig(
        providers=providers,
        stage_routes=stage_routes,
        annotators=annotators,
        external_tasks=external_data.get("external_tasks", {}),
        human_review_required=human_review_required,
    )
    validate_project_config(config)
    return config


def validate_project_config(config: ProjectConfig) -> None:
    for stage, route in config.stage_routes.items():
        if route.primary_provider not in config.providers:
            raise ConfigValidationError(f"stage route {stage} references missing provider {route.primary_provider}")
        if route.primary_model not in config.providers[route.primary_provider].models:
            raise ConfigValidationError(
                f"stage route {stage} model {route.primary_model} is not available on provider {route.primary_provider}"
            )
        if route.fallback_provider:
            if route.fallback_provider not in config.providers:
                raise ConfigValidationError(f"stage route {stage} references missing provider {route.fallback_provider}")
            if route.fallback_model and route.fallback_model not in config.providers[route.fallback_provider].models:
                raise ConfigValidationError(
                    f"stage route {stage} model {route.fallback_model} is not available on provider {route.fallback_provider}"
                )

    for annotator_id, annotator in config.annotators.items():
        if annotator.provider_route_id and annotator.provider_route_id not in config.stage_routes:
            raise ConfigValidationError(
                f"annotator {annotator_id} references missing stage route {annotator.provider_route_id}"
            )


def _read_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def _load_providers(data: dict) -> dict[str, ProviderConfig]:
    return {
        provider_id: ProviderConfig(
            provider_id=provider_id,
            kind=values["kind"],
            models=list(values.get("models", [])),
            default_model=values["default_model"],
            secret_ref=values.get("secret_ref"),
        )
        for provider_id, values in data.items()
    }


def _load_stage_routes(data: dict) -> dict[str, StageRoute]:
    return {
        stage: StageRoute(
            stage=stage,
            primary_provider=values["primary_provider"],
            primary_model=values["primary_model"],
            primary_effort=values.get("primary_effort", "medium"),
            fallback_provider=values.get("fallback_provider"),
            fallback_model=values.get("fallback_model"),
            fallback_effort=values.get("fallback_effort"),
        )
        for stage, values in data.items()
    }


def _load_annotators(data: dict) -> dict[str, AnnotatorConfig]:
    return {
        annotator_id: AnnotatorConfig(
            annotator_id=annotator_id,
            display_name=values.get("display_name", annotator_id),
            modalities=list(values.get("modalities", [])),
            annotation_types=list(values.get("annotation_types", [])),
            input_artifact_kinds=list(values.get("input_artifact_kinds", [])),
            output_artifact_kinds=list(values.get("output_artifact_kinds", [])),
            provider_route_id=values.get("provider_route_id"),
            external_tool_id=values.get("external_tool_id"),
            preview_renderer_id=values.get("preview_renderer_id"),
            human_review_policy_id=values.get("human_review_policy_id"),
            fallback_annotator_id=values.get("fallback_annotator_id"),
            enabled=bool(values.get("enabled", True)),
            metadata=dict(values.get("metadata", {})),
        )
        for annotator_id, values in data.items()
    }
