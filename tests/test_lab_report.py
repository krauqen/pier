import json
from datetime import datetime
from pathlib import Path

from pier.lab.metadata import (
    HarnessRunInfo,
    LabSession,
    TaskExposureLabel,
    write_harness_run_info,
    write_lab_session,
)
from pier.lab.report import (
    build_lab_report,
    render_lab_report_json,
    render_lab_report_markdown,
    write_lab_report,
)
from pier.models.task.id import LocalTaskId
from pier.models.trial.config import AgentConfig, TaskConfig, TrialConfig
from pier.models.trial.result import AgentInfo, ExceptionInfo, ModelInfo, TrialResult
from pier.models.verifier.result import VerifierResult


def _write_trial_result(
    job_dir: Path,
    trial_name: str,
    *,
    task_name: str,
    agent_name: str = "claude-code",
    model_name: str | None = "anthropic/claude-sonnet-4",
    rewards: dict[str, float | int] | None = None,
    exception_type: str | None = None,
) -> Path:
    trial_dir = job_dir / trial_name
    trial_dir.mkdir(parents=True)
    task_path = Path("/tmp") / task_name
    config = TrialConfig(
        task=TaskConfig(path=task_path, source="test"),
        trial_name=trial_name,
        trials_dir=job_dir,
        agent=AgentConfig(name=agent_name, model_name=model_name),
    )
    result = TrialResult(
        task_name=task_name,
        trial_name=trial_name,
        trial_uri=trial_dir.resolve().as_uri(),
        task_id=LocalTaskId(path=task_path),
        source="test",
        task_checksum=f"checksum-{task_name}",
        config=config,
        agent_info=AgentInfo(
            name=agent_name,
            version="1",
            model_info=(ModelInfo(name=model_name) if model_name is not None else None),
        ),
        verifier_result=(
            VerifierResult(rewards=rewards) if rewards is not None else None
        ),
        exception_info=(
            ExceptionInfo(
                exception_type=exception_type,
                exception_message="boom",
                exception_traceback="traceback",
                occurred_at=datetime(2026, 6, 13, 12, 0),
            )
            if exception_type is not None
            else None
        ),
    )
    (trial_dir / "result.json").write_text(
        result.model_dump_json(indent=2),
        encoding="utf-8",
    )
    return trial_dir


def _write_session(trial_dir: Path, exposure: TaskExposureLabel) -> None:
    write_lab_session(
        trial_dir,
        LabSession(
            mode="interactive",
            task_name=trial_dir.name,
            task_exposure=exposure,
        ),
    )


def test_lab_report_groups_trials_by_exposure_and_tolerates_missing_metadata(
    tmp_path,
):
    job_dir = tmp_path / "jobs" / "job-a"
    unseen_trial = _write_trial_result(
        job_dir,
        "z-unseen",
        task_name="task-unseen",
        rewards={"reward": 0},
    )
    interactive_trial = _write_trial_result(
        job_dir,
        "a-interactive",
        task_name="task-interactive",
        rewards={"score": 1, "tests": 7},
    )
    regression_trial = _write_trial_result(
        job_dir,
        "m-regression",
        task_name="task-regression",
        rewards={"reward": 0},
        exception_type="RuntimeError",
    )
    holdout_trial = _write_trial_result(
        job_dir,
        "b-holdout",
        task_name="task-holdout",
        rewards={"reward": 1},
    )
    _write_session(interactive_trial, TaskExposureLabel.SEEN_INTERACTIVE)
    _write_session(regression_trial, TaskExposureLabel.REGRESSION)
    _write_session(holdout_trial, TaskExposureLabel.HOLDOUT)

    report = build_lab_report([job_dir])

    assert report.total_trials == 4
    assert unseen_trial.name == "z-unseen"
    assert [
        trial.task_name for trial in report.exposures[TaskExposureLabel.UNSEEN].trials
    ] == ["task-unseen"]
    assert report.exposures[TaskExposureLabel.SEEN_INTERACTIVE].trials[0].reward == 1
    assert (
        report.exposures[TaskExposureLabel.REGRESSION].trials[0].exception_type
        == "RuntimeError"
    )
    assert report.exposures[TaskExposureLabel.HOLDOUT].trials[0].trial_path == str(
        holdout_trial.resolve()
    )


def test_lab_report_includes_harness_info_and_model_fallback(tmp_path):
    job_dir = tmp_path / "jobs" / "job-a"
    trial_dir = _write_trial_result(
        job_dir,
        "trial",
        task_name="task",
        agent_name="benchmaxx",
        model_name=None,
        rewards={"pass": 1},
    )
    _write_session(trial_dir, TaskExposureLabel.SEEN_LOGS_ONLY)
    write_harness_run_info(
        trial_dir,
        HarnessRunInfo(
            harness_name="benchmaxx-agent",
            harness_version="0.1.0",
            harness_git_sha="abc123",
            profile="default",
            base_agent="claude-code",
            model="anthropic/claude-opus-4",
        ),
    )

    report = build_lab_report([job_dir])
    trial = report.exposures[TaskExposureLabel.SEEN_LOGS_ONLY].trials[0]

    assert trial.model == "anthropic/claude-opus-4"
    assert trial.reward == 1
    assert trial.harness is not None
    assert trial.harness.harness_name == "benchmaxx-agent"
    assert trial.harness.harness_git_sha == "abc123"
    assert "benchmaxx-agent 0.1.0 profile=default sha=abc123" in (
        render_lab_report_markdown(report)
    )


def test_lab_report_renderers_are_deterministic(tmp_path):
    job_b = tmp_path / "jobs" / "job-b"
    job_a = tmp_path / "jobs" / "job-a"
    _write_trial_result(job_b, "trial-b", task_name="task-b", rewards={"reward": 1})
    _write_trial_result(job_a, "trial-a", task_name="task-a", rewards={"reward": 0})

    report = build_lab_report([job_b, job_a, job_b])
    json_report = render_lab_report_json(report)
    markdown_report = render_lab_report_markdown(report)

    payload = json.loads(json_report)
    assert payload["job_dirs"] == [str(job_a), str(job_b)]
    assert payload["exposures"]["unseen"]["n_trials"] == 2
    assert [
        trial["task_name"] for trial in payload["exposures"]["unseen"]["trials"]
    ] == ["task-a", "task-b"]
    assert "## unseen (2)" in markdown_report
    assert "| task-a | 0 |  | claude-code | anthropic/claude-sonnet-4 |  |" in (
        markdown_report
    )
    assert "## regression (0)\n\nNo trials." in markdown_report


def test_write_lab_report_uses_suffix_to_choose_format(tmp_path):
    job_dir = tmp_path / "jobs" / "job"
    _write_trial_result(job_dir, "trial", task_name="task", rewards={"reward": 1})
    report = build_lab_report([job_dir])

    json_path = write_lab_report(report, tmp_path / "report.json")
    md_path = write_lab_report(report, tmp_path / "report.md")

    assert json.loads(json_path.read_text(encoding="utf-8"))["total_trials"] == 1
    assert md_path.read_text(encoding="utf-8").startswith("# Pier Lab Report\n")
