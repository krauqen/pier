from __future__ import annotations

from pathlib import Path
from typing import Annotated

from rich.console import Console
from typer import Argument, Option, Typer

from pier.cli.jobs import abort as job_abort
from pier.cli.jobs import attach as job_attach
from pier.cli.jobs import finish as job_finish
from pier.cli.jobs import interactive as job_interactive
from pier.cli.jobs import start as job_start
from pier.lab.metadata import TaskExposureLabel
from pier.lab.report import build_lab_report, write_lab_report
from pier.models.environment_type import EnvironmentType
from pier.models.job.config import JobConfig
from pier.models.trial.config import ResourceMode


lab_app = Typer(
    no_args_is_help=True, context_settings={"help_option_names": ["-h", "--help"]}
)
console = Console()


def read_task_names_file(path: Path | str) -> list[str]:
    task_names: list[str] = []
    for raw_line in Path(path).read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        task_names.append(line)
    return task_names


def _merge_task_names(
    task_names: list[str] | None, tasks_file: Path | None
) -> list[str] | None:
    merged = list(task_names or [])
    if tasks_file is not None:
        merged.extend(read_task_names_file(tasks_file))
    return merged or None


@lab_app.command()
def interactive(
    config_path: Annotated[
        Path | None,
        Option(
            "-c",
            "--config",
            help="A job configuration path in yaml or json format.",
            rich_help_panel="Config",
            show_default=False,
        ),
    ] = None,
    job_name: Annotated[
        str | None,
        Option(
            "--job-name",
            help="Name of the job (default: timestamp)",
            rich_help_panel="Job Settings",
            show_default=False,
        ),
    ] = None,
    jobs_dir: Annotated[
        Path | None,
        Option(
            "-o",
            "--jobs-dir",
            help="Directory to store job results",
            rich_help_panel="Job Settings",
            show_default=False,
        ),
    ] = None,
    path: Annotated[
        Path | None,
        Option(
            "-p",
            "--path",
            help="Path to a local task or dataset directory",
            rich_help_panel="Dataset",
            show_default=False,
        ),
    ] = None,
    dataset_task_names: Annotated[
        list[str] | None,
        Option(
            "-i",
            "--include-task-name",
            help="Task name to include from dataset (supports glob patterns)",
            rich_help_panel="Dataset",
            show_default=False,
        ),
    ] = None,
    tasks_file: Annotated[
        Path | None,
        Option(
            "--tasks-file",
            "--split-file",
            help="File containing task names to include from the dataset.",
            rich_help_panel="Dataset",
            show_default=False,
        ),
    ] = None,
    dataset_exclude_task_names: Annotated[
        list[str] | None,
        Option(
            "-x",
            "--exclude-task-name",
            help="Task name to exclude from dataset (supports glob patterns)",
            rich_help_panel="Dataset",
            show_default=False,
        ),
    ] = None,
    n_tasks: Annotated[
        int | None,
        Option(
            "-l",
            "--n-tasks",
            help="Maximum number of tasks to run",
            rich_help_panel="Dataset",
            show_default=False,
        ),
    ] = None,
    sample_seed: Annotated[
        int | None,
        Option(
            "--sample-seed",
            help="Seed for deterministic random task sampling/order.",
            rich_help_panel="Dataset",
            show_default=False,
        ),
    ] = None,
    environment_force_build: Annotated[
        bool | None,
        Option(
            "--force-build/--no-force-build",
            help="Whether to force rebuild the environment",
            rich_help_panel="Environment",
            show_default=False,
        ),
    ] = None,
    environment_delete: Annotated[
        bool,
        Option(
            "--delete/--no-delete",
            help="Whether to delete the environment after completion",
            rich_help_panel="Environment",
        ),
    ] = False,
    install_codex: Annotated[
        bool,
        Option(
            "--install-codex/--no-install-codex",
            help="Install Codex CLI in the interactive SSH container.",
            rich_help_panel="Interactive",
        ),
    ] = False,
    ssh_user: Annotated[
        str | None,
        Option(
            "--ssh-user",
            help="SSH username to configure inside the container.",
            rich_help_panel="Interactive",
            show_default=False,
        ),
    ] = None,
    public_key_path: Annotated[
        Path | None,
        Option(
            "--interactive-public-key",
            help="Existing public key to authorize instead of generating one.",
            rich_help_panel="Interactive",
            show_default=False,
        ),
    ] = None,
    allow_root_login: Annotated[
        bool,
        Option(
            "--allow-root-login/--no-allow-root-login",
            help="Allow key-only root SSH login when --ssh-user root is used.",
            rich_help_panel="Interactive",
        ),
    ] = False,
    poll_interval_sec: Annotated[
        float,
        Option(
            "--poll-interval-sec",
            help="Seconds between finish/abort signal checks.",
            rich_help_panel="Interactive",
        ),
    ] = 1.0,
    wait_timeout_sec: Annotated[
        float,
        Option(
            "--wait-timeout-sec",
            help="Maximum time the interactive agent can wait.",
            rich_help_panel="Interactive",
        ),
    ] = 86400.0,
    task_exposure: Annotated[
        TaskExposureLabel,
        Option(
            "--task-exposure",
            help="Lab exposure label to record in lab/session.json.",
            rich_help_panel="Interactive",
        ),
    ] = TaskExposureLabel.SEEN_INTERACTIVE,
    operator: Annotated[
        str | None,
        Option(
            "--operator",
            help="Operator label to record in lab/session.json.",
            rich_help_panel="Interactive",
            show_default=False,
        ),
    ] = "human",
    disable_verification: Annotated[
        bool,
        Option(
            "--disable-verification/--enable-verification",
            help="Disable task verification after finish.",
            rich_help_panel="Job Settings",
            show_default=False,
        ),
    ] = False,
    quiet: Annotated[
        bool,
        Option(
            "-q",
            "--quiet",
            "--silent",
            help="Suppress individual trial progress displays",
            rich_help_panel="Job Settings",
            show_default=False,
        ),
    ] = False,
    debug: Annotated[
        bool,
        Option(
            "--debug",
            help="Enable debug logging",
            rich_help_panel="Job Settings",
            show_default=False,
        ),
    ] = False,
    yes: Annotated[
        bool,
        Option(
            "-y",
            "--yes",
            help="Auto-confirm host environment variable access.",
            rich_help_panel="Job Settings",
        ),
    ] = False,
    env_file: Annotated[
        Path | None,
        Option(
            "--env-file",
            help="Path to a .env file to load into environment.",
            rich_help_panel="Job Settings",
        ),
    ] = None,
):
    """Start a benchmaxx lab interactive trial."""
    return job_interactive(
        config_path=config_path,
        job_name=job_name,
        jobs_dir=jobs_dir,
        path=path,
        dataset_task_names=_merge_task_names(dataset_task_names, tasks_file),
        dataset_exclude_task_names=dataset_exclude_task_names,
        n_tasks=n_tasks,
        sample_seed=sample_seed,
        environment_force_build=environment_force_build,
        environment_delete=environment_delete,
        install_codex=install_codex,
        ssh_user=ssh_user,
        public_key_path=public_key_path,
        allow_root_login=allow_root_login,
        poll_interval_sec=poll_interval_sec,
        wait_timeout_sec=wait_timeout_sec,
        task_exposure=task_exposure,
        operator=operator,
        disable_verification=disable_verification,
        quiet=quiet,
        debug=debug,
        yes=yes,
        env_file=env_file,
    )


@lab_app.command()
def attach(
    trial_id: Annotated[str, Argument(help="Trial id or trial directory path.")],
    jobs_dir: Annotated[
        Path,
        Option(
            "-o",
            "--jobs-dir",
            help="Directory containing Pier job results.",
            rich_help_panel="Job Settings",
        ),
    ] = JobConfig.model_fields["jobs_dir"].default,
    execute: Annotated[
        bool,
        Option(
            "--execute/--print-only",
            help="Execute ssh immediately, or only print the generated command.",
            rich_help_panel="Interactive",
        ),
    ] = True,
):
    """Attach to a running lab interactive SSH trial."""
    return job_attach(trial_id=trial_id, jobs_dir=jobs_dir, execute=execute)


@lab_app.command()
def finish(
    trial_id: Annotated[str, Argument(help="Trial id or trial directory path.")],
    jobs_dir: Annotated[
        Path,
        Option(
            "-o",
            "--jobs-dir",
            help="Directory containing Pier job results.",
            rich_help_panel="Job Settings",
        ),
    ] = JobConfig.model_fields["jobs_dir"].default,
):
    """Signal a lab interactive trial to finish and continue to verification."""
    return job_finish(trial_id=trial_id, jobs_dir=jobs_dir)


@lab_app.command()
def abort(
    trial_id: Annotated[str, Argument(help="Trial id or trial directory path.")],
    jobs_dir: Annotated[
        Path,
        Option(
            "-o",
            "--jobs-dir",
            help="Directory containing Pier job results.",
            rich_help_panel="Job Settings",
        ),
    ] = JobConfig.model_fields["jobs_dir"].default,
):
    """Signal a lab interactive trial to abort before verification."""
    return job_abort(trial_id=trial_id, jobs_dir=jobs_dir)


@lab_app.command()
def report(
    job_dirs: Annotated[
        list[Path],
        Argument(help="Pier job directories to include in the lab report."),
    ],
    out: Annotated[
        Path,
        Option(
            "-o",
            "--out",
            help="Output path. Use .json for JSON; other suffixes write Markdown.",
        ),
    ],
):
    """Write a deterministic Markdown or JSON report for Pier lab jobs."""
    output_path = write_lab_report(build_lab_report(job_dirs), out)
    console.print(f"Wrote lab report: {output_path}")


@lab_app.command()
def replay(
    config_path: Annotated[
        Path | None,
        Option(
            "-c",
            "--config",
            help="A job configuration path in yaml or json format.",
            rich_help_panel="Config",
            show_default=False,
        ),
    ] = None,
    job_name: Annotated[
        str | None,
        Option(
            "--job-name",
            help="Name of the job (default: timestamp)",
            rich_help_panel="Job Settings",
            show_default=False,
        ),
    ] = None,
    jobs_dir: Annotated[
        Path | None,
        Option(
            "-o",
            "--jobs-dir",
            help="Directory to store job results.",
            rich_help_panel="Job Settings",
            show_default=False,
        ),
    ] = None,
    path: Annotated[
        Path | None,
        Option(
            "-p",
            "--path",
            help="Path to a local task or dataset directory.",
            rich_help_panel="Dataset",
            show_default=False,
        ),
    ] = None,
    dataset_task_names: Annotated[
        list[str] | None,
        Option(
            "-i",
            "--include-task-name",
            help="Task name to include from dataset (supports glob patterns).",
            rich_help_panel="Dataset",
            show_default=False,
        ),
    ] = None,
    tasks_file: Annotated[
        Path | None,
        Option(
            "--tasks-file",
            "--split-file",
            help="File containing task names to include from the dataset.",
            rich_help_panel="Dataset",
            show_default=False,
        ),
    ] = None,
    dataset_exclude_task_names: Annotated[
        list[str] | None,
        Option(
            "-x",
            "--exclude-task-name",
            help="Task name to exclude from dataset (supports glob patterns).",
            rich_help_panel="Dataset",
            show_default=False,
        ),
    ] = None,
    n_tasks: Annotated[
        int | None,
        Option(
            "-l",
            "--n-tasks",
            help="Maximum number of tasks to run.",
            rich_help_panel="Dataset",
            show_default=False,
        ),
    ] = None,
    sample_seed: Annotated[
        int | None,
        Option(
            "--sample-seed",
            help="Seed for deterministic random task sampling/order.",
            rich_help_panel="Dataset",
            show_default=False,
        ),
    ] = None,
    agent_import_path: Annotated[
        str,
        Option(
            "--agent-import-path",
            help="Import path for the harness agent.",
            rich_help_panel="Harness",
        ),
    ] = ...,
    model_names: Annotated[
        list[str] | None,
        Option(
            "-m",
            "--model",
            help="Model name for the harness agent.",
            rich_help_panel="Harness",
            show_default=True,
        ),
    ] = None,
    agent_kwargs: Annotated[
        list[str] | None,
        Option(
            "--ak",
            "--agent-kwarg",
            help="Additional harness agent kwarg in the format 'key=value'.",
            rich_help_panel="Harness",
            show_default=False,
        ),
    ] = None,
    agent_env: Annotated[
        list[str] | None,
        Option(
            "--ae",
            "--agent-env",
            help="Environment variable to pass to the harness agent in KEY=VALUE format.",
            rich_help_panel="Harness",
            show_default=False,
        ),
    ] = None,
    environment_type: Annotated[
        EnvironmentType | None,
        Option(
            "-e",
            "--env",
            help="Environment type.",
            rich_help_panel="Environment",
            show_default=False,
        ),
    ] = None,
    environment_import_path: Annotated[
        str | None,
        Option(
            "--environment-import-path",
            help="Import path for custom environment (module.path:ClassName).",
            rich_help_panel="Environment",
            show_default=False,
        ),
    ] = None,
    environment_force_build: Annotated[
        bool | None,
        Option(
            "--force-build/--no-force-build",
            help="Whether to force rebuild the environment.",
            rich_help_panel="Environment",
            show_default=False,
        ),
    ] = None,
    environment_delete: Annotated[
        bool | None,
        Option(
            "--delete/--no-delete",
            help="Whether to delete the environment after completion.",
            rich_help_panel="Environment",
            show_default=False,
        ),
    ] = None,
    cpus: Annotated[
        ResourceMode | None,
        Option(
            "--cpus",
            help="How to apply task CPU resources.",
            rich_help_panel="Environment",
            show_default=False,
        ),
    ] = None,
    memory: Annotated[
        ResourceMode | None,
        Option(
            "--memory",
            help="How to apply task memory resources.",
            rich_help_panel="Environment",
            show_default=False,
        ),
    ] = None,
    quiet: Annotated[
        bool,
        Option(
            "-q",
            "--quiet",
            "--silent",
            help="Suppress individual trial progress displays.",
            rich_help_panel="Job Settings",
            show_default=False,
        ),
    ] = False,
    debug: Annotated[
        bool,
        Option(
            "--debug",
            help="Enable debug logging.",
            rich_help_panel="Job Settings",
            show_default=False,
        ),
    ] = False,
    yes: Annotated[
        bool,
        Option(
            "-y",
            "--yes",
            help="Auto-confirm host environment variable access.",
            rich_help_panel="Job Settings",
        ),
    ] = False,
    env_file: Annotated[
        Path | None,
        Option(
            "--env-file",
            help="Path to a .env file to load into environment.",
            rich_help_panel="Job Settings",
        ),
    ] = None,
    disable_verification: Annotated[
        bool,
        Option(
            "--disable-verification/--enable-verification",
            help="Disable task verification.",
            rich_help_panel="Job Settings",
            show_default=False,
        ),
    ] = False,
):
    """Replay tasks with a harness agent import path."""
    return job_start(
        config_path=config_path,
        job_name=job_name,
        jobs_dir=jobs_dir,
        agent_name=None,
        agent_import_path=agent_import_path,
        model_names=model_names,
        agent_kwargs=agent_kwargs,
        agent_env=agent_env,
        environment_type=environment_type,
        environment_import_path=environment_import_path,
        environment_force_build=environment_force_build,
        environment_delete=environment_delete,
        cpus=cpus,
        memory=memory,
        quiet=quiet,
        debug=debug,
        path=path,
        dataset_task_names=_merge_task_names(dataset_task_names, tasks_file),
        dataset_exclude_task_names=dataset_exclude_task_names,
        n_tasks=n_tasks,
        sample_seed=sample_seed,
        yes=yes,
        env_file=env_file,
        disable_verification=disable_verification,
    )
