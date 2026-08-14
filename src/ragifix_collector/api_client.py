"""Client HTTP vers ragifix.

Pousse le contenu en corps de requête brut (pas de multipart) — voir la
note de sécurité dans ragifix.routes sur les écritures temporaires disque
évitées par ce choix.
"""

from __future__ import annotations

import json
import logging
from urllib.parse import quote

import httpx

logger = logging.getLogger(__name__)


def _encode_doc_id(doc_id: str) -> str:
    """Percent-encode chaque segment du doc_id, en préservant les '/' comme
    séparateurs (le endpoint côté ragifix utilise un convertisseur de
    chemin Starlette `:path` qui attend et redécode exactement ce format)."""
    return quote(doc_id, safe="/")


class DocumentTooLargeError(Exception):
    pass


class UnsupportedFileTypeError(Exception):
    pass


class RagifixClient:
    def __init__(
        self,
        base_url: str,
        token: str,
        timeout: float = 60.0,
        transport: httpx.BaseTransport | None = None,
    ):
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {token}"},
            timeout=timeout,
            transport=transport,
        )

    def put_document(self, doc_id: str, content: bytes, extension: str, metadata: dict) -> dict:
        """Ajoute ou met à jour un document. Retourne le DocumentResponse JSON."""
        params = {"extension": extension}
        if metadata:
            params["metadata"] = json.dumps(metadata, ensure_ascii=False)

        response = self._client.put(
            f"/documents/{_encode_doc_id(doc_id)}",
            params=params,
            content=content,
            headers={"Content-Type": "application/octet-stream"},
        )
        if response.status_code == 413:
            raise DocumentTooLargeError(response.json().get("detail", "Document trop volumineux"))
        if response.status_code == 415:
            raise UnsupportedFileTypeError(response.json().get("detail", "Type de fichier non supporté"))
        response.raise_for_status()
        return response.json()

    def delete_document(self, doc_id: str) -> None:
        """Supprime un document. Idempotent : un 404 (déjà absent) n'est
        pas considéré comme une erreur."""
        response = self._client.delete(f"/documents/{_encode_doc_id(doc_id)}")
        if response.status_code == 404:
            return
        response.raise_for_status()

    def list_documents(self, prefix: str | None = None) -> list[dict]:
        params = {"prefix": prefix} if prefix else None
        response = self._client.get("/documents", params=params)
        response.raise_for_status()
        return response.json()["documents"]

    def health(self) -> bool:
        try:
            response = self._client.get("/health")
            return response.status_code == 200
        except httpx.HTTPError:
            return False

    def close(self) -> None:
        self._client.close()
