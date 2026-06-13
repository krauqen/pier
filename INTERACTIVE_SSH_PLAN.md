# Interactive SSH Mode Plan

## Goal

Add a Pier workflow for benchmark tasks where Pier starts the task container,
exposes a stable SSH entry point for a human or the Codex native app, waits for
an explicit completion signal, then runs the normal verifier against the final
workspace.

Target UX:

```bash
pier jobs interactive -p path/to/deepswe --include-task-name TASK_ID --agent interactive-ssh
```

Pier should:

1. Build/start the same environment it would use for a normal trial.
2. Install the selected agent tooling when requested, especially `codex`.
3. Start an SSH-capable access path into the active container.
4. Print a stable SSH target usable by OpenSSH and the Codex app.
5. Block until the user explicitly runs `pier jobs finish <trial-id>` or creates
   a supported in-container done marker.
6. Run the normal verifier.
7. Save the usual Pier trial result, verifier logs, artifacts, and final summary.

Non-goals for the first implementation:

- Do not make ephemeral containers first-class remote hosts in the Codex app
  without a stable Pier-managed SSH abstraction.
- Do not expose unauthenticated container SSH ports on public interfaces.
- Do not require Pier to control a live Codex app session; Pier only provides
  the remote shell endpoint and lifecycle gate.

## Current Architecture Touchpoints

Relevant existing code:

- `src/pier/cli/jobs.py`
  - Typer command surface for `pier jobs start` and `pier jobs resume`.
  - Builds `JobConfig`, runs environment preflight, then runs `Job`.
- `src/pier/job.py`
  - Creates `TrialConfig` objects and runs them through `TrialQueue`.
  - Already supports lifecycle hooks for trial start, environment start, agent
    start, verification start, end, and cancel.
- `src/pier/trial/trial.py`
  - Trial sequence is already split into environment setup, agent setup, agent
    execution, verification, cleanup, and result writing.
  - The interactive wait should live in the agent execution phase so
    verification remains unchanged.
- `src/pier/trial/execution.py`
  - Creates the agent and environment, starts the environment, runs agent setup,
    and calls `agent.run(...)`.
- `src/pier/environments/docker/docker.py`
  - Docker Compose-based environment startup.
  - Existing mounted log directories: `/logs/agent`, `/logs/verifier`,
    `/logs/artifacts`.
  - Existing compose overrides for build/prebuilt images, mounts, resources,
    no-network, and filtered egress.
- `src/pier/agents/installed/codex.py`
  - Existing Codex install spec can preinstall `codex` into task images.
  - Useful for a Codex-app-backed interactive flow if the container user can run
    `codex app-server`.

The lowest-risk design is to implement the wait as an installed agent named
`interactive-ssh`, then add Docker-only SSH support behind an environment
capability.

## User-Facing UX

### Minimal CLI

Add three commands:

```bash
pier jobs interactive [same selection/config flags as jobs start]
pier jobs attach TRIAL_ID
pier jobs finish TRIAL_ID
```

Optional fourth command:

```bash
pier jobs abort TRIAL_ID
```

`interactive` should behave like `start`, but force or default:

- `n_concurrent_trials = 1` unless explicitly overridden later.
- `environment.delete = false` while waiting.
- agent name defaults to `interactive-ssh`.
- verifier still runs after finish unless `--disable-verification` is passed.

`attach` should print and optionally execute the SSH command for a running
interactive trial.

`finish` should be idempotent. If the trial is already finished, it should print
the verifier/result path and exit successfully.

`abort` should stop the container and mark the trial cancelled or errored
without running the verifier.

### Example Session

```bash
pier jobs interactive \
  -p ~/benchmarks/deepswe \
  --include-task-name astropy__astropy-12345 \
  --agent interactive-ssh \
  --agent-kwarg install_codex=true
```

Pier prints:

```text
Interactive trial ready.

Trial: astropy__astropy-12345__interactive-ssh__...
Workspace: /workspace
SSH: ssh pier-astropy__astropy-12345__...
Codex app: Settings > Connections > add SSH host `pier-astropy__astropy-12345__...`

When done:
  pier jobs finish astropy__astropy-12345__interactive-ssh__...
```

From the Mac:

```bash
ssh pier-astropy__astropy-12345__...
```

Or in Codex app:

1. Open Settings > Connections.
2. Add the generated host alias.
3. Select `/workspace` or the task-specific workspace path.
4. Work normally.
5. Run `pier jobs finish ...` on the Pier host when done.

## Completion Signal

Support two completion signals:

1. Host-side command:

   ```bash
   pier jobs finish TRIAL_ID
   ```

2. In-container marker:

   ```bash
   touch /logs/agent/.pier-finish
   ```

The host-side command should be the documented primary signal because SSH
disconnects are easy to trigger accidentally. The marker exists for automation
and for users who are already inside the container.

Represent state with files under the trial directory:

```text
trial_dir/
  interactive/
    state.json
    ssh_config
    known_hosts
    finish
    abort
```

`state.json` should include:

```json
{
  "schema_version": 1,
  "trial_id": "...",
  "status": "waiting",
  "created_at": "...",
  "updated_at": "...",
  "workspace_path": "/workspace",
  "ssh": {
    "mode": "proxy_command",
    "host_alias": "pier-...",
    "user": "agent",
    "port": null
  },
  "container": {
    "compose_project": "...",
    "service": "main",
    "container_id": "..."
  }
}
```

Allowed statuses:

- `starting`
- `waiting`
- `finishing`
- `verifying`
- `complete`
- `aborted`
- `error`

## SSH Transport Design

Prefer a ProxyCommand-based transport for dynamic containers:

```sshconfig
Host pier-TRIAL_ID
  User agent
  ProxyCommand pier jobs ssh-proxy TRIAL_ID
  StrictHostKeyChecking accept-new
  UserKnownHostsFile ~/.ssh/pier_known_hosts
```

The command:

```bash
pier jobs ssh-proxy TRIAL_ID
```

should:

1. Locate the trial directory from the Pier jobs root or an explicit
   `--jobs-dir`.
2. Resolve the active Docker Compose project/service.
3. Ensure the container is still running.
4. Bridge stdin/stdout to an SSH server inside the container.

There are two implementation options.

### Option A: Published Container SSH Port

Pier writes a compose override that adds an SSH server to `main` and publishes a
host-local port:

```yaml
services:
  main:
    ports:
      - "127.0.0.1:${PIER_SSH_PORT}:22"
```

Then Mac access goes through the WSL host:

```sshconfig
Host pier-TRIAL_ID
  HostName wsl-host
  Port 22231
  User agent
  IdentityFile ~/.ssh/id_ed25519
```

Pros:

- Simple to debug with normal `ssh -p`.
- Works with clients that do not support complex `ProxyCommand` chains.

Cons:

- Requires port allocation and cleanup.
- Requires WSL/Windows/Mac network routing.
- More dangerous if accidentally bound to a public interface.

### Option B: ProxyCommand Over Docker Exec

Do not publish any container port. Instead, the Mac connects to WSL and WSL runs
Pier as a proxy:

```sshconfig
Host pier-TRIAL_ID
  User agent
  ProxyCommand ssh wslbox pier jobs ssh-proxy TRIAL_ID
```

The proxy command can bridge to the container in either of these ways:

- `docker exec -i <container> /usr/sbin/sshd -i`
- `docker exec -i <container> socat - TCP:127.0.0.1:22`

Preferred first implementation: `sshd -i` inside `docker exec -i`.

Pros:

- No exposed port.
- Stable alias can route to whatever container is current.
- Fits Pier's dynamic lifecycle.

Cons:

- Requires careful stdio handling.
- Requires the container image to include an SSH server.
- Host key handling is less conventional unless Pier persists keys.

Use Option B as the default. Keep Option A as a fallback for users whose SSH
clients or network setup cannot support ProxyCommand.

## Container SSH Setup

Add a Docker environment helper that prepares SSH during environment startup
when requested by the agent or config.

Required in-container pieces:

- `openssh-server`
- `authorized_keys` for the selected user
- stable host keys, persisted under `trial_dir/interactive/ssh_host_keys`
- a writable home for the SSH user
- `codex` on `PATH` if the user wants Codex app remote control
- workspace path that matches the path shown to Codex app

For Debian/Ubuntu images:

```bash
apt-get update
apt-get install -y openssh-server
mkdir -p /run/sshd
```

For Alpine:

```bash
apk add --no-cache openssh
ssh-keygen -A
```

Do not assume every task image has a package manager. The implementation should
fail with a clear error if SSH cannot be installed:

```text
Interactive SSH requested, but the environment image does not contain sshd and
Pier could not install openssh-server. Add SSH support to the task image or use
--interactive-transport exec-shell.
```

Authentication:

- Generate a per-trial keypair by default under `trial_dir/interactive/id_ed25519`.
- Add the public key to the in-container user's `~/.ssh/authorized_keys`.
- Let advanced users pass `--interactive-public-key ~/.ssh/id_ed25519.pub`.
- Never print private key material.
- Store generated keys with `0600` permissions.

Host keys:

- Generate or persist host keys under `trial_dir/interactive/host_keys`.
- Mount or copy them into `/etc/ssh`.
- This avoids OpenSSH warnings when the container restarts during the same
  trial.

User:

- Default to the task agent user if configured; otherwise use `agent`.
- Fall back to `root` only with an explicit flag such as
  `--interactive-root-login`.

## Agent Design

Add `InteractiveSshAgent` under `src/pier/agents/installed/interactive_ssh.py`.

Behavior:

- `name()` returns `interactive-ssh`.
- `install_spec()` can optionally include Codex installation by reusing or
  composing with `Codex.install_spec()`.
- `setup()` calls the normal installed-agent setup, then asks the environment to
  enable interactive SSH.
- `run()` prints connection metadata, writes state, then waits for finish/abort.
- `populate_context_post_run()` records minimal context:
  - finish time
  - whether Codex session logs were found
  - optional notes from `/logs/agent/interactive-notes.md`

Suggested kwargs:

- `install_codex: bool = true`
- `workspace_path: str = "/workspace"`
- `finish_marker: str = "/logs/agent/.pier-finish"`
- `abort_marker: str = "/logs/agent/.pier-abort"`
- `poll_interval_sec: float = 2.0`
- `ssh_user: str | None = None`
- `transport: "proxy_command" | "published_port" = "proxy_command"`
- `public_key_path: str | None = None`
- `allow_root_login: bool = false`

The agent should not run a solver itself. Its "agent execution" is the wait
period.

## Environment API Additions

Add optional methods to `BaseEnvironment`:

```python
async def enable_interactive_ssh(self, request: InteractiveSshRequest) -> InteractiveSshInfo:
    raise NotImplementedError

async def interactive_ssh_proxy(self, trial_id: str) -> None:
    raise NotImplementedError
```

Or keep the first pass Docker-only with concrete helpers on `DockerEnvironment`
and feature-detect with `hasattr`. A typed base API is cleaner if Daytona/Modal
might later grow similar support.

Models:

```python
class InteractiveSshRequest(BaseModel):
    user: str
    workspace_path: str
    transport: Literal["proxy_command", "published_port"]
    public_key: str | None
    allow_root_login: bool = False

class InteractiveSshInfo(BaseModel):
    host_alias: str
    user: str
    workspace_path: str
    ssh_config: str
    transport: Literal["proxy_command", "published_port"]
```

Docker implementation details:

- Resolve compose project name using the same sanitized `session_id`.
- Resolve the `main` service container ID with:

  ```bash
  docker compose ... ps -q main
  ```

- Install/configure SSH via `environment.exec(..., user="root")`.
- For ProxyCommand, generate an SSH config that uses:

  ```text
  ProxyCommand ssh wslbox pier jobs ssh-proxy TRIAL_ID
  ```

  The `wslbox` part cannot be inferred reliably on the remote host. Let the user
  configure it globally:

  ```bash
  pier config set interactive.proxy_host wslbox
  ```

  Or print a template with a placeholder.

## Workspace Mounting

Codex app remote projects need a stable project path. Define the interactive
workspace path explicitly.

Options:

1. Use the container's default working directory from the task image.
2. Mount a host workspace at `/workspace`.
3. Use the same path Pier already expects the agent to operate in.

Recommendation for benchmark tasks:

- Add a Pier-owned bind mount for the mutable task workspace at `/workspace`.
- Ensure the verifier runs against the same final filesystem state.
- If the current Pier task model mutates the container filesystem directly, keep
  using that for the first pass and report the actual `pwd` as the workspace.

Before implementing, confirm how DeepSWE tasks place the repository under test
inside the environment. The plan should preserve that benchmark contract rather
than force every task into `/workspace`.

## Lifecycle

Detailed lifecycle for one interactive trial:

1. `Job` creates a normal `TrialConfig`.
2. `Trial._setup_environment()` starts the Docker environment.
3. `Trial._setup_agent()` installs `interactive-ssh` dependencies, optionally
   including Codex.
4. `Trial._execute_agent()` invokes `InteractiveSshAgent.run()`.
5. `InteractiveSshAgent.run()`:
   - calls `enable_interactive_ssh`
   - writes `interactive/state.json`
   - writes `interactive/ssh_config`
   - logs and prints attach instructions
   - polls for finish/abort markers
6. User works through SSH or Codex app.
7. User runs `pier jobs finish TRIAL_ID`.
8. `finish` writes `interactive/finish`.
9. Agent wait loop exits successfully.
10. `Trial._run_verification()` runs unchanged.
11. `Trial._cleanup_and_finalize()` writes `result.json`.
12. If `environment.delete` is true, stop/delete the container only after
    verification.

Abort path:

1. User runs `pier jobs abort TRIAL_ID`.
2. `abort` writes `interactive/abort`.
3. Agent wait loop raises a controlled exception or returns with an aborted
   context.
4. Trial result records `exception_info` with `InteractiveAbort`.
5. Environment cleanup runs.

Timeout path:

- Respect existing agent timeout semantics.
- For interactive mode, default `agent_timeout_sec = None` or a long explicit
  value.
- If timeout fires, record `AgentTimeoutError` and clean up.

## CLI State Discovery

`finish`, `attach`, and `ssh-proxy` need to find a trial by ID.

Implement a small resolver:

```python
def find_trial_dir(trial_id: str, jobs_dir: Path | None = None) -> TrialPaths:
    ...
```

Search order:

1. Explicit `--trial-dir`.
2. Explicit `--job-path`.
3. Explicit `--jobs-dir`, recursively one level into job dirs.
4. Default `JobConfig.jobs_dir`.

If multiple matching trial IDs are found, fail and ask for `--job-path` or
`--trial-dir`.

## Config Surface

First pass can use agent kwargs:

```bash
--agent interactive-ssh
--agent-kwarg install_codex=true
--agent-kwarg transport=proxy_command
--agent-kwarg workspace_path=/workspace
```

Later, add typed config:

```yaml
interactive:
  enabled: true
  transport: proxy_command
  install_codex: true
  workspace_path: /workspace
  ssh_user: agent
  public_key_path: ~/.ssh/id_ed25519.pub
```

Do not put interactive state into `task.toml`; this is a run mode, not a task
definition property.

## Codex App Compatibility

For the Mac Codex app, the generated SSH target must satisfy:

```bash
ssh pier-TRIAL_ID
which codex
codex --version
pwd
```

The Codex app will start `codex app-server` over SSH. Therefore the container
must have:

- `codex` CLI installed and authenticated or able to authenticate.
- `node` available if using npm-installed Codex.
- a shell environment where `codex` is on `PATH` for non-interactive SSH
  commands.
- persistent Codex home if the container is restarted.

Persist Codex state for interactive trials with a mounted directory:

```text
trial_dir/interactive/codex-home -> /home/agent/.codex
```

If Codex auth should be shared from the Pier host, make that explicit and
opt-in. Do not silently mount a user's entire home directory.

## Security

Defaults:

- Prefer ProxyCommand over published ports.
- If publishing a port, bind to `127.0.0.1` only.
- Generate per-trial SSH keys unless a user supplies a public key.
- Disable password login.
- Disable root login unless explicitly requested.
- Keep `environment.delete = false` only while waiting; honor final delete
  policy after verifier.
- Do not expose `codex app-server` directly on a TCP listener.

Suggested sshd config:

```text
PasswordAuthentication no
PubkeyAuthentication yes
PermitRootLogin no
AllowTcpForwarding no
X11Forwarding no
PermitTunnel no
```

The user can still run commands in the container; this mode is inherently
interactive and should be treated as a trusted local development workflow.

## Result and Artifact Capture

Keep normal verifier outputs unchanged.

Add optional interactive artifacts:

```text
trial_dir/interactive/state.json
trial_dir/interactive/ssh_config
trial_dir/agent/interactive-session.json
trial_dir/agent/interactive-notes.md
```

If Codex logs exist in the mounted Codex home, either:

- leave them under `interactive/codex-home`, or
- copy a summary into `trial_dir/agent/codex-sessions`.

Do not make Codex logs required for verification. A user might solve manually
over SSH.

## Testing Plan

Unit tests:

- state file transitions:
  - `starting -> waiting -> finishing`
  - idempotent finish
  - abort marker wins over finish or has deterministic precedence
- trial resolver:
  - finds exact trial
  - handles ambiguous trial IDs
  - handles missing interactive state
- SSH config generation:
  - ProxyCommand mode
  - published port mode
  - paths with spaces are quoted or rejected clearly
- `InteractiveSshAgent` wait loop:
  - exits on host finish marker
  - exits on in-container marker
  - raises on abort
  - respects timeout/cancellation

Integration tests:

- Use a tiny Docker task image with OpenSSH installable.
- Start an interactive trial.
- Connect with `ssh` using generated config and run `pwd`.
- Create `/logs/agent/.pier-finish`.
- Verify the normal verifier runs.
- Confirm `result.json` exists.
- Confirm `--delete` cleanup happens after verification.

Manual test:

1. Run an example task with `interactive-ssh`.
2. Add generated host alias to Mac `~/.ssh/config`.
3. Connect from Mac terminal.
4. Connect from Codex app as an SSH host.
5. Make a change in the workspace.
6. Run `pier jobs finish TRIAL_ID`.
7. Confirm verifier observes the change.

## Implementation Phases

### Phase 1: Local Interactive Gate, No SSH

Purpose: prove the lifecycle.

- Add `InteractiveSshAgent` but initially only print instructions and wait for
  `trial_dir/interactive/finish`.
- Add `pier jobs finish`.
- Add tests for wait loop and state transitions.

This gives a useful manual mode even before the SSH bridge is complete.

### Phase 2: Docker SSH Enablement

- Add Docker helper to install/configure SSH in `main`.
- Generate per-trial keys and SSH config.
- Add `pier jobs attach` and `pier jobs ssh-proxy`.
- Support ProxyCommand transport.
- Add Docker integration test.

### Phase 3: Codex App Polish

- Add `install_codex=true` path that reuses Codex install spec.
- Persist `/home/agent/.codex` under the trial interactive directory.
- Print Codex app-specific setup instructions.
- Validate `which codex` during setup and fail clearly if missing.

### Phase 4: Published Port Fallback

- Add optional local port allocation.
- Write a compose override for SSH port publishing.
- Add cleanup and collision handling.
- Document WSL/Windows forwarding requirements.

### Phase 5: DeepSWE-Specific Ergonomics

- Add a short command alias if DeepSWE is a common path:

  ```bash
  pier deepswe interactive TASK_ID
  ```

- Ensure task filters, attempts, retries, and verifier settings map to normal
  `JobConfig`.
- Store DeepSWE-specific metadata in `state.json` if useful for dashboards.

## Open Questions

- Where exactly do DeepSWE task workspaces live inside current Pier containers?
  The SSH workspace path should match that instead of inventing a new path.
- Should interactive trials allow `n_concurrent_trials > 1` in the first
  release? It is possible, but the UX is cleaner if v1 is one trial at a time.
- Should `finish` run from the Mac directly, or should docs assume the command
  runs on the WSL/Pier host? Direct Mac finish requires Pier and the jobs
  directory to be accessible from the Mac.
- Should generated SSH private keys be used by the Codex app, or should users
  bring their normal public key? Bring-your-own public key is simpler for the
  app; generated keys are safer for fully automated tests.
- How much Codex auth state should Pier mount into containers? The safest
  default is a per-trial Codex home with explicit setup/auth.

## Recommended First PR

Implement Phase 1 plus enough interfaces for Phase 2 without wiring SSH yet:

- Add `InteractiveSshAgent`.
- Add `interactive/state.json` helpers.
- Add `pier jobs finish`.
- Add wait-loop tests.
- Add one documentation example.

That creates a narrow, testable lifecycle change. The SSH transport can then be
added behind the same state model without reworking trial execution.
