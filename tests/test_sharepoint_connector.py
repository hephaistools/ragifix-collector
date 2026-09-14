"""Tests du connecteur SharePoint (Microsoft Graph).

Nécessite l'extra `sharepoint` (msal) — voir `pyproject.toml`
`[project.optional-dependencies]`. Ignoré automatiquement si msal n'est
pas installé (`importorskip`). Aucun accès réseau réel : `httpx.AsyncClient`
et `msal.ConfidentialClientApplication` sont mockés.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

msal = pytest.importorskip("msal")

from ragifix_collector.connectors import sharepoint as sharepoint_module  # noqa: E402
from ragifix_collector.connectors.base import ChangeType  # noqa: E402
from ragifix_collector.connectors.sharepoint import SharePointConnector  # noqa: E402

pytestmark = pytest.mark.heavy


def _connector(**overrides) -> SharePointConnector:
    kwargs = {
        "tenant_id": "t",
        "client_id": "c",
        "client_secret": "secret",
        "site_url": "https://contoso.sharepoint.com/sites/X",
        "folder_path": "/Documents/RAG",
        "is_allowed_extension": lambda ext: ext not in {"zip"},
    }
    kwargs.update(overrides)
    return SharePointConnector(**kwargs)


def test_init_raises_import_error_without_msal(monkeypatch):
    import sys

    monkeypatch.setitem(sys.modules, "msal", None)
    with pytest.raises(ImportError):
        _connector()


class _FakeMsalApp:
    def __init__(self, token="tok-123", expires_in=3600, error=None):
        self._token = token
        self._expires_in = expires_in
        self._error = error
        self.calls = 0

    def acquire_token_for_client(self, scopes):
        self.calls += 1
        if self._error:
            return {"error": "invalid_client", "error_description": self._error}
        return {"access_token": self._token, "expires_in": self._expires_in}


def test_ensure_auth_sets_token(monkeypatch):
    fake_app = _FakeMsalApp()
    monkeypatch.setattr(msal, "ConfidentialClientApplication", lambda **kw: fake_app)

    connector = _connector()
    asyncio.run(connector._ensure_auth())
    assert connector._token == "tok-123"
    assert fake_app.calls == 1


def test_ensure_auth_reuses_cached_token(monkeypatch):
    fake_app = _FakeMsalApp()
    monkeypatch.setattr(msal, "ConfidentialClientApplication", lambda **kw: fake_app)

    connector = _connector()
    asyncio.run(connector._ensure_auth())
    asyncio.run(connector._ensure_auth())
    assert fake_app.calls == 1  # pas de second appel : token encore valide


def test_ensure_auth_raises_on_auth_failure(monkeypatch):
    fake_app = _FakeMsalApp(error="identifiants invalides")
    monkeypatch.setattr(msal, "ConfidentialClientApplication", lambda **kw: fake_app)

    connector = _connector()
    with pytest.raises(RuntimeError, match="identifiants invalides"):
        asyncio.run(connector._ensure_auth())


# -- _to_change -----------------------------------------------------------------

def test_to_change_skips_folders():
    connector = _connector()
    assert connector._to_change({"id": "1", "folder": {}}) is None


def test_to_change_deleted_item():
    connector = _connector()
    change = connector._to_change({"id": "1", "deleted": {"state": "deleted"}})
    assert change.change_type == ChangeType.DELETED
    assert change.doc_id == "1"


def test_to_change_filters_disallowed_extension():
    connector = _connector()
    assert connector._to_change({"id": "1", "name": "archive.zip"}) is None


def test_to_change_returns_modified_with_metadata():
    connector = _connector()
    change = connector._to_change(
        {
            "id": "1",
            "name": "doc.txt",
            "webUrl": "https://contoso/doc.txt",
            "lastModifiedDateTime": "2026-01-01T00:00:00Z",
        }
    )
    assert change.change_type == ChangeType.MODIFIED
    assert change.extension == "txt"
    assert change.metadata == {
        "collector_type": "sharepoint",
        "filename": "doc.txt",
        "uri": "https://contoso/doc.txt",
        "path": "/Documents/RAG/doc.txt",
        "modified_at": "2026-01-01T00:00:00Z",
    }


def test_to_change_uses_parent_reference_path_when_present():
    connector = _connector()
    change = connector._to_change(
        {
            "id": "1",
            "name": "doc.txt",
            "webUrl": "https://contoso/doc.txt",
            "parentReference": {"path": "/drive/root:/Documents/RAG/Sous-dossier"},
        }
    )
    assert change.metadata["path"] == "/drive/root:/Documents/RAG/Sous-dossier/doc.txt"


# -- list_changes / get_content (httpx.AsyncClient mocké) -----------------------

class _FakeResponse:
    def __init__(self, payload=None, chunks=None, status=200):
        self._payload = payload
        self._chunks = chunks or []
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("erreur", request=None, response=None)

    def json(self):
        return self._payload

    async def aiter_bytes(self, chunk_size):
        for chunk in self._chunks:
            yield chunk


class _FakeStreamCtx:
    def __init__(self, response):
        self._response = response

    async def __aenter__(self):
        return self._response

    async def __aexit__(self, *exc):
        return False


class _FakeAsyncClient:
    def __init__(self, get_responses, stream_response=None, **kwargs):
        self._get_responses = get_responses
        self._stream_response = stream_response

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, headers=None):
        return self._get_responses[url]

    def stream(self, method, url, headers=None):
        return _FakeStreamCtx(self._stream_response)


def _patch_auth(monkeypatch, connector: SharePointConnector):
    connector._token = "tok"
    connector._token_expires_at = 1e18  # jamais expiré


def test_list_changes_single_page(monkeypatch):
    connector = _connector()
    _patch_auth(monkeypatch, connector)
    connector._drive_id = "drive1"
    connector._folder_item_id = "folder1"

    delta_url = "https://graph.microsoft.com/v1.0/drives/drive1/items/folder1/delta"
    responses = {
        delta_url: _FakeResponse(
            payload={
                "value": [{"id": "1", "name": "a.txt"}],
                "@odata.deltaLink": "https://graph/delta-link-2",
            }
        )
    }

    monkeypatch.setattr(
        sharepoint_module.httpx, "AsyncClient", lambda **kw: _FakeAsyncClient(responses)
    )

    changes, new_cursor = asyncio.run(connector.list_changes(None))
    assert len(changes) == 1
    assert changes[0].doc_id == "1"
    assert new_cursor == "https://graph/delta-link-2"


def test_list_changes_paginates_via_next_link(monkeypatch):
    connector = _connector()
    _patch_auth(monkeypatch, connector)
    connector._drive_id = "drive1"
    connector._folder_item_id = "folder1"

    page1_url = "https://graph.microsoft.com/v1.0/drives/drive1/items/folder1/delta"
    page2_url = "https://graph/page-2"
    responses = {
        page1_url: _FakeResponse(
            payload={"value": [{"id": "1", "name": "a.txt"}], "@odata.nextLink": page2_url}
        ),
        page2_url: _FakeResponse(
            payload={"value": [{"id": "2", "name": "b.txt"}], "@odata.deltaLink": "https://graph/delta-3"}
        ),
    }

    monkeypatch.setattr(
        sharepoint_module.httpx, "AsyncClient", lambda **kw: _FakeAsyncClient(responses)
    )

    changes, new_cursor = asyncio.run(connector.list_changes(None))
    assert {c.doc_id for c in changes} == {"1", "2"}
    assert new_cursor == "https://graph/delta-3"


def test_resolve_site_and_folder_populates_ids(monkeypatch):
    connector = _connector()
    _patch_auth(monkeypatch, connector)

    responses = {
        "https://graph.microsoft.com/v1.0/sites/contoso.sharepoint.com:/sites/X": _FakeResponse(
            payload={"id": "site-1"}
        ),
        "https://graph.microsoft.com/v1.0/sites/site-1/drive": _FakeResponse(payload={"id": "drive-1"}),
        "https://graph.microsoft.com/v1.0/drives/drive-1/root:/Documents/RAG": _FakeResponse(
            payload={"id": "folder-1"}
        ),
    }
    client = _FakeAsyncClient(responses)

    asyncio.run(connector._resolve_site_and_folder(client))

    assert connector._drive_id == "drive-1"
    assert connector._folder_item_id == "folder-1"


def test_resolve_site_and_folder_is_noop_if_already_resolved(monkeypatch):
    connector = _connector()
    connector._folder_item_id = "already-set"
    client = _FakeAsyncClient({})  # aucune réponse configurée : une requête ferait échouer le test

    asyncio.run(connector._resolve_site_and_folder(client))  # ne doit faire aucun appel HTTP

    assert connector._folder_item_id == "already-set"


def test_get_content_streams_bytes(monkeypatch):
    connector = _connector()
    _patch_auth(monkeypatch, connector)

    stream_response = _FakeResponse(chunks=[b"chunk1", b"chunk2"])
    monkeypatch.setattr(
        sharepoint_module.httpx,
        "AsyncClient",
        lambda **kw: _FakeAsyncClient({}, stream_response=stream_response),
    )

    async def _read():
        pieces = []
        async for piece in connector.get_content("item-1"):
            pieces.append(piece)
        return b"".join(pieces)

    assert asyncio.run(_read()) == b"chunk1chunk2"
