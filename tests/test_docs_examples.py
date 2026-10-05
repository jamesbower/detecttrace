"""Every example in README.md and docs/*.md loads with the loader it documents, so the docs can't drift from the code."""

import json
import re
import textwrap
import types
from dataclasses import dataclass
from pathlib import Path

import pytest
import yaml

from detecttrace.checklist import load_checklists
from detecttrace.runconfig import RunConfig, load_run_config
from detecttrace.serve.config import ServeConfig, load_serve_config
from detecttrace.serve.verdict_api import parse_verdicts_body

ROOT = Path(__file__).parent.parent
DOC_FILES = [ROOT / "README.md", *sorted((ROOT / "docs").glob("*.md"))]
# A fence may be indented, as in a list item; the closing fence has the same indent.
_FENCE = re.compile(r"^( *)```(\w*)\n(.*?)^\1```$", re.MULTILINE | re.DOTALL)
# Any fence line at any indent; fences alternate, so every other one opens a block.
_FENCE_LINE = re.compile(r"^ *```(.*)$", re.MULTILINE)
# Lowercase and spelled one way, so a block can't slip past the loaders as `yml` or `YAML`.
FENCE_LANGUAGES = {"", "csv", "json", "python", "sh", "text", "yaml"}


@dataclass(frozen=True)
class Block:
    where: str  # "<file>:<line of the opening fence>"
    language: str
    text: str
    kind: str


def _classify(language: str, text: str) -> str:
    if language == "python":
        return "python"
    if language == "csv":
        return "verdicts_csv"
    if language == "json":
        return "verdicts_json" if re.match(r'\{\s*"verdicts"\s*:', text) else "json"
    # Checked first: a Collector configuration also has a `traces:` key, under its pipelines.
    if re.search(r"^exporters:", text, re.MULTILINE):
        return "collector"
    if re.search(r"^items:", text, re.MULTILINE):
        return "checklist"
    if re.search(r"^serve:", text, re.MULTILINE):
        # A fragment shows only the serve section; a complete example has its tokens too.
        return "serve" if re.search(r"^tokens:", text, re.MULTILINE) else "serve_fragment"
    if re.search(r"^traces:", text, re.MULTILINE):
        return "config"
    # A fragment that shows only the mapping section of a configuration.
    if re.fullmatch(r"mapping:\n(?:[ #].*\n|\n)*", text):
        return "mapping"
    return "unknown"


def _find_blocks() -> list[Block]:
    blocks: list[Block] = []
    for path in DOC_FILES:
        text = path.read_text(encoding="utf-8")
        for match in _FENCE.finditer(text):
            language = match[2]
            if language not in ("yaml", "python", "json", "csv"):
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


@pytest.mark.parametrize("path", DOC_FILES, ids=[path.name for path in DOC_FILES])
def test_every_fence_names_an_allowed_language(path: Path) -> None:
    languages = _FENCE_LINE.findall(path.read_text(encoding="utf-8"))[::2]

    assert sorted(set(languages) - FENCE_LANGUAGES) == []


@pytest.mark.parametrize("path", DOC_FILES, ids=[path.name for path in DOC_FILES])
def test_every_fence_closes_at_its_own_indent(path: Path) -> None:
    # A block the pattern misses, such as one closed at another indent, is never loaded.
    text = path.read_text(encoding="utf-8")

    assert len(_FENCE_LINE.findall(text)[::2]) == len(_FENCE.findall(text))


def test_docs_hold_every_kind_of_example() -> None:
    assert {block.kind for block in BLOCKS} >= {
        "config",
        "checklist",
        "collector",
        "python",
        "serve",
        "serve_fragment",
        "verdicts_json",
        "verdicts_csv",
    }


def test_every_yaml_example_is_a_known_kind_of_configuration() -> None:
    assert _ids(_of_kind("unknown")) == []


@pytest.mark.parametrize("block", _of_kind("config"), ids=_ids(_of_kind("config")))
def test_config_example_loads(block: Block, tmp_path: Path) -> None:
    path = tmp_path / "detecttrace.yaml"
    path.write_text(block.text, encoding="utf-8")

    config = load_run_config(path)

    assert isinstance(config, RunConfig)


# The smallest configuration a mapping fragment can sit in; the paths need not exist to load.
_MINIMAL_CONFIG = "traces:\n  path: traces/\nverdicts:\n  path: verdicts.csv\n"


@pytest.mark.parametrize("block", _of_kind("mapping"), ids=_ids(_of_kind("mapping")))
def test_mapping_example_loads_in_a_minimal_config(block: Block, tmp_path: Path) -> None:
    path = tmp_path / "detecttrace.yaml"
    path.write_text(_MINIMAL_CONFIG + block.text, encoding="utf-8")

    config = load_run_config(path)

    assert isinstance(config, RunConfig)


@pytest.mark.parametrize("block", _of_kind("checklist"), ids=_ids(_of_kind("checklist")))
def test_checklist_example_loads(block: Block, tmp_path: Path) -> None:
    path = tmp_path / "checklist.yaml"
    path.write_text(block.text, encoding="utf-8")

    checklists = load_checklists(path)

    assert len(checklists) == 1


@pytest.mark.parametrize("block", _of_kind("serve"), ids=_ids(_of_kind("serve")))
def test_serve_config_example_loads(block: Block, tmp_path: Path) -> None:
    path = tmp_path / "detecttrace-serve.yaml"
    path.write_text(block.text, encoding="utf-8")

    config = load_serve_config(path)

    assert isinstance(config, ServeConfig)


# The smallest tokens section a serve fragment can sit in; the hashes need no known token.
_MINIMAL_TOKENS = (
    "tokens:\n"
    f"  ingest: [{{name: i, hash: 'sha256:{'1' * 64}'}}]\n"
    f"  verdicts: [{{name: v, hash: 'sha256:{'2' * 64}'}}]\n"
    f"  read: [{{name: r, hash: 'sha256:{'3' * 64}'}}]\n"
)


@pytest.mark.parametrize("block", _of_kind("serve_fragment"), ids=_ids(_of_kind("serve_fragment")))
def test_serve_fragment_loads_with_minimal_tokens(block: Block, tmp_path: Path) -> None:
    path = tmp_path / "detecttrace-serve.yaml"
    path.write_text(block.text + _MINIMAL_TOKENS, encoding="utf-8")

    config = load_serve_config(path)

    assert isinstance(config, ServeConfig)


def _load_documented_serve_config(tmp_path: Path) -> ServeConfig:
    # Verdict examples use the labels of the documented serve configuration.
    path = tmp_path / "detecttrace-serve.yaml"
    path.write_text(_of_kind("serve")[0].text, encoding="utf-8")
    return load_serve_config(path)


_VERDICT_BLOCKS = [*_of_kind("verdicts_json"), *_of_kind("verdicts_csv")]
_CONTENT_TYPES = {"json": "application/json", "csv": "text/csv"}


@pytest.mark.parametrize("block", _VERDICT_BLOCKS, ids=_ids(_VERDICT_BLOCKS))
def test_verdict_example_is_accepted_whole(block: Block, tmp_path: Path) -> None:
    config = _load_documented_serve_config(tmp_path)

    _, rejected, _ = parse_verdicts_body(
        block.text.encode(), _CONTENT_TYPES[block.language], config
    )

    assert rejected == []


@pytest.mark.parametrize("block", _VERDICT_BLOCKS, ids=_ids(_VERDICT_BLOCKS))
def test_verdict_example_has_rows(block: Block, tmp_path: Path) -> None:
    config = _load_documented_serve_config(tmp_path)

    rows, _, _ = parse_verdicts_body(block.text.encode(), _CONTENT_TYPES[block.language], config)

    assert len(rows) > 0


@pytest.mark.parametrize("block", _of_kind("json"), ids=_ids(_of_kind("json")))
def test_json_example_parses(block: Block) -> None:
    assert isinstance(json.loads(block.text), dict)


# Collector configurations belong to the OpenTelemetry Collector, which DetectTrace doesn't
# load, so they are only checked to be valid YAML.
@pytest.mark.parametrize("block", _of_kind("collector"), ids=_ids(_of_kind("collector")))
def test_collector_example_is_yaml(block: Block) -> None:
    assert isinstance(yaml.safe_load(block.text), dict)


@pytest.mark.parametrize("block", _of_kind("python"), ids=_ids(_of_kind("python")))
def test_python_example_compiles(block: Block) -> None:
    assert isinstance(compile(block.text, block.where, "exec"), types.CodeType)


# A hash in a public example must match no token, or copying the example would accept a token
# anyone can read here.
_HASH = re.compile(r"sha256:[0-9a-f]{64}")
_PLACEHOLDER_HASH = re.compile(r"sha256:0{60}[0-9a-f]{4}")
HASH_FILES = [*DOC_FILES, *sorted((ROOT / "deploy").glob("*"))]


@pytest.mark.parametrize("path", HASH_FILES, ids=[path.name for path in HASH_FILES])
def test_every_token_hash_is_a_placeholder(path: Path) -> None:
    hashes = _HASH.findall(path.read_text(encoding="utf-8"))

    assert [value for value in hashes if not _PLACEHOLDER_HASH.fullmatch(value)] == []
