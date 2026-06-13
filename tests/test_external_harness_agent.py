import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from examples.agents.benchmaxx_harness import (
    DummyBenchmaxxHarnessAgent,
    ExternalHarnessAgent,
)
from pier.agents.factory import AgentFactory
from pier.environments.base import ExecResult
from pier.lab.metadata import read_harness_run_info
from pier.models.agent.context import AgentContext


class FakeEnvironment:
    def __init__(self, return_code: int = 0) -> None:
        self.return_code = return_code
        self.uploads = []
        self.exec_calls = []

    async def upload_file(self, source_path, target_path):
        self.uploads.append((source_path, target_path))

    async def exec(self, command, cwd=None, env=None, timeout_sec=None, user=None):
        self.exec_calls.append(
            {
                "command": command,
                "cwd": cwd,
                "timeout_sec": timeout_sec,
                "user": user,
            }
        )
        return ExecResult(
            stdout="harness stdout\n",
            stderr="harness stderr\n",
            return_code=self.return_code,
        )


@pytest.fixture
def anyio_backend():
    return "asyncio"


def test_dummy_harness_agent_can_be_loaded_by_import_path(tmp_path):
    agent = AgentFactory.create_agent_from_import_path(
        "examples.agents.benchmaxx_harness:DummyBenchmaxxHarnessAgent",
        logs_dir=tmp_path / "trial" / "agent",
    )

    assert isinstance(agent, DummyBenchmaxxHarnessAgent)
    assert agent.name() == "dummy-benchmaxx-harness"
    assert agent.version() == "0.1.0"


@pytest.mark.anyio
async def test_external_harness_agent_writes_metadata_from_agent_logs_dir(tmp_path):
    trial_dir = tmp_path / "trial"
    env = FakeEnvironment()
    context = AgentContext()
    agent = ExternalHarnessAgent(
        logs_dir=trial_dir / "agent",
        model_name="test-provider/test-model",
        command=(
            "benchmaxx run --instruction {instruction_path} "
            "--logs {logs_dir} --workspace {workspace}"
        ),
        harness_name="benchmaxx-agent",
        harness_version="0.1.0",
        harness_git_sha="abc123",
        config_hash="sha256:config",
        profile="default",
        base_agent="codex",
        workspace_path="/workspace",
        timeout_sec=17,
    )

    await agent.run("solve the task", env, context)

    assert env.uploads[0][1] == "/logs/agent/instruction.md"
    assert env.exec_calls == [
        {
            "command": (
                "benchmaxx run --instruction /logs/agent/instruction.md "
                "--logs /logs/agent --workspace /workspace"
            ),
            "cwd": "/workspace",
            "timeout_sec": 17,
            "user": None,
        }
    ]
    info = read_harness_run_info(trial_dir)
    assert info.harness_name == "benchmaxx-agent"
    assert info.harness_version == "0.1.0"
    assert info.harness_git_sha == "abc123"
    assert info.config_hash == "sha256:config"
    assert info.profile == "default"
    assert info.base_agent == "codex"
    assert info.model == "test-provider/test-model"
    assert context.n_agent_steps == 1
    assert context.metadata["harness"]["harness_name"] == "benchmaxx-agent"
    assert (trial_dir / "agent" / "stdout.txt").read_text() == "harness stdout\n"
    assert (trial_dir / "agent" / "stderr.txt").read_text() == "harness stderr\n"


@pytest.mark.anyio
async def test_dummy_harness_agent_records_hello_world_command(tmp_path):
    trial_dir = tmp_path / "trial"
    env = FakeEnvironment()
    agent = DummyBenchmaxxHarnessAgent(logs_dir=trial_dir / "agent")

    await agent.run("create hello", env, AgentContext())

    assert env.exec_calls[0]["command"] == "printf 'Hello, world!\\n' > /app/hello.txt"
    assert read_harness_run_info(trial_dir).harness_name == "dummy-benchmaxx-harness"


@pytest.mark.anyio
async def test_external_harness_agent_raises_on_failed_command(tmp_path):
    env = FakeEnvironment(return_code=2)
    agent = ExternalHarnessAgent(
        logs_dir=tmp_path / "trial" / "agent",
        command="false",
    )

    with pytest.raises(RuntimeError, match="exited with 2"):
        await agent.run("solve", env, AgentContext())
