#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORK_DIR="$(mktemp -d)"

cleanup() {
  rm -rf "$WORK_DIR"
}
trap cleanup EXIT

export UV_CACHE_DIR="${UV_CACHE_DIR:-/tmp/uv-cache}"
export UV_LINK_MODE="${UV_LINK_MODE:-copy}"

cd "$ROOT_DIR"

uv run annotation-pipeline --help >/dev/null
uv run annotation-pipeline init --project-root "$WORK_DIR/project"
uv run annotation-pipeline doctor --project-root "$WORK_DIR/project"
uv run annotation-pipeline provider doctor --project-root "$WORK_DIR/project"
uv run annotation-pipeline provider targets --project-root "$WORK_DIR/project" >/dev/null

test -f "$WORK_DIR/project/.annotation-pipeline/workflow.yaml"
test -f "$WORK_DIR/project/.annotation-pipeline/llm_profiles.yaml"
test -d "$WORK_DIR/project/.annotation-pipeline/tasks"

echo "skill installability verification passed"
