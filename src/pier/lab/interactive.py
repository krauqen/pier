from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Literal, TypeVar

from pydantic import BaseModel, Field


class InteractiveStatus(str, Enum):
    STARTING = "starting"
    WAITING = "waiting"
    FINISHING = "finishing"
    VERIFYING = "verifying"
    COMPLETE = "complete"
    ABORTED = "aborted"
    ERROR = "error"


class InteractiveSignal(str, Enum):
    FINISH = "finish"
    ABORT = "abort"


class InteractiveSshRequest(BaseModel):
    user: str | None = None
    workspace_path: str | None = None
    transport: Literal["proxy_command"] = "proxy_command"
    public_key_path: str | None = None
    allow_root_login: bool = False


class InteractiveSshInfo(BaseModel):
    mode: Literal["proxy_command"] = "proxy_command"
    host_alias: str
    user: str
    port: int | None = None
    workspace_path: str | None = None
    ssh_config_path: str
    private_key_path: str | None = None
    public_key_path: str
    known_hosts_path: str
    command: str


class InteractiveContainerInfo(BaseModel):
    compose_project: str
    service: str = "main"
    container_id: str
    container_name: str | None = None


class InteractiveState(BaseModel):
    schema_version: int = 1
    trial_id: str
    status: InteractiveStatus = InteractiveStatus.STARTING
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    workspace_path: str | None = None
    finish_signal_path: str | None = None
    abort_signal_path: str | None = None
    environment: dict[str, str | None] = Field(default_factory=dict)
    ssh: InteractiveSshInfo | None = None
    container: InteractiveContainerInfo | None = None


_StateModel = TypeVar("_StateModel", bound=BaseModel)


def interactive_dir(trial_dir: Path | str) -> Path:
    return Path(trial_dir) / "interactive"


def interactive_state_path(trial_dir: Path | str) -> Path:
    return interactive_dir(trial_dir) / "state.json"


def finish_signal_path(trial_dir: Path | str) -> Path:
    return interactive_dir(trial_dir) / InteractiveSignal.FINISH.value


def abort_signal_path(trial_dir: Path | str) -> Path:
    return interactive_dir(trial_dir) / InteractiveSignal.ABORT.value


def read_interactive_state(trial_dir: Path | str) -> InteractiveState | None:
    return _read_json_model(interactive_state_path(trial_dir), InteractiveState)


def write_interactive_state(trial_dir: Path | str, state: InteractiveState) -> Path:
    state.updated_at = datetime.now(timezone.utc)
    return _write_json_model(interactive_state_path(trial_dir), state)


def update_interactive_state(
    trial_dir: Path | str,
    *,
    status: InteractiveStatus,
    workspace_path: str | None = None,
    ssh: InteractiveSshInfo | None = None,
    container: InteractiveContainerInfo | None = None,
) -> InteractiveState | None:
    state = read_interactive_state(trial_dir)
    if state is None:
        return None
    state.status = status
    if workspace_path is not None:
        state.workspace_path = workspace_path
    if ssh is not None:
        state.ssh = ssh
    if container is not None:
        state.container = container
    write_interactive_state(trial_dir, state)
    return state


def write_finish_signal(trial_dir: Path | str) -> Path:
    return _write_signal(finish_signal_path(trial_dir))


def write_abort_signal(trial_dir: Path | str) -> Path:
    return _write_signal(abort_signal_path(trial_dir))


def _write_signal(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch(exist_ok=True)
    return path


def _read_json_model(path: Path, model_type: type[_StateModel]) -> _StateModel | None:
    if not path.exists():
        return None
    return model_type.model_validate_json(path.read_text(encoding="utf-8"))


def _write_json_model(path: Path, model: BaseModel) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(model.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return path
