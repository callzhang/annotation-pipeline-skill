import json
import sys

from annotation_pipeline_skill.interfaces.api import DashboardApi
from annotation_pipeline_skill.interfaces.cli import main
from annotation_pipeline_skill.store.sqlite_store import SqliteStore


def test_provider_config_api_returns_profiles_targets_and_local_diagnostics(tmp_path, monkeypatch):
    main(["init", "--project-root", str(tmp_path)])
    profiles = tmp_path / ".annotation-pipeline" / "llm_profiles.yaml"
    profiles.write_text(
        f"""
profiles:
  local_python:
    provider: local_cli
    cli_kind: codex
    cli_binary: {sys.executable}
    model: test-model
  missing_api:
    provider: openai_compatible
    provider_flavor: deepseek
    model: deepseek-chat
    api_key_env: DEEPSEEK_API_KEY
    base_url: https://api.deepseek.com
targets:
  annotation: local_python
  qc: missing_api
limits:
  local_cli_global_concurrency: 2
""",
        encoding="utf-8",
    )
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    api = DashboardApi(SqliteStore.open(tmp_path / ".annotation-pipeline"))

    status, _headers, body = api.handle_get("/api/providers")

    payload = json.loads(body.decode("utf-8"))
    assert status == 200
    assert payload["config_valid"] is True
    assert payload["targets"] == {"annotation": "local_python", "qc": "missing_api"}
    assert payload["limits"] == {"local_cli_global_concurrency": 2}
    assert payload["profiles"][0]["name"] == "local_python"
    assert payload["profiles"][1]["provider_flavor"] == "deepseek"
    assert payload["diagnostics"]["local_python"]["status"] == "ok"
    assert payload["diagnostics"]["missing_api"]["status"] == "error"
    assert payload["diagnostics"]["missing_api"]["checks"][0]["id"] == "api_key_env_present"


def test_provider_config_api_saves_structured_provider_configuration(tmp_path):
    main(["init", "--project-root", str(tmp_path)])
    api = DashboardApi(SqliteStore.open(tmp_path / ".annotation-pipeline"))

    status, _headers, body = api.handle_put(
        "/api/providers",
        json.dumps(
            {
                "profiles": [
                    {
                        "name": "local_codex",
                        "provider": "local_cli",
                        "cli_kind": "codex",
                        "cli_binary": "codex",
                        "model": "gpt-5.4-mini",
                        "reasoning_effort": "none",
                        "timeout_seconds": 900,
                    },
                    {
                        "name": "deepseek_default",
                        "provider": "openai_compatible",
                        "provider_flavor": "deepseek",
                        "model": "deepseek-chat",
                        "api_key_env": "DEEPSEEK_API_KEY",
                        "base_url": "https://api.deepseek.com",
                        "timeout_seconds": 300,
                    },
                ],
                "targets": {
                    "annotation": "local_codex",
                    "qc": "deepseek_default",
                    "coordinator": "local_codex",
                },
                "limits": {"local_cli_global_concurrency": 3},
            }
        ).encode("utf-8"),
    )

    payload = json.loads(body.decode("utf-8"))
    saved = (tmp_path / ".annotation-pipeline" / "llm_profiles.yaml").read_text(encoding="utf-8")
    assert status == 200
    assert payload["targets"]["qc"] == "deepseek_default"
    assert "provider_flavor: deepseek" in saved
    assert "local_cli_global_concurrency: 3" in saved
