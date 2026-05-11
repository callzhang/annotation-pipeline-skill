from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Mapping

import yaml

from annotation_pipeline_skill.llm.profiles import LLMProfile, ProfileValidationError, load_llm_registry


PROFILE_FIELDS = (
    "provider",
    "provider_flavor",
    "cli_kind",
    "cli_binary",
    "model",
    "api_key_env",
    "base_url",
    "reasoning_effort",
    "permission_mode",
    "timeout_seconds",
    "max_retries",
    "concurrency_limit",
    "no_progress_timeout_seconds",
)


def build_provider_config_snapshot(
    config_root: Path,
    *,
    env: Mapping[str, str] = os.environ,
) -> dict[str, Any]:
    registry = load_llm_registry(config_root / "llm_profiles.yaml")
    profiles = [_profile_to_dict(profile) for profile in registry.profiles.values()]
    diagnostics = {
        profile.name: _profile_diagnostics(profile, env=env)
        for profile in registry.profiles.values()
    }
    return {
        "config_valid": True,
        "profiles": profiles,
        "targets": registry.targets,
        "limits": {"local_cli_global_concurrency": registry.local_cli_global_concurrency},
        "diagnostics": diagnostics,
    }


def save_provider_config(config_root: Path, payload: Mapping[str, Any]) -> dict[str, Any]:
    data = _payload_to_yaml_data(payload)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False, suffix=".yaml") as handle:
        temp_path = Path(handle.name)
        yaml.safe_dump(data, handle, sort_keys=False)
    try:
        load_llm_registry(temp_path)
    finally:
        temp_path.unlink(missing_ok=True)

    target_path = config_root / "llm_profiles.yaml"
    target_path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return build_provider_config_snapshot(config_root)


def _payload_to_yaml_data(payload: Mapping[str, Any]) -> dict[str, Any]:
    raw_profiles = payload.get("profiles")
    raw_targets = payload.get("targets")
    raw_limits = payload.get("limits") or {}
    if not isinstance(raw_profiles, list):
        raise ProfileValidationError("provider config payload missing profiles list")
    if not isinstance(raw_targets, dict):
        raise ProfileValidationError("provider config payload missing targets mapping")
    if not isinstance(raw_limits, dict):
        raise ProfileValidationError("provider config payload limits must be a mapping")

    profiles: dict[str, dict[str, Any]] = {}
    for raw_profile in raw_profiles:
        if not isinstance(raw_profile, dict):
            raise ProfileValidationError("provider profile must be a mapping")
        name = raw_profile.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ProfileValidationError("provider profile missing name")
        profiles[name] = {
            field: raw_profile[field]
            for field in PROFILE_FIELDS
            if raw_profile.get(field) not in (None, "")
        }

    return {
        "profiles": profiles,
        "targets": {str(target): str(profile_name) for target, profile_name in raw_targets.items()},
        "limits": {
            "local_cli_global_concurrency": raw_limits.get("local_cli_global_concurrency"),
        },
    }


def _profile_to_dict(profile: LLMProfile) -> dict[str, Any]:
    return {
        "name": profile.name,
        "provider": profile.provider,
        "provider_flavor": profile.provider_flavor,
        "cli_kind": profile.cli_kind,
        "cli_binary": profile.cli_binary,
        "model": profile.model,
        "api_key_env": profile.api_key_env,
        "base_url": profile.base_url,
        "reasoning_effort": profile.reasoning_effort,
        "permission_mode": profile.permission_mode,
        "timeout_seconds": profile.timeout_seconds,
        "max_retries": profile.max_retries,
        "concurrency_limit": profile.concurrency_limit,
        "no_progress_timeout_seconds": profile.no_progress_timeout_seconds,
    }


def _profile_diagnostics(profile: LLMProfile, *, env: Mapping[str, str]) -> dict[str, Any]:
    checks = []
    if profile.provider == "local_cli":
        found = _cli_binary_found(profile.cli_binary)
        checks.append(
            {
                "id": "cli_binary_found",
                "status": "ok" if found else "error",
                "message": f"{profile.cli_binary} is available" if found else f"{profile.cli_binary} was not found on PATH",
            }
        )
    else:
        key_present = bool(profile.api_key) or bool(profile.api_key_env and profile.resolve_api_key(env))
        env_label: str | None
        if profile.api_key_env is None:
            env_label = None
        elif isinstance(profile.api_key_env, str):
            env_label = profile.api_key_env
        else:
            env_label = ", ".join(profile.api_key_env)
        checks.append(
            {
                "id": "api_key_env_present",
                "status": "ok" if key_present else "error",
                "message": (
                    f"{env_label} is available"
                    if key_present and env_label
                    else "inline api_key configured"
                    if key_present
                    else f"{env_label} is missing"
                ),
            }
        )
        checks.append(
            {
                "id": "api_base_url_configured",
                "status": "ok",
                "message": f"{profile.base_url} configured",
            }
        )

    status = "ok" if all(check["status"] == "ok" for check in checks) else "error"
    return {"status": status, "checks": checks}


def _cli_binary_found(binary: str | None) -> bool:
    if not binary:
        return False
    path = Path(binary)
    if path.is_absolute():
        return path.exists()
    return shutil.which(binary) is not None
