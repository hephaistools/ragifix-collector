"""Tests de l'orchestrateur de synchronisation (`Syncer`)."""

from __future__ import annotations

import asyncio
import json

from ragifix_collector.connectors.base import Change, ChangeType
from ragifix_collector.syncer import Syncer, get_extension, namespaced_doc_id


# -- Helpers purs ---------------------------------------------------------------

def test_namespaced_doc_id():
    assert namespaced_doc_id("src1", "/a/b.txt") == "src1:/a/b.txt"


def test_get_extension():
    assert get_extension("/a/b/file.PDF") == "pdf"
    assert get_extension("/a/b/no_extension") == ""


# -- sync_source: application des changements ------------------------------------

def test_sync_source_created_change_puts_document(fake_connector_cls, fake_client_cls, state_store):
    change = Change(doc_id="a.txt", change_type=ChangeType.CREATED, extension="txt", metadata={"k": "v"})
    connector = fake_connector_cls(changes=[change], new_cursor="cursor-2")
    client = fake_client_cls()
    syncer = Syncer(client, state_store, max_retries=3)

    asyncio.run(syncer.sync_source("src1", connector))

    assert len(client.put_calls) == 1
    doc_id, content, extension, metadata = client.put_calls[0]
    assert doc_id == "src1:a.txt"
    assert content == b"contenu-par-defaut"
    assert extension == "txt"
    assert metadata == {"k": "v", "source": "src1"}
    assert state_store.get_cursor("src1") == "cursor-2"


def test_sync_source_deleted_change_deletes_document(fake_connector_cls, fake_client_cls, state_store):
    change = Change(doc_id="a.txt", change_type=ChangeType.DELETED)
    connector = fake_connector_cls(changes=[change])
    client = fake_client_cls()
    syncer = Syncer(client, state_store, max_retries=3)

    asyncio.run(syncer.sync_source("src1", connector))

    assert client.delete_calls == ["src1:a.txt"]
    assert client.put_calls == []


def test_sync_source_clears_success_state_after_apply(fake_connector_cls, fake_client_cls, state_store):
    state_store.record_failure("src1", "a.txt", "ancien échec", 1)
    change = Change(doc_id="a.txt", change_type=ChangeType.CREATED, extension="txt")
    connector = fake_connector_cls(changes=[change])
    client = fake_client_cls()
    syncer = Syncer(client, state_store, max_retries=3)

    asyncio.run(syncer.sync_source("src1", connector))

    assert state_store.get_failed("src1", max_retries=3) == []


# -- sync_source: échecs et retries ------------------------------------------------

def test_sync_source_failure_records_and_advances_cursor(fake_connector_cls, fake_client_cls, state_store):
    change = Change(doc_id="a.txt", change_type=ChangeType.CREATED, extension="txt")
    connector = fake_connector_cls(changes=[change], new_cursor="cursor-2")
    client = fake_client_cls(fail_on={"src1:a.txt"})
    syncer = Syncer(client, state_store, max_retries=3)

    asyncio.run(syncer.sync_source("src1", connector))

    # Le cursor avance même en cas d'échec.
    assert state_store.get_cursor("src1") == "cursor-2"
    failed = state_store.get_failed("src1", max_retries=3)
    assert failed == [("a.txt", 1, "échec simulé pour src1:a.txt", None, "txt", "{}")]


def test_sync_source_records_origin_from_metadata_on_failure(fake_connector_cls, fake_client_cls, state_store):
    change = Change(
        doc_id="a.txt",
        change_type=ChangeType.CREATED,
        extension="txt",
        metadata={"origin": {"kind": "file", "uri": "file:///a.txt", "label": "a.txt"}},
    )
    connector = fake_connector_cls(changes=[change])
    client = fake_client_cls(fail_on={"src1:a.txt"})
    syncer = Syncer(client, state_store, max_retries=3)

    asyncio.run(syncer.sync_source("src1", connector))

    failed = state_store.get_failed("src1", max_retries=3)
    assert len(failed) == 1
    origin_json = failed[0][3]
    assert json.loads(origin_json) == {"kind": "file", "uri": "file:///a.txt", "label": "a.txt"}


def test_sync_source_abandons_doc_after_max_retries(fake_connector_cls, fake_client_cls, state_store):
    # Aucun nouveau changement : seule la file de retentatives est exercée.
    connector = fake_connector_cls(changes=[])
    client = fake_client_cls(fail_on={"src1:a.txt"})
    syncer = Syncer(client, state_store, max_retries=2)

    # Simule un premier échec déjà enregistré (error_count=1) avant ce run.
    state_store.record_failure("src1", "a.txt", "premier échec", 1)

    asyncio.run(syncer.sync_source("src1", connector))

    # error_count atteint max_retries (2) : le document est exclu de get_failed.
    assert state_store.get_failed("src1", max_retries=2) == []
    assert state_store.get_failed("src1", max_retries=3) == [("a.txt", 2, "échec simulé pour src1:a.txt", None, "txt", '{"retry": true, "source": "src1"}')]


def test_sync_source_retries_failed_documents_before_new_changes(fake_connector_cls, fake_client_cls, state_store):
    state_store.record_failure("src1", "old.txt", "ancien échec", 1, origin='{"kind": "file"}')
    connector = fake_connector_cls(changes=[], contents={"old.txt": b"contenu-retente"})
    client = fake_client_cls()
    syncer = Syncer(client, state_store, max_retries=3)

    asyncio.run(syncer.sync_source("src1", connector))

    assert len(client.put_calls) == 1
    doc_id, content, extension, metadata = client.put_calls[0]
    assert doc_id == "src1:old.txt"
    assert content == b"contenu-retente"
    assert metadata["retry"] is True
    assert metadata["origin"] == {"kind": "file"}
    # Le retry réussi nettoie l'état d'échec.
    assert state_store.get_failed("src1", max_retries=3) == []


def test_sync_source_retry_failure_increments_error_count(fake_connector_cls, fake_client_cls, state_store):
    state_store.record_failure("src1", "old.txt", "ancien échec", 1)
    connector = fake_connector_cls(changes=[])
    client = fake_client_cls(fail_on={"src1:old.txt"})
    syncer = Syncer(client, state_store, max_retries=3)

    asyncio.run(syncer.sync_source("src1", connector))

    failed = state_store.get_failed("src1", max_retries=3)
    assert failed == [("old.txt", 2, "échec simulé pour src1:old.txt", None, "txt", '{"retry": true, "source": "src1"}')]


# -- retry : réutilisation de l'extension et des métadonnées enregistrées --------

def test_retry_reuses_stored_extension_for_uid(fake_connector_cls, fake_client_cls, state_store):
    """Un uid csv_events (pas de chemin) ne doit pas faire recalculer l'extension
    depuis le doc_id : l'extension enregistrée à l'échec est réutilisée telle quel."""
    state_store.record_failure(
        "src1",
        "99992315",
        "échec simulé",
        1,
        extension="md",
        metadata={"uid": 99992315, "city": "Marseille", "status": "confirmé"},
    )
    connector = fake_connector_cls(changes=[], contents={"99992315": b"contenu"})
    client = fake_client_cls()
    syncer = Syncer(client, state_store, max_retries=3)

    asyncio.run(syncer.sync_source("src1", connector))

    assert len(client.put_calls) == 1
    doc_id, content, extension, metadata = client.put_calls[0]
    assert doc_id == "src1:99992315"
    assert content == b"contenu"
    assert extension == "md"
    assert state_store.get_failed("src1", max_retries=3) == []


def test_retry_reuses_stored_metadata(fake_connector_cls, fake_client_cls, state_store):
    """Le retry restaute les métadonnées structurées enregistrées (ville, date, ...)."""
    state_store.record_failure(
        "src1",
        "99992315",
        "échec simulé",
        1,
        origin='{"kind": "file", "uri": "file:///x.csv", "label": "x.csv"}',
        extension="md",
        metadata={"uid": 99992315, "city": "Marseille", "status": "confirmé"},
    )
    connector = fake_connector_cls(changes=[], contents={"99992315": b"contenu"})
    client = fake_client_cls()
    syncer = Syncer(client, state_store, max_retries=3)

    asyncio.run(syncer.sync_source("src1", connector))

    _, _, _, metadata = client.put_calls[0]
    assert metadata["uid"] == 99992315
    assert metadata["city"] == "Marseille"
    assert metadata["status"] == "confirmé"
    assert metadata["retry"] is True
    assert metadata["origin"] == {"kind": "file", "uri": "file:///x.csv", "label": "x.csv"}


def test_error_count_accumulates_when_doc_in_queue_fails_again_same_run(fake_connector_cls, fake_client_cls, state_store):
    """Un document déjà en file d'attente qui rééchoue dans le même cycle ne doit
    pas faire lever d'erreur de décompression : l'échec précédent est conservé."""
    state_store.record_failure("src1", "a.txt", "précédent échec", 1, extension="txt", metadata={"k": "v"})
    change = Change(doc_id="a.txt", change_type=ChangeType.MODIFIED, extension="txt", metadata={"k": "v"})
    connector = fake_connector_cls(changes=[change])
    client = fake_client_cls(fail_on={"src1:a.txt"})
    syncer = Syncer(client, state_store, max_retries=5)

    asyncio.run(syncer.sync_source("src1", connector))

    failed = state_store.get_failed("src1", max_retries=5)
    assert len(failed) == 1
    doc_id, error_count, last_error, origin, extension, metadata = failed[0]
    assert doc_id == "a.txt"
    assert error_count == 3
    assert extension == "txt"
    assert json.loads(metadata) == {"k": "v"}


# -- sync_all_sources -----------------------------------------------------------

def test_sync_all_sources_builds_expected_payload(fake_client_cls, state_store):
    from ragifix_collector.config import SourceConfig

    sources = [
        SourceConfig.model_validate({"name": "s1", "type": "local_fs", "description": "Ma source", "enabled": True}),
        SourceConfig.model_validate({"name": "s2", "type": "sharepoint", "enabled": False}),
    ]
    client = fake_client_cls()
    syncer = Syncer(client, state_store, max_retries=3)

    asyncio.run(syncer.sync_all_sources(sources))

    assert client.sources_calls == [
        [
            {"name": "s1", "description": "Ma source", "enabled": True},
            {"name": "s2", "description": "Source sharepoint", "enabled": False},
        ]
    ]


def test_sync_all_sources_swallows_client_errors(fake_client_cls, state_store):
    from ragifix_collector.config import SourceConfig

    class FailingClient(fake_client_cls):
        def set_sources(self, sources):
            raise RuntimeError("ragifix injoignable")

    sources = [SourceConfig.model_validate({"name": "s1", "type": "local_fs"})]
    syncer = Syncer(FailingClient(), state_store, max_retries=3)

    asyncio.run(syncer.sync_all_sources(sources))  # ne doit pas lever
