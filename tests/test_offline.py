"""DetectTrace makes no network calls: no module imports a network library, and the
commands still succeed when every socket connection raises."""

import ast
import shutil
import socket
from pathlib import Path

import pytest
from typer.testing import CliRunner, Result

from detecttrace import cli

SOURCE = Path(cli.__file__).parent
DEMO_DATA = SOURCE / cli.DEMO_FOLDER
SOURCE_FILES = sorted(SOURCE.rglob("*.py"), key=lambda path: path.as_posix())
# The whole of urllib is banned, urllib.parse included: nothing uses it, and one rule for
# the package is easier to keep than an exception. asyncio is banned for its streams and
# event-loop sockets; nothing uses it either.
NETWORK_MODULES = (
    "aiohttp",
    "asyncio",
    "ftplib",
    "http",
    "httpx",
    "imaplib",
    "nntplib",
    "poplib",
    "requests",
    "smtplib",
    "socket",
    "socketserver",
    "ssl",
    "telnetlib",
    "urllib",
    "urllib3",
    "xmlrpc",
)


@pytest.mark.parametrize(
    "path", SOURCE_FILES, ids=[path.relative_to(SOURCE).as_posix() for path in SOURCE_FILES]
)
def test_module_imports_no_network_library(path: Path) -> None:
    assert _network_imports(path) == []


def test_demo_runs_without_the_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_network: None
) -> None:
    monkeypatch.chdir(tmp_path)

    result = _invoke("demo", "--quiet")

    assert result.exit_code == 0


def test_check_runs_without_the_network(tmp_path: Path, no_network: None) -> None:
    shutil.copytree(DEMO_DATA, tmp_path / "demo")

    result = _invoke("check", "--config", str(tmp_path / "demo" / "detecttrace.yaml"))

    assert result.exit_code == 0


def test_init_runs_without_the_network(tmp_path: Path, no_network: None) -> None:
    result = _invoke(
        "init",
        "--traces",
        str(DEMO_DATA / "traces"),
        "--verdicts",
        str(DEMO_DATA / "verdicts.csv"),
        "--config",
        str(tmp_path / "detecttrace.yaml"),
        "--yes",
        "--dry-run",
    )

    assert result.exit_code == 0


@pytest.fixture
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(socket.socket, "connect", _refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", _refuse)
    monkeypatch.setattr(socket, "create_connection", _refuse)
    monkeypatch.setattr(socket, "getaddrinfo", _refuse)


def _refuse(*_args: object, **_kwargs: object) -> None:
    raise OSError("network access is not allowed in this test")


def _invoke(*args: str) -> Result:
    return CliRunner().invoke(cli.app, list(args))


def _network_imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module is not None:
            names.append(node.module)
    return sorted(name for name in names if _is_network_module(name))


def _is_network_module(name: str) -> bool:
    return any(name == banned or name.startswith(f"{banned}.") for banned in NETWORK_MODULES)
