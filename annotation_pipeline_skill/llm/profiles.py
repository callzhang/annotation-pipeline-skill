from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Mapping

import yaml


ProviderName = Literal["openai_responses", "local_cli"]
CliKind = Literal["codex", "claude"]


class ProfileValidationError(ValueError):
    pass


@dataclass(frozen=True)
class LLMProfile:
    name: str
    provider: ProviderName
    model: str
    api_key: str | None = None
    api_key_env: str | None = None
    base_url: str | None = None
    reasoning_effort: str | None = None
    cli_kind: CliKind | None = None
    cli_binary: str | None = None
    permission_mode: str | None = None
    timeout_seconds: int | None = None
    max_retries: int | None = None
    concurrency_limit: int | None = None
    no_progress_timeout_seconds: int | None = None

    def resolve_api_key(self, env: Mapping[str, str] = os.environ) -> str:
        if self.api_key:
            return self.api_key
        if self.api_key_env:
            return env.get(self.api_key_env, "")
        return ""


@dataclass(frozen=True)
class LLMRegistry:
    profiles: dict[str, LLMProfile]
    targets: dict[str, str]
    local_cli_global_concurrency: int | None = None

    def resolve(self, target: str) -> LLMProfile:
        profile_name = self.targets.get(target)
        if not profile_name:
            raise ProfileValidationError(f"LLM target is not configured: {target}")
        profile = self.profiles.get(profile_name)
        if profile is None:
            raise ProfileValidationError(f"LLM target {target} references missing profile {profile_name}")
        return profile


def load_llm_registry(path: Path | str) -> LLMRegistry:
    payload = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ProfileValidationError("LLM profile registry must be a mapping")
    raw_profiles = payload.get("profiles")
    raw_targets = payload.get("targets")
    if not isinstance(raw_profiles, dict):
        raise ProfileValidationError("LLM profile registry missing profiles")
    if not isinstance(raw_targets, dict):
        raise ProfileValidationError("LLM profile registry missing targets")
    profiles = {
        str(name): _parse_profile(str(name), values)
        for name, values in raw_profiles.items()
    }
    targets = {str(target): str(profile_name) for target, profile_name in raw_targets.items()}
    limits = payload.get("limits") or {}
    if not isinstance(limits, dict):
        raise ProfileValidationError("LLM profile limits must be a mapping")
    global_limit = _optional_positive_int(limits.get("local_cli_global_concurrency"), "limits.local_cli_global_concurrency")
    registry = LLMRegistry(profiles=profiles, targets=targets, local_cli_global_concurrency=global_limit)
    for target in targets:
        registry.resolve(target)
    return registry


def reasoning_kwargs(model: str | None, effort: str | None) -> dict:
    normalized_effort = str(effort or "").strip().lower()
    if normalized_effort in {"", "none", "default"}:
        return {}
    if not _is_reasoning_model(model):
        return {}
    return {"reasoning": {"effort": normalized_effort}}


def _parse_profile(name: str, raw: object) -> LLMProfile:
    if not isinstance(raw, dict):
        raise ProfileValidationError(f"LLM profile must be a mapping: {name}")
    provider = raw.get("provider")
    if provider not in {"openai_responses", "local_cli"}:
        raise ProfileValidationError(f"LLM profile {name} has invalid provider")
    model = _required_string(raw.get("model"), f"profile {name} model")
    profile = LLMProfile(
        name=name,
        provider=provider,
        model=model,
        api_key=_optional_string(raw.get("api_key"), f"profile {name} api_key"),
        api_key_env=_optional_string(raw.get("api_key_env"), f"profile {name} api_key_env"),
        base_url=_optional_string(raw.get("base_url"), f"profile {name} base_url"),
        reasoning_effort=_optional_string(raw.get("reasoning_effort"), f"profile {name} reasoning_effort"),
        cli_kind=_optional_cli_kind(raw.get("cli_kind"), f"profile {name} cli_kind"),
        cli_binary=_optional_string(raw.get("cli_binary"), f"profile {name} cli_binary"),
        permission_mode=_optional_string(raw.get("permission_mode"), f"profile {name} permission_mode"),
        timeout_seconds=_optional_positive_int(raw.get("timeout_seconds"), f"profile {name} timeout_seconds"),
        max_retries=_optional_non_negative_int(raw.get("max_retries"), f"profile {name} max_retries"),
        concurrency_limit=_optional_positive_int(raw.get("concurrency_limit"), f"profile {name} concurrency_limit"),
        no_progress_timeout_seconds=_optional_positive_int(
            raw.get("no_progress_timeout_seconds"),
            f"profile {name} no_progress_timeout_seconds",
        ),
    )
    _validate_profile(profile)
    return profile


def _validate_profile(profile: LLMProfile) -> None:
    if profile.provider == "openai_responses":
        if not profile.base_url:
            raise ProfileValidationError(f"LLM profile {profile.name} missing base_url")
        if not (profile.api_key or profile.api_key_env):
            raise ProfileValidationError(f"LLM profile {profile.name} missing api_key or api_key_env")
        return
    if profile.provider == "local_cli":
        if not profile.cli_kind:
            raise ProfileValidationError(f"LLM profile {profile.name} missing cli_kind")
        if not profile.cli_binary:
            raise ProfileValidationError(f"LLM profile {profile.name} missing cli_binary")
        return


def _is_reasoning_model(model: str | None) -> bool:
    normalized = str(model or "")
    return normalized.startswith("gpt-5") or normalized.startswith("o1") or normalized.startswith("o3")


def _required_string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ProfileValidationError(f"invalid {label}")
    return value


def _optional_string(value: object, label: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ProfileValidationError(f"invalid {label}")
    return value


def _optional_cli_kind(value: object, label: str) -> CliKind | None:
    if value is None:
        return None
    if value not in {"codex", "claude"}:
        raise ProfileValidationError(f"invalid {label}")
    return value


def _optional_positive_int(value: object, label: str) -> int | None:
    if value is None:
        return None
    try:
        parsed = int(value)
    except Exception as exc:
        raise ProfileValidationError(f"invalid {label}") from exc
    if parsed <= 0:
        raise ProfileValidationError(f"invalid {label}")
    return parsed


def _optional_non_negative_int(value: object, label: str) -> int | None:
    if value is None:
        return None
    try:
        parsed = int(value)
    except Exception as exc:
        raise ProfileValidationError(f"invalid {label}") from exc
    if parsed < 0:
        raise ProfileValidationError(f"invalid {label}")
    return parsed
