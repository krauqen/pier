# Benchmaxx Lab Handoff Plan

## Purpose

Build a Pier-based workflow for developing and evaluating a custom agent
harness against DeepSWE-style Harbor tasks.

This is broader than "interactive SSH". Interactive SSH is only the inspection
primitive. The real product is a tight R&D loop:

1. Launch an exact benchmark task environment interactively.
2. Let a human, Codex app, Claude Remote, or another exploratory agent solve or
   diagnose the task.
3. Record what happened: commands, patch, verifier failures, notes, and result.
4. Convert the useful lesson into benchmark-agnostic harness behavior.
5. Re-run the task non-interactively with the harness.
6. Compare harness versions across seen and unseen tasks.

The goal is personal learning and a credible public writeup, not official
leaderboard submission purity. Still, preserve enough metadata to distinguish
seen/inspected tasks from clean transfer tasks.

## Current Repo Context

Relevant existing files:

- `INTERACTIVE_SSH_PLAN.md`
  - Low-level SSH transport and lifecycle plan.
  - Keep this as the detailed implementation reference for interactive access.
- `src/pier/cli/jobs.py`
  - Existing `pier job` command surface and `start` implementation.
  - Best first place to add `interactive`, `finish`, `attach`, and later lab
    convenience commands if keeping everything under `pier job`.
- `src/pier/cli/main.py`
  - Adds top-level Typer apps. Add a `lab` app here if the workflow deserves a
    separate namespace.
- `src/pier/job.py`
  - Builds `TrialConfig` objects and runs them through the queue.
- `src/pier/trial/trial.py`
  - Owns setup, agent execution, verification, cleanup, and result writing.
- `src/pier/trial/execution.py`
  - Creates the agent and environment, passes `agent.install_spec()` and
    `agent.network_allowlist()` to the environment, and calls `agent.run(...)`.
- `src/pier/agents/base.py`
  - Agent API. A custom harness should be wrapped as a normal `BaseAgent`.
- `src/pier/environments/base.py`
  - Shared environment behavior, resource override warnings, and task config.
- `src/pier/environments/docker/docker.py`
  - First target for interactive SSH support.
- `src/pier/viewer/server.py` and `apps/viewer`
  - Later destination for lab dashboards and case-study browsing.

Important existing design fact:

- Pier is already the right layer to own task loading, sandbox startup,
  verification, logs, artifacts, and result capture.
- The harness should remain a standalone CLI/package. Pier should call it; it
  should not become inseparable from Pier.

## Product Shape

Implement three layers.

### Layer 1: Interactive Inspection

Human/debug workflow for a single task.

Target command:

```bash
pier lab interactive \
  -p ~/benchmarks/deepswe \
  --include-task-name astropy__astropy-12345 \
  --install-codex
```

Acceptable first-pass command if a new `lab` app is too much:

```bash
pier job interactive \
  -p ~/benchmarks/deepswe \
  --include-task-name astropy__astropy-12345 \
  --agent interactive-ssh \
  --agent-kwarg install_codex=true
```

Expected behavior:

- Start the exact task environment used by normal Pier runs.
- Configure interactive access through the plan in `INTERACTIVE_SSH_PLAN.md`.
- Print attach instructions.
- Wait for `pier lab finish TRIAL_ID` or `pier job finish TRIAL_ID`.
- Run the normal verifier against the final workspace.
- Save normal Pier trial outputs plus lab metadata.

### Layer 2: Harness Replay

Non-interactive workflow for the custom harness.

Target command:

```bash
pier lab replay \
  -p ~/benchmarks/deepswe \
  --include-task-name astropy__astropy-12345 \
  --harness-cmd "benchmaxx-agent run --task-dir {task_dir} --workspace {workspace}"
```

The implementation can start simpler by requiring a normal Pier agent config:

```bash
pier run \
  -p ~/benchmarks/deepswe \
  --include-task-name astropy__astropy-12345 \
  --agent-import-path benchmaxx.pier:BenchmaxxAgent \
  --agent-kwarg command="benchmaxx-agent run"
```

Expected behavior:

- Launch the harness in the sandbox as a `BaseAgent`.
- Preserve benchmark constraints.
- Capture stdout/stderr, harness metadata, patch, final verifier result, and
  trajectory if the harness can emit one.
- Store `harness_git_sha`, harness version, config hash, and prompt/profile id.

### Layer 3: Compare And Report

Batch workflow for iteration and public artifacts.

Target commands:

```bash
pier lab compare \
  -p ~/benchmarks/deepswe \
  --tasks-file lab/splits/dev-seen.txt \
  --baseline-agent claude-code \
  --candidate-agent-import-path benchmaxx.pier:BenchmaxxAgent

pier lab report jobs/benchmaxx-* --out reports/benchmaxx.html
```

First implementation can be much simpler:

- Use existing `pier run` for batches.
- Add a small report scanner that reads Pier `result.json` files and lab
  metadata.
- Produce Markdown or JSON before building a viewer page.

## Naming Recommendation

Use `lab` for the higher-level workflow and keep `interactive-ssh` as the
low-level agent/mode.

Rationale:

- `interactive-ssh` is transport/lifecycle infrastructure.
- The user-facing project is "interactive discovery -> harness improvement ->
  non-interactive replay".
- `lab` leaves room for Claude Remote, terminal sharing, transcript capture, and
  dashboards without tying everything to SSH.

Suggested CLI namespace:

```text
pier lab interactive
pier lab attach
pier lab finish
pier lab abort
pier lab replay
pier lab compare
pier lab report
```

If implementation speed matters, alias these to `pier job ...` commands rather
than building all commands at once.

## Data Model

Add lab metadata under each trial directory without changing the task format.

Suggested layout:

```text
trial_dir/
  lab/
    session.json
    notes.md
    commands.log
    patch.diff
    harness.json
    lessons.md
  interactive/
    state.json
    ssh_config
    finish
    abort
```

Keep `interactive/` for SSH lifecycle state from `INTERACTIVE_SSH_PLAN.md`.
Keep `lab/` for research metadata.

`lab/session.json`:

```json
{
  "schema_version": 1,
  "mode": "interactive",
  "task_name": "astropy__astropy-12345",
  "dataset_path": "/home/user/benchmarks/deepswe",
  "task_exposure": "seen_interactive",
  "started_at": "2026-06-13T12:00:00Z",
  "finished_at": null,
  "workspace_path": "/workspace",
  "operator": "human",
  "tools": ["ssh", "codex-app"],
  "followup_replay_job": null,
  "tags": ["deepswe", "debug"]
}
```

`lab/harness.json` for replay trials:

```json
{
  "schema_version": 1,
  "harness_name": "benchmaxx-agent",
  "harness_version": "0.1.0",
  "harness_git_sha": "...",
  "command": "benchmaxx-agent run ...",
  "config_hash": "...",
  "profile": "default",
  "base_agent": "claude-code",
  "model": "..."
}
```

`lab/lessons.md` should be human-written and intentionally terse:

```markdown
# Lesson

Observed failure:

Reusable tactic added:

Why this should transfer:

Harness change:

Replay result:
```

Task exposure labels:

- `unseen`
- `seen_interactive`
- `seen_logs_only`
- `seen_solution`
- `regression`
- `holdout`

These labels are for analysis and storytelling; do not put them in `task.toml`.

## Harness Agent Adapter

Create a thin adapter in the harness project first, not in Pier:

```python
class BenchmaxxAgent(BaseAgent):
    @staticmethod
    def name() -> str:
        return "benchmaxx"

    def version(self) -> str | None:
        return get_harness_version()

    def install_spec(self) -> AgentInstallSpec | None:
        return ...

    def network_allowlist(self) -> NetworkAllowlist:
        return ...

    async def setup(self, environment: BaseEnvironment) -> None:
        ...

    async def run(
        self,
        instruction: str,
        environment: BaseEnvironment,
        context: AgentContext,
    ) -> None:
        ...
```

Load it with `agent.import_path` during development. Only register it as a
built-in Pier agent if the harness becomes part of this repo.

Adapter responsibilities:

- Install or mount the harness.
- Write the task instruction to a known file if the harness wants file input.
- Launch the harness in the task workspace.
- Stream stdout/stderr to `logs/agent`.
- Save harness metadata to `lab/harness.json`.
- Populate `AgentContext` enough for Pier result summaries.
- Optionally convert harness-native traces into Pier augmented ATIF.

Do not hardcode DeepSWE task ids into the harness adapter.

## Interactive Implementation Strategy

Use `INTERACTIVE_SSH_PLAN.md` as the detailed subplan, but reframe it:

- `InteractiveSshAgent` is a manual/debug gate, not the benchmark agent under
  evaluation.
- Its output is an inspected task state plus verifier result.
- It should record lab metadata and support notes/patch capture.

Minimum useful implementation:

1. Add `InteractiveSshAgent`.
2. Add state helpers for `interactive/state.json`.
3. Add `finish` and `abort` commands.
4. Add a no-SSH local wait mode.
5. Add Docker SSH after the lifecycle works.

The no-SSH mode is valuable because it lets another agent implement and test
the Pier lifecycle before fighting SSH details.

## Patch And Transcript Capture

At finish time, capture best-effort artifacts:

- `git diff` from the task workspace if it is a git repo.
- `git status --short`.
- shell transcript if available.
- `commands.log` if using a wrapper shell.
- `interactive-notes.md` from `/logs/agent/interactive-notes.md` if present.

Do not fail the trial if patch capture fails. Verification result is still the
source of truth.

For first pass, patch capture can be implemented by calling
`environment.exec("git diff", cwd=workspace_path)` and writing the result.

## Splits And Reporting

Maintain split files outside task definitions:

```text
lab/splits/
  dev-seen.txt
  dev-unseen.txt
  regression.txt
  holdout.txt
```

Recommended discipline:

- Put every interactively opened task into `dev-seen.txt`.
- Use `regression.txt` for tasks that should stay solved after harness changes.
- Keep `holdout.txt` untouched until a writeup milestone.

Report metrics separately:

- baseline score on dev-seen
- harness score on dev-seen
- baseline score on dev-unseen
- harness score on dev-unseen
- ablation scores by harness change

This makes the public post stronger without requiring leaderboard-pure claims.

## Implementation Phases

### Phase 0: Handoff Setup

Deliverables:

- This file.
- `INTERACTIVE_SSH_PLAN.md` marked as the low-level SSH subplan.
- No code behavior changes.

Acceptance:

- Another agent can start from "Recommended First PR" below without asking for
  project direction.

### Phase 1: Lab Metadata Helpers

Deliverables:

- `src/pier/lab/metadata.py`
- Models for `LabSession`, `HarnessRunInfo`, and exposure labels.
- Helpers to read/write `trial_dir/lab/*.json`.
- Unit tests.

Acceptance:

- Metadata round-trips with stable JSON.
- Existing Pier jobs are unaffected when no lab metadata exists.

### Phase 2: Interactive Lifecycle, No SSH

Deliverables:

- `InteractiveSshAgent` or `InteractiveLabAgent`.
- `pier job finish TRIAL_ID` and `pier job abort TRIAL_ID`.
- Wait loop that exits on host-side finish/abort files.
- Writes `interactive/state.json` and `lab/session.json`.
- Tests for state transitions and idempotent finish.

Acceptance:

- A tiny local task can be started, left waiting, finished, verified, and
  written as a normal Pier result.

### Phase 3: Docker SSH

Deliverables:

- Docker-only `enable_interactive_ssh` helper.
- Generated SSH config.
- `pier job attach TRIAL_ID`.
- `pier job ssh-proxy TRIAL_ID`.
- Per-trial SSH keys and safe defaults.

Acceptance:

- User can SSH into a running Docker task, edit workspace, finish, and verifier
  observes the edit.

### Phase 4: Harness Adapter Path

Deliverables:

- Documented `agent.import_path` recipe for an external harness.
- Optional example adapter under `examples/agents/benchmaxx_agent.py`.
- Harness metadata writing.
- Non-interactive replay command can be plain `pier run` at this phase.

Acceptance:

- A dummy external harness can modify a toy task and pass under Pier.
- Result contains `lab/harness.json`.

### Phase 5: Lab CLI Conveniences

Deliverables:

- `pier lab interactive` alias/wrapper.
- `pier lab replay` wrapper around `pier run` plus harness metadata.
- `pier lab compare` wrapper or documented batch config.
- Split-file support.

Acceptance:

- Common DeepSWE loop can be run without constructing long raw `pier run`
  commands each time.

### Phase 6: Reporting

Deliverables:

- Scanner for Pier job dirs plus lab metadata.
- Markdown or JSON report.
- Optional viewer integration later.

Acceptance:

- Report separates seen/unseen/regression tasks.
- Report links each failed or improved task to trial logs and verifier output.

## Recommended First PR

Do this first:

1. Add `src/pier/lab/metadata.py`.
2. Add models and tests for lab metadata.
3. Add a short README section or doc snippet explaining the benchmaxx loop.
4. Do not implement SSH yet.

Why:

- It creates the data contract that interactive mode, harness replay, and
  reporting will all use.
- It is low risk and easy to test.
- It lets future implementation phases avoid inventing incompatible metadata.

## Recommended Second PR

Implement Phase 2 from `INTERACTIVE_SSH_PLAN.md`, but write both interactive
state and lab metadata.

Do not start with Docker SSH. First prove:

- job starts
- agent waits
- finish command unblocks
- verifier runs
- result is normal
- lab metadata exists

## Open Questions For The Implementing Agent

Answer these by inspecting a real DeepSWE task before coding workspace logic:

- Where does the mutable repo live inside the task container?
- Is the task workspace always a git repo?
- Does the verifier expect changes in-place or copied somewhere?
- Does DeepSWE use canary markers, hidden tests, or special resource limits?
- Which environment is the first target: Docker only, or Modal too?
- Should the public writeup use only local runs, or also a final official
  evaluator run?

Do not block Phase 1 on these questions.

## Guardrails

- Keep task format unchanged.
- Keep harness code portable outside Pier.
- Do not mount private home directories silently.
- Do not publish container SSH ports except as an explicit fallback.
- Do not conflate interactive/manual passes with non-interactive harness passes.
- Keep resource override warnings intact because some benchmark submissions may
  care about them.
- Do not require ATIF support from the first custom harness, but leave a path to
  add trajectory conversion.

## Definition Of Ready For Handoff

The next agent should be able to proceed with this prompt:

```text
Read BENCHMAXX_LAB_PLAN.md and INTERACTIVE_SSH_PLAN.md. Implement Phase 1 from
BENCHMAXX_LAB_PLAN.md. Keep changes scoped, add tests, and do not implement SSH
yet.
```

