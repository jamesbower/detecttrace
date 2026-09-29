"""Configuration models (PRD Appendix A). M1 covers the mapping and the label maps."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from detecttrace import conventions
from detecttrace.model import Verdict


def normalize_label(text: str) -> str:
    """Normalize a verdict label or alert class for matching (PRD §7.2)."""
    return " ".join(text.split()).casefold()


class OperationConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    attribute: str = conventions.OPERATION_ATTRIBUTE
    agent_value: str = conventions.INVOKE_AGENT
    tool_value: str = conventions.EXECUTE_TOOL
    span_name_fallback: bool = True


class MappingConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    case_id: str = "detecttrace.case_id"
    alert_class: str = "detecttrace.alert_class"
    verdict: str = "detecttrace.verdict"
    prompt_version: str = "detecttrace.prompt_version"
    prompt_version_lookup: Literal["root_then_resource", "descendant"] = "root_then_resource"
    operation: OperationConfig = Field(default_factory=OperationConfig)


class Config(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    mapping: MappingConfig = Field(default_factory=MappingConfig)
    label_map: dict[str, Verdict] = Field(default_factory=dict)
    agent_label_map: dict[str, Verdict] = Field(default_factory=dict)

    @field_validator("label_map", "agent_label_map")
    @classmethod
    def _normalize_keys(cls, raw: dict[str, Verdict]) -> dict[str, Verdict]:
        normalized: dict[str, Verdict] = {}
        first_key: dict[str, str] = {}
        for key, verdict in raw.items():
            norm = normalize_label(key)
            if norm in normalized and normalized[norm] != verdict:
                raise ValueError(
                    f"labels '{first_key[norm]}' and '{key}' are the same after normalization "
                    f"but map to '{normalized[norm]}' and '{verdict}'. Keep only one of them."
                )
            normalized[norm] = verdict
            first_key.setdefault(norm, key)
        return normalized

    def to_analyst_verdict(self, label: str) -> Verdict | None:
        return self.label_map.get(normalize_label(label))

    def to_agent_verdict(self, label: str) -> Verdict | None:
        norm = normalize_label(label)
        return self.agent_label_map.get(norm) or self.label_map.get(norm)
