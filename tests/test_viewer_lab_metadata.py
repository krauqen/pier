from __future__ import annotations

from datetime import datetime
from pathlib import Path

from fastapi.testclient import TestClient

from pier.lab.metadata import (
    HarnessRunInfo,
    LabSession,
    TaskExposureLabel,
    write_harness_run_info,
    write_lab_session,
)
from pier.models.task.id import LocalTaskId
from pier.models.trial.config import AgentConfig, TaskConfig, TrialConfig
from pier.models.trial.result import AgentInfo, ModelInfo, TrialResult
from pier.viewer.scanner import JobScanner
from pier.viewer.server import create_app


def _write_trial_result(
    job_dir: Path,
    trial_name: str,
    *,
    task_name: str = "task-a",
) -> Path:
    trial_dir = job_dir / trial_name
    trial_dir.mkdir(parents=True)
    task_path = Path("/tmp") / task_name
    config = TrialConfig(
        task=TaskConfig(path=task_path, source="test"),
        trial_name=trial_name,
        trials_dir=job_dir,
        agent=AgentConfig(name="benchmaxx", model_name="openai/gpt-5"),
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
            name="benchmaxx",
            version="1",
            model_info=ModelInfo(name="openai/gpt-5"),
        ),
        started_at=datetime(2026, 6, 13, 12, 0),
    )
    (trial_dir / "result.json").write_text(
        result.model_dump_json(indent=2),
        encoding="utf-8",
    )
    return trial_dir


def test_viewer_scanner_lab_metadata_tolerates_missing_files(tmp_path):
    jobs_dir = tmp_path / "jobs"
    job_dir = jobs_dir / "job-a"
    _write_trial_result(job_dir, "trial-a")

    scanner = JobScanner(jobs_dir)

    assert scanner.get_trial_lab_metadata("job-a", "trial-a") is None


def test_viewer_scanner_reads_lab_session_and_harness_metadata(tmp_path):
    jobs_dir = tmp_path / "jobs"
    job_dir = jobs_dir / "job-a"
    trial_dir = _write_trial_result(job_dir, "trial-a")
    write_lab_session(
        trial_dir,
        LabSession(
            mode="interactive",
            task_name="task-a",
            task_exposure=TaskExposureLabel.SEEN_INTERACTIVE,
            operator="human",
            tools=["ssh", "codex-app"],
            tags=["deepswe", "debug"],
        ),
    )
    write_harness_run_info(
        trial_dir,
        HarnessRunInfo(
            harness_name="benchmaxx-agent",
            harness_version="0.1.0",
            profile="default",
            model="openai/gpt-5",
        ),
    )

    metadata = JobScanner(jobs_dir).get_trial_lab_metadata("job-a", "trial-a")

    assert metadata is not None
    assert metadata.session is not None
    assert metadata.session.task_exposure == TaskExposureLabel.SEEN_INTERACTIVE
    assert metadata.session.tools == ["ssh", "codex-app"]
    assert metadata.harness is not None
    assert metadata.harness.harness_name == "benchmaxx-agent"
    assert metadata.harness.profile == "default"


def test_viewer_api_exposes_lab_metadata_without_affecting_plain_trials(tmp_path):
    jobs_dir = tmp_path / "jobs"
    job_dir = jobs_dir / "job-a"
    plain_trial = _write_trial_result(job_dir, "plain")
    lab_trial = _write_trial_result(job_dir, "lab")
    write_lab_session(
        lab_trial,
        LabSession(
            mode="replay",
            task_name="task-a",
            task_exposure=TaskExposureLabel.REGRESSION,
            operator="benchmaxx",
            tools=["harness"],
            tags=["regression"],
        ),
    )
    write_harness_run_info(
        lab_trial,
        HarnessRunInfo(
            harness_name="benchmaxx-agent",
            harness_version="0.2.0",
            profile="regression",
        ),
    )

    client = TestClient(create_app(jobs_dir, mode="jobs"))

    assert client.get("/api/jobs/job-a/trials/plain/lab-metadata").json() is None
    lab_metadata = client.get("/api/jobs/job-a/trials/lab/lab-metadata").json()
    assert lab_metadata["session"]["task_exposure"] == "regression"
    assert lab_metadata["session"]["mode"] == "replay"
    assert lab_metadata["harness"]["harness_name"] == "benchmaxx-agent"
    assert lab_metadata["harness"]["profile"] == "regression"

    trials = client.get("/api/jobs/job-a/trials").json()["items"]
    by_name = {trial["name"]: trial for trial in trials}
    assert by_name[plain_trial.name]["lab_metadata"] is None
    assert (
        by_name[lab_trial.name]["lab_metadata"]["harness"]["harness_version"] == "0.2.0"
    )
