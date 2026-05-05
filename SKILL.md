# annotation-pipeline-skill

Use this skill when an algorithm engineer wants an agent to start, manage, monitor, and configure an LLM-managed annotation project that produces training data for model development.

## What The Agent Operates

The agent coordinates a local annotation project with durable task state, attempts, artifacts, feedback, QC, optional Human Review, and accepted training-data readiness. The user is usually an algorithm engineer who wants usable labeled data, not just a labeling UI.

Core responsibilities:

- Initialize the local project with `annotation-pipeline init`.
- Ingest raw JSONL tasks with `annotation-pipeline create-tasks`.
- Run subagent cycles for LLM-backed annotation work.
- Monitor queues and surface tasks that need Human Review.
- Record QC feedback and annotator/QC discussion so both sides can agree on the final label decision.
- Keep provider settings in project config, not in chat history.

## Configure Subagents

Use `.annotation-pipeline/llm_profiles.yaml` to configure stage subagents.

Supported runtimes:

- OpenAI Responses API through `provider: openai_responses`
- local LLM CLI through `provider: local_cli`, with `cli_kind: codex`

Run `annotation-pipeline provider doctor --project-root <project>` after edits.
Run `annotation-pipeline run-cycle --runtime subagent --project-root <project>` to use configured subagents.

Do not put raw secrets in skill docs or committed config. Prefer `api_key_env`.

## Stage Targets

`llm_profiles.yaml` maps stage targets such as `annotation`, `qc`, and `coordinator` to provider profiles. This lets an agent use a local Codex subagent for annotation, OpenAI Responses API for QC, and a different provider later for active-learning coordination.

## Human Review

Human Review is optional and sits after QC. When a task is routed there, remind the user that the goal is to decide whether the produced labels are usable for training data, need manual correction, or need a batch/code update rule.

## Feedback Agreement

QC feedback is not a one-way order. The annotator and QC agent may exchange opinions, partially agree, and record a final consensus. When all open feedback items have consensus, the task can pass QC and move to Accepted even if the final resolution differs from the original QC suggestion.
