"""Tests du daemon (`CollectorDaemon`).

Aucun accès réseau réel : `RagifixClient.health` et `build_connector` sont
monkeypatchés, le state store utilise `tmp_path`.
"""

from __future__ import annotations

import asyncio
import logging

import pytest

from ragifix_collector import daemon as daemon_module
from ragifix_collector.config import AppConfig
from ragifix_collector.daemon import CollectorDaemon


@pytest.fixture
def app_config(minimal_config_dict, ragifix_token_env):
    return AppConfig.model_validate(minimal_config_dict)


def test_disabled_sources_excluded(minimal_config_dict, ragifix_token_env):
    minimal_config_dict["sources"].append(
        {
            "name": "disabled_source",
            "type": "local_fs",
            "enabled": False,
            "paths": ["/tmp"],
        }
    )
    config = AppConfig.model_validate(minimal_config_dict)
    daemon = CollectorDaemon(config)
    try:
        assert [s.name for s in daemon._sources] == ["docs_internes"]
    finally:
        daemon._state_store.close()
        daemon._client.close()


def test_warns_when_no_source_enabled(minimal_config_dict, ragifix_token_env, caplog):
    minimal_config_dict["sources"][0]["enabled"] = False
    config = AppConfig.model_validate(minimal_config_dict)
    with caplog.at_level(logging.WARNING):
        daemon = CollectorDaemon(config)
    try:
        assert any("Aucune source activée" in record.message for record in caplog.records)
    finally:
        daemon._state_store.close()
        daemon._client.close()


def test_run_cycle_skips_sync_when_ragifix_unhealthy(app_config, monkeypatch):
    daemon = CollectorDaemon(app_config)
    monkeypatch.setattr(daemon._client, "health", lambda: False)

    build_calls = []
    monkeypatch.setattr(
        daemon_module, "build_connector", lambda source: build_calls.append(source.name)
    )

    asyncio.run(daemon._run_cycle())

    assert build_calls == []
    daemon._state_store.close()
    daemon._client.close()


def test_run_cycle_syncs_healthy_sources(app_config, monkeypatch):
    daemon = CollectorDaemon(app_config)
    monkeypatch.setattr(daemon._client, "health", lambda: True)

    sync_calls = []

    async def _fake_sync_source(source_name, connector):
        sync_calls.append(source_name)

    async def _fake_sync_all_sources(sources):
        sync_calls.append("sync_all_sources")

    monkeypatch.setattr(daemon._syncer, "sync_source", _fake_sync_source)
    monkeypatch.setattr(daemon._syncer, "sync_all_sources", _fake_sync_all_sources)
    monkeypatch.setattr(daemon_module, "build_connector", lambda source: object())

    asyncio.run(daemon._run_cycle())

    assert sync_calls == ["docs_internes", "sync_all_sources"]
    daemon._state_store.close()
    daemon._client.close()


def test_run_cycle_continues_after_connector_init_failure(minimal_config_dict, ragifix_token_env, monkeypatch):
    minimal_config_dict["sources"].append(
        {"name": "second_source", "type": "local_fs", "enabled": True, "paths": ["/tmp"]}
    )
    config = AppConfig.model_validate(minimal_config_dict)
    daemon = CollectorDaemon(config)
    monkeypatch.setattr(daemon._client, "health", lambda: True)

    def _fake_build_connector(source):
        if source.name == "docs_internes":
            raise RuntimeError("init échouée")
        return object()

    sync_calls = []

    async def _fake_sync_source(source_name, connector):
        sync_calls.append(source_name)

    async def _fake_sync_all_sources(sources):
        pass

    monkeypatch.setattr(daemon_module, "build_connector", _fake_build_connector)
    monkeypatch.setattr(daemon._syncer, "sync_source", _fake_sync_source)
    monkeypatch.setattr(daemon._syncer, "sync_all_sources", _fake_sync_all_sources)

    asyncio.run(daemon._run_cycle())  # ne doit pas lever

    assert sync_calls == ["second_source"]
    daemon._state_store.close()
    daemon._client.close()


def test_run_one_shot_mode_executes_single_cycle(app_config, monkeypatch):
    minimal = app_config
    daemon = CollectorDaemon(minimal)
    assert daemon._config.sync.interval_seconds == 0

    cycle_calls = []

    async def _fake_run_cycle():
        cycle_calls.append(1)

    monkeypatch.setattr(daemon, "_run_cycle", _fake_run_cycle)

    asyncio.run(daemon.run())

    assert cycle_calls == [1]
