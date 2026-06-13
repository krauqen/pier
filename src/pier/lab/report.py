from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from pier.lab.metadata import HarnessRunInfo, TaskExposureLabel
from pier.lab.metadata import read_harness_run_info, read_lab_session
from pier.models.trial.result import TrialResult


EXPOSURE_ORDER: tuple[TaskExposureLabel, ...] = (
    TaskExposureLabel.UNSEEN,
    TaskExposureLabel.SEEN_INTERACTIVE,
    TaskExposureLabel.SEEN_LOGS_ONLY,
    TaskExposureLabel.SEEN_SOLUTION,
    TaskExposureLabel.REGRESSION,
    TaskExposureLabel.HOLDOUT,
)


class LabReportTrial(BaseModel):
    task_exposure: TaskExposureLabel
    task_name: str
    trial_name: str
    trial_path: str
    agent_name: str
    model: str | None = None
    reward: float | int | None = None
    rewards: dict[str, float | int] | None = None
    exception_type: str | None = None
    harness: HarnessRunInfo | None = None


class LabReportExposureGroup(BaseModel):
    task_exposure: TaskExposureLabel
    n_trials: int
    trials: list[LabReportTrial] = Field(default_factory=list)


class LabReport(BaseModel):
    schema_version: int = 1
    job_dirs: list[str]
    total_trials: int
    exposures: dict[TaskExposureLabel, LabReportExposureGroup]


def build_lab_report(job_dirs: Iterable[Path | str]) -> LabReport:
    """Scan Pier job directories and group trial results by lab exposure."""
    normalized_job_dirs = sorted({Path(job_dir) for job_dir in job_dirs}, key=str)
    exposure_groups = {
        label: LabReportExposureGroup(task_exposure=label, n_trials=0)
        for label in EXPOSURE_ORDER
    }

    for job_dir in normalized_job_dirs:
        for trial_dir in _iter_trial_dirs(job_dir):
            trial = _read_trial_report(trial_dir)
            exposure_groups[trial.task_exposure].trials.append(trial)

    for group in exposure_groups.values():
        group.trials.sort(key=lambda trial: (trial.task_name, trial.trial_name))
        group.n_trials = len(group.trials)

    return LabReport(
        job_dirs=[str(job_dir) for job_dir in normalized_job_dirs],
        total_trials=sum(group.n_trials for group in exposure_groups.values()),
        exposures=exposure_groups,
    )


def render_lab_report_json(report: LabReport) -> str:
    return report.model_dump_json(indent=2) + "\n"


def render_lab_report_markdown(report: LabReport) -> str:
    lines = [
        "# Pier Lab Report",
        "",
        f"Total trials: {report.total_trials}",
        "",
    ]

    for label in EXPOSURE_ORDER:
        group = report.exposures[label]
        lines.extend([f"## {label.value} ({group.n_trials})", ""])
        if not group.trials:
            lines.extend(["No trials.", ""])
            continue

        lines.append("| Task | Reward | Exception | Agent | Model | Harness | Trial |")
        lines.append("| --- | ---: | --- | --- | --- | --- | --- |")
        for trial in group.trials:
            lines.append(
                "| "
                + " | ".join(
                    [
                        _markdown_cell(trial.task_name),
                        _markdown_cell(_format_reward(trial)),
                        _markdown_cell(trial.exception_type or ""),
                        _markdown_cell(trial.agent_name),
                        _markdown_cell(trial.model or ""),
                        _markdown_cell(_format_harness(trial.harness)),
                        _markdown_cell(trial.trial_path),
                    ]
                )
                + " |"
            )
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def write_lab_report(
    report: LabReport,
    output_path: Path | str,
    *,
    format: Literal["json", "markdown"] | None = None,
) -> Path:
    path = Path(output_path)
    report_format = format or _format_from_suffix(path)
    content = (
        render_lab_report_json(report)
        if report_format == "json"
        else render_lab_report_markdown(report)
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _iter_trial_dirs(job_dir: Path) -> list[Path]:
    if not job_dir.is_dir():
        return []
    return sorted(
        [
            path
            for path in job_dir.iterdir()
            if path.is_dir() and (path / "result.json").is_file()
        ],
        key=lambda path: path.name,
    )


def _read_trial_report(trial_dir: Path) -> LabReportTrial:
    result = TrialResult.model_validate_json(
        (trial_dir / "result.json").read_text(encoding="utf-8")
    )
    session = read_lab_session(trial_dir)
    harness = read_harness_run_info(trial_dir)
    model = (
        result.agent_info.model_info.name
        if result.agent_info.model_info is not None
        else result.config.agent.model_name
    )

    if model is None and harness is not None:
        model = harness.model

    rewards = (
        result.verifier_result.rewards if result.verifier_result is not None else None
    )

    return LabReportTrial(
        task_exposure=(
            session.task_exposure if session is not None else TaskExposureLabel.UNSEEN
        ),
        task_name=result.task_name,
        trial_name=result.trial_name,
        trial_path=str(trial_dir.resolve()),
        agent_name=result.agent_info.name,
        model=model,
        reward=_select_reward(rewards),
        rewards=rewards,
        exception_type=(
            result.exception_info.exception_type
            if result.exception_info is not None
            else None
        ),
        harness=harness,
    )


def _select_reward(rewards: dict[str, float | int] | None) -> float | int | None:
    if not rewards:
        return None
    for preferred_key in ("reward", "score"):
        if preferred_key in rewards:
            return rewards[preferred_key]
    first_key = sorted(rewards)[0]
    return rewards[first_key]


def _format_reward(trial: LabReportTrial) -> str:
    if trial.reward is None:
        return ""
    if trial.rewards is None or len(trial.rewards) <= 1:
        return str(trial.reward)
    return f"{trial.reward} ({', '.join(sorted(trial.rewards))})"


def _format_harness(harness: HarnessRunInfo | None) -> str:
    if harness is None:
        return ""

    details = [harness.harness_name]
    if harness.harness_version:
        details.append(harness.harness_version)
    if harness.profile:
        details.append(f"profile={harness.profile}")
    if harness.harness_git_sha:
        details.append(f"sha={harness.harness_git_sha}")
    return " ".join(details)


def _format_from_suffix(path: Path) -> Literal["json", "markdown"]:
    if path.suffix.lower() == ".json":
        return "json"
    return "markdown"


def _markdown_cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")
