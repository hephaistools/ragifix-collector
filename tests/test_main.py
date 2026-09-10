"""Tests du point d'entrée CLI (main).

Seulement les chemins légers : validation des arguments, erreur de
configuration, et démarrage réussi avec un daemon mocké (pour éviter de
lancer une vraie boucle asyncio infinie ou un accès réseau).
"""

from __future__ import annotations

import pytest

from ragifix_collector import main as main_module
from ragifix_collector.main import main


def test_main_missing_config_arg_exits(monkeypatch):
    monkeypatch.setattr("sys.argv", ["ragifix-collector"])
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 2


def test_main_invalid_config_exits(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "sys.argv",
        ["ragifix-collector", "--config", str(tmp_path / "inexistant.yaml")],
    )
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 1


def test_main_daemon_init_failure_exits(monkeypatch, write_config, minimal_config_dict, ragifix_token_env):
    path = write_config(minimal_config_dict)
    monkeypatch.setattr("sys.argv", ["ragifix-collector", "--config", str(path)])

    def _raise(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(main_module, "CollectorDaemon", _raise)

    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 1


def test_main_success_runs_daemon(monkeypatch, write_config, minimal_config_dict, ragifix_token_env):
    path = write_config(minimal_config_dict)
    monkeypatch.setattr("sys.argv", ["ragifix-collector", "--config", str(path)])

    calls = []

    class _FakeDaemon:
        def __init__(self, config):
            calls.append(config)

        async def run(self):
            calls.append("ran")

    monkeypatch.setattr(main_module, "CollectorDaemon", _FakeDaemon)

    main()  # ne doit pas lever

    assert calls[-1] == "ran"
