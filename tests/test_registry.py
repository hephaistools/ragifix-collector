"""Tests du registre des connecteurs (`build_connector`)."""

from __future__ import annotations

import sys

import pytest

from ragifix_collector.config import ConfigError, SourceConfig
from ragifix_collector.connectors.local_fs import LocalFsConnector
from ragifix_collector.connectors.registry import build_connector


def _source(**overrides) -> SourceConfig:
    data = {"name": "s1", "type": "local_fs"}
    data.update(overrides)
    return SourceConfig.model_validate(data)


# -- local_fs -----------------------------------------------------------------

def test_build_local_fs_success(tmp_path):
    source = _source(paths=[str(tmp_path)])
    connector = build_connector(source)
    assert isinstance(connector, LocalFsConnector)


def test_build_local_fs_missing_paths_raises():
    source = _source()
    with pytest.raises(ConfigError, match="paths"):
        build_connector(source)


def test_build_local_fs_empty_paths_raises():
    source = _source(paths=[])
    with pytest.raises(ConfigError):
        build_connector(source)


# -- sharepoint -----------------------------------------------------------------

def _sharepoint_source(**overrides) -> SourceConfig:
    data = {
        "name": "sp",
        "type": "sharepoint",
        "tenant_id": "t",
        "client_id": "c",
        "client_secret_env": "SP_SECRET",
        "site_url": "https://contoso.sharepoint.com/sites/X",
        "folder_path": "/Documents/RAG",
    }
    data.update(overrides)
    return SourceConfig.model_validate(data)


def test_build_sharepoint_missing_required_fields_raises():
    source = SourceConfig.model_validate({"name": "sp", "type": "sharepoint"})
    with pytest.raises(ConfigError, match="manquant"):
        build_connector(source)


def test_build_sharepoint_missing_secret_env_raises(monkeypatch):
    monkeypatch.delenv("SP_SECRET", raising=False)
    source = _sharepoint_source()
    with pytest.raises(ConfigError, match="SP_SECRET"):
        build_connector(source)


def test_build_sharepoint_without_msal_raises_config_error(monkeypatch):
    monkeypatch.setenv("SP_SECRET", "secret")
    monkeypatch.setitem(sys.modules, "msal", None)  # force l'ImportError lors de `import msal`
    source = _sharepoint_source()
    with pytest.raises(ConfigError):
        build_connector(source)


def test_build_sharepoint_success_with_msal(monkeypatch):
    pytest.importorskip("msal")
    monkeypatch.setenv("SP_SECRET", "secret")
    source = _sharepoint_source()
    connector = build_connector(source)
    from ragifix_collector.connectors.sharepoint import SharePointConnector

    assert isinstance(connector, SharePointConnector)


# -- type inconnu / plugins -----------------------------------------------------

def test_build_connector_unknown_type_raises():
    source = _source(type="unknown_type")
    with pytest.raises(ConfigError, match="unknown_type"):
        build_connector(source)


def test_build_connector_discovers_plugin(monkeypatch):
    class _FakeConnector:
        pass

    def _fake_factory(source: SourceConfig):
        return _FakeConnector()

    class _FakeEntryPoint:
        name = "my_plugin"

        def load(self):
            return _fake_factory

    monkeypatch.setattr(
        "ragifix_collector.connectors.registry.entry_points",
        lambda group=None: [_FakeEntryPoint()],
    )

    source = _source(type="my_plugin")
    connector = build_connector(source)
    assert isinstance(connector, _FakeConnector)


def test_build_connector_unknown_type_lists_plugins_in_error(monkeypatch):
    class _FakeEntryPoint:
        name = "my_plugin"

        def load(self):
            return lambda source: object()

    monkeypatch.setattr(
        "ragifix_collector.connectors.registry.entry_points",
        lambda group=None: [_FakeEntryPoint()],
    )

    source = _source(type="does_not_exist")
    with pytest.raises(ConfigError, match="my_plugin"):
        build_connector(source)
