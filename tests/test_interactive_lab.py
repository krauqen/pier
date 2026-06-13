import asyncio
import json
from pathlib import Path

import pytest

from pier.agents.factory import AgentFactory
from pier.agents.interactive_lab import InteractiveLabAbortError, InteractiveLabAgent
from pier.cli.jobs import _resolve_trial_dir
from pier.environments.base import ExecResult
from pier.lab.interactive import (
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


class FakeEnvironment:
    session_id = "task__interactive"

    async def exec(self, command: str):
        assert command == "pwd"
        return ExecResult(stdout="/workspace\n", return_code=0)


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


def test_finish_and_abort_signals_are_idempotent(tmp_path):
    trial_dir = tmp_path / "trial"

    assert write_finish_signal(trial_dir) == finish_signal_path(trial_dir)
    assert write_finish_signal(trial_dir) == finish_signal_path(trial_dir)
    assert write_abort_signal(trial_dir) == abort_signal_path(trial_dir)
    assert write_abort_signal(trial_dir) == abort_signal_path(trial_dir)
    assert finish_signal_path(trial_dir).exists()
    assert abort_signal_path(trial_dir).exists()


def test_resolve_trial_dir_finds_trial_by_id(tmp_path):
    trial_dir = tmp_path / "jobs" / "job" / "trial-id"
    trial_dir.mkdir(parents=True)

    assert _resolve_trial_dir("trial-id", tmp_path / "jobs") == trial_dir
    assert _resolve_trial_dir(str(trial_dir), tmp_path / "jobs") == trial_dir


def test_resolve_trial_dir_reports_missing_jobs_dir(tmp_path):
    with pytest.raises(ValueError, match="Jobs directory does not exist"):
        _resolve_trial_dir("trial-id", tmp_path / "missing")


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
