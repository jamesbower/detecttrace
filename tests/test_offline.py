"""DetectTrace makes no network calls: no module imports a network library or imports a
module by name, and the commands succeed without trying to connect when every socket
connection and name lookup raises."""

import ast
import shutil
import socket
from collections.abc import Callable
from pathlib import Path
from typing import NoReturn

import pytest
from typer.testing import CliRunner, Result

from detecttrace import cli

SOURCE = Path(cli.__file__).parent
DEMO_DATA = SOURCE / cli.DEMO_FOLDER
SOURCE_FILES = sorted(SOURCE.rglob("*.py"), key=lambda path: path.as_posix())
# The whole of urllib is banned, urllib.parse included: nothing uses it, and one rule for
# the package is easier to keep than an exception. asyncio is banned for its streams and
# event-loop sockets, multiprocessing for the sockets of its connection and managers modules,
# subprocess and webbrowser because another program can reach the network, and ctypes because
# it can call the C socket functions directly. Nothing uses any of them.
NETWORK_MODULES = (
    "aiohttp",
    "asyncio",
    "ctypes",
    "ftplib",
    "http",
    "httpx",
    "imaplib",
    "multiprocessing",
    "nntplib",
    "poplib",
    "requests",
    "smtplib",
    "socket",
    "socketserver",
    "ssl",
    "subprocess",
    "telnetlib",
    "urllib",
    "urllib3",
    "webbrowser",
    "xmlrpc",
)
# An import by name would hide a banned module from this check. Plain imports of other
# importlib modules stay allowed: __version__ comes from importlib.metadata and the templates
# from importlib.resources.
DYNAMIC_IMPORTS = ("__import__", "importlib.import_module")


@pytest.mark.parametrize(
    "path", SOURCE_FILES, ids=[path.relative_to(SOURCE).as_posix() for path in SOURCE_FILES]
)
def test_module_imports_no_network_library(path: Path) -> None:
    assert _network_imports(path.read_text(encoding="utf-8")) == []


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("from urllib.request import urlopen", ["urllib.request"]),
        ("import http.client", ["http.client"]),
        ("from .socket import x", []),
        ("import subprocess", ["subprocess"]),
        ("import webbrowser", ["webbrowser"]),
        ("from ctypes import CDLL", ["ctypes"]),
        ("from multiprocessing.connection import Client", ["multiprocessing.connection"]),
        ("import importlib\nimportlib.import_module('x')", ["importlib.import_module"]),
        ("from importlib import import_module", ["importlib.import_module"]),
        ("__import__('x')", ["__import__"]),
        ("import importlib.metadata", []),
        ("from importlib import metadata", []),
    ],
    ids=[
        "from-import",
        "dotted-import",
        "relative-import",
        "subprocess",
        "webbrowser",
        "ctypes",
        "multiprocessing",
        "import-module-call",
        "import-module-import",
        "dunder-import",
        "importlib-metadata",
        "from-importlib-metadata",
    ],
)
def test_import_guard_finds_network_imports(source: str, expected: list[str]) -> None:
    assert _network_imports(source) == expected


def test_demo_runs_without_the_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, network_attempts: list[str]
) -> None:
    result = _run_demo(tmp_path, monkeypatch)

    assert result.exit_code == 0


def test_demo_tries_no_connection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, network_attempts: list[str]
) -> None:
    _run_demo(tmp_path, monkeypatch)

    assert network_attempts == []


def test_check_runs_without_the_network(tmp_path: Path, network_attempts: list[str]) -> None:
    result = _run_check(tmp_path)

    assert result.exit_code == 0


def test_check_tries_no_connection(tmp_path: Path, network_attempts: list[str]) -> None:
    _run_check(tmp_path)

    assert network_attempts == []


def test_init_runs_without_the_network(tmp_path: Path, network_attempts: list[str]) -> None:
    result = _run_init(tmp_path)

    assert result.exit_code == 0


def test_init_tries_no_connection(tmp_path: Path, network_attempts: list[str]) -> None:
    _run_init(tmp_path)

    assert network_attempts == []


@pytest.fixture
def network_attempts(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Make every connection and name lookup raise, and record each attempt, so an attempt
    whose error the code swallows still shows."""
    attempts: list[str] = []
    for owner, name in (
        (socket.socket, "connect"),
        (socket.socket, "connect_ex"),
        (socket.socket, "sendto"),
        (socket, "create_connection"),
        (socket, "getaddrinfo"),
        (socket, "gethostbyname"),
        (socket, "gethostbyname_ex"),
    ):
        monkeypatch.setattr(owner, name, _create_refusal(name, attempts))
    # Windows sockets have no sendmsg, so it may have to be added rather than replaced.
    monkeypatch.setattr(
        socket.socket, "sendmsg", _create_refusal("sendmsg", attempts), raising=False
    )
    return attempts


def _create_refusal(name: str, attempts: list[str]) -> Callable[..., NoReturn]:
    def refuse(*_args: object, **_kwargs: object) -> NoReturn:
        attempts.append(name)
        raise OSError("network access is not allowed in this test")

    return refuse


def _run_demo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Result:
    monkeypatch.chdir(tmp_path)
    return _invoke("demo", "--quiet")


def _run_check(tmp_path: Path) -> Result:
    shutil.copytree(DEMO_DATA, tmp_path / "demo")
    return _invoke("check", "--config", str(tmp_path / "demo" / "detecttrace.yaml"))


def _run_init(tmp_path: Path) -> Result:
    return _invoke(
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


def _invoke(*args: str) -> Result:
    return CliRunner().invoke(cli.app, list(args))


def _network_imports(source: str) -> list[str]:
    tree = ast.parse(source)
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module is not None:
            names.append(node.module)
            names.extend(
                name
                for alias in node.names
                if (name := f"{node.module}.{alias.name}") in DYNAMIC_IMPORTS
            )
        elif isinstance(node, ast.Call):
            names.append(_to_dynamic_import_name(node.func))
    return sorted(name for name in names if _is_banned(name))


def _to_dynamic_import_name(function: ast.expr) -> str:
    # By the called name alone, so an alias such as `il = importlib` can't hide the call.
    if isinstance(function, ast.Attribute):
        called = function.attr
    elif isinstance(function, ast.Name):
        called = function.id
    else:
        return ""
    if called == "import_module":
        return "importlib.import_module"
    # Any other called name is left out, so a call such as `parser.http()` is not a module.
    return "__import__" if called == "__import__" else ""


def _is_banned(name: str) -> bool:
    return name in DYNAMIC_IMPORTS or any(
        name == banned or name.startswith(f"{banned}.") for banned in NETWORK_MODULES
    )
