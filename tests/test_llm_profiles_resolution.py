from pathlib import Path

from annotation_pipeline_skill.llm.profiles import (
    LLM_PROFILES_FILENAME,
    resolve_llm_profiles_path,
)


def _write(path: Path, text: str = "profiles: {}\ntargets: {}\n") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_returns_workspace_path_when_only_workspace_file_exists(tmp_path):
    workspace = tmp_path / "workspace"
    project_config = tmp_path / "workspace" / "proj" / ".annotation-pipeline"
    project_config.mkdir(parents=True)
    workspace_file = _write(workspace / LLM_PROFILES_FILENAME)

    resolved = resolve_llm_profiles_path(
        workspace_root=workspace,
        project_config_root=project_config,
    )

    assert resolved == workspace_file


def test_returns_project_path_when_only_project_file_exists(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    project_config = tmp_path / "workspace" / "proj" / ".annotation-pipeline"
    project_file = _write(project_config / LLM_PROFILES_FILENAME)

    resolved = resolve_llm_profiles_path(
        workspace_root=workspace,
        project_config_root=project_config,
    )

    assert resolved == project_file


def test_workspace_takes_precedence_when_both_exist(tmp_path):
    workspace = tmp_path / "workspace"
    project_config = tmp_path / "workspace" / "proj" / ".annotation-pipeline"
    workspace_file = _write(workspace / LLM_PROFILES_FILENAME, "profiles: {}\ntargets: {}\n# workspace\n")
    _write(project_config / LLM_PROFILES_FILENAME, "profiles: {}\ntargets: {}\n# project\n")

    resolved = resolve_llm_profiles_path(
        workspace_root=workspace,
        project_config_root=project_config,
    )

    assert resolved == workspace_file


def test_returns_none_when_neither_exists(tmp_path):
    workspace = tmp_path / "workspace"
    project_config = tmp_path / "workspace" / "proj" / ".annotation-pipeline"
    workspace.mkdir()
    project_config.mkdir(parents=True)

    resolved = resolve_llm_profiles_path(
        workspace_root=workspace,
        project_config_root=project_config,
    )

    assert resolved is None


def test_returns_none_when_only_project_config_root_given_but_no_file(tmp_path):
    project_config = tmp_path / "proj" / ".annotation-pipeline"
    project_config.mkdir(parents=True)

    resolved = resolve_llm_profiles_path(project_config_root=project_config)

    assert resolved is None


def test_returns_none_when_only_workspace_root_given_but_no_file(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    resolved = resolve_llm_profiles_path(workspace_root=workspace)

    assert resolved is None
