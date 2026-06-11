import json
import shlex
import uuid
from pathlib import Path

import pytest

from pier.agents.factory import AgentFactory
from pier.agents.installed.claude_code import ClaudeCode
from pier.agents.installed.claude_remote import ClaudeRemote
from pier.models.agent.name import AgentName


def test_claude_remote_is_registered(tmp_path: Path):
    agent = AgentFactory.create_agent_from_name(
        AgentName.CLAUDE_REMOTE,
        logs_dir=tmp_path,
        model_name="anthropic/claude-fable-5",
    )

    assert isinstance(agent, ClaudeRemote)
    assert agent.name() == "claude-remote"


def test_claude_remote_name_is_accepted():
    assert "claude-remote" in AgentName.values()
    assert AgentName("claude-remote") is AgentName.CLAUDE_REMOTE


def test_claude_remote_command_is_interactive_with_remote_control(tmp_path: Path):
    agent = ClaudeRemote(logs_dir=tmp_path)

    command = agent._build_claude_command(shlex.quote("fix the bug"), "")

    assert "--remote-control" in command
    assert "--dangerously-skip-permissions" in command
    assert "--permission-mode=bypassPermissions" not in command
    assert f"--session-id {agent.claude_session_id}" in command
    assert "'fix the bug'" in command
    assert "tee /logs/agent/claude-remote.txt" in command
    # PTY is allocated in-container; the session must stay interactive.
    assert "script -qefc" in command
    assert "--print" not in command
    assert "--output-format=stream-json" not in command
    assert "</dev/null" not in command


def test_claude_remote_run_blocks_via_inherited_run(tmp_path: Path):
    # ClaudeRemote must not override run(): it reuses ClaudeCode's setup +
    # blocking exec flow and only customizes env/setup/launch construction.
    assert "run" not in ClaudeRemote.__dict__


def test_claude_code_command_unchanged(tmp_path: Path):
    agent = ClaudeCode(logs_dir=tmp_path)

    command = agent._build_claude_command(shlex.quote("fix the bug"), "")

    assert command == (
        'export PATH="$HOME/.local/bin:$PATH"; '
        "claude --verbose --output-format=stream-json "
        "--permission-mode=bypassPermissions "
        "--print -- 'fix the bug' 2>&1 </dev/null | tee "
        "/logs/agent/claude-code.txt"
    )


def test_claude_remote_session_id_must_be_uuid(tmp_path: Path):
    with pytest.raises(ValueError, match="valid UUID"):
        ClaudeRemote(logs_dir=tmp_path, session_id="not-a-uuid")

    session_id = str(uuid.uuid4())
    agent = ClaudeRemote(logs_dir=tmp_path, session_id=session_id)
    assert agent.claude_session_id == session_id

    # Auto-generated ids are valid UUIDs too.
    generated = ClaudeRemote(logs_dir=tmp_path).claude_session_id
    assert str(uuid.UUID(generated)) == generated


def test_claude_remote_get_session_dir_uses_session_id(tmp_path: Path):
    agent = ClaudeRemote(logs_dir=tmp_path)

    project_dir = tmp_path / "sessions" / "projects" / "-app"
    project_dir.mkdir(parents=True)
    # Decoy session from a different run; the base-class heuristic would
    # bail out on the ambiguity, but the deterministic id resolves it.
    other_dir = tmp_path / "sessions" / "projects" / "-other"
    other_dir.mkdir(parents=True)
    (other_dir / f"{uuid.uuid4()}.jsonl").write_text("{}\n")
    (project_dir / f"{agent.claude_session_id}.jsonl").write_text("{}\n")

    assert agent._get_session_dir() == project_dir


def test_claude_remote_install_spec_adds_pty_helper(tmp_path: Path):
    agent = ClaudeRemote(logs_dir=tmp_path)

    spec = agent.install_spec()

    assert spec.agent_name == "claude-remote"
    util_linux_steps = [s for s in spec.steps if "util-linux" in s.run]
    assert util_linux_steps and util_linux_steps[0].user == "root"
    # Base claude-code install steps are preserved.
    assert any("claude.ai/install.sh" in s.run for s in spec.steps)


def test_claude_remote_network_allowlist_includes_remote_control_domains(
    tmp_path: Path,
):
    agent = ClaudeRemote(logs_dir=tmp_path)

    domains = set(agent.network_allowlist().domains)

    assert {
        "api.anthropic.com",
        ".anthropic.com",
        "claude.ai",
        ".claude.ai",
        ".claude.com",
    } <= domains


CREDENTIALS = '{"claudeAiOauth": {"accessToken": "full-scope", "scopes": ["user:inference", "user:profile"]}}'


def test_claude_remote_injects_credentials_into_setup(tmp_path: Path):
    agent = ClaudeRemote(logs_dir=tmp_path, credentials_json=CREDENTIALS)

    setup_command = agent._build_setup_command()

    assert "$CLAUDE_CONFIG_DIR/.credentials.json" in setup_command
    assert "umask 077" in setup_command
    assert "full-scope" in setup_command
    assert "hasCompletedOnboarding" in setup_command
    assert "hasTrustDialogAccepted" in setup_command
    assert "remoteDialogSeen" in setup_command
    assert "tengu_disable_bypass_permissions_mode" in setup_command
    assert "agentPushNotifEnabled" in setup_command
    assert "skipDangerousModePermissionPrompt" in setup_command
    assert "/app" in setup_command
    # Base setup (dirs, skills copy) is preserved.
    assert "mkdir -p $CLAUDE_CONFIG_DIR/debug" in setup_command


def test_claude_remote_preseeds_onboarding_without_credentials(tmp_path: Path):
    agent = ClaudeRemote(logs_dir=tmp_path)

    setup_command = agent._build_setup_command()

    assert "hasCompletedOnboarding" in setup_command
    assert "remoteDialogSeen" in setup_command
    assert "agentPushNotifEnabled" in setup_command
    assert ".credentials.json" not in setup_command


def test_claude_remote_redacts_credentials_from_logged_commands(tmp_path: Path):
    agent = ClaudeRemote(logs_dir=tmp_path, credentials_json=CREDENTIALS)

    command = agent._build_setup_command()
    redacted = agent._redact_command_for_logging(command)

    assert "full-scope" in command
    assert "full-scope" not in redacted
    assert "[redacted-claude-credentials]" in redacted


def test_claude_remote_credentials_drop_env_auth(tmp_path: Path):
    agent = ClaudeRemote(
        logs_dir=tmp_path,
        credentials_json=CREDENTIALS,
        extra_env={
            "ANTHROPIC_API_KEY": "sk-test",
            "CLAUDE_CODE_OAUTH_TOKEN": "long-lived",
        },
    )

    env = agent._build_run_env()

    assert "ANTHROPIC_API_KEY" not in env
    assert "ANTHROPIC_AUTH_TOKEN" not in env
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in env


def test_claude_remote_keeps_remote_control_side_traffic_enabled(tmp_path: Path):
    agent = ClaudeRemote(logs_dir=tmp_path, credentials_json=CREDENTIALS)

    env = agent._build_run_env()

    assert "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC" not in env


def test_claude_remote_without_credentials_keeps_env_auth(tmp_path: Path):
    agent = ClaudeRemote(
        logs_dir=tmp_path,
        extra_env={"ANTHROPIC_API_KEY": "sk-test"},
    )

    env = agent._build_run_env()
    setup_command = agent._build_setup_command()
    command = agent._build_claude_command("'fix'", "")

    assert env["ANTHROPIC_API_KEY"] == "sk-test"
    assert ".credentials.json" not in setup_command
    assert "trap" not in command


def test_claude_remote_credentials_cleanup_trap(tmp_path: Path):
    agent = ClaudeRemote(logs_dir=tmp_path, credentials_json=CREDENTIALS)

    command = agent._build_claude_command("'fix'", "")

    assert (
        "trap 'rm -f \"$CLAUDE_CONFIG_DIR/.credentials.json\"' EXIT INT TERM HUP"
        in command
    )
    # Cleanup must be installed before the claude session starts.
    assert command.index("trap") < command.index("script -qefc")


def test_claude_remote_credentials_from_file_and_env(tmp_path: Path):
    credentials_path = tmp_path / "creds.json"
    credentials_path.write_text(CREDENTIALS)

    by_file = ClaudeRemote(logs_dir=tmp_path, credentials_file=str(credentials_path))
    assert by_file._resolve_credentials_json() == CREDENTIALS

    by_env_json = ClaudeRemote(
        logs_dir=tmp_path,
        extra_env={"CLAUDE_CODE_CREDENTIALS_JSON": CREDENTIALS},
    )
    assert by_env_json._resolve_credentials_json() == CREDENTIALS

    by_env_file = ClaudeRemote(
        logs_dir=tmp_path,
        extra_env={"CLAUDE_CODE_CREDENTIALS_FILE": str(credentials_path)},
    )
    assert by_env_file._resolve_credentials_json() == CREDENTIALS


def test_claude_remote_credentials_must_be_valid_json(tmp_path: Path):
    agent = ClaudeRemote(logs_dir=tmp_path, credentials_json="not json")
    with pytest.raises(ValueError, match="not valid JSON"):
        agent._resolve_credentials_json()

    missing = ClaudeRemote(
        logs_dir=tmp_path, credentials_file=str(tmp_path / "missing.json")
    )
    with pytest.raises(ValueError, match="Failed to read"):
        missing._resolve_credentials_json()


def test_claude_remote_trajectory_without_result_event(tmp_path: Path):
    """Interactive runs have no stream-json result event; cost stays optional."""
    agent = ClaudeRemote(logs_dir=tmp_path)
    session_dir = tmp_path / "sessions" / "projects" / "-app"
    session_dir.mkdir(parents=True)
    events = [
        {
            "type": "user",
            "message": {"role": "user", "content": "Fix the bug"},
            "timestamp": "2026-06-11T00:00:00Z",
            "sessionId": agent.claude_session_id,
            "version": "2.0.0",
        },
        {
            "type": "assistant",
            "message": {
                "id": "msg_1",
                "role": "assistant",
                "model": "claude-fable-5",
                "content": [{"type": "text", "text": "Done."}],
                "usage": {"input_tokens": 10, "output_tokens": 5},
            },
            "timestamp": "2026-06-11T00:00:01Z",
            "sessionId": agent.claude_session_id,
        },
    ]
    session_file = session_dir / f"{agent.claude_session_id}.jsonl"
    session_file.write_text("".join(json.dumps(e) + "\n" for e in events))

    trajectory = agent._convert_events_to_trajectory(agent._get_session_dir())

    assert trajectory is not None
    assert trajectory.agent.name == "claude-remote"
    assert trajectory.session_id == agent.claude_session_id
    assert trajectory.final_metrics is not None
    assert trajectory.final_metrics.total_cost_usd is None
    assert trajectory.final_metrics.total_completion_tokens == 5
