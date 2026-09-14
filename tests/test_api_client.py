"""Tests du client HTTP vers ragifix.

Aucun accès réseau : `RagifixClient` accepte un `transport` injectable
(`httpx.MockTransport`), ce qui permet de simuler précisément les réponses
de l'API ragifix.
"""

from __future__ import annotations

import json

import httpx
import pytest

from ragifix_collector.api_client import (
    DocumentTooLargeError,
    RagifixClient,
    UnsupportedFileTypeError,
    _encode_doc_id,
)


# -- _encode_doc_id -----------------------------------------------------------

def test_encode_doc_id_preserves_slashes():
    assert _encode_doc_id("source/a/b/c.txt") == "source/a/b/c.txt"


def test_encode_doc_id_encodes_spaces():
    assert _encode_doc_id("a b.txt") == "a%20b.txt"


def test_encode_doc_id_encodes_special_chars_but_not_slash():
    encoded = _encode_doc_id("src:déjà vu/été.txt")
    assert "/" in encoded
    assert " " not in encoded


# -- put_document --------------------------------------------------------------

def _client_with_handler(handler) -> RagifixClient:
    transport = httpx.MockTransport(handler)
    return RagifixClient("http://test", "test-token", transport=transport)


def test_put_document_success_sends_auth_and_body():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["path"] = request.url.path
        captured["auth"] = request.headers.get("authorization")
        captured["content_type"] = request.headers.get("content-type")
        captured["params"] = dict(request.url.params)
        captured["body"] = request.read()
        return httpx.Response(200, json={"doc_id": "doc1", "chunk_count": 2})

    client = _client_with_handler(handler)
    result = client.put_document("doc1", b"hello", "txt", {"source": "s1"})

    assert result == {"doc_id": "doc1", "chunk_count": 2}
    assert captured["method"] == "PUT"
    assert captured["path"] == "/documents/doc1"
    assert captured["auth"] == "Bearer test-token"
    assert captured["content_type"] == "application/octet-stream"
    assert "extension" not in captured["params"]
    assert json.loads(captured["params"]["metadata"]) == {"source": "s1", "extension": "txt"}
    assert captured["body"] == b"hello"


def test_put_document_merges_extension_into_metadata_when_empty():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["params"] = dict(request.url.params)
        return httpx.Response(200, json={})

    client = _client_with_handler(handler)
    client.put_document("doc1", b"x", "txt", {})
    assert json.loads(captured["params"]["metadata"]) == {"extension": "txt"}


def test_put_document_413_raises_document_too_large():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(413, json={"detail": "trop gros"})

    client = _client_with_handler(handler)
    with pytest.raises(DocumentTooLargeError, match="trop gros"):
        client.put_document("doc1", b"x", "txt", {})


def test_put_document_415_raises_unsupported_file_type():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(415, json={"detail": "type non supporté"})

    client = _client_with_handler(handler)
    with pytest.raises(UnsupportedFileTypeError, match="type non supporté"):
        client.put_document("doc1", b"x", "txt", {})


def test_put_document_other_error_raises_http_status_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"detail": "boom"})

    client = _client_with_handler(handler)
    with pytest.raises(httpx.HTTPStatusError):
        client.put_document("doc1", b"x", "txt", {})


# -- delete_document ------------------------------------------------------------

def test_delete_document_success():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "DELETE"
        return httpx.Response(204)

    client = _client_with_handler(handler)
    client.delete_document("doc1")  # ne doit pas lever


def test_delete_document_404_is_idempotent():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    client = _client_with_handler(handler)
    client.delete_document("doc1")  # ne doit pas lever


def test_delete_document_other_error_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    client = _client_with_handler(handler)
    with pytest.raises(httpx.HTTPStatusError):
        client.delete_document("doc1")


# -- list_documents ---------------------------------------------------------------

def test_list_documents_returns_documents():
    def handler(request: httpx.Request) -> httpx.Response:
        assert dict(request.url.params) == {}
        return httpx.Response(200, json={"documents": [{"doc_id": "a"}]})

    client = _client_with_handler(handler)
    assert client.list_documents() == [{"doc_id": "a"}]


# -- health -----------------------------------------------------------------------

def test_health_true_on_200():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "ok"})

    client = _client_with_handler(handler)
    assert client.health() is True


def test_health_false_on_non_200():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    client = _client_with_handler(handler)
    assert client.health() is False


def test_health_false_on_connection_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connexion refusée", request=request)

    client = _client_with_handler(handler)
    assert client.health() is False


# -- sources ---------------------------------------------------------------------

def test_set_sources_posts_json_body():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["body"] = json.loads(request.read())
        return httpx.Response(200)

    client = _client_with_handler(handler)
    client.set_sources([{"name": "s1", "description": "d", "enabled": True}])
    assert captured["method"] == "POST"
    assert captured["body"] == [{"name": "s1", "description": "d", "enabled": True}]


def test_get_sources_parses_response():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"sources": [{"name": "s1"}]})

    client = _client_with_handler(handler)
    assert client.get_sources() == [{"name": "s1"}]


def test_get_sources_raises_on_error_status():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    client = _client_with_handler(handler)
    with pytest.raises(httpx.HTTPStatusError):
        client.get_sources()


def test_close_does_not_raise():
    client = _client_with_handler(lambda r: httpx.Response(200))
    client.close()
