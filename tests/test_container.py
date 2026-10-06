"""The container image: static checks of its recipe, and Docker checks of the image it builds."""

import base64
import gzip
import re
import shutil
import subprocess
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from importlib.metadata import version
from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml
from page_data import read_results, read_view

from detecttrace.serve.auth import create_token, hash_token
from detecttrace.serve.config import load_serve_config

ROOT = Path(__file__).parent.parent
DEMO_FOLDER = ROOT / "src" / "detecttrace" / "demo_data"
# Unique per run, so parallel runs on one Docker host never share or remove each other's images.
RUN_ID = uuid.uuid4().hex[:12]
IMAGE = f"detecttrace-test-{RUN_ID}"
BUILD_IMAGE = f"detecttrace-test-{RUN_ID}-build"
NON_ROOT_UID = "10001"
# What .dockerignore lets into the build stage; a new file at the repo root must not appear here.
BUILD_CONTEXT = [
    "CHANGELOG.md",
    "LICENSE",
    "README.md",
    "THIRD_PARTY_NOTICES",
    "pyproject.toml",
    "src",
    "uv.lock",
]


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


def _read_compose() -> dict[str, Any]:
    return yaml.safe_load((ROOT / "compose.yaml").read_text(encoding="utf-8"))


def test_example_serve_config_loads() -> None:
    config = load_serve_config(ROOT / "deploy" / "detecttrace-serve.example.yaml")

    assert config.serve.database == ROOT.absolute() / "deploy" / "detecttrace.db"


def test_image_default_command_serves_the_config_in_the_data_volume() -> None:
    cmd_lines = [line for line in _read_lines("Dockerfile") if line.startswith("CMD ")]

    assert cmd_lines == ['CMD ["serve", "--config", "/data/detecttrace-serve.yaml"]']


def test_compose_publishes_every_port_on_loopback_only() -> None:
    services = _read_compose()["services"].values()

    ports = [port for service in services for port in service.get("ports", [])]

    assert [port for port in ports if not port.startswith("127.0.0.1:")] == []


def test_compose_pins_every_pulled_image_by_digest() -> None:
    services = _read_compose()["services"].values()

    pulled = [service["image"] for service in services if "build" not in service]

    assert [image for image in pulled if "@sha256:" not in image] == []


@pytest.mark.parametrize("service", ["detecttrace", "collector"])
def test_compose_runs_each_service_with_a_read_only_root(service: str) -> None:
    settings = _read_compose()["services"][service]

    assert settings["read_only"] is True


@pytest.mark.parametrize("service", ["detecttrace", "collector"])
def test_compose_drops_every_capability(service: str) -> None:
    settings = _read_compose()["services"][service]

    assert settings["cap_drop"] == ["ALL"]


@pytest.mark.parametrize("service", ["detecttrace", "collector"])
def test_compose_forbids_gaining_privileges(service: str) -> None:
    settings = _read_compose()["services"][service]

    assert "no-new-privileges:true" in settings["security_opt"]


@pytest.mark.parametrize("service", ["detecttrace", "collector"])
def test_compose_limits_processes(service: str) -> None:
    settings = _read_compose()["services"][service]

    assert isinstance(settings["pids_limit"], int)


@pytest.mark.parametrize("service", ["detecttrace", "collector"])
def test_compose_limits_memory(service: str) -> None:
    settings = _read_compose()["services"][service]

    assert isinstance(settings["mem_limit"], str)


def _read_collector_config() -> dict[str, Any]:
    return yaml.safe_load((ROOT / "deploy" / "collector.yaml").read_text(encoding="utf-8"))


def test_collector_receiver_demands_a_bearer_token() -> None:
    http = _read_collector_config()["receivers"]["otlp"]["protocols"]["http"]

    assert http["auth"] == {"authenticator": "bearertokenauth"}


def test_collector_limits_memory_before_anything_else() -> None:
    pipeline = _read_collector_config()["service"]["pipelines"]["traces"]

    assert pipeline["processors"][0] == "memory_limiter"


# The demo traces hold this many spans, and the verdict file this many rows.
DEMO_SPAN_COUNT = 2129
DEMO_VERDICT_COUNT = 201
SETTLED_SECONDS = 90
# Longer than the recompute debounce, so an unchanged generation means no run is pending.
QUIET_SECONDS = 7
STOP_GRACE_SECONDS = 30
# The online backup the docs describe; it must work under the read-only root.
BACKUP = (
    "import sqlite3; source = sqlite3.connect('/data/detecttrace.db'); "
    "target = sqlite3.connect('/data/backup.db'); source.backup(target); target.close()"
)
CHECK_BACKUP = (
    "import sqlite3; "
    "print(sqlite3.connect('/data/backup.db').execute('PRAGMA integrity_check').fetchone()[0])"
)


@dataclass(frozen=True)
class _Served:
    """The service's answers before and after restarts, read once for the tests below."""

    health: str
    dashboard_status: int
    dashboard_html: str
    first: tuple[dict[str, Any], bytes]
    after_restart: tuple[dict[str, Any], bytes]
    after_down_and_up: tuple[dict[str, Any], bytes]
    backup_check: str
    stop_seconds: float
    stop_exit_code: str
    unauthenticated_receiver_status: int


@dataclass(frozen=True)
class _Stack:
    project: str
    folder: Path
    container: str
    read_token: str

    def compose(self, *args: str) -> str:
        return _docker(
            "compose", "-p", self.project, "--project-directory", str(self.folder), *args
        )

    def url(self, service: str, port: int) -> str:
        # The test publishes on ephemeral host ports; they change whenever a container restarts.
        address = self.compose("port", service, str(port)).strip()
        return f"http://{address}"

    def read(self, path: str) -> httpx.Response:
        credentials = base64.b64encode(f"analyst:{self.read_token}".encode()).decode("ascii")
        return httpx.get(
            self.url("detecttrace", 4320) + path,
            headers={"Authorization": f"Basic {credentials}"},
            timeout=10,
        )


@pytest.fixture(scope="module")
def served(docker_images: None, tmp_path_factory: pytest.TempPathFactory) -> Iterator[_Served]:
    tokens = {role: create_token() for role in ("ingest", "verdicts", "read")}
    receiver_token = create_token()
    stack = _Stack(
        project=f"detecttrace-test-{RUN_ID}",
        folder=_write_project(tmp_path_factory.mktemp("compose"), tokens, receiver_token),
        container=f"detecttrace-test-{RUN_ID}",
        read_token=tokens["read"],
    )
    try:
        stack.compose("up", "-d", "--wait")
        unauthenticated = httpx.post(
            stack.url("collector", 4318) + "/v1/traces",
            content=_read_demo_trace_lines()[0],
            headers={"Content-Type": "application/json"},
            timeout=30,
        )
        _send_demo_data(stack, tokens, receiver_token)
        first = _wait_until_settled(stack)
        health = _docker("inspect", "-f", "{{.State.Health.Status}}", stack.container).strip()
        dashboard = stack.read("/")
        stack.compose("restart", "detecttrace")
        after_restart = _wait_until_settled(stack)
        stack.compose("down")
        stack.compose("up", "-d", "--wait")
        after_down_and_up = _wait_until_settled(stack)
        _docker("exec", stack.container, "python", "-c", BACKUP)
        backup_check = _docker("exec", stack.container, "python", "-c", CHECK_BACKUP).strip()
        started = time.monotonic()
        _docker("stop", "-t", str(STOP_GRACE_SECONDS), stack.container)
        stop_seconds = time.monotonic() - started
        exit_code = _docker("inspect", "-f", "{{.State.ExitCode}}", stack.container).strip()
        yield _Served(
            health=health,
            dashboard_status=dashboard.status_code,
            dashboard_html=dashboard.text,
            first=first,
            after_restart=after_restart,
            after_down_and_up=after_down_and_up,
            backup_check=backup_check,
            stop_seconds=stop_seconds,
            stop_exit_code=exit_code,
            unauthenticated_receiver_status=unauthenticated.status_code,
        )
    finally:
        stack.compose("down", "-v")


def _write_project(folder: Path, tokens: dict[str, str], receiver_token: str) -> Path:
    """A copy of compose.yaml and deploy/ that uses the test image, real tokens and free ports."""
    compose = _read_compose()
    detecttrace = compose["services"]["detecttrace"]
    del detecttrace["build"]
    detecttrace["image"] = IMAGE
    detecttrace["container_name"] = f"detecttrace-test-{RUN_ID}"
    detecttrace["ports"] = ["127.0.0.1::4320"]
    compose["services"]["collector"]["ports"] = ["127.0.0.1::4318"]
    (folder / "compose.yaml").write_text(yaml.safe_dump(compose), encoding="utf-8")

    deploy = folder / "deploy"
    deploy.mkdir()
    shutil.copy(ROOT / "deploy" / "collector.yaml", deploy / "collector.yaml")
    example = ROOT / "deploy" / "detecttrace-serve.example.yaml"
    config = yaml.safe_load(example.read_text(encoding="utf-8"))
    config["tokens"] = {
        role: [{"name": f"test-{role}", "hash": hash_token(token)}]
        for role, token in tokens.items()
    }
    (deploy / "detecttrace-serve.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
    (folder / ".env").write_text(
        f"DETECTTRACE_INGEST_TOKEN={tokens['ingest']}\nCOLLECTOR_RECEIVER_TOKEN={receiver_token}\n",
        encoding="utf-8",
    )
    return folder


def _read_demo_trace_lines() -> list[bytes]:
    return [
        line
        for path in sorted((DEMO_FOLDER / "traces").glob("*.jsonl.gz"))
        for line in gzip.decompress(path.read_bytes()).splitlines()
    ]


def _send_demo_data(stack: _Stack, tokens: dict[str, str], receiver_token: str) -> None:
    """Spans go through the Collector, as an agent's would; verdicts go straight to the API."""
    collector = stack.url("collector", 4318)
    for line in _read_demo_trace_lines():
        httpx.post(
            collector + "/v1/traces",
            content=line,
            headers={
                "Authorization": f"Bearer {receiver_token}",
                "Content-Type": "application/json",
            },
            timeout=30,
        ).raise_for_status()
    httpx.post(
        stack.url("detecttrace", 4320) + "/api/verdicts",
        content=(DEMO_FOLDER / "verdicts.csv").read_bytes(),
        headers={
            "Authorization": f"Bearer {tokens['verdicts']}",
            "Content-Type": "text/csv; charset=utf-8",
        },
        timeout=30,
    ).raise_for_status()


def _wait_until_settled(stack: _Stack) -> tuple[dict[str, Any], bytes]:
    """Wait for a snapshot of all the demo data with no run pending; return it and the status."""
    deadline = time.monotonic() + SETTLED_SECONDS
    status: dict[str, Any] = {}
    while time.monotonic() < deadline:
        status = _read_status(stack)
        if _is_complete(status):
            time.sleep(QUIET_SECONDS)
            if _read_status(stack) == status:
                return status, stack.read("/api/results.json").content
        time.sleep(1)
    logs = stack.compose("logs", "--no-color")
    pytest.fail(f"the service did not settle in {SETTLED_SECONDS} s: {status}\n{logs}")


def _read_status(stack: _Stack) -> dict[str, Any]:
    try:
        response = stack.read("/api/status")
    except httpx.TransportError:
        return {}
    return response.json() if response.status_code == 200 else {}


def _is_complete(status: dict[str, Any]) -> bool:
    return (
        status.get("span_count") == DEMO_SPAN_COUNT
        and status.get("verdict_count") == DEMO_VERDICT_COUNT
        and status.get("generation", 0) > 0
        and status.get("recompute_running") is False
    )


@pytest.mark.container
def test_collector_refuses_spans_without_the_receiver_token(served: _Served) -> None:
    assert served.unauthenticated_receiver_status == 401


@pytest.mark.container
def test_service_container_becomes_healthy(served: _Served) -> None:
    assert served.health == "healthy"


@pytest.mark.container
def test_dashboard_answers_a_basic_read_token(served: _Served) -> None:
    assert served.dashboard_status == 200


@pytest.mark.container
def test_dashboard_is_the_served_page_with_results(served: _Served) -> None:
    html = served.dashboard_html

    # The page's script asks for newer results only when its view is served.
    assert (read_view(html)["served"] is not None, read_results(html)["classes"] != []) == (
        True,
        True,
    )


@pytest.mark.container
def test_results_survive_a_container_restart(served: _Served) -> None:
    assert served.after_restart == served.first


@pytest.mark.container
def test_results_survive_compose_down_and_up(served: _Served) -> None:
    assert served.after_down_and_up == served.first


@pytest.mark.container
def test_online_backup_writes_a_sound_copy_to_the_data_volume(served: _Served) -> None:
    assert served.backup_check == "ok"


@pytest.mark.container
def test_docker_stop_ends_the_service_within_the_grace_period(served: _Served) -> None:
    assert served.stop_seconds < STOP_GRACE_SECONDS


@pytest.mark.container
def test_docker_stop_ends_the_service_with_exit_code_0(served: _Served) -> None:
    assert served.stop_exit_code == "0"
