"""The run configuration file (detecttrace.yaml): where the inputs are and how to read them."""

from pathlib import Path
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    ValidationError,
    field_validator,
)

from detecttrace.config import Config
from detecttrace.model import InputFileError
from detecttrace.yaml12 import Yaml12Error, load_yaml12

# Real configuration files are a few KB, and PyYAML's pure-Python parser takes seconds per megabyte.
_MAX_FILE_BYTES = 1 << 20


class ConfigFileError(InputFileError):
    """The configuration file cannot be used: missing, unreadable, or invalid."""


def _check_path(value: object) -> object:
    # Runs before conversion because Path("") silently becomes ".", the config folder.
    if isinstance(value, str):
        if not value.strip():
            raise ValueError("path is empty")
        if Path(value).parts[:1] == ("~",):
            raise ValueError(
                "`~` is not expanded; write the full path or a path relative to this file"
            )
    return value


class TracesConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    path: Path
    format: Literal["otlp_jsonl", "otlp_json", "langfuse"] = "otlp_jsonl"

    _check_path = field_validator("path", mode="before")(_check_path)

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
    _check_path = field_validator("path", mode="before")(_check_path)


class DashboardConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    max_detail_cases: StrictInt = Field(default=2000, ge=0)


class RunConfig(Config):
    """Everything in detecttrace.yaml; inherits the mapping and label maps (and their checks)."""

    traces: TracesConfig
    verdicts: VerdictsConfig
    checklists: Path | None = None
    output: Path = Path("detecttrace-dashboard.html")
    dashboard: DashboardConfig = Field(default_factory=DashboardConfig)
    # Validated now so a bad value fails early; it has no effect yet.
    telemetry: StrictBool = False

    _check_path = field_validator("checklists", "output", mode="before")(_check_path)

    def to_config(self) -> Config:
        return Config(
            mapping=self.mapping, label_map=self.label_map, agent_label_map=self.agent_label_map
        )


def load_run_config(path: Path) -> RunConfig:
    """Load and validate a configuration file; any problem raises ConfigFileError.

    Relative paths in the file are resolved against the file's folder, never the working
    folder, so a run gives the same result from anywhere. A path starting with `~` is
    rejected rather than expanded.
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
