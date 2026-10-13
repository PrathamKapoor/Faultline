"""Validate the GitHub Actions workflows are structurally well-formed YAML.

The point is narrow: a typo in a workflow file only surfaces when GitHub parses
it, which is exactly when nobody is looking. This checks that each file loads,
declares jobs, and that every step has the two keys GitHub requires.
"""

from __future__ import annotations

from pathlib import Path

import pytest

WORKFLOWS = Path(__file__).resolve().parents[1] / ".github" / "workflows"

yaml = pytest.importorskip("yaml", reason="PyYAML is needed to validate workflow files")


@pytest.mark.parametrize("path", sorted(WORKFLOWS.glob("*.yml")), ids=lambda p: p.name)
def test_workflow_is_valid_yaml_with_named_jobs(path: Path) -> None:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))

    assert isinstance(data, dict), path.name
    assert "jobs" in data, f"{path.name} declares no jobs"
    assert data["jobs"], f"{path.name} declares an empty job list"


@pytest.mark.parametrize("path", sorted(WORKFLOWS.glob("*.yml")), ids=lambda p: p.name)
def test_every_step_is_structurally_valid(path: Path) -> None:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))

    for job_name, job in data["jobs"].items():
        assert "runs-on" in job, f"{path.name}:{job_name} has no runner"
        for step in job.get("steps", []):
            assert "uses" in step or "run" in step, (
                f"{path.name}:{job_name} has a step with no action"
            )
            assert not ({"uses", "run"} <= set(step)), (
                f"{path.name}:{job_name} has a step that both uses and runs"
            )


@pytest.mark.parametrize("path", sorted(WORKFLOWS.glob("*.yml")), ids=lambda p: p.name)
def test_no_workflow_requests_write_permissions(path: Path) -> None:
    """A build that runs target-shaped code should not be able to push anything."""

    data = yaml.safe_load(path.read_text(encoding="utf-8"))

    assert data.get("permissions") == {"contents": "read"}, path.name


def test_ci_workflow_never_executes_target_code() -> None:
    """The fast workflow must deselect every container-starting test.

    Checked per *step* rather than by searching the whole file, because the
    header comment explains that Docker is deliberately excluded from this
    workflow and must be able to say so.
    """

    data = yaml.safe_load((WORKFLOWS / "ci.yml").read_text(encoding="utf-8"))
    assert '-m "not integration"' in (WORKFLOWS / "ci.yml").read_text(encoding="utf-8")

    for job_name, job in data["jobs"].items():
        for step in job.get("steps", []):
            script = str(step.get("run", ""))
            assert "docker " not in script, f"{job_name} would invoke Docker"
            assert "-m integration" not in script, f"{job_name} would run the container suite"


def test_the_docker_workflow_only_runs_synthetic_fixtures() -> None:
    """No workflow may execute a repository outside this project."""

    text = (WORKFLOWS / "docker-integration.yml").read_text(encoding="utf-8")
    assert "-m integration" in text
    assert "edgehunter benchmark --allow-execution" in text
    for forbidden in ("pull_request_target", "secrets.", "GITHUB_TOKEN"):
        assert forbidden not in text, forbidden


def test_the_docker_workflow_rebuilds_the_worker_image() -> None:
    """A cached image by tag is how a previous worker survives a source edit."""

    text = (WORKFLOWS / "docker-integration.yml").read_text(encoding="utf-8")
    assert "docker image rm edgehunter-runtime:1" in text


def test_the_docker_workflow_asserts_container_cleanup() -> None:
    text = (WORKFLOWS / "docker-integration.yml").read_text(encoding="utf-8")
    assert "no EdgeHunter containers remain" in text
    assert "edgehunter-run-" in text
