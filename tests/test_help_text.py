"""The dashboard's help text agrees with docs/metrics.md and describes without judging."""

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
HELP_DIR = REPO_ROOT / "dashboard" / "src" / "help"
TERMS_TEXT = (HELP_DIR / "terms.ts").read_text(encoding="utf-8")
METRICS_TEXT = (REPO_ROOT / "docs" / "metrics.md").read_text(encoding="utf-8")
HELP_FILES = sorted(
    (path for path in HELP_DIR.iterdir() if path.is_file() and ".test." not in path.name),
    key=lambda path: path.name,
)

SHORT_TEXTS = dict(
    re.findall(r'^  (\w+): \{.*?short:\s*"((?:[^"\\]|\\.)*)"', TERMS_TEXT, re.MULTILINE | re.DOTALL)
)
DOCS_SECTIONS = dict(
    re.findall(r"^## ([^\n]+)\n(.*?)(?=^## |\Z)", METRICS_TEXT, re.MULTILINE | re.DOTALL)
)

PARITY_CASES = [
    ("completeness", "Evidence completeness", "satisfied"),
    ("completeness", "Evidence completeness", "checklist items"),
    ("agreement", "Verdict agreement", "verdict equals the analyst's"),
    ("kappa", "Chance-corrected agreement (κ)", "chance"),
    ("kappa", "Chance-corrected agreement (κ)", "1"),
    ("kappa", "Chance-corrected agreement (κ)", "0"),
    ("dangerous", "Dangerous false closes", "true positive"),
    ("dangerous", "Dangerous false closes", "false positive or benign"),
]
# Words that judge a result rather than describe it.
BANNED_WORDS = ["better", "worse", "good", "bad", "threshold", "should", "regress"]
# Describes what κ = 0 means, so it is not a judgement.
ALLOWED_PHRASE = re.compile(r"no better than chance", re.IGNORECASE)


def normalize(text: str) -> str:
    return " ".join(text.replace("`", "").replace("_", " ").split()).lower()


@pytest.mark.parametrize(("term", "heading", "phrase"), PARITY_CASES)
def test_short_text_has_docs_phrase(term: str, heading: str, phrase: str) -> None:
    assert phrase in normalize(SHORT_TEXTS[term])


@pytest.mark.parametrize(("term", "heading", "phrase"), PARITY_CASES)
def test_docs_section_has_short_text_phrase(term: str, heading: str, phrase: str) -> None:
    assert phrase in normalize(DOCS_SECTIONS[heading])


@pytest.mark.parametrize("path", HELP_FILES, ids=lambda path: path.name)
@pytest.mark.parametrize("word", BANNED_WORDS)
def test_help_text_does_not_judge(path: Path, word: str) -> None:
    text = ALLOWED_PHRASE.sub("", path.read_text(encoding="utf-8"))

    assert re.search(rf"\b{word}\b", text, re.IGNORECASE) is None
