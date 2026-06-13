from __future__ import annotations

from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, Field


class TaskExposureLabel(str, Enum):
    """Labels used to separate inspected, replayed, and held-out tasks."""

    UNSEEN = "unseen"
    SEEN_INTERACTIVE = "seen_interactive"
    SEEN_LOGS_ONLY = "seen_logs_only"
    SEEN_SOLUTION = "seen_solution"
    REGRESSION = "regression"
    HOLDOUT = "holdout"


class LabSession(BaseModel):
    schema_version: int = 1
    mode: str
    task_name: str
    dataset_path: str | None = None
    task_exposure: TaskExposureLabel = TaskExposureLabel.UNSEEN
    started_at: datetime | None = None
    finished_at: datetime | None = None
    workspace_path: str | None = None
    operator: str | None = None
    tools: list[str] = Field(default_factory=list)
    followup_replay_job: str | None = None
    tags: list[str] = Field(default_factory=list)


class HarnessRunInfo(BaseModel):
    schema_version: int = 1
    harness_name: str
    harness_version: str | None = None
    harness_git_sha: str | None = None
    command: str | None = None
    config_hash: str | None = None
    profile: str | None = None
    base_agent: str | None = None
    model: str | None = None


_MetadataModel = TypeVar("_MetadataModel", bound=BaseModel)


def lab_dir(trial_dir: Path | str) -> Path:
    return Path(trial_dir) / "lab"


def session_metadata_path(trial_dir: Path | str) -> Path:
    return lab_dir(trial_dir) / "session.json"


def harness_metadata_path(trial_dir: Path | str) -> Path:
    return lab_dir(trial_dir) / "harness.json"


def trial_dir_from_agent_logs_dir(logs_dir: Path | str) -> Path:
    """Return the trial directory for a normal Pier agent logs directory."""
    logs_path = Path(logs_dir)
    if logs_path.name != "agent":
        raise ValueError(
            "Agent logs directory must be the trial's 'agent' directory; "
            f"got {logs_path}"
        )
    return logs_path.parent


def harness_metadata_path_from_agent_logs_dir(logs_dir: Path | str) -> Path:
    return harness_metadata_path(trial_dir_from_agent_logs_dir(logs_dir))


def read_lab_session(trial_dir: Path | str) -> LabSession | None:
    return _read_metadata(session_metadata_path(trial_dir), LabSession)


def write_lab_session(trial_dir: Path | str, session: LabSession) -> Path:
    return _write_metadata(session_metadata_path(trial_dir), session)


def read_harness_run_info(trial_dir: Path | str) -> HarnessRunInfo | None:
    return _read_metadata(harness_metadata_path(trial_dir), HarnessRunInfo)


def write_harness_run_info(trial_dir: Path | str, info: HarnessRunInfo) -> Path:
    return _write_metadata(harness_metadata_path(trial_dir), info)


def read_harness_run_info_from_agent_logs_dir(
    logs_dir: Path | str,
) -> HarnessRunInfo | None:
    return _read_metadata(
        harness_metadata_path_from_agent_logs_dir(logs_dir), HarnessRunInfo
    )


def write_harness_run_info_from_agent_logs_dir(
    logs_dir: Path | str, info: HarnessRunInfo
) -> Path:
    return _write_metadata(harness_metadata_path_from_agent_logs_dir(logs_dir), info)


def _read_metadata(
    path: Path, model_type: type[_MetadataModel]
) -> _MetadataModel | None:
    if not path.exists():
        return None
    return model_type.model_validate_json(path.read_text(encoding="utf-8"))


def _write_metadata(path: Path, model: BaseModel) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(model.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return path
