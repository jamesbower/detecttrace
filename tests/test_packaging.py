"""The built wheel and sdist hold exactly the files users need, and nothing private or stray."""

import shutil
import subprocess
import tarfile
import tomllib
import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
VERSION = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"][
    "version"
]
DIST_INFO = f"detecttrace-{VERSION}.dist-info"
PACKAGE_FILES = {
    "detecttrace/__init__.py",
    "detecttrace/__main__.py",
    "detecttrace/cases.py",
    "detecttrace/charts.py",
    "detecttrace/checklist.py",
    "detecttrace/cli.py",
    "detecttrace/config.py",
    "detecttrace/conventions.py",
    "detecttrace/dashboard.py",
    "detecttrace/dashboard_view.py",
    "detecttrace/demo_data/README.txt",
    "detecttrace/demo_data/checklists/impossible_travel.yaml",
    "detecttrace/demo_data/checklists/oauth_consent.yaml",
    "detecttrace/demo_data/detecttrace.yaml",
    "detecttrace/demo_data/traces/traces-2026-08-18T00-57-52.000.jsonl.gz",
    "detecttrace/demo_data/traces/traces-2026-09-03T09-30-00.000.jsonl.gz",
    "detecttrace/demo_data/traces/traces.jsonl.gz",
    "detecttrace/demo_data/verdicts.csv",
    "detecttrace/durations.py",
    "detecttrace/evidence.py",
    "detecttrace/files.py",
    "detecttrace/init_proposal.py",
    "detecttrace/init_writer.py",
    "detecttrace/join.py",
    "detecttrace/jsontext.py",
    "detecttrace/langfuse.py",
    "detecttrace/metrics.py",
    "detecttrace/model.py",
    "detecttrace/otel.py",
    "detecttrace/otlp.py",
    "detecttrace/pipeline.py",
    "detecttrace/py.typed",
    "detecttrace/results.py",
    "detecttrace/runconfig.py",
    "detecttrace/serve/__init__.py",
    "detecttrace/serve/app.py",
    "detecttrace/serve/auth.py",
    "detecttrace/serve/config.py",
    "detecttrace/serve/receiver.py",
    "detecttrace/serve/store.py",
    "detecttrace/stats.py",
    "detecttrace/summary.py",
    "detecttrace/templates/dashboard.css",
    "detecttrace/templates/dashboard.html.j2",
    "detecttrace/templates/dashboard.js",
    "detecttrace/traces.py",
    "detecttrace/verdicts.py",
    "detecttrace/yaml12.py",
}
EXPECTED_WHEEL = PACKAGE_FILES | {
    f"{DIST_INFO}/METADATA",
    f"{DIST_INFO}/RECORD",
    f"{DIST_INFO}/WHEEL",
    f"{DIST_INFO}/entry_points.txt",
    f"{DIST_INFO}/licenses/LICENSE",
}
EXPECTED_SDIST = {f"src/{path}" for path in PACKAGE_FILES} | {
    "CHANGELOG.md",
    "LICENSE",
    "PKG-INFO",
    "README.md",
    "pyproject.toml",
}


@pytest.fixture(scope="module")
def dist(tmp_path_factory: pytest.TempPathFactory) -> Path:
    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("uv is not on PATH, so the wheel and sdist can't be built")
    out_dir = tmp_path_factory.mktemp("dist")
    # Building the sdist first, then the wheel from it, also proves the sdist is complete.
    # --force-pep517 builds with the uv_build that pyproject.toml pins, as the release does;
    # uv's bundled backend follows uv's own version and changes the sdist between releases.
    subprocess.run(
        [uv, "build", "--force-pep517", "--quiet", "--out-dir", str(out_dir), str(REPO_ROOT)],
        check=True,
        capture_output=True,
    )
    return out_dir


def test_wheel_holds_exactly_the_package_and_its_metadata(dist: Path) -> None:
    with zipfile.ZipFile(dist / f"detecttrace-{VERSION}-py3-none-any.whl") as wheel:
        names = {name for name in wheel.namelist() if not name.endswith("/")}

    assert names == EXPECTED_WHEEL


def test_sdist_holds_exactly_the_source_readme_license_and_changelog(dist: Path) -> None:
    prefix = f"detecttrace-{VERSION}/"
    with tarfile.open(dist / f"detecttrace-{VERSION}.tar.gz") as sdist:
        names = {
            member.name.removeprefix(prefix) for member in sdist.getmembers() if member.isfile()
        }

    assert names == EXPECTED_SDIST
