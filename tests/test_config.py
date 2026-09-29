import pytest
from pydantic import ValidationError

from detecttrace.config import Config, OperationConfig, normalize_label
from detecttrace.model import Verdict


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("Closed - Benign ", "closed - benign"),
        ("Closed -  Benign", "closed - benign"),
        ("\tMalicious\n", "malicious"),
        ("Closed-Benign", "closed-benign"),
    ],
)
def test_normalize_label_trims_folds_case_and_collapses_spaces(label: str, expected: str) -> None:
    assert normalize_label(label) == expected


def test_analyst_label_lookup_ignores_case_and_whitespace() -> None:
    config = Config(label_map={"Closed - Benign": Verdict.BENIGN})

    assert config.to_analyst_verdict(" closed -  BENIGN ") == Verdict.BENIGN


def test_unmapped_analyst_label_gives_none() -> None:
    config = Config(label_map={"TP": Verdict.TRUE_POSITIVE})

    assert config.to_analyst_verdict("Escalated") is None


def test_label_keys_that_collide_with_different_verdicts_are_rejected() -> None:
    with pytest.raises(ValidationError, match="same after normalization"):
        Config(label_map={"Benign": Verdict.BENIGN, "benign ": Verdict.FALSE_POSITIVE})


def test_agent_label_keys_that_collide_with_different_verdicts_are_rejected() -> None:
    with pytest.raises(ValidationError, match="same after normalization"):
        Config(agent_label_map={"Benign": Verdict.BENIGN, "benign ": Verdict.FALSE_POSITIVE})


def test_label_keys_that_collide_with_the_same_verdict_are_accepted() -> None:
    config = Config(label_map={"TP": Verdict.TRUE_POSITIVE, "tp": Verdict.TRUE_POSITIVE})

    assert config.to_analyst_verdict("TP") == Verdict.TRUE_POSITIVE


def test_label_key_that_is_empty_after_normalization_is_rejected() -> None:
    with pytest.raises(ValidationError, match="empty after normalization"):
        Config(label_map={"  ": Verdict.BENIGN})


def test_label_map_value_outside_the_verdict_set_is_rejected() -> None:
    with pytest.raises(ValidationError, match="label_map"):
        Config.model_validate({"label_map": {"TP": "malicious"}})


def test_agent_label_map_is_checked_before_label_map() -> None:
    config = Config(
        label_map={"Escalate": Verdict.FALSE_POSITIVE},
        agent_label_map={"escalate": Verdict.TRUE_POSITIVE},
    )

    assert config.to_agent_verdict("Escalate") == Verdict.TRUE_POSITIVE


def test_agent_label_falls_back_to_label_map() -> None:
    config = Config(label_map={"Benign": Verdict.BENIGN}, agent_label_map={"x": Verdict.BENIGN})

    assert config.to_agent_verdict("benign") == Verdict.BENIGN


def test_agent_label_map_lookup_ignores_case_and_whitespace() -> None:
    config = Config(agent_label_map={"Escalate": Verdict.TRUE_POSITIVE})

    assert config.to_agent_verdict(" escalate") == Verdict.TRUE_POSITIVE


def test_agent_label_in_neither_map_gives_none() -> None:
    config = Config(label_map={"TP": Verdict.TRUE_POSITIVE})

    assert config.to_agent_verdict("Escalated") is None


def test_analyst_lookup_never_uses_agent_label_map() -> None:
    config = Config(agent_label_map={"escalate": Verdict.TRUE_POSITIVE})

    assert config.to_analyst_verdict("escalate") is None


def test_mapping_defaults_to_detecttrace_attributes() -> None:
    assert Config().mapping.case_id == "detecttrace.case_id"


def test_span_name_fallback_rejects_a_yes_string() -> None:
    with pytest.raises(ValidationError, match="span_name_fallback"):
        OperationConfig(span_name_fallback="yes")  # type: ignore[arg-type]
