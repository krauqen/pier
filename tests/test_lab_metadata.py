import json
from datetime import datetime, timezone

from pier.lab.metadata import (
    HarnessRunInfo,
    LabSession,
    TaskExposureLabel,
    harness_metadata_path,
    harness_metadata_path_from_agent_logs_dir,
    lab_dir,
    read_harness_run_info,
    read_harness_run_info_from_agent_logs_dir,
    read_lab_session,
    session_metadata_path,
    trial_dir_from_agent_logs_dir,
    write_harness_run_info,
    write_harness_run_info_from_agent_logs_dir,
    write_lab_session,
)


def test_lab_session_round_trips_with_stable_json(tmp_path):
    trial_dir = tmp_path / "trial"
    started_at = datetime(2026, 6, 13, 12, 0, tzinfo=timezone.utc)
    session = LabSession(
        mode="interactive",
        task_name="astropy__astropy-12345",
        dataset_path="/home/user/benchmarks/deepswe",
        task_exposure=TaskExposureLabel.SEEN_INTERACTIVE,
        started_at=started_at,
        workspace_path="/workspace",
        operator="human",
        tools=["ssh", "codex-app"],
        tags=["deepswe", "debug"],
    )

    path = write_lab_session(trial_dir, session)

    assert path == trial_dir / "lab" / "session.json"
    expected_json = {
        "schema_version": 1,
        "mode": "interactive",
        "task_name": "astropy__astropy-12345",
        "dataset_path": "/home/user/benchmarks/deepswe",
        "task_exposure": "seen_interactive",
        "started_at": "2026-06-13T12:00:00Z",
        "finished_at": None,
        "workspace_path": "/workspace",
        "operator": "human",
        "tools": ["ssh", "codex-app"],
        "followup_replay_job": None,
        "tags": ["deepswe", "debug"],
    }
    assert (
        path.read_text(encoding="utf-8") == json.dumps(expected_json, indent=2) + "\n"
    )
    assert read_lab_session(trial_dir) == session


def test_harness_run_info_round_trips_with_stable_json(tmp_path):
    trial_dir = tmp_path / "trial"
    info = HarnessRunInfo(
        harness_name="benchmaxx-agent",
        harness_version="0.1.0",
        harness_git_sha="abc123",
        command="benchmaxx-agent run --task-dir /task --workspace /workspace",
        config_hash="sha256:config",
        profile="default",
        base_agent="claude-code",
        model="anthropic/claude-fable-5",
    )

    path = write_harness_run_info(trial_dir, info)

    assert path == trial_dir / "lab" / "harness.json"
    expected_json = {
        "schema_version": 1,
        "harness_name": "benchmaxx-agent",
        "harness_version": "0.1.0",
        "harness_git_sha": "abc123",
        "command": "benchmaxx-agent run --task-dir /task --workspace /workspace",
        "config_hash": "sha256:config",
        "profile": "default",
        "base_agent": "claude-code",
        "model": "anthropic/claude-fable-5",
    }
    assert (
        path.read_text(encoding="utf-8") == json.dumps(expected_json, indent=2) + "\n"
    )
    assert read_harness_run_info(trial_dir) == info


def test_missing_lab_metadata_is_absent_without_creating_files(tmp_path):
    trial_dir = tmp_path / "trial"

    assert read_lab_session(trial_dir) is None
    assert read_harness_run_info(trial_dir) is None
    assert not lab_dir(trial_dir).exists()


def test_metadata_path_helpers_accept_strings(tmp_path):
    trial_dir = str(tmp_path / "trial")

    assert session_metadata_path(trial_dir).name == "session.json"
    assert harness_metadata_path(trial_dir).name == "harness.json"
    assert (
        session_metadata_path(trial_dir).parent
        == harness_metadata_path(trial_dir).parent
    )


def test_harness_metadata_helpers_accept_agent_logs_dir(tmp_path):
    trial_dir = tmp_path / "trial"
    logs_dir = trial_dir / "agent"
    info = HarnessRunInfo(harness_name="dummy")

    path = write_harness_run_info_from_agent_logs_dir(logs_dir, info)

    assert trial_dir_from_agent_logs_dir(logs_dir) == trial_dir
    assert harness_metadata_path_from_agent_logs_dir(logs_dir) == path
    assert path == trial_dir / "lab" / "harness.json"
    assert read_harness_run_info_from_agent_logs_dir(logs_dir) == info
