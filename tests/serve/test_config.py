from pathlib import Path
from typing import Any

import pytest
import yaml

from detecttrace.runconfig import ConfigFileError
from detecttrace.serve.auth import hash_token
from detecttrace.serve.config import load_serve_config

HASH = hash_token("example-token")


def _entry(name: str = "team", hash_value: str = HASH) -> dict[str, str]:
    return {"name": name, "hash": hash_value}


def _document(**changes: Any) -> dict[str, Any]:
    document: dict[str, Any] = {
        "serve": {"database": "detecttrace.db"},
        "tokens": {
            "ingest": [_entry("collector")],
            "verdicts": [_entry("soar")],
            "read": [_entry("analysts")],
        },
    }
    document.update(changes)
    return document


def _load(tmp_path: Path, document: dict[str, Any]) -> Any:
    path = tmp_path / "serve.yaml"
    path.write_text(yaml.safe_dump(document), encoding="utf-8")
    return load_serve_config(path)


def _error(tmp_path: Path, document: dict[str, Any]) -> str:
    with pytest.raises(ConfigFileError) as caught:
        _load(tmp_path, document)
    return str(caught.value)


def _with_serve(**serve: Any) -> dict[str, Any]:
    return _document(serve={"database": "detecttrace.db", **serve})


def _with_tokens(**roles: Any) -> dict[str, Any]:
    document = _document()
    document["tokens"].update(roles)
    return document


def test_full_config_loads_with_defaults(tmp_path: Path) -> None:
    config = _load(
        tmp_path,
        _document(
            checklists="checklists.yaml",
            dashboard={"max_detail_cases": 10},
            label_map={"TP": "true_positive"},
        ),
    )

    assert (config.serve.host, config.serve.port, config.serve.settle_seconds) == (
        "127.0.0.1",
        4320,
        300,
    )


def test_relative_database_resolves_against_the_config_folder(tmp_path: Path) -> None:
    config = _load(tmp_path, _with_serve(database="data/detecttrace.db"))

    assert config.serve.database == tmp_path.absolute() / "data/detecttrace.db"


def test_relative_tls_files_resolve_against_the_config_folder(tmp_path: Path) -> None:
    config = _load(tmp_path, _with_serve(tls={"certfile": "c.pem", "keyfile": "k.pem"}))

    assert config.serve.tls.keyfile == tmp_path.absolute() / "k.pem"


def test_relative_checklists_resolve_against_the_config_folder(tmp_path: Path) -> None:
    config = _load(tmp_path, _document(checklists="checklists.yaml"))

    assert config.checklists == tmp_path.absolute() / "checklists.yaml"


def test_tilde_database_is_rejected(tmp_path: Path) -> None:
    assert "`~` is not expanded" in _error(tmp_path, _with_serve(database="~/x.db"))


def test_missing_role_is_rejected(tmp_path: Path) -> None:
    document = _document()
    del document["tokens"]["verdicts"]

    assert "tokens.verdicts" in _error(tmp_path, document)


def test_empty_role_list_is_rejected(tmp_path: Path) -> None:
    assert "tokens.read" in _error(tmp_path, _with_tokens(read=[]))


@pytest.mark.parametrize(
    "bad_hash",
    [
        "md5:" + "a" * 64,
        "sha256:" + "a" * 63,
        "sha256:" + "A" * 64,
        "a" * 64,
    ],
    ids=["wrong-prefix", "short", "uppercase-hex", "no-prefix"],
)
def test_malformed_hash_is_rejected(tmp_path: Path, bad_hash: str) -> None:
    assert "tokens.read.0.hash" in _error(
        tmp_path, _with_tokens(read=[_entry(hash_value=bad_hash)])
    )


def test_duplicate_name_within_a_role_is_rejected(tmp_path: Path) -> None:
    other_hash = hash_token("another")

    assert "used twice" in _error(
        tmp_path, _with_tokens(read=[_entry("team"), _entry("team", other_hash)])
    )


def test_same_name_in_different_roles_is_accepted(tmp_path: Path) -> None:
    config = _load(tmp_path, _with_tokens(read=[_entry("team")], ingest=[_entry("team")]))

    assert config.tokens.read[0].name == "team"


@pytest.mark.parametrize("bad_name", ["has space", "a" * 65, "", "semi;colon"])
def test_bad_token_name_is_rejected(tmp_path: Path, bad_name: str) -> None:
    assert "tokens.read.0.name" in _error(tmp_path, _with_tokens(read=[_entry(bad_name)]))


def test_name_of_64_characters_is_accepted(tmp_path: Path) -> None:
    config = _load(tmp_path, _with_tokens(read=[_entry("a" * 64)]))

    assert config.tokens.read[0].name == "a" * 64


def test_unknown_top_level_key_is_rejected(tmp_path: Path) -> None:
    assert "surprise" in _error(tmp_path, _document(surprise=1))


def test_unknown_serve_key_is_rejected(tmp_path: Path) -> None:
    assert "serve.surprise" in _error(tmp_path, _with_serve(surprise=1))


def test_unknown_token_entry_key_is_rejected(tmp_path: Path) -> None:
    entry = {**_entry(), "surprise": 1}

    assert "tokens.read.0.surprise" in _error(tmp_path, _with_tokens(read=[entry]))


def test_unknown_tls_key_is_rejected(tmp_path: Path) -> None:
    tls = {"certfile": "c.pem", "keyfile": "k.pem", "surprise": 1}

    assert "serve.tls.surprise" in _error(tmp_path, _with_serve(tls=tls))


@pytest.mark.parametrize("port", [0, 65536, True, 4320.5])
def test_port_out_of_range_or_not_an_integer_is_rejected(tmp_path: Path, port: Any) -> None:
    assert "serve.port" in _error(tmp_path, _with_serve(port=port))


def test_highest_port_is_accepted(tmp_path: Path) -> None:
    assert _load(tmp_path, _with_serve(port=65535)).serve.port == 65535


def test_negative_settle_seconds_is_rejected(tmp_path: Path) -> None:
    assert "serve.settle_seconds" in _error(tmp_path, _with_serve(settle_seconds=-1))


def test_boolean_settle_seconds_is_rejected(tmp_path: Path) -> None:
    assert "serve.settle_seconds" in _error(tmp_path, _with_serve(settle_seconds=True))


def test_zero_settle_seconds_is_accepted(tmp_path: Path) -> None:
    assert _load(tmp_path, _with_serve(settle_seconds=0)).serve.settle_seconds == 0


def test_non_loopback_host_without_tls_names_the_keys_to_set(tmp_path: Path) -> None:
    message = _error(tmp_path, _with_serve(host="0.0.0.0"))

    assert "serve.allow_plain_http" in message


def test_non_loopback_host_error_also_names_tls(tmp_path: Path) -> None:
    assert "serve.tls" in _error(tmp_path, _with_serve(host="0.0.0.0"))


def test_non_loopback_host_with_allow_plain_http_is_accepted(tmp_path: Path) -> None:
    config = _load(tmp_path, _with_serve(host="0.0.0.0", allow_plain_http=True))

    assert config.serve.host == "0.0.0.0"


def test_non_loopback_host_with_tls_is_accepted(tmp_path: Path) -> None:
    config = _load(
        tmp_path, _with_serve(host="0.0.0.0", tls={"certfile": "c.pem", "keyfile": "k.pem"})
    )

    assert config.serve.host == "0.0.0.0"


@pytest.mark.parametrize("host", ["::1", "127.0.0.2", "LOCALHOST", "localhost"])
def test_loopback_host_is_accepted_without_tls(tmp_path: Path, host: str) -> None:
    assert _load(tmp_path, _with_serve(host=host)).serve.host == host


def test_hostname_without_tls_is_rejected(tmp_path: Path) -> None:
    assert "serve.allow_plain_http" in _error(tmp_path, _with_serve(host="example.com"))


def test_non_boolean_allow_plain_http_is_rejected(tmp_path: Path) -> None:
    assert "serve.allow_plain_http" in _error(tmp_path, _with_serve(allow_plain_http="yes"))


def test_non_mapping_document_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "serve.yaml"
    path.write_text("- a\n", encoding="utf-8")

    with pytest.raises(ConfigFileError, match="expected a mapping"):
        load_serve_config(path)


def test_missing_file_is_a_config_file_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigFileError, match="cannot be read"):
        load_serve_config(tmp_path / "missing.yaml")
