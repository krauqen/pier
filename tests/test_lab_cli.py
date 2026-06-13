import json
from datetime import datetime
from pathlib import Path

from typer.testing import CliRunner

from pier.cli import lab
from pier.cli.main import app
from pier.models.task.id import LocalTaskId
from pier.models.trial.config import AgentConfig, TaskConfig, TrialConfig
from pier.models.trial.result import AgentInfo, ModelInfo, TrialResult
from pier.models.verifier.result import VerifierResult


runner = CliRunner()


def _write_trial_result(job_dir: Path, trial_name: str, task_name: str) -> Path:
    trial_dir = job_dir / trial_name
    trial_dir.mkdir(parents=True)
    task_path = Path("/tmp") / task_name
    config = TrialConfig(
        task=TaskConfig(path=task_path, source="test"),
        trial_name=trial_name,
        trials_dir=job_dir,
        agent=AgentConfig(name="claude-code", model_name="anthropic/claude-sonnet-4"),
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
            name="claude-code",
            version="1",
            model_info=ModelInfo(name="anthropic/claude-sonnet-4"),
        ),
        verifier_result=VerifierResult(rewards={"reward": 1}),
        started_at=datetime(2026, 6, 13, 12, 0),
        finished_at=datetime(2026, 6, 13, 12, 1),
    )
    (trial_dir / "result.json").write_text(
        result.model_dump_json(indent=2),
        encoding="utf-8",
    )
    return trial_dir


def test_lab_app_is_registered():
    result = runner.invoke(app, ["lab", "--help"])

    assert result.exit_code == 0
    assert "interactive" in result.output
    assert "attach" in result.output
    assert "finish" in result.output
    assert "abort" in result.output
    assert "report" in result.output
    assert "replay" in result.output


def test_read_task_names_file_skips_blank_lines_and_comments(tmp_path):
    tasks_file = tmp_path / "dev-seen.txt"
    tasks_file.write_text(
        "\n# comment\nastropy__astropy-1\n\nsympy__sympy-2\n",
        encoding="utf-8",
    )

    assert lab.read_task_names_file(tasks_file) == [
        "astropy__astropy-1",
        "sympy__sympy-2",
    ]


def test_lab_interactive_delegates_to_job_interactive_with_split_file(
    tmp_path, monkeypatch
):
    calls = []
    tasks_file = tmp_path / "regression.txt"
    tasks_file.write_text("task-b\ntask-c\n", encoding="utf-8")

    def fake_interactive(**kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(lab, "job_interactive", fake_interactive)

    lab.interactive(
        path=tmp_path / "dataset",
        dataset_task_names=["task-a"],
        tasks_file=tasks_file,
        disable_verification=True,
        pre_verification_patch=tmp_path / "fix.diff",
        yes=True,
    )

    assert calls[0]["path"] == tmp_path / "dataset"
    assert calls[0]["dataset_task_names"] == ["task-a", "task-b", "task-c"]
    assert calls[0]["disable_verification"] is True
    assert calls[0]["pre_verification_patch"] == tmp_path / "fix.diff"
    assert calls[0]["yes"] is True


def test_lab_replay_delegates_to_job_start_with_harness_import_path(
    tmp_path, monkeypatch
):
    calls = []
    tasks_file = tmp_path / "holdout.txt"
    tasks_file.write_text("task-a\n", encoding="utf-8")

    def fake_start(**kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(lab, "job_start", fake_start)

    lab.replay(
        path=tmp_path / "dataset",
        tasks_file=tasks_file,
        agent_import_path="examples.agents.benchmaxx_harness:DummyBenchmaxxHarnessAgent",
        agent_kwargs=["profile=default"],
        disable_verification=True,
    )

    assert calls[0]["agent_import_path"] == (
        "examples.agents.benchmaxx_harness:DummyBenchmaxxHarnessAgent"
    )
    assert calls[0]["dataset_task_names"] == ["task-a"]
    assert calls[0]["agent_kwargs"] == ["profile=default"]
    assert calls[0]["disable_verification"] is True


def test_lab_finish_attach_and_abort_delegate(monkeypatch, tmp_path):
    calls = []

    monkeypatch.setattr(
        lab,
        "job_finish",
        lambda **kwargs: calls.append(("finish", kwargs)),
    )
    monkeypatch.setattr(
        lab,
        "job_attach",
        lambda **kwargs: calls.append(("attach", kwargs)),
    )
    monkeypatch.setattr(
        lab,
        "job_abort",
        lambda **kwargs: calls.append(("abort", kwargs)),
    )

    lab.finish("trial-id", jobs_dir=tmp_path / "jobs")
    lab.attach("trial-id", jobs_dir=tmp_path / "jobs", execute=False)
    lab.abort("trial-id", jobs_dir=tmp_path / "jobs")

    assert calls == [
        ("finish", {"trial_id": "trial-id", "jobs_dir": tmp_path / "jobs"}),
        (
            "attach",
            {
                "trial_id": "trial-id",
                "jobs_dir": tmp_path / "jobs",
                "execute": False,
            },
        ),
        ("abort", {"trial_id": "trial-id", "jobs_dir": tmp_path / "jobs"}),
    ]


def test_lab_report_cli_writes_markdown_and_json(tmp_path):
    job_dir = tmp_path / "jobs" / "job-a"
    _write_trial_result(job_dir, "trial-a", "task-a")
    md_path = tmp_path / "report.md"
    json_path = tmp_path / "report.json"

    md_result = runner.invoke(
        app, ["lab", "report", str(job_dir), "--out", str(md_path)]
    )
    json_result = runner.invoke(
        app, ["lab", "report", str(job_dir), "--out", str(json_path)]
    )

    assert md_result.exit_code == 0
    assert json_result.exit_code == 0
    assert md_path.read_text(encoding="utf-8").startswith("# Pier Lab Report\n")
    assert json.loads(json_path.read_text(encoding="utf-8"))["total_trials"] == 1
