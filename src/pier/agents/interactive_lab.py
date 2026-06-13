from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path

from pier.agents.base import BaseAgent
from pier.agents.installed.codex import Codex
from pier.environments.base import BaseEnvironment
from pier.lab.interactive import (
    InteractiveSshRequest,
    InteractiveState,
    InteractiveStatus,
    abort_signal_path,
    finish_signal_path,
    update_interactive_state,
    write_interactive_state,
)
from pier.lab.metadata import LabSession, TaskExposureLabel, write_lab_session
from pier.models.agent.context import AgentContext
from pier.models.agent.install import AgentInstallSpec, InstallStep
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
        ssh_user: str | None = None,
        public_key_path: str | None = None,
        allow_root_login: bool = False,
        install_codex: bool = False,
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
        self.ssh_user = ssh_user
        self.public_key_path = public_key_path
        self.allow_root_login = allow_root_login
        self.install_codex = install_codex

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
        ssh_info = None
        container_info = None
        if self._ssh_enabled:
            if not hasattr(environment, "enable_interactive_ssh"):
                raise RuntimeError(
                    "Interactive SSH is currently supported only for Docker "
                    "environments. Use the interactive-lab agent for no-SSH mode."
                )
            ssh_info, container_info = await environment.enable_interactive_ssh(
                InteractiveSshRequest(
                    user=self.ssh_user,
                    workspace_path=workspace_path,
                    public_key_path=self.public_key_path,
                    allow_root_login=self.allow_root_login,
                )
            )
            workspace_path = ssh_info.workspace_path or workspace_path

        started_at = datetime.now(timezone.utc)
        state = InteractiveState(
            trial_id=self.trial_id,
            status=InteractiveStatus.WAITING,
            created_at=started_at,
            workspace_path=workspace_path,
            finish_signal_path=str(finish_signal_path(self.trial_dir)),
            abort_signal_path=str(abort_signal_path(self.trial_dir)),
            environment=self._environment_info(environment),
            ssh=ssh_info,
            container=container_info,
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
                "ssh": ssh_info.model_dump(mode="json") if ssh_info else None,
                "container": (
                    container_info.model_dump(mode="json") if container_info else None
                ),
            }
        }

        self._print_ready_message(state)

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

    @property
    def _ssh_enabled(self) -> bool:
        return self.name() == AgentName.INTERACTIVE_SSH.value

    def _print_ready_message(self, state: InteractiveState) -> None:
        print()
        print("Interactive trial ready.")
        print(f"Trial: {self.trial_id}")
        if state.workspace_path:
            print(f"Workspace: {state.workspace_path}")
        if state.ssh:
            print(f"SSH: {state.ssh.command}")
            print(f"SSH config: {state.ssh.ssh_config_path}")
        print()
        print("When done:")
        print(f"  pier job finish {self.trial_id}")
        print("To abort:")
        print(f"  pier job abort {self.trial_id}")
        print()

    def _finish_session(self) -> None:
        from pier.lab.metadata import read_lab_session

        session = read_lab_session(self.trial_dir)
        if session is None:
            return
        session.finished_at = datetime.now(timezone.utc)
        write_lab_session(self.trial_dir, session)


class InteractiveSshAgent(InteractiveLabAgent):
    SUPPORTS_WINDOWS: bool = False

    @staticmethod
    def name() -> str:
        return AgentName.INTERACTIVE_SSH.value

    def install_spec(self) -> AgentInstallSpec:
        ssh_root_run = (
            "set -euo pipefail; "
            "if ! command -v sshd >/dev/null 2>&1; then "
            "  if [ -f /etc/alpine-release ] || ldd --version 2>&1 | grep -qi musl; then "
            "    apk add --no-cache openssh bash; "
            "  elif command -v apt-get >/dev/null 2>&1; then "
            "    apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends openssh-server openssh-client ca-certificates && rm -rf /var/lib/apt/lists/*; "
            "  elif command -v dnf >/dev/null 2>&1; then "
            "    dnf install -y openssh-server openssh-clients; "
            "  elif command -v yum >/dev/null 2>&1; then "
            "    yum install -y openssh-server openssh-clients; "
            "  else "
            "    echo 'Interactive SSH requested, but no supported package manager was found to install openssh-server.' >&2; exit 86; "
            "  fi; "
            "fi; "
            "if ! id agent >/dev/null 2>&1; then "
            "  if command -v useradd >/dev/null 2>&1; then useradd -m -s /bin/bash agent; "
            "  elif command -v adduser >/dev/null 2>&1; then adduser -D -s /bin/bash agent; "
            "  else echo 'Could not create default interactive user agent.' >&2; exit 87; fi; "
            "fi"
        )
        steps = [
            InstallStep(
                user="root",
                env={"DEBIAN_FRONTEND": "noninteractive"},
                run=ssh_root_run,
            )
        ]
        if self.install_codex:
            steps.extend(Codex(self.logs_dir).install_spec().steps)
            steps.append(
                InstallStep(
                    user="root",
                    run=(
                        "set -euo pipefail; "
                        "for bin in node codex; do "
                        '  bin_path="$(command -v "$bin" 2>/dev/null || true)"; '
                        '  if [ -z "$bin_path" ]; then '
                        '    bin_path="$(find /root /home/agent -path \'*/bin/\'"$bin" -type f -perm /111 2>/dev/null | head -n 1)"; '
                        "  fi; "
                        '  if [ -n "$bin_path" ]; then '
                        '    ln -sf "$bin_path" "/usr/local/bin/$bin"; '
                        "  fi; "
                        "done; "
                        "test -x /usr/local/bin/codex"
                    ),
                )
            )
        return AgentInstallSpec(
            agent_name=self.name(),
            version=self.version(),
            steps=steps,
            cache_key=(
                f"{self.name()}-openssh" + ("-codex" if self.install_codex else "")
            ),
        )
