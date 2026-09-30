"""Configuration models. M1 covers the attribute mapping and the label maps."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator

from detecttrace import conventions
from detecttrace.model import Verdict


def normalize_label(text: str) -> str:
    """Normalize a verdict label or alert class for matching: collapse whitespace and ignore case."""
    return " ".join(text.split()).casefold()


PromptVersionLookup = Literal["root_then_resource", "descendant"]


class OperationConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    attribute: str = conventions.OPERATION_ATTRIBUTE
    agent_value: str = conventions.INVOKE_AGENT
    tool_value: str = conventions.EXECUTE_TOOL
    span_name_fallback: StrictBool = True


class MappingConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    case_id: str = "detecttrace.case_id"
    alert_class: str = "detecttrace.alert_class"
    verdict: str = "detecttrace.verdict"
    prompt_version: str = "detecttrace.prompt_version"
    prompt_version_lookup: PromptVersionLookup = "root_then_resource"
    tool_name: str = conventions.TOOL_NAME
    tool_arguments: str = conventions.TOOL_CALL_ARGUMENTS
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
            normalized_key = normalize_label(key)
            if not normalized_key:
                raise ValueError(f"label '{key}' is empty after normalization")
            if normalized_key in normalized and normalized[normalized_key] != verdict:
                raise ValueError(
                    f"labels '{first_key[normalized_key]}' and '{key}' are the same after normalization "
                    f"but map to '{normalized[normalized_key]}' and '{verdict}'. Keep only one of them."
                )
            normalized[normalized_key] = verdict
            first_key.setdefault(normalized_key, key)
        return normalized

    def to_analyst_verdict(self, label: str) -> Verdict | None:
        return self.label_map.get(normalize_label(label))

    def to_agent_verdict(self, label: str) -> Verdict | None:
        normalized_key = normalize_label(label)
        if normalized_key in self.agent_label_map:
            return self.agent_label_map[normalized_key]
        return self.label_map.get(normalized_key)
