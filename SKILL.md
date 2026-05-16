---
name: annotation-pipeline-skill
description: Use when an algorithm engineer needs an agent to start, configure, monitor, or operate an LLM-managed annotation project that produces model-training data.
---

# annotation-pipeline-skill

Use this skill when an algorithm engineer wants an agent to start, manage, monitor, and configure an LLM-managed annotation project that produces training data for model development.

## Agent Quickstart

Install with the host agent's skill installer. If the runtime supports a `codex skill install` command, use one of:

```bash
codex skill install annotation-pipeline-skill
codex skill install /path/to/annotation-pipeline-skill
```

Otherwise copy or clone this repository to `$CODEX_HOME/skills/annotation-pipeline-skill`.

When the user asks you to operate an annotation project, create or enter a project directory and run:

```bash
annotation-pipeline init --project-root ./annotation-project
annotation-pipeline doctor --project-root ./annotation-project
annotation-pipeline provider doctor --project-root ./annotation-project
```

Create tasks from JSONL, then inspect the project:

```bash
annotation-pipeline create-tasks \
  --project-root ./annotation-project \
  --source ./input.jsonl \
  --pipeline-id <project-id>

annotation-pipeline runtime status --project-root ./annotation-project
annotation-pipeline coordinator report --project-root ./annotation-project --project-id <project-id>
annotation-pipeline report readiness --project-root ./annotation-project --project-id <project-id>
```

Start the operator API when the user needs the Kanban dashboard:

```bash
annotation-pipeline serve --project-root ./annotation-project --host 127.0.0.1 --port 8509
```

Before handing this skill to another agent, run the clean handoff verification:

```bash
bash scripts/verify_agent_handoff.sh
bash scripts/verify_skill_installability.sh
```

`verify_agent_handoff.sh` copies the skill into a temporary `CODEX_HOME/skills/annotation-pipeline-skill`, runs the CLI from that installed location, starts the API, checks project-scoped dashboard endpoints, records coordinator findings, and exports a training-data package.

## What The Agent Operates

The agent coordinates a local annotation project with durable task state, attempts, artifacts, feedback, QC, optional Human Review, and accepted training-data readiness. The user is usually an algorithm engineer who wants usable labeled data, not just a labeling UI.

Core responsibilities:

- Initialize the local project with `annotation-pipeline init`.
- Ingest raw JSONL tasks with `annotation-pipeline create-tasks`.
- Pull external HTTP tasks with `annotation-pipeline external pull` when `.annotation-pipeline/external_tasks.yaml` is configured.
- Run subagent cycles for LLM-backed annotation work.
- Monitor queues and surface tasks that need Human Review.
- Record QC feedback and annotator/QC discussion so both sides can agree on the final label decision.
- Keep provider settings in project config, not in chat history.

## Configure Subagents

Use `.annotation-pipeline/llm_profiles.yaml` to configure stage subagents.

Supported runtimes:

- OpenAI Responses API through `provider: openai_responses`
- OpenAI-compatible API through `provider: openai_compatible`, with `provider_flavor: deepseek`, `glm`, or `minimax`
- local LLM CLI through `provider: local_cli`, with `cli_kind: codex` or `claude`

Run `annotation-pipeline provider doctor --project-root <project>` after edits.
Run `annotation-pipeline run-cycle --runtime subagent --project-root <project>` to use configured subagents.

Do not put raw secrets in skill docs or committed config. Prefer `api_key_env`.

## Stage Targets

`llm_profiles.yaml` maps stage targets such as `annotation`, `qc`, and `coordinator` to provider profiles. This lets an agent use a local Codex subagent for annotation, OpenAI Responses API for QC, and a different provider later for active-learning coordination.

Common provider choices:

```yaml
targets:
  annotation: local_codex
  qc: deepseek_default
  coordinator: local_codex
```

Use `openai_responses` for OpenAI Responses API, `openai_compatible` with `provider_flavor: deepseek`, `glm`, or `minimax` for OpenAI-compatible APIs, and `local_cli` with `cli_kind: codex` or `claude` for local CLI subagents.

## Human Review

Human Review is optional and sits after QC. When a task is routed there, remind the user that the goal is to decide whether the produced labels are usable for training data, need manual correction, or need a batch/code update rule.

Record the decision with:

```bash
annotation-pipeline human-review decide \
  --project-root ./annotation-project \
  --task-id <task-id> \
  --action request_changes \
  --correction-mode batch_code_update \
  --actor algorithm-engineer \
  --feedback "Apply the updated rule before QC retries."
```

Use `--action accept` for training-ready labels, `--action reject` for unusable tasks, and `--action request_changes` when the annotator should revise the labels with either `manual_annotation` or `batch_code_update`.

## Feedback Agreement

QC feedback is not a one-way order. The annotator and QC agent may exchange opinions, partially agree, and record a final consensus. When all open feedback items have consensus, the task can pass QC and move to Accepted even if the final resolution differs from the original QC suggestion.

## Coordinator Records

When QC, Human Review, or model-training feedback reveals a project-level issue, record it as a coordinator artifact instead of leaving it only in chat:

```bash
annotation-pipeline coordinator rule-update \
  --project-root ./annotation-project \
  --project-id <project-id> \
  --source qc \
  --summary "Boundary examples are missing." \
  --action "Update annotation_rules.yaml and rerun affected tasks."

annotation-pipeline coordinator long-tail-issue \
  --project-root ./annotation-project \
  --project-id <project-id> \
  --category ambiguous_case \
  --summary "This case needs user guidance." \
  --recommended-action "Ask the algorithm engineer for a rule."
```

Use `annotation-pipeline coordinator report --project-root ./annotation-project --project-id <project-id>` before handoff. It summarizes queues, Human Review, feedback, provider diagnostics, outbox status, readiness, rule updates, and long-tail issues.

## Handoff Checklist

Before telling the algorithm engineer that data is ready:

- `provider doctor` passes or the Coordinator report explains provider blockers.
- Runtime status has no stale active runs or retry drain failures.
- Human Review count is zero or the user has explicitly accepted the remaining tasks.
- Open QC feedback has consensus or has been sent back to annotation.
- Coordinator rule updates and long-tail issues are recorded for project-level decisions.
- `export training-data` wrote a manifest and `report readiness` shows the next action.
