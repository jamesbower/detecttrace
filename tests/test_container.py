"""The container image: static checks of its recipe, and Docker checks of the image it builds."""

import re
import shutil
import subprocess
import uuid
from collections.abc import Iterator
from importlib.metadata import version
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
IMAGE = "detecttrace:test"
BUILD_IMAGE = "detecttrace:test-build"
NON_ROOT_UID = "10001"
# Unreadable folders make find exit non-zero as the non-root user; the listing is what counts.
PRIVATE_FILES_FIND = (
    "find / \\( -path /proc -o -path /sys \\) -prune -o "
    r'\( -name "*prd*" -o -name "lessons.md" -o -name "CLAUDE.md" -o -name notes \) -print 2>/dev/null; true'
)


def _read_lines(name: str) -> list[str]:
    return (ROOT / name).read_text(encoding="utf-8").splitlines()


def test_every_base_image_is_pinned_by_digest() -> None:
    from_lines = [line for line in _read_lines("Dockerfile") if line.startswith("FROM ")]

    unpinned = [line for line in from_lines if not re.search(r"@sha256:[0-9a-f]{64}(\s|$)", line)]

    assert unpinned == []


def test_dockerfile_has_a_build_and_a_runtime_stage() -> None:
    from_lines = [line for line in _read_lines("Dockerfile") if line.startswith("FROM ")]

    assert len(from_lines) >= 2


def test_runtime_stage_runs_as_a_non_root_user() -> None:
    lines = _read_lines("Dockerfile")
    last_from = max(i for i, line in enumerate(lines) if line.startswith("FROM "))

    users = [line for line in lines[last_from:] if line.startswith("USER ")]

    assert users[-1:] == ["USER detecttrace"]


@pytest.mark.parametrize("entry", ["notes/", "tasks/", ".git/", ".venv/", "CLAUDE.md", ".claude/"])
def test_dockerignore_excludes_private_paths(entry: str) -> None:
    assert entry in _read_lines(".dockerignore")


@pytest.fixture(scope="session")
def docker_images() -> None:
    if shutil.which("docker") is None:
        pytest.skip("docker is not on PATH")
    # The build stage holds the whole build context, so it shows what .dockerignore lets in;
    # the runtime stage only ever copies the wheel.
    subprocess.run(
        ["docker", "build", "--target", "build", "-t", BUILD_IMAGE, str(ROOT)], check=True
    )
    subprocess.run(["docker", "build", "-t", IMAGE, str(ROOT)], check=True)


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    command = ["docker", "run", "--rm", "--read-only", "--tmpfs", "/tmp", *args]
    return subprocess.run(command, check=True, capture_output=True, text=True, encoding="utf-8")


@pytest.mark.container
@pytest.mark.usefixtures("docker_images")
def test_image_prints_the_package_version() -> None:
    result = _run(IMAGE, "--version")

    assert result.stdout.strip() == f"detecttrace {version('detecttrace')}"


@pytest.fixture
def data_volume() -> Iterator[str]:
    name = f"detecttrace-test-{uuid.uuid4().hex[:12]}"
    subprocess.run(["docker", "volume", "create", name], check=True, capture_output=True)
    yield name
    subprocess.run(["docker", "volume", "rm", "-f", name], check=True, capture_output=True)


@pytest.mark.container
@pytest.mark.usefixtures("docker_images")
def test_demo_writes_a_dashboard_to_the_data_volume(data_volume: str) -> None:
    _run("-v", f"{data_volume}:/data", IMAGE, "demo", "--out", "/data/demo.html")

    result = _run(
        "-v", f"{data_volume}:/data", "--entrypoint", "tail", IMAGE, "-c", "16", "/data/demo.html"
    )

    assert "</html>" in result.stdout


@pytest.mark.container
@pytest.mark.usefixtures("docker_images")
def test_dashboard_in_the_data_volume_is_owned_by_the_non_root_user(data_volume: str) -> None:
    # A named volume, because a bind mount's owner as the host sees it is remapped on macOS.
    _run("-v", f"{data_volume}:/data", IMAGE, "demo", "--out", "/data/demo.html")

    result = _run(
        "-v", f"{data_volume}:/data", "--entrypoint", "stat", IMAGE, "-c", "%u", "/data/demo.html"
    )

    assert result.stdout.strip() == NON_ROOT_UID


@pytest.mark.container
@pytest.mark.usefixtures("docker_images")
@pytest.mark.parametrize("image", [IMAGE, BUILD_IMAGE])
def test_image_carries_no_private_file(image: str) -> None:
    result = _run("--entrypoint", "sh", image, "-c", PRIVATE_FILES_FIND)

    assert result.stdout.strip() == ""
