"""Every example in README.md and docs/*.md loads with the loader it documents, so the docs can't drift from the code."""

import re
import textwrap
import types
from dataclasses import dataclass
from pathlib import Path

import pytest
import yaml

from detecttrace.checklist import load_checklists
from detecttrace.runconfig import RunConfig, load_run_config

ROOT = Path(__file__).parent.parent
DOC_FILES = [ROOT / "README.md", *sorted((ROOT / "docs").glob("*.md"))]
# A fence may be indented, as in a list item; the closing fence has the same indent.
_FENCE = re.compile(r"^( *)```(\w*)\n(.*?)^\1```$", re.MULTILINE | re.DOTALL)


@dataclass(frozen=True)
class Block:
    where: str  # "<file>:<line of the opening fence>"
    language: str
    text: str
    kind: str


def _classify(language: str, text: str) -> str:
    if language == "python":
        return "python"
    # Checked first: a Collector configuration also has a `traces:` key, under its pipelines.
    if re.search(r"^exporters:", text, re.MULTILINE):
        return "collector"
    if re.search(r"^items:", text, re.MULTILINE):
        return "checklist"
    if re.search(r"^traces:", text, re.MULTILINE):
        return "config"
    return "unknown"


def _find_blocks() -> list[Block]:
    blocks: list[Block] = []
    for path in DOC_FILES:
        text = path.read_text(encoding="utf-8")
        for match in _FENCE.finditer(text):
            language = match[2]
            if language not in ("yaml", "python"):
                continue
            line = text.count("\n", 0, match.start()) + 1
            where = f"{path.name}:{line}"
            body = textwrap.dedent(match[3])
            blocks.append(Block(where, language, body, _classify(language, body)))
    return blocks


BLOCKS = _find_blocks()


def _of_kind(kind: str) -> list[Block]:
    return [block for block in BLOCKS if block.kind == kind]


def _ids(blocks: list[Block]) -> list[str]:
    return [block.where for block in blocks]


def test_docs_hold_every_kind_of_example() -> None:
    assert {block.kind for block in BLOCKS} >= {"config", "checklist", "collector", "python"}


def test_every_yaml_example_is_a_config_checklist_or_collector_config() -> None:
    assert _ids(_of_kind("unknown")) == []


@pytest.mark.parametrize("block", _of_kind("config"), ids=_ids(_of_kind("config")))
def test_config_example_loads(block: Block, tmp_path: Path) -> None:
    path = tmp_path / "detecttrace.yaml"
    path.write_text(block.text, encoding="utf-8")

    config = load_run_config(path)

    assert isinstance(config, RunConfig)


@pytest.mark.parametrize("block", _of_kind("checklist"), ids=_ids(_of_kind("checklist")))
def test_checklist_example_loads(block: Block, tmp_path: Path) -> None:
    path = tmp_path / "checklist.yaml"
    path.write_text(block.text, encoding="utf-8")

    checklists = load_checklists(path)

    assert len(checklists) == 1


# Collector configurations belong to the OpenTelemetry Collector, which DetectTrace doesn't
# load, so they are only checked to be valid YAML.
@pytest.mark.parametrize("block", _of_kind("collector"), ids=_ids(_of_kind("collector")))
def test_collector_example_is_yaml(block: Block) -> None:
    assert isinstance(yaml.safe_load(block.text), dict)


@pytest.mark.parametrize("block", _of_kind("python"), ids=_ids(_of_kind("python")))
def test_python_example_compiles(block: Block) -> None:
    assert isinstance(compile(block.text, block.where, "exec"), types.CodeType)
