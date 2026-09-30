"""`init --yes --dry-run` prints exactly each trace format variant's expected_init.yaml, its
proposal agrees with the variant's hand-written configuration, and init's configuration
reproduces the demo's numbers.

After an intended change, rewrite the golden files with
`uv run python scripts/synthetic/generate.py --update-golden` and review the diff.
"""

import csv
import difflib
import importlib.util
import json
import shutil
from pathlib import Path
from typing import Any

import fixture_specs
import generate
import pytest
import yaml
from typer.testing import CliRunner, Result

from detecttrace import cli
from detecttrace.runconfig import RunConfig, load_run_config, to_run_config
from detecttrace.yaml12 import parse_yaml12

FIXTURE_ROOT = fixture_specs.FIXTURE_ROOT
DEMO_DIR = generate.REPO_ROOT / "src" / "detecttrace" / "demo_data"
DEMO_GOLDEN = FIXTURE_ROOT / "demo" / generate.GOLDEN_NAME
REAL_API_PAGES = [f"v2_all_fields_page{number}.json" for number in (1, 2, 3, 4)]
NEEDS_ZSTD = pytest.mark.skipif(
    importlib.util.find_spec("zstandard") is None, reason="zstandard is not installed"
)
VARIANTS = [
    pytest.param(name, id=name, marks=[NEEDS_ZSTD] if "zstd" in name else [])
    for name in fixture_specs.INIT_VARIANTS
]
# The line init prints between the configuration and the example checklist.
CHECKLIST_MARKER = "\n--- # "
# init maps only labels that mean one verdict wherever they appear (AUTO_LABEL_MAP), so the
# demo's "Malicious" and "Closed - Benign", which the hand-written configurations map, are left
# for the user. Every variant's verdict file has both.
LABELS_INIT_LEAVES_UNMAPPED = ("Malicious", "Closed - Benign")


def _invoke(*args: str) -> Result:
    return CliRunner().invoke(cli.app, list(args))


def _init_variant(name: str) -> Result:
    return _invoke(*fixture_specs.to_init_arguments(FIXTURE_ROOT / name))


def _load_golden_config(name: str) -> RunConfig:
    """The golden's configuration, read as if it were the variant's detecttrace.yaml."""
    folder = FIXTURE_ROOT / name
    text = (folder / generate.INIT_GOLDEN_NAME).read_text(encoding="utf-8")
    config_path = folder / fixture_specs.CONFIG_NAME
    return to_run_config(parse_yaml12(text.split(CHECKLIST_MARKER)[0], config_path), config_path)


def _load_hand_config(name: str) -> RunConfig:
    return load_run_config(FIXTURE_ROOT / name / fixture_specs.CONFIG_NAME)


def _to_verdicts_by_label(config: RunConfig, name: str) -> dict[str, dict[str, str | None]]:
    """The verdict each analyst label in the variant's verdict file, and each label the demo's
    agent writes, resolves to."""
    with (FIXTURE_ROOT / name / "verdicts.csv").open(encoding="utf-8", newline="") as file:
        analyst = sorted({row["verdict"] for row in csv.DictReader(file)})
    agent = sorted(generate.load_scenario("demo").agent_labels.values())
    return {
        "analyst": {label: config.to_analyst_verdict(label) for label in analyst},
        "agent": {label: config.to_agent_verdict(label) for label in agent},
    }


def _diff(expected: str, actual: str) -> str:
    return "".join(
        difflib.unified_diff(
            expected.splitlines(keepends=True),
            actual.splitlines(keepends=True),
            generate.INIT_GOLDEN_NAME,
            "init --yes --dry-run",
        )
    )


# Goldens


@pytest.mark.parametrize("name", VARIANTS)
def test_init_dry_run_prints_the_golden_file(name: str) -> None:
    expected = (FIXTURE_ROOT / name / generate.INIT_GOLDEN_NAME).read_text(encoding="utf-8")

    actual = _init_variant(name).stdout

    assert _diff(expected, actual) == ""


@pytest.mark.parametrize("name", VARIANTS)
def test_init_dry_run_exits_0(name: str) -> None:
    assert _init_variant(name).exit_code == 0


def test_every_init_variant_is_a_registered_fixture() -> None:
    assert set(fixture_specs.INIT_VARIANTS) <= {spec.name for spec in fixture_specs.FIXTURES}


# The proposal against the hand-written configuration


@pytest.mark.parametrize("name", VARIANTS)
def test_init_proposes_the_hand_written_trace_format(name: str) -> None:
    assert _load_golden_config(name).traces.format == _load_hand_config(name).traces.format


@pytest.mark.parametrize("name", VARIANTS)
def test_init_proposes_the_hand_written_mapping(name: str) -> None:
    assert _load_golden_config(name).mapping == _load_hand_config(name).mapping


@pytest.mark.parametrize("name", VARIANTS)
def test_init_proposes_the_hand_written_input_paths(name: str) -> None:
    golden, hand = _load_golden_config(name), _load_hand_config(name)

    assert (golden.traces.path, golden.verdicts.path, golden.checklists) == (
        hand.traces.path,
        hand.verdicts.path,
        hand.checklists,
    )


@pytest.mark.parametrize("name", VARIANTS)
def test_init_resolves_labels_as_the_hand_config_but_leaves_malicious_and_closed_benign(
    name: str,
) -> None:
    hand = _to_verdicts_by_label(_load_hand_config(name), name)
    expected = {
        "analyst": {**hand["analyst"], **dict.fromkeys(LABELS_INIT_LEAVES_UNMAPPED)},
        "agent": hand["agent"],
    }

    assert _to_verdicts_by_label(_load_golden_config(name), name) == expected


# The real Langfuse capture has no verdict file of its own, so it has no golden file; these
# check the lines that come from the capture itself.


@pytest.fixture(scope="module")
def real_langfuse_output(tmp_path_factory: pytest.TempPathFactory) -> list[Any]:
    """The configuration and example checklist init prints, as parsed YAML (untyped: Any)."""
    folder = tmp_path_factory.mktemp("langfuse_real")
    shutil.copytree(
        FIXTURE_ROOT / "langfuse_real",
        folder / "traces",
        ignore=lambda _folder, names: [name for name in names if name not in REAL_API_PAGES],
    )
    (folder / "verdicts.csv").write_text(
        "case_id,alert_class,verdict\n"
        + "".join(f"CASE-900{number},impossible_travel,TP\n" for number in (1, 2, 3, 4)),
        encoding="utf-8",
    )
    return list(yaml.safe_load_all(_invoke(*fixture_specs.to_init_arguments(folder)).stdout))


def test_real_langfuse_capture_maps_its_guide_style_agent_labels(
    real_langfuse_output: list[Any],
) -> None:
    assert real_langfuse_output[0]["agent_label_map"] == {
        "BenignPositive": "benign",
        "FalsePositive": "false_positive",
        "TruePositive": "true_positive",
    }


def test_real_langfuse_capture_example_lists_its_tools(
    real_langfuse_output: list[Any],
) -> None:
    assert [item["tool"] for item in real_langfuse_output[1]["items"]] == [
        "get_oauth_grants",
        "get_signin_logs",
        "get_user_profile",
        "lookup_ip_reputation",
    ]


# End to end


def test_init_configuration_with_the_demo_checklists_gives_the_demo_results_up_to_the_version(
    tmp_path: Path,
) -> None:
    shutil.copytree(DEMO_DIR / "traces", tmp_path / "traces")
    shutil.copy(DEMO_DIR / "verdicts.csv", tmp_path / "verdicts.csv")
    config_path = tmp_path / "detecttrace.yaml"
    _invoke(
        "init",
        "--traces",
        str(tmp_path / "traces"),
        "--verdicts",
        str(tmp_path / "verdicts.csv"),
        "--config",
        str(config_path),
        "--yes",
        # The labels init leaves for the user, set as the demo's configuration maps them.
        "--set",
        "label_map.Malicious=true_positive",
        "--set",
        "label_map.Closed - Benign=benign",
    )
    checklists = tmp_path / "checklists"
    (checklists / "impossible_travel.yaml.example").rename(checklists / "impossible_travel.yaml")
    # The example lists every tool called, not the playbook, and covers one class; the
    # demo's hand-written checklists replace it, so the configuration alone is under test.
    for checklist in sorted((DEMO_DIR / "checklists").iterdir()):
        shutil.copy(checklist, checklists / checklist.name)
    _invoke("check", "--config", str(config_path), "--json", str(tmp_path / "results.json"))

    results = json.loads((tmp_path / "results.json").read_text(encoding="utf-8"))

    # generated_by carries the version; the golden file keeps only the tool name.
    assert generate.to_golden_text(generate.normalize_results(results)) == DEMO_GOLDEN.read_text(
        encoding="utf-8"
    )
