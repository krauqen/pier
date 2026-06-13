# pier

Pier is a [Harbor](https://www.harborframework.com/docs/tasks)-compatible framework for evaluating coding agents in sandboxed environments. It reads Harbor's task format and runs trials against it.

```bash
pier run -p path/to/task --agent claude-code --env modal
```

## Why pier

Pier is a fork. We wanted a smaller, more opinionated base to build on. On top of Harbor, Pier adds:

- **Installed agents in air-gapped tasks (`allow_internet = false`).** When the agent runs *inside* the sandbox (Claude Code, Codex, etc.), both the install step and the inference call need the network. Pier lets agents declare their install scripts and a network allowlist, which `docker` and `modal` environments honor when setting up the sandbox.
- **Augmented ATIF v1.7.** Strict one step per API turn, strict reasoning vs agent message separation, no fabricated assistant text, `peak_context_tokens`, `summarization_count`, `llm_call_count`, real upstream timestamps.
- **A chat-style trajectory viewer** (`pier view`).
- **`pier critique run`** for inspecting completed trials with a fresh agent in a fresh sandbox.

## What works today

- **Task format:** Harbor-compatible.
- **Environments:** `docker`, `modal`. Per-agent install specs and network allowlists are honored on both, so installed agents work under `allow_internet = false`.
- **Agents:** `nop`, `oracle`, `claude-code`, `claude-remote`, `codex`, `cursor-cli`, `gemini-cli`, `opencode`, `mini-swe-agent`. All emit augmented ATIF v1.7.
- **Datasets:** local Harbor-format task directories via `-p` / `--path`.
- **CLI:** `pier run`, `pier job`, `pier view`, `pier critique run`, `pier check` / `pier analyze` (vendored from Harbor)

Pier does not currently resolve or download Harbor registry datasets directly.

## Install

```bash
uv tool install datacurve-pier
# or
pip install datacurve-pier
```

## Run

```bash
export ANTHROPIC_API_KEY=...
pier run -p path/to/task --agent claude-code --env modal --env-file .env
```

Run a local dataset, optionally a deterministic random subset:

```bash
pier run -p path/to/dataset --agent claude-code --env modal
pier run -p path/to/dataset --n-tasks 10 --sample-seed 0
```

To use a Harbor registry dataset, download it with Harbor first, then point Pier at it:

```bash
uv run --directory ~/code/harbor harbor download swebenchpro -o ~/code/pier/datasets
uv run pier run -p datasets/swebenchpro --n-tasks 10 --sample-seed 0
```

Trials land under `jobs/<timestamp_or_name>/<trial_id>/`. See `pier run --help`, `pier job --help`, `pier critique --help`, and `pier view --help` for everything else.

## External harness adapters

Custom harnesses can run under Pier as normal agents by exposing a small
`BaseAgent` adapter and loading it with `--agent-import-path`. Keep the harness
logic in your own package; the adapter should install or mount the harness,
write the task instruction somewhere stable, run the harness in the task
environment, and record portable metadata in `trial_dir/lab/harness.json`.

The agent receives `logs_dir`, which is normally `trial_dir/agent`. Use
`write_harness_run_info_from_agent_logs_dir(logs_dir, HarnessRunInfo(...))` to
write the metadata without depending on a benchmark-specific task format.

```python
from pier.agents.base import BaseAgent
from pier.lab.metadata import HarnessRunInfo, write_harness_run_info_from_agent_logs_dir


class BenchmaxxAgent(BaseAgent):
    @staticmethod
    def name() -> str:
        return "benchmaxx"

    def version(self) -> str | None:
        return "0.1.0"

    async def setup(self, environment):
        pass

    async def run(self, instruction, environment, context):
        info = HarnessRunInfo(
            harness_name="benchmaxx-agent",
            harness_version=self.version(),
            command="benchmaxx-agent run --instruction /logs/agent/instruction.md",
            profile="default",
            model=self.model_name,
        )
        write_harness_run_info_from_agent_logs_dir(self.logs_dir, info)
        result = await environment.exec("benchmaxx-agent run ...")
        context.metadata = {"harness": info.model_dump(mode="json")}
        if result.return_code != 0:
            raise RuntimeError("benchmaxx-agent failed")
```

For a runnable toy adapter, see
`examples/agents/benchmaxx_harness.py`. From the repository root:

```bash
PYTHONPATH=$PWD uv run pier run \
  -p examples/tasks/hello-world-no-internet \
  --agent-import-path examples.agents.benchmaxx_harness:DummyBenchmaxxHarnessAgent \
  --job-name dummy-harness \
  --yes
```

That dummy adapter writes `/app/hello.txt`, passes the toy verifier, and writes
`jobs/dummy-harness/<trial_id>/lab/harness.json`.

## Agent runtime configuration

Use `agent.model_name` for trial metadata, `agent.env` for runtime env vars, and agent-specific `kwargs` for tool config. Pier's network allowlist also reads URLs out of those configs (Codex `config_toml`, OpenCode `opencode_config`, mini-swe `config_yaml`), so any base URL you set is allowlisted without code changes.

A few things we've learned plumbing this through Respan and OpenRouter:

**Claude Code** routes through the Anthropic face from Respan. Plan mode is disabled by default (`--disallowedTools EnterPlanMode`).

```yaml
- name: claude-code
  model_name: claude-opus-4-7
  env:
    ANTHROPIC_AUTH_TOKEN: ${RESPAN_API_KEY}
    ANTHROPIC_BASE_URL: https://endpoint.respan.ai/api/anthropic
    ANTHROPIC_CUSTOM_HEADERS: "X-Respan-Route-Provider: vertex_ai"
  kwargs:
    reasoning_effort: max
```

**Claude Remote** (`--agent claude-remote`) runs Claude Code interactively with
[Claude Remote Control](https://docs.claude.com/en/docs/claude-code) enabled, so a
human can attach to (or take over) the live session from claude.ai or the Claude
mobile app while the trial is running. It reuses the entire `claude-code` setup
(install, env, session dir, skills/memory/MCP registration, network allowlist,
trajectory conversion); the only difference is the launch: no `--print`, no
`--output-format=stream-json`, stdin kept open, and `--remote-control` plus a
deterministic `--session-id` passed so Pier can locate the session JSONL
afterwards. Pier blocks until the interactive session exits (quit Claude Code
from the attached client, or let the agent timeout fire), then proceeds with the
usual post-run flow: log download, `trajectory.json` conversion, verification,
and artifact collection. Notes:

- Pier's environment exec path does not allocate a TTY, so the agent allocates a
  pseudo-TTY *inside* the container via the `script` utility (util-linux; on
  Alpine images it is installed automatically, and the run fails loudly if
  `script` is missing). The in-container terminal is headless — interact through
  Remote Control only.
- Interactive mode never emits a final stream-json `result` event, so
  `total_cost_usd` is absent from final metrics; this is expected and not an
  error. Terminal output is teed to `logs/agent/claude-remote.txt`.
- Under `allow_internet = false`, the allowlist is widened beyond the inference
  endpoint (`.anthropic.com`, `claude.ai`) so the Remote Control relay works.
- The agent `timeout_sec` still applies — raise it (or
  `--agent-timeout-multiplier`) to leave time for a human to attach.

**Authentication.** Remote Control requires a *full-scope login* credential.
API keys and long-lived tokens (`claude setup-token` / `CLAUDE_CODE_OAUTH_TOKEN`)
are inference-only and `--remote-control` rejects them. Run `claude auth login`
on a trusted machine, then hand the resulting credential to the trial:

```bash
# Linux: credentials live in the Claude config dir
export CLAUDE_CODE_CREDENTIALS_FILE=~/.claude/.credentials.json
# macOS: credentials live in the Keychain
export CLAUDE_CODE_CREDENTIALS_JSON="$(security find-generic-password -s 'Claude Code-credentials' -w)"
```

(or pass `kwargs.credentials_json` / `kwargs.credentials_file` in the agent
config). Pier writes it to `$CLAUDE_CONFIG_DIR/.credentials.json` (mode 600)
during setup and removes it when the session's shell exits, so the token —
including any refresh token Claude Code rotates into that file — does not
persist into the downloaded job logs. Caveats:

- A hard kill (SIGKILL / container teardown mid-run) skips the cleanup trap and
  can leave the credential in `logs/agent/sessions/`; treat job logs from
  aborted remote runs as sensitive.
- When a credential is injected, env-based auth (`ANTHROPIC_API_KEY`,
  `ANTHROPIC_AUTH_TOKEN`, `CLAUDE_CODE_OAUTH_TOKEN`) is dropped from the
  process env — otherwise it would take precedence and put the session back
  into inference-only mode. Usage bills to the logged-in account's plan, and
  `ANTHROPIC_BASE_URL` gateway setups don't combine with Remote Control.
- Without a credential, the run starts but Claude Code refuses
  `--remote-control`; Pier logs a warning up front.

```yaml
- name: claude-remote
  model_name: claude-opus-4-7
  kwargs:
    session_id: 1f1597e6-0eb9-4763-9bb4-25ed35a3c721  # optional, any UUID; auto-generated when omitted
    credentials_file: ~/.claude/.credentials.json     # or credentials_json: '{"claudeAiOauth": ...}'
```

**Codex** needs a `[model_providers.<name>]` block with `wire_api = "responses"` (not WebSockets, which Codex defaults to and Respan doesn't speak).

```yaml
- name: codex
  model_name: openai/gpt-5.5
  env: { RESPAN_API_KEY: ${RESPAN_API_KEY} }
  kwargs:
    config_toml: |
      model_provider = "respan"
      [model_providers.respan]
      name = "Respan Gateway"
      base_url = "https://endpoint.respan.ai/api/"
      wire_api = "responses"
      env_key = "RESPAN_API_KEY"
    reasoning_effort: xhigh
```

**Gemini CLI**:

```yaml
- name: gemini-cli
  model_name: gemini/gemini-3.1-pro-preview
  env:
    GEMINI_API_KEY: ${RESPAN_API_KEY}
    GOOGLE_GENERATIVE_AI_API_KEY: ${RESPAN_API_KEY}
    GEMINI_API_BASE: https://endpoint.respan.ai/api/google/vertexai/v1beta
    GOOGLE_GEMINI_BASE_URL: https://endpoint.respan.ai/api/google/vertexai/
```

**Cursor CLI** uses the installed `cursor-agent` binary, so it fits the same
inside-the-sandbox path as Claude Code, Codex, Gemini CLI, and OpenCode. Use
`cursor/composer-2.5` for Composer 2.5 trial metadata and pass `CURSOR_API_KEY`
through your env file.

```yaml
- name: cursor-cli
  model_name: cursor/composer-2.5
  env:
    CURSOR_API_KEY: ${CURSOR_API_KEY}
```

**OpenCode** uses `opencode_config` to add unknown providers or override known ones. To redirect Google to Respan, override just `options.baseURL`; to add a fully custom provider, use `opencode_config.provider.<name>` with the npm package, options, and models.

**mini-swe-agent** picks a native adapter from the model-name prefix: `openai/...` → `litellm_response` (OpenAI Responses end-to-end), `openrouter/...` → `openrouter` (BYOK costs from `cost_details.upstream_inference_cost`), everything else → LiteLLM auto.

For Gemini 3 via mini-swe-agent/LiteLLM, omitting `reasoning_effort` uses the Gemini API default high/dynamic thinking level, but it does not request readable thought summaries. Set `kwargs.reasoning_effort: high` explicitly when you want LiteLLM to send `includeThoughts` and preserve returned summaries as reasoning content.

```yaml
- name: mini-swe-agent
  model_name: openrouter/qwen/qwen3.6-plus
  env: { OPENROUTER_API_KEY: ${OPENROUTER_API_KEY} }
  kwargs:
    set_cache_control: default_end
```
