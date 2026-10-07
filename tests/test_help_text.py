"""The dashboard's help text agrees with docs/metrics.md and describes without judging."""

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
HELP_DIR = REPO_ROOT / "dashboard" / "src" / "help"
TERMS_TEXT = (HELP_DIR / "terms.ts").read_text(encoding="utf-8")
METRICS_TEXT = (REPO_ROOT / "docs" / "metrics.md").read_text(encoding="utf-8")
HELP_FILES = sorted(path for path in HELP_DIR.glob("*.ts*") if ".test." not in path.name)

TERM_KEYS = {"completeness", "agreement", "kappa", "dangerous"}
# `[^}]` keeps a term whose `short` isn't a double-quoted string from borrowing the next term's.
SHORT_TEXTS = dict(
    re.findall(r'^\s+(\w+): \{[^}]*?short:\s*"((?:[^"\\]|\\.)*)"', TERMS_TEXT, re.MULTILINE)
)
DOCS_SECTIONS = dict(
    re.findall(r"^## ([^\n]+)\n(.*?)(?=^## |\Z)", METRICS_TEXT, re.MULTILINE | re.DOTALL)
)
DOCS_HEADINGS = [
    "Evidence completeness",
    "Verdict agreement",
    "Chance-corrected agreement (κ)",
    "Dangerous false closes",
]

TERM_PHRASES = [
    ("completeness", "satisfied"),
    ("completeness", "checklist items"),
    ("agreement", "verdict equals the analyst's"),
    ("kappa", "chance"),
    ("kappa", "perfect agreement"),
    ("kappa", "no better than chance"),
    ("dangerous", "true positive"),
    ("dangerous", "false positive or benign"),
]
DOCS_PHRASES = [
    ("Evidence completeness", "satisfied"),
    ("Evidence completeness", "checklist items"),
    ("Verdict agreement", "verdict equals the analyst's"),
    ("Chance-corrected agreement (κ)", "chance"),
    ("Chance-corrected agreement (κ)", "perfect agreement"),
    ("Chance-corrected agreement (κ)", "no better than chance"),
    ("Dangerous false closes", "true positive"),
    ("Dangerous false closes", "false positive or benign"),
]
# Words that judge a result rather than describe it.
BANNED_WORDS = ["better", "worse", "good", "bad", "threshold", "should", "regress"]
# Describes what κ = 0 means, so it is not a judgement.
ALLOWED_PHRASE = re.compile(r"no\s+better\s+than\s+chance", re.IGNORECASE)


def normalize(text: str) -> str:
    return " ".join(text.replace("`", "").replace("_", " ").split()).lower()


def test_terms_ts_short_texts_are_all_extracted() -> None:
    assert set(SHORT_TEXTS) == TERM_KEYS, (
        "Each term in terms.ts needs a double-quoted `short` string inside its own braces"
    )


@pytest.mark.parametrize("heading", DOCS_HEADINGS)
def test_docs_has_every_term_heading(heading: str) -> None:
    assert heading in DOCS_SECTIONS, f'docs/metrics.md has no "## {heading}" section'


@pytest.mark.parametrize(
    ("term", "phrase"), TERM_PHRASES, ids=[f"{term}: {phrase}" for term, phrase in TERM_PHRASES]
)
def test_short_text_has_docs_phrase(term: str, phrase: str) -> None:
    assert phrase in normalize(SHORT_TEXTS.get(term, ""))


@pytest.mark.parametrize(
    ("heading", "phrase"),
    DOCS_PHRASES,
    ids=[f"{heading}: {phrase}" for heading, phrase in DOCS_PHRASES],
)
def test_docs_section_has_short_text_phrase(heading: str, phrase: str) -> None:
    assert phrase in normalize(DOCS_SECTIONS.get(heading, ""))


@pytest.mark.parametrize("path", HELP_FILES, ids=lambda path: path.name)
@pytest.mark.parametrize("word", BANNED_WORDS)
def test_help_text_does_not_judge(path: Path, word: str) -> None:
    text = ALLOWED_PHRASE.sub("", path.read_text(encoding="utf-8"))

    assert re.search(rf"\b{word}(?:s|es|ed|ion|ions|ing)?\b", text, re.IGNORECASE) is None
