"""Fixtures et utilitaires partagés pour la suite de tests de ragifix-collector.

Aucun accès réseau réel : les appels vers l'API ragifix sont mockés via
`httpx.MockTransport` (le client `RagifixClient` accepte un `transport`
injectable), et les connecteurs sont soit réels sur `tmp_path` (local_fs),
soit remplacés par un `FakeConnector` respectant le protocole `Connector`.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import AsyncIterator

import pytest
import yaml

# Rend le package importable sans installation (lancement direct de pytest).
_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from ragifix_collector.connectors.base import Change, ChangeType  # noqa: E402
from ragifix_collector.state import SqliteStateStore  # noqa: E402


class FakeConnector:
    """Connecteur factice implémentant le protocole `Connector`.

    Utilisé par les tests du `Syncer` : les changements et le contenu
    retournés sont entièrement configurables, et chaque appel est enregistré
    dans `self.calls` pour assertion.
    """

    def __init__(
        self,
        changes: list[Change] | None = None,
        new_cursor: str = "cursor-2",
        contents: dict[str, bytes] | None = None,
    ):
        self.changes = changes or []
        self.new_cursor = new_cursor
        self.contents = contents or {}
        self.calls: list[tuple] = []

    async def list_changes(self, cursor: str | None) -> tuple[list[Change], str]:
        self.calls.append(("list_changes", cursor))
        return self.changes, self.new_cursor

    async def get_content(self, doc_id: str) -> AsyncIterator[bytes]:
        self.calls.append(("get_content", doc_id))
        yield self.contents.get(doc_id, b"contenu-par-defaut")


class FakeRagifixClient:
    """Fake du client API ragifix (interface utilisée par `Syncer`).

    Chaque méthode enregistre ses appels ; `put_document`/`delete_document`
    peuvent être configurés pour lever une exception sur des doc_id donnés,
    afin de tester les chemins d'échec/retry du `Syncer`.
    """

    def __init__(self, fail_on: set[str] | None = None):
        self.fail_on = fail_on or set()
        self.put_calls: list[tuple] = []
        self.delete_calls: list[str] = []
        self.sources_calls: list[list[dict]] = []

    def put_document(self, doc_id: str, content: bytes, extension: str, metadata: dict) -> dict:
        if doc_id in self.fail_on:
            raise RuntimeError(f"échec simulé pour {doc_id}")
        self.put_calls.append((doc_id, content, extension, metadata))
        return {"doc_id": doc_id, "chunk_count": 1}

    def delete_document(self, doc_id: str) -> None:
        if doc_id in self.fail_on:
            raise RuntimeError(f"échec simulé pour {doc_id}")
        self.delete_calls.append(doc_id)

    def set_sources(self, sources: list[dict]) -> None:
        self.sources_calls.append(sources)


@pytest.fixture
def fake_connector_cls():
    return FakeConnector


@pytest.fixture
def fake_client_cls():
    return FakeRagifixClient


@pytest.fixture
def state_store(tmp_path):
    store = SqliteStateStore(str(tmp_path / "state.db"))
    yield store
    store.close()


@pytest.fixture
def minimal_config_dict(tmp_path):
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    return {
        "ragifix": {
            "base_url": "http://127.0.0.1:8421",
            "api_token_env": "RAGIFIX_API_TOKEN",
        },
        "sync": {"interval_seconds": 0, "max_retries": 3},
        "sources": [
            {
                "name": "docs_internes",
                "type": "local_fs",
                "enabled": True,
                "paths": [str(docs_dir)],
                "extensions": {"method": "allow", "list": ["txt"]},
            }
        ],
        "state_store": {"backend": "sqlite", "sqlite": {"path": str(tmp_path / "state.db")}},
        "logging": {"level": "INFO"},
    }


@pytest.fixture
def write_config(tmp_path):
    def _write(data: dict) -> Path:
        path = tmp_path / "config.yaml"
        path.write_text(yaml.safe_dump(data), encoding="utf-8")
        return path

    return _write


@pytest.fixture
def ragifix_token_env(monkeypatch):
    monkeypatch.setenv("RAGIFIX_API_TOKEN", "test-token")
    return "test-token"
