"""`detecttrace ui`: its options, startup failures, and a real process end to end."""

import os
import signal
import socket
import stat
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import httpx
import pytest
from fastapi import FastAPI
from typer.testing import CliRunner, Result

import detecttrace.serve
from detecttrace import cli
from detecttrace.serve import ui_server
from detecttrace.serve.ui_config import CONFIG_NAME
from serve.served_process import ServedProcess, find_free_port, start_ui
from serve.ui_support import DEMO_TRACES, DEMO_VERDICTS, NO_EDITS, UPLOAD_HEADERS

needs_posix = pytest.mark.skipif(
    sys.platform == "win32", reason="sends SIGINT, which Windows can't deliver to a process"
)
# The modules that import the serve extra, so a test can import them afresh without it.
UI_MODULES = (
    "detecttrace.serve.ui_server",
    "detecttrace.serve.ui",
    "detecttrace.serve.ui_recompute",
    "detecttrace.serve.server",
    "detecttrace.serve.app",
)
BROWSER_SCRIPT = '#!/bin/sh\nprintf %s "$1" > "$(dirname "$0")/opened.txt"\n'


@dataclass(frozen=True)
class UiCall:
    data_dir: Path
    port: int
    should_open: bool


@pytest.fixture
def run_ui_calls(monkeypatch: pytest.MonkeyPatch) -> list[UiCall]:
    """Each run_ui call the command makes, instead of running the app."""
    calls: list[UiCall] = []

    def record(
        data_dir: Path, port: int, *, should_open: bool, announce: Callable[[str], None]
    ) -> None:
        calls.append(UiCall(data_dir, port, should_open))

    monkeypatch.setattr(ui_server, "run_ui", record)
    return calls


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    folder = tmp_path / "home"
    folder.mkdir()
    monkeypatch.setenv("HOME", str(folder))
    monkeypatch.setenv("USERPROFILE", str(folder))
    return folder


def invoke_ui(*args: str) -> Result:
    return CliRunner().invoke(cli.app, ["ui", *args])


# Options


def test_the_app_listens_on_loopback_only() -> None:
    config = ui_server.create_ui_uvicorn_config(FastAPI(), 4321)

    assert config.host == "127.0.0.1"


def test_the_app_does_not_trust_forwarded_headers() -> None:
    config = ui_server.create_ui_uvicorn_config(FastAPI(), 4321)

    assert config.proxy_headers is False


def test_the_default_port_is_4321(run_ui_calls: list[UiCall], home: Path) -> None:
    invoke_ui()

    assert run_ui_calls[0].port == ui_server.DEFAULT_PORT == 4321


def test_the_port_option_is_used(run_ui_calls: list[UiCall], home: Path) -> None:
    invoke_ui("--port", "5001")

    assert run_ui_calls[0].port == 5001


@pytest.mark.parametrize("port", ["0", "65536"])
def test_a_port_out_of_range_exits_1(run_ui_calls: list[UiCall], home: Path, port: str) -> None:
    result = invoke_ui("--port", port)

    assert result.exit_code == 1


def test_the_default_data_folder_is_in_the_home_folder(
    run_ui_calls: list[UiCall], home: Path
) -> None:
    invoke_ui()

    assert run_ui_calls[0].data_dir == home / ".detecttrace"


def test_the_data_folder_option_is_used(run_ui_calls: list[UiCall], tmp_path: Path) -> None:
    invoke_ui("--data-dir", str(tmp_path / "data"))

    assert run_ui_calls[0].data_dir == tmp_path / "data"


def test_the_browser_opens_by_default(run_ui_calls: list[UiCall], home: Path) -> None:
    invoke_ui()

    assert run_ui_calls[0].should_open is True


def test_no_open_keeps_the_browser_closed(run_ui_calls: list[UiCall], home: Path) -> None:
    invoke_ui("--no-open")

    assert run_ui_calls[0].should_open is False


# Startup failures


@pytest.fixture
def start_on_busy_port(tmp_path: Path) -> tuple[Result, int]:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as taken:
        taken.bind(("127.0.0.1", 0))
        taken.listen()
        port = taken.getsockname()[1]
        result = invoke_ui("--no-open", "--port", str(port), "--data-dir", str(tmp_path / "data"))
    return result, port


def test_a_busy_port_exits_1(start_on_busy_port: tuple[Result, int]) -> None:
    assert start_on_busy_port[0].exit_code == 1


def test_a_busy_port_says_to_pass_another(start_on_busy_port: tuple[Result, int]) -> None:
    result, port = start_on_busy_port

    assert result.stderr == f"Error: Port {port} is in use. Pass --port with a free port.\n"


@pytest.fixture
def start_with_broken_configuration(tmp_path: Path) -> Result:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / CONFIG_NAME).write_text("mapping: [\n", encoding="utf-8")
    return invoke_ui("--no-open", "--port", str(find_free_port()), "--data-dir", str(data_dir))


def test_a_broken_saved_configuration_exits_1(start_with_broken_configuration: Result) -> None:
    assert start_with_broken_configuration.exit_code == 1


def test_a_broken_saved_configuration_says_how_to_fix_it(
    start_with_broken_configuration: Result,
) -> None:
    assert "or delete it and confirm the configuration again in the app." in (
        start_with_broken_configuration.stderr
    )


@pytest.fixture
def start_with_leftover_folders(tmp_path: Path) -> Path:
    """A start that stops at its broken configuration, after tidying the data folder."""
    data_dir = tmp_path / "data"
    for name in ("upload-k2j4x9", "backup-p0q1r2", "uploads"):
        (data_dir / name).mkdir(parents=True)
        (data_dir / name / "traces.jsonl").write_text("{}", encoding="utf-8")
    (data_dir / CONFIG_NAME).write_text("mapping: [\n", encoding="utf-8")
    invoke_ui("--no-open", "--port", str(find_free_port()), "--data-dir", str(data_dir))
    return data_dir


def test_a_start_removes_the_folders_an_interrupted_upload_left(
    start_with_leftover_folders: Path,
) -> None:
    folders = sorted(path.name for path in start_with_leftover_folders.iterdir() if path.is_dir())

    assert folders == ["checklists", "uploads"]


def test_a_data_folder_that_is_a_file_exits_1(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    data_dir.write_text("not a folder", encoding="utf-8")

    result = invoke_ui("--no-open", "--port", str(find_free_port()), "--data-dir", str(data_dir))

    assert result.exit_code == 1


@pytest.fixture
def start_without_extra(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Result:
    # A None entry makes `import fastapi` fail as if the package were not installed; the
    # modules that import it are dropped so the CLI imports them afresh.
    monkeypatch.setitem(sys.modules, "fastapi", None)
    for name in UI_MODULES:
        monkeypatch.delitem(sys.modules, name, raising=False)
        monkeypatch.delattr(detecttrace.serve, name.rpartition(".")[2], raising=False)
    return invoke_ui("--no-open", "--data-dir", str(tmp_path / "data"))


def test_a_missing_serve_extra_exits_1(start_without_extra: Result) -> None:
    assert start_without_extra.exit_code == 1


def test_a_missing_serve_extra_says_how_to_install_it(start_without_extra: Result) -> None:
    assert start_without_extra.stderr == (
        'Error: detecttrace ui needs the serve extra: pip install "detecttrace[serve]"\n'
    )


# A real process


@dataclass(frozen=True)
class UiRun:
    data_dir: Path
    page_status: int
    first_generation: int
    restarted_generation: int
    no_open_exit_code: int
    open_exit_code: int
    stdout: str
    base_url: str
    restarted_url: str
    opened_after_no_open: bool
    opened_url: str


def create_browser(folder: Path) -> dict[str, str]:
    """An environment whose browser writes the URL it is asked to open to opened.txt."""
    script = folder / "browser.sh"
    script.write_text(BROWSER_SCRIPT, encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return {**os.environ, "BROWSER": str(script)}


def confirm_demo(served: ServedProcess) -> None:
    """Upload the demo traces and verdicts and save the proposed configuration."""
    write_headers = {"X-DetectTrace": "1", "Origin": served.base_url}
    with httpx.Client(base_url=served.base_url, timeout=30) as client:
        for kind, path in (("traces", DEMO_TRACES), ("verdicts", DEMO_VERDICTS)):
            client.post(
                f"/api/upload/{kind}",
                params={"name": path.name},
                content=path.read_bytes(),
                headers={**UPLOAD_HEADERS, "Origin": served.base_url},
            ).raise_for_status()
        client.post("/api/config", json=NO_EDITS, headers=write_headers).raise_for_status()


@pytest.fixture(scope="module")
def ui_run(tmp_path_factory: pytest.TempPathFactory) -> UiRun:
    folder = tmp_path_factory.mktemp("ui")
    # Not created beforehand, so the app creates it with its own mode.
    data_dir = folder / "nested" / "data"
    env = create_browser(folder)
    served = start_ui(folder, data_dir, ["--no-open"], env)
    try:
        page_status = served.get("/").status_code
        confirm_demo(served)
        first_generation = cast(int, served.wait_for_generation(1, timeout=60)["generation"])
    finally:
        no_open_exit_code = served.stop(signal.SIGINT)
    stdout = served.stdout_path.read_text(encoding="utf-8")
    opened_after_no_open = (folder / "opened.txt").exists()
    # The same folder again: the saved configuration is loaded and recomputed at startup.
    restarted = start_ui(folder, data_dir, [], env)
    try:
        status = restarted.wait_for_generation(first_generation + 1, timeout=60)
    finally:
        open_exit_code = restarted.stop(signal.SIGINT)
    return UiRun(
        data_dir=data_dir,
        page_status=page_status,
        first_generation=first_generation,
        restarted_generation=cast(int, status["generation"]),
        no_open_exit_code=no_open_exit_code,
        open_exit_code=open_exit_code,
        stdout=stdout,
        base_url=served.base_url,
        restarted_url=restarted.base_url,
        opened_after_no_open=opened_after_no_open,
        opened_url=(folder / "opened.txt").read_text(encoding="utf-8"),
    )


@needs_posix
def test_the_app_serves_its_page(ui_run: UiRun) -> None:
    assert ui_run.page_status == 200


@needs_posix
def test_the_app_says_where_it_runs(ui_run: UiRun) -> None:
    assert ui_run.stdout == (
        f"DetectTrace is running at {ui_run.base_url}/ (press Ctrl+C to stop).\n"
    )


@needs_posix
def test_sigint_stops_the_app_with_exit_code_0(ui_run: UiRun) -> None:
    assert ui_run.no_open_exit_code == 0


@needs_posix
def test_a_saved_configuration_computes_the_dashboard(ui_run: UiRun) -> None:
    assert ui_run.first_generation > 0


@needs_posix
def test_a_restart_recomputes_with_the_saved_configuration(ui_run: UiRun) -> None:
    assert ui_run.restarted_generation > ui_run.first_generation


@needs_posix
def test_no_open_opens_no_browser(ui_run: UiRun) -> None:
    assert ui_run.opened_after_no_open is False


@needs_posix
def test_the_browser_opens_the_app(ui_run: UiRun) -> None:
    assert ui_run.opened_url == f"{ui_run.restarted_url}/"


@needs_posix
def test_the_data_folder_is_private(ui_run: UiRun) -> None:
    assert stat.S_IMODE(ui_run.data_dir.stat().st_mode) == 0o700


@needs_posix
def test_the_database_is_private(ui_run: UiRun) -> None:
    assert stat.S_IMODE((ui_run.data_dir / "detecttrace.db").stat().st_mode) == 0o600
