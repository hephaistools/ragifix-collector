"""Tests des modèles de configuration de ragifix-collector."""

from __future__ import annotations

import pytest

from ragifix_collector.config import (
    AppConfig,
    ConfigError,
    ExtensionsFilter,
    RagifixConfig,
    SourceConfig,
    load_config,
)


# -- ExtensionsFilter -------------------------------------------------------

def test_extensions_allow_list_matches():
    f = ExtensionsFilter(method="allow", list=["txt", "md"])
    assert f.is_allowed("txt") is True
    assert f.is_allowed("pdf") is False


def test_extensions_deny_list_matches():
    f = ExtensionsFilter(method="deny", list=["zip", "exe"])
    assert f.is_allowed("zip") is False
    assert f.is_allowed("txt") is True


def test_extensions_default_is_deny_empty_list():
    f = ExtensionsFilter()
    assert f.method == "deny"
    assert f.is_allowed("anything") is True


def test_extensions_dot_prefix_normalized():
    f = ExtensionsFilter(method="allow", list=[".txt"])
    assert f.is_allowed("txt") is True
    assert f.is_allowed(".txt") is True


def test_extensions_case_insensitive():
    f = ExtensionsFilter(method="allow", list=["TXT"])
    assert f.is_allowed("txt") is True
    assert f.is_allowed("Txt") is True


# -- RagifixConfig / secrets via env -----------------------------------------

def test_api_token_missing_raises(monkeypatch):
    monkeypatch.delenv("RAGIFIX_API_TOKEN", raising=False)
    cfg = RagifixConfig(api_token_env="RAGIFIX_API_TOKEN")
    with pytest.raises(ConfigError):
        _ = cfg.api_token


def test_api_token_present(monkeypatch):
    monkeypatch.setenv("RAGIFIX_API_TOKEN", "secret")
    cfg = RagifixConfig(api_token_env="RAGIFIX_API_TOKEN")
    assert cfg.api_token == "secret"


def test_ragifix_config_defaults():
    cfg = RagifixConfig(api_token_env="X")
    assert cfg.base_url == "http://127.0.0.1:8421"
    assert cfg.timeout_seconds == 60.0


# -- SourceConfig (extra="allow") --------------------------------------------

def test_source_config_preserves_connector_specific_fields():
    source = SourceConfig.model_validate(
        {"name": "s1", "type": "local_fs", "paths": ["/tmp/docs"]}
    )
    assert getattr(source, "paths") == ["/tmp/docs"]
    assert source.enabled is True
    assert source.description is None


def test_source_config_sharepoint_fields_preserved():
    source = SourceConfig.model_validate(
        {
            "name": "sp",
            "type": "sharepoint",
            "tenant_id": "t",
            "client_id": "c",
            "client_secret_env": "SECRET_ENV",
            "site_url": "https://contoso.sharepoint.com/sites/X",
            "folder_path": "/Documents/RAG",
        }
    )
    assert source.tenant_id == "t"
    assert source.client_secret_env == "SECRET_ENV"


# -- AppConfig ----------------------------------------------------------------

def test_app_config_valid(minimal_config_dict):
    cfg = AppConfig.model_validate(minimal_config_dict)
    assert cfg.ragifix.base_url == "http://127.0.0.1:8421"
    assert len(cfg.sources) == 1
    assert cfg.sync.interval_seconds == 0
    assert cfg.state_store.backend == "sqlite"


def test_app_config_defaults():
    cfg = AppConfig.model_validate(
        {
            "ragifix": {"api_token_env": "X"},
            "sources": [{"name": "s1", "type": "local_fs", "paths": ["/tmp"]}],
        }
    )
    assert cfg.sync.interval_seconds == 300
    assert cfg.sync.max_retries == 5
    assert cfg.state_store.sqlite.path == "/var/lib/ragifix-collector/state.db"
    assert cfg.logging.level == "INFO"


def test_app_config_duplicate_source_names_raises(minimal_config_dict):
    minimal_config_dict["sources"].append(dict(minimal_config_dict["sources"][0]))
    with pytest.raises(ValueError):
        AppConfig.model_validate(minimal_config_dict)


def test_app_config_requires_sources_key():
    with pytest.raises(ValueError):
        AppConfig.model_validate({"ragifix": {"api_token_env": "X"}})


# -- load_config ----------------------------------------------------------------

def test_load_config_missing(tmp_path):
    with pytest.raises(ConfigError):
        load_config(str(tmp_path / "inexistant.yaml"))


def test_load_config_empty(tmp_path):
    f = tmp_path / "empty.yaml"
    f.write_text("", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(str(f))


def test_load_config_invalid_yaml_content(tmp_path):
    f = tmp_path / "invalid.yaml"
    f.write_text("ragifix:\n  api_token_env: X\n", encoding="utf-8")  # sources manquant
    with pytest.raises(ConfigError):
        load_config(str(f))


def test_load_config_valid(write_config, minimal_config_dict):
    path = write_config(minimal_config_dict)
    cfg = load_config(path)
    assert isinstance(cfg, AppConfig)
    assert cfg.sources[0].name == "docs_internes"
