import json
import re
import shlex
import uuid
from pathlib import Path
from typing import ClassVar

from pier.agents.installed.claude_code import ClaudeCode
from pier.models.agent.install import AgentInstallSpec, InstallStep
from pier.models.agent.name import AgentName
from pier.models.agent.network import NetworkAllowlist


def _squid_policy_domains(allowlist: NetworkAllowlist) -> list[str]:
    """Return a Squid dstdomain-compatible allowlist for Claude Remote."""
    domains = set(allowlist.domains)
    suffixes = {domain for domain in domains if domain.startswith(".")}

    filtered: list[str] = []
    for domain in sorted(domains):
        if domain.startswith("."):
            apex = domain[1:]
            if any(
                other != domain and apex.endswith(other)
                for other in suffixes
            ):
                continue
        elif any(
            domain == suffix[1:] or domain.endswith(suffix)
            for suffix in suffixes
        ):
            continue
        filtered.append(domain)
    return filtered


class ClaudeRemote(ClaudeCode):
    """Claude Code in interactive mode with Claude Remote Control enabled.

    Unlike :class:`ClaudeCode`, this agent does not run ``claude --print``
    with stream-json output. It launches the interactive TUI with
    ``--remote-control`` so a human can attach to (and take over) the live
    session from claude.ai or the Claude mobile app while the run is active.
    Pier blocks until the interactive process exits, then continues with its
    normal post-run flow: log download, session-JSONL-to-trajectory
    conversion, verification, and artifact collection.

    TTY handling: Pier's environment exec path (e.g. ``docker compose exec``
    spawned with stdin/stdout pipes on the host) does not allocate a TTY, but
    Claude Code's interactive UI requires one. We therefore allocate a
    pseudo-TTY *inside* the container with the ``script`` utility
    (util-linux), and keep the session's stdin open via a FIFO held open
    read-write so the TUI never sees EOF. The in-container terminal is not
    connected to a human; all interaction happens through Remote Control.

    Interactive mode never emits a final ``{"type": "result"}`` event, so
    ``total_cost_usd`` is unavailable; trajectory conversion already treats
    it as optional.

    Authentication: Remote Control requires a full-scope login credential.
    Long-lived tokens (``claude setup-token`` / ``CLAUDE_CODE_OAUTH_TOKEN``)
    and API keys are inference-only and are rejected by ``--remote-control``.
    Run ``claude auth login`` on a trusted machine and pass the resulting
    credential via the ``credentials_json`` / ``credentials_file`` kwargs (or
    the ``CLAUDE_CODE_CREDENTIALS_JSON`` / ``CLAUDE_CODE_CREDENTIALS_FILE``
    env vars). It is written to ``$CLAUDE_CONFIG_DIR/.credentials.json``
    during setup and removed when the session's shell exits, so the
    full-scope token does not persist into the downloaded job logs (a hard
    kill of the container can still leave it behind). When a credential is
    injected, env-based auth (``ANTHROPIC_API_KEY``, ``ANTHROPIC_AUTH_TOKEN``,
    ``CLAUDE_CODE_OAUTH_TOKEN``) is dropped from the process env so it cannot
    take precedence and put the session back into inference-only mode.
    """

    STREAM_LOG_FILENAME: ClassVar[str] = "claude-remote.txt"

    @staticmethod
    def name() -> str:
        return AgentName.CLAUDE_REMOTE.value

    def __init__(
        self,
        logs_dir: Path,
        session_id: str | None = None,
        credentials_json: str | None = None,
        credentials_file: str | None = None,
        *args,
        **kwargs,
    ):
        self._credentials_json_kwarg = credentials_json
        self._credentials_file_kwarg = credentials_file
        self._resolved_credentials: str | None = None
        self._credentials_resolved = False
        if session_id is not None:
            # Claude Code requires --session-id to be a valid UUID; validating
            # here also makes it safe to interpolate into the shell command.
            try:
                uuid.UUID(session_id)
            except ValueError as exc:
                raise ValueError(
                    f"session_id must be a valid UUID, got {session_id!r}"
                ) from exc
        # Fixed ahead of launch so _get_session_dir() can deterministically
        # locate the session JSONL written by this run.
        self.claude_session_id = session_id or str(uuid.uuid4())
        super().__init__(logs_dir, *args, **kwargs)

    def install_spec(self) -> AgentInstallSpec:
        spec = super().install_spec()
        # `script` ships with bsdutils on Debian/Ubuntu (essential), but Alpine
        # needs util-linux for the in-container PTY allocation.
        spec.steps.insert(
            1,
            InstallStep(
                user="root",
                run=(
                    "if command -v apk &> /dev/null; then"
                    "  apk add --no-cache util-linux-misc"
                    "  || apk add --no-cache util-linux;"
                    " fi"
                ),
            ),
        )
        return spec

    def network_allowlist(self) -> NetworkAllowlist:
        # Remote Control relays the session through Anthropic infrastructure
        # beyond the inference endpoint, so widen the inference-only allowlist.
        base = super().network_allowlist()
        domains = [
            *base.domains,
            ".anthropic.com",
            "claude.ai",
            ".claude.ai",
            ".claude.com",
        ]
        return NetworkAllowlist(
            domains=domains,
            proxy_domains=_squid_policy_domains(NetworkAllowlist(domains=domains)),
        )

    def _resolve_credentials_json(self) -> str | None:
        """Resolve the full-scope login credential for Remote Control.

        Precedence: ``credentials_json`` kwarg, ``credentials_file`` kwarg,
        then the ``CLAUDE_CODE_CREDENTIALS_JSON`` / ``CLAUDE_CODE_CREDENTIALS_FILE``
        env vars. Returns ``None`` when no credential is configured.
        """
        if self._credentials_resolved:
            return self._resolved_credentials

        raw: str | None = None
        source: str | None = None
        if self._credentials_json_kwarg:
            raw = self._credentials_json_kwarg
            source = "credentials_json kwarg"
        elif self._credentials_file_kwarg:
            raw = self._read_credentials_file(self._credentials_file_kwarg)
            source = f"credentials_file kwarg ({self._credentials_file_kwarg})"
        elif self._get_env("CLAUDE_CODE_CREDENTIALS_JSON"):
            raw = self._get_env("CLAUDE_CODE_CREDENTIALS_JSON")
            source = "CLAUDE_CODE_CREDENTIALS_JSON env var"
        elif self._get_env("CLAUDE_CODE_CREDENTIALS_FILE"):
            path = self._get_env("CLAUDE_CODE_CREDENTIALS_FILE") or ""
            raw = self._read_credentials_file(path)
            source = f"CLAUDE_CODE_CREDENTIALS_FILE env var ({path})"

        if raw is not None:
            try:
                json.loads(raw)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"Claude credentials from {source} are not valid JSON: {exc}"
                ) from exc
        else:
            self.logger.warning(
                "No full-scope Claude credentials configured for claude-remote. "
                "Remote Control rejects API keys and long-lived tokens "
                "(inference-only); run `claude auth login` and pass the "
                "credential via credentials_json/credentials_file kwargs or "
                "CLAUDE_CODE_CREDENTIALS_JSON/CLAUDE_CODE_CREDENTIALS_FILE."
            )

        self._resolved_credentials = raw
        self._credentials_resolved = True
        return raw

    @staticmethod
    def _read_credentials_file(path: str) -> str:
        credentials_path = Path(path).expanduser()
        try:
            return credentials_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise ValueError(
                f"Failed to read Claude credentials file {credentials_path}: {exc}"
            ) from exc

    def _build_run_env(self) -> dict[str, str]:
        env = super()._build_run_env()
        # Remote Control depends on Claude Code's relay/session side traffic.
        # The base claude-code agent disables that traffic for deterministic
        # headless runs, but doing so prevents remote attach from registering.
        env.pop("CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC", None)
        if self._resolve_credentials_json():
            # Env-based auth takes precedence over the injected login
            # credential and is inference-only, which Remote Control rejects.
            for key in (
                "ANTHROPIC_API_KEY",
                "ANTHROPIC_AUTH_TOKEN",
                "CLAUDE_CODE_OAUTH_TOKEN",
            ):
                env.pop(key, None)
        return env

    def _build_setup_command(self) -> str:
        setup_command = super()._build_setup_command()
        # Remote Control runs headlessly behind an in-container PTY. A fresh
        # Claude config can otherwise stop at the interactive first-run theme
        # picker, with no terminal attached to answer it.
        onboarding_config = shlex.quote(
            json.dumps(
                {
                    "hasCompletedOnboarding": True,
                    "lastOnboardingVersion": "2.1.158",
                    "remoteDialogSeen": True,
                    "tengu_disable_bypass_permissions_mode": False,
                    "projects": {
                        "/app": {
                            "allowedTools": [],
                            "hasTrustDialogAccepted": True,
                            "projectOnboardingSeenCount": 1,
                        }
                    },
                },
                separators=(",", ":"),
            )
        )
        setup_command += (
            " && if [ ! -s $CLAUDE_CONFIG_DIR/.claude.json ]; then "
            f"printf '%s\\n' {onboarding_config} > $CLAUDE_CONFIG_DIR/.claude.json; "
            "fi"
        )
        user_settings = shlex.quote(
            json.dumps(
                {
                    "agentPushNotifEnabled": True,
                    "skipDangerousModePermissionPrompt": True,
                },
                separators=(",", ":"),
            )
        )
        setup_command += (
            " && if [ ! -s $CLAUDE_CONFIG_DIR/settings.json ]; then "
            f"printf '%s\\n' {user_settings} > $CLAUDE_CONFIG_DIR/settings.json; "
            "fi"
        )
        credentials = self._resolve_credentials_json()
        if credentials:
            escaped = shlex.quote(credentials)
            setup_command += (
                " && (umask 077 && "
                f"echo {escaped} > $CLAUDE_CONFIG_DIR/.credentials.json)"
            )
        return setup_command

    def _redact_command_for_logging(self, command: str) -> str:
        command = super()._redact_command_for_logging(command)
        return re.sub(
            r"echo .+? > \$CLAUDE_CONFIG_DIR/\.credentials\.json",
            "echo '[redacted-claude-credentials]' > "
            "$CLAUDE_CONFIG_DIR/.credentials.json",
            command,
        )

    def _get_session_dir(self) -> Path | None:
        """Locate the session JSONL via the deterministic --session-id."""
        project_root = self.logs_dir / "sessions" / "projects"
        if project_root.is_dir():
            matches = [
                f
                for f in project_root.rglob(f"{self.claude_session_id}.jsonl")
                if "subagents" not in f.parent.parts
            ]
            if matches:
                return matches[0].parent
        return super()._get_session_dir()

    def _build_claude_command(self, escaped_instruction: str, extra_flags: str) -> str:
        # No --print, no --output-format=stream-json, no </dev/null: this is a
        # live interactive session driven through Remote Control.
        claude_command = (
            f"claude --verbose "
            f"--dangerously-skip-permissions "
            f"--remote-control "
            f"{extra_flags}"
            f"--session-id {self.claude_session_id} "
            f"-- {escaped_instruction}"
        )
        # Remove the injected full-scope credential (including any rotated
        # refresh token Claude Code wrote back) before Pier collects the
        # logs dir. EXIT does not fire on SIGKILL; that residual risk is
        # documented in the README.
        cleanup = (
            "trap 'rm -f \"$CLAUDE_CONFIG_DIR/.credentials.json\"' EXIT INT TERM HUP; "
            if self._resolve_credentials_json()
            else ""
        )
        return (
            'export PATH="$HOME/.local/bin:$PATH"; '
            f"{cleanup}"
            # Fail loudly rather than ship a broken interactive mode when the
            # PTY helper is unavailable in the task image.
            "command -v script >/dev/null 2>&1 || { "
            "echo 'claude-remote requires the script utility (util-linux) "
            "to allocate a PTY for interactive mode' >&2; exit 1; }; "
            # FIFO opened read-write on fd 9: reads block instead of hitting
            # EOF, keeping the interactive session's stdin open for its
            # entire lifetime. The delayed command makes Remote Control enter
            # /rc active reliably in hidden-PTY runs where --remote-control
            # alone leaves the session detached.
            'pty_stdin="$(mktemp -u)" && mkfifo "$pty_stdin" && '
            'exec 9<>"$pty_stdin" && rm -f "$pty_stdin"; '
            "(sleep 5; printf '/remote-control\\r' >&9) & "
            f"script -qefc {shlex.quote(claude_command)} /dev/null <&9 2>&1 | tee "
            f"/logs/agent/{self.STREAM_LOG_FILENAME}"
        )
