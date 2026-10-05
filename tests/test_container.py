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
# What .dockerignore lets into the build stage; a new file at the repo root must not appear here.
BUILD_CONTEXT = ["CHANGELOG.md", "LICENSE", "README.md", "pyproject.toml", "src", "uv.lock"]


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


def test_dockerignore_starts_by_excluding_everything() -> None:
    effective = [line for line in _read_lines(".dockerignore") if line and not line.startswith("#")]

    assert effective[0] == "*"


def _docker(*args: str) -> str:
    result = subprocess.run(
        ["docker", *args], check=False, capture_output=True, text=True, encoding="utf-8"
    )
    if result.returncode != 0:
        pytest.fail(f"docker {' '.join(args)} exited {result.returncode}:\n{result.stderr}")
    return result.stdout


@pytest.fixture(scope="session")
def docker_images() -> Iterator[None]:
    if shutil.which("docker") is None:
        pytest.skip("docker is not on PATH")
    # The build stage holds the whole build context, so it shows what .dockerignore lets in;
    # the runtime stage only ever copies the wheel and the lock export.
    _docker("build", "--target", "build", "-t", BUILD_IMAGE, str(ROOT))
    _docker("build", "-t", IMAGE, str(ROOT))
    yield
    _docker("rmi", "-f", IMAGE, BUILD_IMAGE)


def _run(*args: str) -> str:
    return _docker("run", "--rm", "--read-only", "--tmpfs", "/tmp", *args)


@pytest.mark.container
@pytest.mark.usefixtures("docker_images")
def test_image_prints_the_package_version() -> None:
    output = _run(IMAGE, "--version")

    assert output.strip() == f"detecttrace {version('detecttrace')}"


@pytest.fixture
def data_volume() -> Iterator[str]:
    name = f"detecttrace-test-{uuid.uuid4().hex[:12]}"
    _docker("volume", "create", name)
    yield name
    _docker("volume", "rm", "-f", name)


@pytest.mark.container
@pytest.mark.usefixtures("docker_images")
def test_demo_writes_a_dashboard_to_the_data_volume(data_volume: str) -> None:
    _run("-v", f"{data_volume}:/data", IMAGE, "demo", "--out", "/data/demo.html")

    output = _run(
        "-v", f"{data_volume}:/data", "--entrypoint", "tail", IMAGE, "-c", "16", "/data/demo.html"
    )

    assert "</html>" in output


@pytest.mark.container
@pytest.mark.usefixtures("docker_images")
def test_dashboard_in_the_data_volume_is_owned_by_the_non_root_user(data_volume: str) -> None:
    # A named volume, because a bind mount's owner as the host sees it is remapped on macOS.
    _run("-v", f"{data_volume}:/data", IMAGE, "demo", "--out", "/data/demo.html")

    output = _run(
        "-v", f"{data_volume}:/data", "--entrypoint", "stat", IMAGE, "-c", "%u", "/data/demo.html"
    )

    assert output.strip() == NON_ROOT_UID


@pytest.mark.container
@pytest.mark.usefixtures("docker_images")
def test_build_stage_holds_only_the_allow_listed_files() -> None:
    output = _run("--entrypoint", "ls", BUILD_IMAGE, "-A", "/src")

    assert sorted(output.split()) == BUILD_CONTEXT


@pytest.mark.container
@pytest.mark.usefixtures("docker_images")
def test_runtime_image_has_no_source_folder() -> None:
    output = _run("--entrypoint", "sh", IMAGE, "-c", "test -e /src && echo present || echo absent")

    assert output.strip() == "absent"


@pytest.mark.container
@pytest.mark.usefixtures("docker_images")
def test_zstd_extra_is_installed() -> None:
    output = _run("--entrypoint", "python", IMAGE, "-c", "import zstandard; print('ok')")

    assert output.strip() == "ok"


@pytest.mark.container
@pytest.mark.usefixtures("docker_images")
def test_installed_package_files_are_the_tracked_sources() -> None:
    listing = _run(
        "--entrypoint",
        "sh",
        IMAGE,
        "-c",
        'cd "$(python -c "import detecttrace, os; print(os.path.dirname(detecttrace.__path__[0]))")"'
        " && find detecttrace -type f -not -path '*/__pycache__/*'",
    )
    tracked = subprocess.run(
        ["git", "ls-files", "src/detecttrace"],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=ROOT,
    ).stdout

    assert sorted(listing.split()) == sorted(line.removeprefix("src/") for line in tracked.split())
