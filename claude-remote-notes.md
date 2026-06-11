# Claude Remote Notes

This repo was used to debug `claude-remote` under Pier's Docker egress proxy.

## Working Variant

The variant that made the Claude Code UI show the remote session used:

- `ClaudeRemote.network_allowlist()` returning the normal Claude Code base domain plus Remote Control domains:
  - `api.anthropic.com`
  - `.anthropic.com`
  - `claude.ai`
  - `.claude.ai`
  - `.claude.com`
- generic Squid policy normalization in `pier.environments.agent_setup.proxy_policy_env()`.
- generated proxy `ALLOWLIST_DOMAINS`:
  - `.anthropic.com,.claude.ai,.claude.com`

The normalization matters because Squid can fail or behave inconsistently with overlapping `dstdomain` ACL entries. For example, `.claude.ai` should win over `claude.ai`, and `.anthropic.com` should win over `api.anthropic.com`.

Observed allowed traffic in the working run:

- `api.anthropic.com:443`
- `downloads.claude.ai:443`

Observed denied traffic, expected and unrelated to Remote Control:

- `raw.githubusercontent.com:443`
- `http-intake.logs.us5.datadoghq.com:443`

## Localized Attempt

The localized attempt only changed `ClaudeRemote.network_allowlist()` and left generic proxy handling unchanged.

It returned only:

- `.anthropic.com`
- `.claude.ai`
- `.claude.com`

This generated the same visible proxy allowlist:

- `.anthropic.com,.claude.ai,.claude.com`

However, the Claude Code UI did not show the Remote Control session in at least one run, while the earlier working variant did. The visible Squid traffic looked healthy, so the difference may be in Claude Code session discovery behavior rather than simple CONNECT allow/deny results.

## Current Direction

Prefer the working variant for now:

- keep `claude-remote` broad at the agent allowlist layer.
- normalize overlapping domains at the Squid policy layer.
- verify every run by checking:
  - generated `docker-compose-egress-proxy.json`
  - `claude --remote-control` process args
  - `/logs/agent/sessions/sessions/*.json`
  - Squid `/tmp/squid_access.log`
