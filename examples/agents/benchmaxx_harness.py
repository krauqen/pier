from __future__ import annotations

from pathlib import Path

from pier.agents.base import BaseAgent
from pier.environments.base import BaseEnvironment
from pier.lab.metadata import HarnessRunInfo, write_harness_run_info_from_agent_logs_dir
from pier.models.agent.context import AgentContext
from pier.models.trial.paths import EnvironmentPaths


class ExternalHarnessAgent(BaseAgent):
    """A minimal import-path adapter for a command-style external harness."""

    def __init__(
        self,
        logs_dir: Path,
        command: str,
        harness_name: str = "external-harness",
        harness_version: str | None = None,
        harness_git_sha: str | None = None,
        config_hash: str | None = None,
        profile: str | None = None,
        base_agent: str | None = None,
        workspace_path: str | None = None,
        timeout_sec: int | None = None,
        *args,
        **kwargs,
    ) -> None:
        super().__init__(logs_dir, *args, **kwargs)
        self.command = command
        self.harness_name = harness_name
        self.harness_version = harness_version
        self.harness_git_sha = harness_git_sha
        self.config_hash = config_hash
        self.profile = profile
        self.base_agent = base_agent
        self.workspace_path = workspace_path
        self.timeout_sec = timeout_sec

    @staticmethod
    def name() -> str:
        return "external-harness"

    def version(self) -> str | None:
        return self.harness_version

    async def setup(self, environment: BaseEnvironment) -> None:
        pass

    async def run(
        self,
        instruction: str,
        environment: BaseEnvironment,
        context: AgentContext,
    ) -> None:
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        instruction_path = self.logs_dir / "instruction.md"
        instruction_path.write_text(instruction, encoding="utf-8")
        await environment.upload_file(
            instruction_path, (EnvironmentPaths.agent_dir / "instruction.md").as_posix()
        )

        command = self.command.format(
            instruction_path=(EnvironmentPaths.agent_dir / "instruction.md").as_posix(),
            logs_dir=EnvironmentPaths.agent_dir.as_posix(),
            workspace=self.workspace_path or ".",
        )

        harness_info = HarnessRunInfo(
            harness_name=self.harness_name,
            harness_version=self.harness_version,
            harness_git_sha=self.harness_git_sha,
            command=command,
            config_hash=self.config_hash,
            profile=self.profile,
            base_agent=self.base_agent,
            model=self.model_name,
        )
        write_harness_run_info_from_agent_logs_dir(self.logs_dir, harness_info)

        result = await environment.exec(
            command,
            cwd=self.workspace_path,
            timeout_sec=self.timeout_sec,
        )
        (self.logs_dir / "stdout.txt").write_text(result.stdout or "", encoding="utf-8")
        (self.logs_dir / "stderr.txt").write_text(result.stderr or "", encoding="utf-8")

        context.n_agent_steps = 1
        context.metadata = {
            "harness": harness_info.model_dump(mode="json"),
            "return_code": result.return_code,
        }
        if result.return_code != 0:
            raise RuntimeError(
                f"External harness command exited with {result.return_code}"
            )


class DummyBenchmaxxHarnessAgent(ExternalHarnessAgent):
    """Toy harness adapter for examples/tasks/hello-world-no-internet."""

    def __init__(
        self,
        logs_dir: Path,
        command: str = "printf 'Hello, world!\\n' > /app/hello.txt",
        harness_name: str = "dummy-benchmaxx-harness",
        harness_version: str | None = "0.1.0",
        profile: str | None = "hello-world",
        *args,
        **kwargs,
    ) -> None:
        super().__init__(
            *args,
            logs_dir=logs_dir,
            command=command,
            harness_name=harness_name,
            harness_version=harness_version,
            profile=profile,
            **kwargs,
        )

    @staticmethod
    def name() -> str:
        return "dummy-benchmaxx-harness"
