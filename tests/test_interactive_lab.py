import asyncio
import json
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

from pier.agents.factory import AgentFactory
from pier.agents.interactive_lab import (
    InteractiveLabAbortError,
    InteractiveLabAgent,
    InteractiveSshAgent,
)
from pier.cli.jobs import _resolve_trial_dir, ssh_proxy
from pier.environments.base import ExecResult
from pier.environments.docker.docker import DockerEnvironment
from pier.lab.interactive import (
    InteractiveContainerInfo,
    InteractiveSshInfo,
    InteractiveSshRequest,
    InteractiveState,
    InteractiveStatus,
    abort_signal_path,
    finish_signal_path,
    read_interactive_state,
    update_interactive_state,
    write_abort_signal,
    write_finish_signal,
    write_interactive_state,
)
from pier.lab.metadata import TaskExposureLabel, read_lab_session
from pier.models.agent.context import AgentContext
from pier.models.agent.name import AgentName
from pier.trial.trial import _apply_patch_file_to_environment


class FakeEnvironment:
    session_id = "task__interactive"

    async def exec(self, command: str):
        assert command == "pwd"
        return ExecResult(stdout="/workspace\n", return_code=0)


class FakeSshEnvironment(FakeEnvironment):
    def __init__(self):
        self.request: InteractiveSshRequest | None = None

    async def enable_interactive_ssh(self, request: InteractiveSshRequest):
        self.request = request
        return (
            InteractiveSshInfo(
                host_alias="pier-task__interactive",
                user="agent",
                workspace_path=request.workspace_path,
                ssh_config_path="/tmp/ssh_config",
                private_key_path="/tmp/id_ed25519",
                public_key_path="/tmp/id_ed25519.pub",
                known_hosts_path="/tmp/known_hosts",
                command="ssh -F /tmp/ssh_config pier-task__interactive",
            ),
            InteractiveContainerInfo(
                compose_project="task__interactive",
                container_id="container123",
                container_name="task__interactive-main-1",
            ),
        )


class FakePatchEnvironment:
    def __init__(self, return_code: int = 0):
        self.return_code = return_code
        self.uploads = []
        self.commands = []

    async def upload_file(self, source_path: Path | str, target_path: str):
        self.uploads.append((Path(source_path), target_path))

    async def exec(
        self,
        command: str,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        timeout_sec: int | None = None,
        user: str | int | None = None,
    ):
        self.commands.append(
            {
                "command": command,
                "cwd": cwd,
                "env": env,
                "timeout_sec": timeout_sec,
                "user": user,
            }
        )
        return ExecResult(stdout="ok\n", stderr="", return_code=self.return_code)


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def _wait_for_state(trial_dir: Path) -> InteractiveState:
    for _ in range(50):
        state = read_interactive_state(trial_dir)
        if state is not None:
            return state
        await asyncio.sleep(0.01)
    raise AssertionError("interactive state was not written")


@pytest.mark.anyio
async def test_interactive_lab_agent_waits_until_finish_signal(tmp_path):
    trial_dir = tmp_path / "job" / "task__interactive"
    context = AgentContext()
    agent = InteractiveLabAgent(
        logs_dir=trial_dir / "agent",
        trial_dir=trial_dir,
        trial_id=trial_dir.name,
        task_name="task",
        dataset_path="/datasets/demo",
        poll_interval_sec=0.01,
    )

    task = asyncio.create_task(agent.run("fix it", FakeEnvironment(), context))
    state = await _wait_for_state(trial_dir)

    assert state.status == InteractiveStatus.WAITING
    assert state.workspace_path == "/workspace"
    assert state.finish_signal_path == str(finish_signal_path(trial_dir))
    assert state.environment == {
        "type": "FakeEnvironment",
        "session_id": "task__interactive",
    }
    assert read_lab_session(trial_dir).task_exposure == (
        TaskExposureLabel.SEEN_INTERACTIVE
    )

    write_finish_signal(trial_dir)
    await task

    state = read_interactive_state(trial_dir)
    session = read_lab_session(trial_dir)
    assert state.status == InteractiveStatus.FINISHING
    assert session.finished_at is not None
    assert context.metadata["interactive"]["status"] == "finishing"


@pytest.mark.anyio
async def test_interactive_lab_agent_aborts_on_abort_signal(tmp_path):
    trial_dir = tmp_path / "job" / "task__interactive"
    agent = InteractiveLabAgent(
        logs_dir=trial_dir / "agent",
        trial_dir=trial_dir,
        trial_id=trial_dir.name,
        task_name="task",
        poll_interval_sec=0.01,
    )

    task = asyncio.create_task(agent.run("fix it", FakeEnvironment(), AgentContext()))
    await _wait_for_state(trial_dir)
    write_abort_signal(trial_dir)

    with pytest.raises(InteractiveLabAbortError):
        await task

    state = read_interactive_state(trial_dir)
    session = read_lab_session(trial_dir)
    assert state.status == InteractiveStatus.ABORTED
    assert session.finished_at is not None


@pytest.mark.anyio
async def test_interactive_ssh_agent_writes_ssh_and_container_state(tmp_path):
    trial_dir = tmp_path / "job" / "task__interactive"
    context = AgentContext()
    environment = FakeSshEnvironment()
    agent = InteractiveSshAgent(
        logs_dir=trial_dir / "agent",
        trial_dir=trial_dir,
        trial_id=trial_dir.name,
        task_name="task",
        poll_interval_sec=0.01,
    )

    task = asyncio.create_task(agent.run("fix it", environment, context))
    state = await _wait_for_state(trial_dir)

    assert environment.request is not None
    assert environment.request.workspace_path == "/workspace"
    assert state.ssh is not None
    assert state.ssh.mode == "proxy_command"
    assert state.ssh.user == "agent"
    assert state.container is not None
    assert state.container.container_id == "container123"
    assert context.metadata["interactive"]["ssh"]["host_alias"] == (
        "pier-task__interactive"
    )

    write_finish_signal(trial_dir)
    await task


def test_interactive_state_writes_stable_json(tmp_path):
    trial_dir = tmp_path / "trial"
    state = InteractiveState(
        trial_id="trial",
        status=InteractiveStatus.WAITING,
        workspace_path="/workspace",
        finish_signal_path=str(finish_signal_path(trial_dir)),
        abort_signal_path=str(abort_signal_path(trial_dir)),
    )

    path = write_interactive_state(trial_dir, state)
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert path.read_text(encoding="utf-8") == json.dumps(payload, indent=2) + "\n"
    assert payload["schema_version"] == 1
    assert payload["trial_id"] == "trial"
    assert payload["status"] == "waiting"
    assert read_interactive_state(trial_dir).workspace_path == "/workspace"

    update_interactive_state(trial_dir, status=InteractiveStatus.VERIFYING)
    assert read_interactive_state(trial_dir).status == InteractiveStatus.VERIFYING


@pytest.mark.anyio
async def test_apply_patch_file_uploads_records_and_applies_before_verifier(tmp_path):
    patch_path = tmp_path / "fix.diff"
    patch_path.write_text("diff --git a/a.txt b/a.txt\n", encoding="utf-8")
    trial_dir = tmp_path / "trial"
    trial_dir.mkdir()
    environment = FakePatchEnvironment()

    recorded_patch = await _apply_patch_file_to_environment(
        patch_path=patch_path,
        trial_dir=trial_dir,
        environment=environment,
        logger=logging.getLogger("test"),
    )

    assert recorded_patch == trial_dir / "pre-verification.patch"
    assert recorded_patch.read_text(encoding="utf-8") == patch_path.read_text(
        encoding="utf-8"
    )
    assert environment.uploads == [(recorded_patch, "/tmp/pier-pre-verification.patch")]
    assert environment.commands == [
        {
            "command": (
                "git -c safe.directory=* apply --whitespace=nowarn "
                "/tmp/pier-pre-verification.patch"
            ),
            "cwd": None,
            "env": None,
            "timeout_sec": 120,
            "user": "root",
        }
    ]
    assert (trial_dir / "pre-verification-patch-stdout.txt").read_text(
        encoding="utf-8"
    ) == "ok\n"


def test_finish_and_abort_signals_are_idempotent(tmp_path):
    trial_dir = tmp_path / "trial"

    assert write_finish_signal(trial_dir) == finish_signal_path(trial_dir)
    assert write_finish_signal(trial_dir) == finish_signal_path(trial_dir)
    assert write_abort_signal(trial_dir) == abort_signal_path(trial_dir)
    assert write_abort_signal(trial_dir) == abort_signal_path(trial_dir)
    assert finish_signal_path(trial_dir).exists()
    assert abort_signal_path(trial_dir).exists()


def test_ssh_proxy_execs_sshd_for_running_container(tmp_path, monkeypatch):
    trial_dir = tmp_path / "jobs" / "job" / "trial-id"
    state = InteractiveState(
        trial_id="trial-id",
        status=InteractiveStatus.WAITING,
        workspace_path="/workspace",
        container=InteractiveContainerInfo(
            compose_project="trial-id",
            container_id="container123",
        ),
    )
    write_interactive_state(trial_dir, state)
    exec_calls = []

    class Completed:
        returncode = 0
        stdout = "true\n"

    def fake_run(*args, **kwargs):
        assert args[0] == [
            "docker",
            "inspect",
            "--format",
            "{{.State.Running}}",
            "container123",
        ]
        return Completed()

    def fake_execvp(file, args):
        exec_calls.append((file, args))
        raise RuntimeError("execvp called")

    monkeypatch.setattr("pier.cli.jobs.subprocess.run", fake_run)
    monkeypatch.setattr("pier.cli.jobs.os.execvp", fake_execvp)

    with pytest.raises(RuntimeError, match="execvp called"):
        ssh_proxy("trial-id", jobs_dir=tmp_path / "jobs")

    assert exec_calls == [
        (
            "docker",
            [
                "docker",
                "exec",
                "-i",
                "-u",
                "root",
                "container123",
                "/usr/sbin/sshd",
                "-i",
                "-e",
                "-f",
                "/etc/ssh/sshd_config.pier",
            ],
        )
    ]


def test_resolve_trial_dir_finds_trial_by_id(tmp_path):
    trial_dir = tmp_path / "jobs" / "job" / "trial-id"
    trial_dir.mkdir(parents=True)

    assert _resolve_trial_dir("trial-id", tmp_path / "jobs") == trial_dir
    assert _resolve_trial_dir(str(trial_dir), tmp_path / "jobs") == trial_dir


def test_resolve_trial_dir_reports_missing_jobs_dir(tmp_path):
    with pytest.raises(ValueError, match="Jobs directory does not exist"):
        _resolve_trial_dir("trial-id", tmp_path / "missing")


def test_docker_interactive_ssh_defaults_to_effective_task_user():
    env = DockerEnvironment.__new__(DockerEnvironment)

    env.default_user = None
    assert env._resolve_interactive_ssh_user(None) == "root"

    env.default_user = "ubuntu"
    assert env._resolve_interactive_ssh_user(None) == "ubuntu"
    assert env._resolve_interactive_ssh_user("agent") == "agent"


def test_implicit_root_interactive_ssh_allows_key_only_root_login(
    tmp_path, monkeypatch
):
    env = DockerEnvironment.__new__(DockerEnvironment)
    env.session_id = "trial-id"
    env.trial_paths = SimpleNamespace(trial_dir=tmp_path / "trial")
    public_key = tmp_path / "id_ed25519.pub"
    public_key.write_text("ssh-ed25519 testkey\n", encoding="utf-8")

    def fake_generate_ssh_key(path: Path, comment: str) -> None:
        path.write_text("private key\n", encoding="utf-8")
        path.with_name(path.name + ".pub").write_text(
            f"ssh-ed25519 generated {comment}\n",
            encoding="utf-8",
        )

    monkeypatch.setattr(env, "_generate_ssh_key", fake_generate_ssh_key)

    paths = env._prepare_interactive_ssh_files(
        request=InteractiveSshRequest(public_key_path=str(public_key)),
        ssh_user="root",
        allow_root_login=True,
    )

    assert "PermitRootLogin prohibit-password" in paths["sshd_config"].read_text(
        encoding="utf-8"
    )


def test_interactive_agents_are_registered(tmp_path):
    for name in (AgentName.INTERACTIVE_LAB, AgentName.INTERACTIVE_SSH):
        agent = AgentFactory.create_agent_from_name(
            name,
            logs_dir=tmp_path,
            trial_dir=tmp_path / "trial",
            trial_id="trial",
            task_name="task",
        )

        assert isinstance(agent, InteractiveLabAgent)
        assert agent.name() == name.value


def test_interactive_ssh_install_codex_exposes_codex_for_ssh_user(tmp_path):
    agent = InteractiveSshAgent(
        logs_dir=tmp_path / "agent",
        trial_dir=tmp_path / "trial",
        trial_id="trial",
        task_name="task",
        install_codex=True,
    )

    install = agent.install_spec()

    assert install.cache_key == "interactive-ssh-openssh-codex"
    assert install.steps[-1].user == "root"
    assert "/usr/local/bin/codex" in install.steps[-1].run
    assert "find /root /home/agent" in install.steps[-1].run
    assert 'cp "$real_path" "/usr/local/bin/$bin"' in install.steps[-1].run
