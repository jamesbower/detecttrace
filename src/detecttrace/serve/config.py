"""The serve configuration file: the run configuration's shared settings plus `serve:` and tokens."""

import ipaddress
from pathlib import Path
from typing import Annotated, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    ValidationError,
    field_validator,
    model_validator,
)

from detecttrace.config import Config
from detecttrace.runconfig import (
    MAX_CONFIG_BYTES,
    ConfigFileError,
    DashboardConfig,
    _check_path,
    describe_validation_error,
)
from detecttrace.yaml12 import Yaml12Error, load_yaml12

HASH_PATTERN = r"^sha256:[0-9a-f]{64}$"
# Names go to logs and an audit table, so they stay plain.
NAME_PATTERN = r"^[A-Za-z0-9_.-]{1,64}$"


class TokenEntry(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(pattern=NAME_PATTERN)
    hash: str = Field(pattern=HASH_PATTERN)


def _check_unique_names(entries: list[TokenEntry]) -> list[TokenEntry]:
    seen: set[str] = set()
    for entry in entries:
        if entry.name in seen:
            raise ValueError(f"token name '{entry.name}' is used twice; names must be unique")
        seen.add(entry.name)
    return entries


TokenList = Annotated[list[TokenEntry], Field(min_length=1)]


class TokenRoles(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    ingest: TokenList
    verdicts: TokenList
    read: TokenList

    _check_unique = field_validator("ingest", "verdicts", "read")(_check_unique_names)


class TlsConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    certfile: Path
    keyfile: Path

    _check_path = field_validator("certfile", "keyfile", mode="before")(_check_path)


class ServeSection(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    database: Path
    host: str = "127.0.0.1"
    port: StrictInt = Field(default=4320, ge=1, le=65535)
    tls: TlsConfig | None = None
    allow_plain_http: StrictBool = False
    settle_seconds: StrictInt = Field(default=300, ge=0)

    _check_path = field_validator("database", mode="before")(_check_path)

    @model_validator(mode="after")
    def _require_tls_off_loopback(self) -> Self:
        if not _is_loopback(self.host) and self.tls is None and not self.allow_plain_http:
            raise ValueError(
                f"host '{self.host}' is not a loopback address, so traffic and tokens would "
                "cross the network unencrypted. Set serve.tls, or set serve.allow_plain_http: "
                "true if TLS ends at a proxy in front of this service."
            )
        return self


class ServeConfig(Config):
    """Everything in a serve configuration file; the mapping, label maps and checklists match."""

    checklists: Path | None = None
    dashboard: DashboardConfig = Field(default_factory=DashboardConfig)
    serve: ServeSection
    tokens: TokenRoles

    _check_path = field_validator("checklists", mode="before")(_check_path)


def load_serve_config(path: Path) -> ServeConfig:
    """Load and validate a serve configuration file; any problem raises ConfigFileError.

    Relative paths are resolved against the file's folder, as in the run configuration.
    """
    try:
        document = load_yaml12(
            path, max_bytes=MAX_CONFIG_BYTES, what="configuration files are a few KB"
        )
    except Yaml12Error as error:
        raise ConfigFileError(str(error)) from None
    if not isinstance(document, dict):
        raise ConfigFileError(
            f"{path}: expected a mapping with 'serve' and 'tokens' at the top level"
        )
    try:
        config = ServeConfig.model_validate(document)
    except ValidationError as error:
        lines = describe_validation_error(error)
        raise ConfigFileError(f"{path}: invalid configuration\n  " + "\n  ".join(lines)) from None
    folder = path.absolute().parent
    tls = config.serve.tls
    section = config.serve.model_copy(
        update={
            "database": folder / config.serve.database,
            "tls": None
            if tls is None
            else tls.model_copy(
                update={"certfile": folder / tls.certfile, "keyfile": folder / tls.keyfile}
            ),
        }
    )
    return config.model_copy(
        update={
            "serve": section,
            "checklists": None if config.checklists is None else folder / config.checklists,
        }
    )


def _is_loopback(host: str) -> bool:
    if host.casefold() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False
