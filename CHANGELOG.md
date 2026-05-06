# Changelog

## v0.1.0 - 2026-05-05

Initial local-first release for an agent-operated annotation pipeline skill.

### Added

- Installable `SKILL.md` for algorithm-engineer annotation projects.
- Python package and `annotation-pipeline` CLI.
- File-backed task store with tasks, attempts, artifacts, audit events, feedback, feedback discussions, outbox records, exports, runtime snapshots, provider config, and Coordinator records.
- JSONL task ingestion, external HTTP task pull, status/submit outbox, readiness reports, and training-data export.
- Configurable provider profiles for OpenAI Responses API, OpenAI-compatible APIs, Codex CLI, and Claude CLI.
- Monitored local runtime for annotation, deterministic validation, QC, retry/heartbeat/capacity reporting, and feedback-driven reruns.
- Optional Human Review after QC with `accept`, `reject`, and `request_changes`.
- Consensus-based annotator/QC feedback discussions.
- React/Vite dashboard with Kanban, Runtime, Readiness, Outbox, Providers, Coordinator, Configuration, Event Log, task details, and image bounding-box preview support.
- Clean agent handoff verification through `scripts/verify_agent_handoff.sh`.
- Real provider smoke scripts for Codex and DeepSeek.
- Memory-ner truth evaluation through `scripts/verify_memory_ner_truth_eval.sh`.
- Memory-ner accepted-state E2E through `scripts/verify_memory_ner_accepted_e2e.sh`.
- Memory-ner dashboard UI acceptance verification through `scripts/verify_memory_ner_ui_acceptance.sh`.
- Active learning/RL workflow design document for the next implementation phase.
- Runtime QC parsing for model responses wrapped in JSON markdown fences.
- Per-task QC sampling policy with `--qc-sample-count`, `--qc-sample-ratio`, and external source QC settings.
- Dashboard editing for task QC policies with audit events.

### Known Limits

- The core is local-first and file-backed; it does not include a distributed scheduler.
- Real multimodal rendering is limited to image bounding-box preview artifacts.
- Active learning/RL workflow support is designed but not implemented in v0.1.0.
- GitHub repository metadata must be configured outside the codebase when GitHub CLI authentication is unavailable.
