from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path

from pier.agents.base import BaseAgent
from pier.environments.base import BaseEnvironment
from pier.lab.interactive import (
    InteractiveState,
    InteractiveStatus,
    abort_signal_path,
    finish_signal_path,
    update_interactive_state,
    write_interactive_state,
)
from pier.lab.metadata import LabSession, TaskExposureLabel, write_lab_session
from pier.models.agent.context import AgentContext
from pier.models.agent.name import AgentName


class InteractiveLabAbortError(Exception):
    pass


class InteractiveLabAgent(BaseAgent):
    SUPPORTS_WINDOWS: bool = True

    def __init__(
        self,
        logs_dir: Path,
        *,
        trial_dir: Path,
        trial_id: str,
        task_name: str,
        dataset_path: str | None = None,
        task_exposure: TaskExposureLabel | str = TaskExposureLabel.SEEN_INTERACTIVE,
        operator: str | None = "human",
        tools: list[str] | None = None,
        poll_interval_sec: float = 1.0,
        workspace_path: str | None = None,
        **kwargs,
    ):
        super().__init__(logs_dir=logs_dir, **kwargs)
        self.trial_dir = trial_dir
        self.trial_id = trial_id
        self.task_name = task_name
        self.dataset_path = dataset_path
        self.task_exposure = TaskExposureLabel(task_exposure)
        self.operator = operator
        self.tools = tools if tools is not None else ["manual"]
        self.poll_interval_sec = poll_interval_sec
        self.workspace_path = workspace_path

    @staticmethod
    def name() -> str:
        return AgentName.INTERACTIVE_LAB.value

    def version(self) -> str:
        return "0.1.0"

    async def setup(self, environment: BaseEnvironment) -> None:
        pass

    async def run(
        self,
        instruction: str,
        environment: BaseEnvironment,
        context: AgentContext,
    ) -> None:
        workspace_path = self.workspace_path or await self._resolve_workspace_path(
            environment
        )
        started_at = datetime.now(timezone.utc)
        state = InteractiveState(
            trial_id=self.trial_id,
            status=InteractiveStatus.WAITING,
            created_at=started_at,
            workspace_path=workspace_path,
            finish_signal_path=str(finish_signal_path(self.trial_dir)),
            abort_signal_path=str(abort_signal_path(self.trial_dir)),
            environment=self._environment_info(environment),
        )
        write_interactive_state(self.trial_dir, state)
        write_lab_session(
            self.trial_dir,
            LabSession(
                mode="interactive",
                task_name=self.task_name,
                dataset_path=self.dataset_path,
                task_exposure=self.task_exposure,
                started_at=started_at,
                workspace_path=workspace_path,
                operator=self.operator,
                tools=self.tools,
            ),
        )

        context.metadata = {
            "interactive": {
                "status": InteractiveStatus.WAITING.value,
                "finish_signal_path": str(finish_signal_path(self.trial_dir)),
                "abort_signal_path": str(abort_signal_path(self.trial_dir)),
                "workspace_path": workspace_path,
                "environment": state.environment,
            }
        }

        while True:
            if abort_signal_path(self.trial_dir).exists():
                update_interactive_state(
                    self.trial_dir, status=InteractiveStatus.ABORTED
                )
                self._finish_session()
                raise InteractiveLabAbortError(
                    f"Interactive trial {self.trial_id} was aborted."
                )
            if finish_signal_path(self.trial_dir).exists():
                update_interactive_state(
                    self.trial_dir, status=InteractiveStatus.FINISHING
                )
                self._finish_session()
                context.metadata["interactive"]["status"] = (
                    InteractiveStatus.FINISHING.value
                )
                return
            await asyncio.sleep(self.poll_interval_sec)

    async def _resolve_workspace_path(self, environment: BaseEnvironment) -> str | None:
        try:
            result = await environment.exec("pwd")
        except Exception:
            return None
        if result.return_code != 0 or result.stdout is None:
            return None
        return result.stdout.strip() or None

    def _environment_info(self, environment: BaseEnvironment) -> dict[str, str | None]:
        info = {
            "type": type(environment).__name__,
            "session_id": environment.session_id,
        }
        try:
            from pier.environments.docker.docker import (
                DockerEnvironment,
                _sanitize_docker_compose_project_name,
            )
        except Exception:
            return info

        if isinstance(environment, DockerEnvironment):
            compose_project = _sanitize_docker_compose_project_name(
                environment.session_id
            )
            info.update(
                {
                    "compose_project": compose_project,
                    "service": "main",
                    "container_name": f"{compose_project}-main-1",
                }
            )
        return info

    def _finish_session(self) -> None:
        from pier.lab.metadata import read_lab_session

        session = read_lab_session(self.trial_dir)
        if session is None:
            return
        session.finished_at = datetime.now(timezone.utc)
        write_lab_session(self.trial_dir, session)


class InteractiveSshAgent(InteractiveLabAgent):
    @staticmethod
    def name() -> str:
        return AgentName.INTERACTIVE_SSH.value
