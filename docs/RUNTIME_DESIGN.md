# Runtime Design

Current behavior of the annotation pipeline runtime (post-2026-05-16). This doc
focuses on the decision rules — state transitions, retry policies, error
recovery. For framework architecture see `TECHNICAL_ARCHITECTURE.md`; for
product context see `PRODUCT_DESIGN.md`.

## System overview

The pipeline processes annotation tasks through a three-agent loop:

```
PENDING ─┬─ (prelabel shortcut) ────────────────────────► QC
         └─► ANNOTATING ─► (validation) ─► QC ─► ACCEPTED
                          │                  │
                          └─► PENDING ◄──────┘  (retry)
                                       │
                         (round_count ≥ max_qc_rounds)
                                       │
                                       ▼
                                  ARBITRATING ─┬─► ACCEPTED
                                               ├─► ARBITRATING  (mechanical retry)
                                               └─► HUMAN_REVIEW
```

Three LLM roles:

| Role | Default profile | Job |
|---|---|---|
| annotator | `minimax_2.7` | Produce structured annotations from raw input |
| QC | `deepseek_flash` | Find defects in the annotator's output, file feedback |
| arbiter | `codex_5.5_arbiter` (codex CLI, gpt-5.5) | Adjudicate disputes, produce a correct fix |

A `fallback` target (`codex_5.4_mini`) sits behind the annotator: any
rate-limit error on the primary provider transparently retries via fallback.

## State machine

`TaskStatus` values used in the runtime:

| Status | Meaning |
|---|---|
| `pending` | Ready for a worker to claim |
| `annotating` | LLM annotation in flight |
| `qc` | Validation passed, QC running (or resuming) |
| `arbitrating` | Arbiter in flight, OR mechanical retry waiting for re-pickup |
| `accepted` | Terminal — annotation passed all checks |
| `human_review` | Terminal-ish — arbiter genuinely uncertain or retry cap hit |
| `rejected` | Reserved for manual operator action |

State is owned by `SqliteStore`; every transition writes an `audit_events` row
with `previous_status`, `next_status`, `reason`, `stage`, `metadata`.

## Pipeline rounds

A round = one annotator pass + one QC pass. Tasks loop until:
1. QC accepts → ACCEPTED, **or**
2. `round_count ≥ max_qc_rounds` (default 3) → arbiter invoked

`round_count` is the count of *open* QC/validation feedbacks (those not closed
by consensus). Consensus-closed feedbacks don't count — they've been resolved.

### Prelabel shortcut

Tasks imported with `metadata.prelabeled=true` and `current_attempt=0` skip the
LLM annotation step on their first round (`subagent_cycle.py:131-166`). The
runtime treats the imported `annotation_result` artifact as if a model produced
it, and goes straight to QC. After the first QC reject, the shortcut no longer
applies and the annotator runs normally.

## Verbatim guard

All output spans (entities, json_structures phrases) must be verbatim substrings
of `input.text`. This applies uniformly across:
- annotator output (rejected → validation feedback)
- arbiter's `corrected_annotation` (rejected before persisting as final)
- operator-submitted human-review correction (rejected with `SchemaValidationError`)

Implementation: `core/schema_validation.py:find_verbatim_violations`. Without
this guard, ~11% of accepted tasks contained hallucinated spans in a 5% audit.

## Arbiter

Invoked at `round_count ≥ max_qc_rounds` or when an operator drags a card from
HR/REJECTED into the Arbitration column.

Input: input task + latest annotation + all open feedbacks (validation + QC) +
annotator's discussion replies (if any).

Output: one verdict per feedback + optional `corrected_annotation`.

```json
{
  "verdicts": [
    {"feedback_id": "...", "verdict": "annotator|qc|neither",
     "confidence": "certain|confident|tentative|unsure",
     "reasoning": "..."}
  ],
  "corrected_annotation": {"rows": [...]} | null
}
```

`verdict`:
- `annotator` — QC's complaint is wrong, current annotation stands
- `qc` — current annotation is wrong as QC said
- `neither` — both partially wrong, here's what's right

`confidence` is a 4-label verbal scale (no numbers). Numeric confidence was
deprecated after empirical calibration showed it was noise (all buckets
produced identical correctness rates).

### Internal retry loop

Inside `_arbitrate_and_apply`, up to `arbiter_verbatim_retries` (default 2)
retries when:
- arbiter ruled `qc`/`neither` at high confidence but emitted `null` for
  `corrected_annotation` — gives explicit "you forgot the JSON" feedback
- arbiter's `corrected_annotation` contains a non-verbatim span — gives the
  specific bad span and demands a verbatim re-emission

After retries exhaust, the bad correction is dropped (`corrected_annotation`
cleared to null) and the loop ends.

### Outcome counters

The arbiter outcome dict carries four counters:

| Counter | Bumped when |
|---|---|
| `closed` | verdict=`annotator`, label ∈ {certain, confident} |
| `fixed` | verdict ∈ {`qc`, `neither`}, label ∈ {certain, confident}, AND `corrected_annotation` was non-null at loop exit |
| `unresolved` | label ∈ {tentative, unsure, None} |
| `mechanical_fail` | verdict ∈ {`qc`, `neither`} at confident/certain label BUT `corrected_annotation` is null (retry-exhausted); OR unknown verdict value |

These drive the post-arbiter decision below.

## HR routing rules

The runtime sends a task to HUMAN_REVIEW only on **genuine arbiter
uncertainty**. All mechanical failures (codex subprocess error, missing fix,
verbatim violation, JSON parse fail) loop back through the arbiter.

```
_terminal_from_arbiter:
  unresolved > 0       → None  (caller decides: HR or retry)
  fixed > 0 + valid    → ACCEPTED  (write correction, accept)
  closed > 0           → ACCEPTED  (annotator's annotation stands)
  else                 → None  (mechanical signal)

caller (validation / qc / rearbitration paths):
  terminal is not None              → ACCEPTED (already transitioned)
  terminal is None, unresolved > 0  → HUMAN_REVIEW
  terminal is None, unresolved == 0 → stay in ARBITRATING (mechanical retry)
                                       └─► after N=3 retries → HUMAN_REVIEW
```

### Mechanical retry cap

`SubagentRuntime.ARBITER_MECHANICAL_RETRY_CAP = 3`. Each mechanical fail
increments `task.metadata.arbiter_mechanical_retries`. When it reaches 3, the
task is forced to HUMAN_REVIEW with reason `arbiter exhausted N mechanical
retries without an actionable verdict`. Counter is persistent — survives
restarts.

Rationale: codex failures are usually transient (one-off subprocess error,
model bad output) and fresh subprocess retries succeed. But pathological tasks
shouldn't loop forever.

### Why mechanical retries stay in ARBITRATING, not PENDING

When the arbiter mechanically fails, the *annotation* didn't change — only the
*judgment* failed. Sending back to PENDING would re-run the annotator
needlessly. Keeping it in ARBITRATING lets the next worker pickup run the
arbiter again on the same annotation. The scheduler's claim logic picks up
unleased ARBITRATING tasks automatically.

## Worker pool

`LocalRuntimeScheduler` runs N async workers (`max_concurrent_tasks`, default
24). Each worker:

```
loop:
  task, lease, run = try_claim_task(stage_target)
  try:
    await wait_for(runtime.run_task_async(...), timeout=worker_task_timeout)
  except TimeoutError | Exception:
    pass
  finally:
    delete_lease(); delete_active_run()
    if task.status == ANNOTATING:
      reset to PENDING  # "worker bailed mid-annotation"
```

### Worker-bail reset

If a worker's LLM call raises (rate-limit, network error, parse fail), the
finally block resets the task from ANNOTATING back to PENDING. Without this,
the smart-resume path would see ANNOTATING-without-lease and bounce the task
back to PENDING anyway — but via a slower, noisier path (700 spurious audit
events/min observed before this fix). The explicit reset closes the loop.

### Smart resume

On scheduler init and during normal claim cycles, `_try_claim_task` inspects
the task state:

| Status seen | Has annotation_result? | Action |
|---|---|---|
| ANNOTATING | yes | Promote to QC with `runtime_next_stage=qc` (skip re-annotation) |
| ANNOTATING | no | Reset to PENDING |
| QC with `runtime_next_stage=qc` | — | Claim, resume from QC |
| ARBITRATING (no lease) | — | Claim, run `_run_rearbitration` |
| PENDING | — | Claim, full pipeline |

This preserves mid-flight work across restarts.

### Zombie recovery on init

`_recover_arbitrating_zombies` runs at scheduler init. Any ARBITRATING task
without a lease at that point is routed to HUMAN_REVIEW (the arbiter already
had a turn pre-restart; auto-rearbitrating without operator intent isn't
useful). Note this means: stopping the runtime mid-arbitration loses the
mechanical-retry counter context — those tasks land in HR. Acceptable
trade-off for now; operator can drag them back to Arbitration.

## Provider fallback

`SubagentRuntime._generate_async` wraps the target client:

```python
try:
    return await self._call_client(target, request)
except Exception as exc:
    if target == "fallback" or not _is_rate_limited(exc):
        raise
    return await self._call_client("fallback", request)
```

`_is_rate_limited` recognizes `openai.RateLimitError`, `status_code == 429`,
and string matches for "rate limit"/"429"/"too many requests" (covers local-CLI
clients that just raise with a message).

Try-first semantics — every call hits the primary; only retries via fallback
on 429. No circuit breaker / recovery window. Trade-off: simple, auto-recovers
when primary's rate limit eases.

## QC stage

`_run_qc_stage` calls QC with the latest annotation, the open feedbacks (with
annotator discussion replies if any), and the project's QC policy. The QC
agent returns:

```json
{"passed": true | false,
 "message": "...",
 "failures": [
   {"feedback_id": "..." | null,
    "category": "missing_phrase|...",
    "message": "...",
    "confidence": "certain|confident|tentative|unsure",
    "target": {...}}
 ],
 "consensus_acknowledgements": ["feedback_id", ...]}
```

`consensus_acknowledgements` closes existing feedback by mutual agreement
(QC concedes after seeing the annotator's rebuttal). `failures` opens new
feedback records.

QC fast-track on resume: if a worker restarts mid-QC, the new worker re-runs
QC only (skipping the LLM annotator) by reading the existing
`annotation_result` artifact. Triggered via `metadata.runtime_next_stage="qc"`.

## Storage

`SqliteStore` at `<project_root>/.annotation-pipeline/db.sqlite` (WAL mode,
thread-local connections). Tables:

| Table | Purpose |
|---|---|
| `tasks` | Current state per task (single row per task) |
| `attempts` | One row per LLM attempt (annotation, qc, arbitration) |
| `artifact_refs` | Pointer to artifacts on disk |
| `audit_events` | Append-only log of every state transition |
| `feedback_records` | QC/validation findings |
| `feedback_discussions` | Annotator rebuttals + arbiter verdicts |
| `runtime_leases` | Worker → task lease (TTL-based) |
| `active_runs` | Currently-running tasks (lease + worker_id) |
| `documents`, `document_versions` | Annotation guidelines |

Artifacts (annotation JSON, QC JSON, arbiter correction JSON, human-review
correction JSON) live on disk under `artifact_payloads/<task_id>/<file>.json`.
The DB stores only the path.

## Configuration

Per project (`projects/<name>/.annotation-pipeline/`):
- `workflow.yaml` — runtime config: `max_concurrent_tasks`, `max_qc_rounds`,
  `worker_task_timeout_seconds`, sampling policies
- `annotators.yaml` — annotator profile definitions (which model, which
  modalities, fallback)
- `external_tasks.yaml` — external task system bindings
- `callbacks.yaml` — webhook/outbox config
- `output_schema.json` — JSON Schema validated against every accepted
  annotation

Workspace-level (`projects/llm_profiles.yaml`):
- `profiles:` — named LLM endpoints (provider, model, api_key, base_url)
- `targets:` — role → profile mapping (`annotation`, `qc`, `arbiter`,
  `fallback`, `coordinator`)
- `limits.local_cli_global_concurrency` — caps concurrent codex subprocesses

Profiles loaded once at scheduler startup; YAML changes require restart.

## Local CLI invocation

For `provider: local_cli` profiles (codex, claude), the runtime spawns
isolated subprocesses with:
- `--ignore-user-config` — no user-level config interference
- `--ignore-rules` — skip user-installed rule files (skills, AGENT.md)
- `--ephemeral` — no thread persistence
- `--disable apps --disable plugins` — no external integrations
- `--config enabled_tools=[]` — suppress tool use (arbiter is pure JSON, no
  bash/read needed)
- `--dangerously-bypass-approvals-and-sandbox --skip-git-repo-check` —
  non-interactive

A fresh isolated `CODEX_HOME` is created per call (auth + config copied),
preventing cross-contamination between concurrent codex invocations.

## Web UI surfaces

| Surface | What it shows |
|---|---|
| Kanban | One column per task status. Each card: task_id, annotator_model badge, QC model badge, attempt count. Drag-and-drop: HR/REJECTED → Arbitration triggers rearbitration. |
| Task drawer | Per-task details: events timeline, feedback discussions, artifacts (annotation/QC/arbiter/HR payloads), HR-reason banner |
| Runtime panel | Active runs, queue depths, per-stage throughput (events/min) |

HR-reason banner shows:
- The transition reason quote (always)
- A detail paragraph for `auto_escalated` cases:
  - `!arbiter_ran` → "Arbiter was skipped..."
  - `arbiter_ran && unresolved > 0` → "Arbiter ran but N disputes remained..."
  - generic auto-escalated → "Auto-escalated after the retry loop exhausted."
- Round count / max_qc_rounds / arbiter run flag — when present in metadata

## CLI

```bash
# Import tasks from JSONL
annotation-pipeline import --project-root <p> --input <jsonl>
annotation-pipeline import --start-batch-offset N  # avoid id collisions

# Run the runtime (long-running)
annotation-pipeline runtime run --project-root <p>

# One-shot processing of N pending tasks
annotation-pipeline runtime once --project-root <p> --limit N

# Export accepted annotations
annotation-pipeline export --project-root <p> --output <dir>

# Audit / repair
scripts/audit_verbatim_accepted.py  # find ACCEPTED tasks with verbatim
                                     # violations, route to ARBITRATING
```

## Error model

All in-flight errors are non-fatal. Three layers absorb them:

1. **`SubagentRuntime`** — every LLM call wrapped; on exception, returns
   early without persisting an attempt. State is left in whatever in-flight
   status was reached.
2. **Worker `finally`** — releases lease + active_run, resets ANNOTATING to
   PENDING. Tight loop on the same task is impossible because the next claim
   sees a clean PENDING.
3. **Smart resume / zombie recovery** — on next claim cycle (or scheduler
   restart), tasks that didn't reach a terminal state get appropriate
   recovery treatment.

The only paths to HR are:
- arbiter `unresolved > 0` (genuine uncertainty)
- arbiter mechanical-retry cap exceeded (3 failed pickups)
- zombie recovery at scheduler init (ARBITRATING + no lease)
- operator explicitly chose Reject in HR drawer (`HumanReviewService`)
