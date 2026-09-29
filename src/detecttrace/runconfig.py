"""The run configuration file (detecttrace.yaml): where the inputs are and how to read them."""

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, ValidationError, field_validator

from detecttrace.config import Config
from detecttrace.model import InputFileError
from detecttrace.yaml12 import Yaml12Error, load_yaml12

# Real configuration files are a few KB, and PyYAML's pure-Python parser takes seconds per megabyte.
_MAX_FILE_BYTES = 1 << 20


class ConfigFileError(InputFileError):
    """The configuration file cannot be used: missing, unreadable, or invalid."""


class TracesConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    path: Path
    format: Literal["otlp_jsonl", "otlp_json", "langfuse"] = "otlp_jsonl"

    @field_validator("format")
    @classmethod
    def _check_supported(cls, value: str) -> str:
        # Listed in the Literal so the message says "not yet" rather than "unknown format".
        if value == "langfuse":
            raise ValueError("'langfuse' is not supported yet; use otlp_jsonl or otlp_json")
        return value


class VerdictsConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    path: Path


class DashboardConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    max_detail_cases: int = Field(default=2000, ge=0)


class RunConfig(Config):
    """Everything in detecttrace.yaml; inherits the mapping and label maps (and their checks)."""

    traces: TracesConfig
    verdicts: VerdictsConfig
    checklists: Path | None = None
    output: Path = Path("detecttrace-dashboard.html")
    dashboard: DashboardConfig = Field(default_factory=DashboardConfig)
    # Validated now so a bad value fails early; it has no effect yet.
    telemetry: StrictBool = False

    def to_config(self) -> Config:
        return Config(
            mapping=self.mapping, label_map=self.label_map, agent_label_map=self.agent_label_map
        )


def load_run_config(path: Path) -> RunConfig:
    """Load and validate a configuration file; any problem raises ConfigFileError.

    Relative paths in the file are resolved against the file's folder, never the working
    folder, so a run gives the same result from anywhere. `~` is not expanded.
    """
    try:
        document = load_yaml12(
            path, max_bytes=_MAX_FILE_BYTES, what="configuration files are a few KB"
        )
    except Yaml12Error as error:
        raise ConfigFileError(str(error)) from None
    if not isinstance(document, dict):
        raise ConfigFileError(
            f"{path}: expected a mapping with 'traces' and 'verdicts' at the top level"
        )
    try:
        config = RunConfig.model_validate(document)
    except ValidationError as error:
        lines = [
            f"{'.'.join(str(part) for part in detail['loc'])}: "
            + str(detail["msg"]).removeprefix("Value error, ")
            for detail in error.errors()
        ]
        raise ConfigFileError(f"{path}: invalid configuration\n  " + "\n  ".join(lines)) from None
    folder = path.absolute().parent
    return config.model_copy(
        update={
            "traces": config.traces.model_copy(update={"path": folder / config.traces.path}),
            "verdicts": config.verdicts.model_copy(update={"path": folder / config.verdicts.path}),
            "checklists": None if config.checklists is None else folder / config.checklists,
            "output": folder / config.output,
        }
    )
