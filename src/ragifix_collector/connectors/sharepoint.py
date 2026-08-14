"""Connecteur SharePoint (Microsoft Graph API).

Extra optionnel : ce module fait partie du paquet ragifix-collector, mais
sa dépendance (msal) n'est installée que via `pip install
ragifix-collector[sharepoint]`. Sans cet extra, l'import de msal échoue
avec un message clair plutôt qu'une trace confuse.

Traitement intégralement en flux/mémoire : aucune écriture de fichier
temporaire sur disque, à aucune étape (du téléchargement du contenu
jusqu'à sa transmission à l'API de ragifix).

Synchronisation incrémentale via l'endpoint /delta de Microsoft Graph : le
cursor est directement l'URL @odata.deltaLink renvoyée par Graph.
"""

from __future__ import annotations

import logging
import time
from typing import AsyncIterator, Callable
from urllib.parse import urlparse

import httpx

from .base import Change, ChangeType

logger = logging.getLogger(__name__)

GRAPH_BASE = "https://graph.microsoft.com/v1.0"


class SharePointConnector:
    def __init__(
        self,
        tenant_id: str,
        client_id: str,
        client_secret: str,
        site_url: str,
        folder_path: str,
        is_allowed_extension: Callable[[str], bool],
    ):
        try:
            import msal  # noqa: F401
        except ImportError as exc:
            raise ImportError(
                "Le connecteur sharepoint nécessite l'extra 'sharepoint' : "
                "installez-le avec `pip install ragifix-collector[sharepoint]`"
            ) from exc

        self._tenant_id = tenant_id
        self._client_id = client_id
        self._client_secret = client_secret
        self._site_url = site_url
        self._folder_path = folder_path
        self._is_allowed_extension = is_allowed_extension

        self._token: str | None = None
        self._token_expires_at: float = 0.0
        self._drive_id: str | None = None
        self._folder_item_id: str | None = None

    # -- Authentification ---------------------------------------------------

    async def _ensure_auth(self) -> None:
        if self._token and time.time() < self._token_expires_at - 60:
            return
        import msal

        authority = f"https://login.microsoftonline.com/{self._tenant_id}"
        app = msal.ConfidentialClientApplication(
            client_id=self._client_id, client_credential=self._client_secret, authority=authority
        )
        result = app.acquire_token_for_client(scopes=["https://graph.microsoft.com/.default"])
        if "access_token" not in result:
            raise RuntimeError(
                f"Échec de l'authentification SharePoint/Entra ID: "
                f"{result.get('error_description', result.get('error'))}"
            )
        self._token = result["access_token"]
        self._token_expires_at = time.time() + float(result.get("expires_in", 3600))

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self._token}"}

    # -- Résolution du site / dossier ---------------------------------------

    async def _resolve_site_and_folder(self, client: httpx.AsyncClient) -> None:
        if self._folder_item_id:
            return

        parsed = urlparse(self._site_url)
        hostname = parsed.netloc
        site_path = parsed.path or "/"

        resp = await client.get(f"{GRAPH_BASE}/sites/{hostname}:{site_path}", headers=self._headers())
        resp.raise_for_status()
        site_id = resp.json()["id"]

        resp = await client.get(f"{GRAPH_BASE}/sites/{site_id}/drive", headers=self._headers())
        resp.raise_for_status()
        self._drive_id = resp.json()["id"]

        folder_rel = self._folder_path.strip("/")
        resp = await client.get(
            f"{GRAPH_BASE}/drives/{self._drive_id}/root:/{folder_rel}", headers=self._headers()
        )
        resp.raise_for_status()
        self._folder_item_id = resp.json()["id"]
        logger.info(
            "SharePoint: dossier '%s' résolu vers item-id '%s'", self._folder_path, self._folder_item_id
        )

    # -- Interface Connector --------------------------------------------------

    async def list_changes(self, cursor: str | None) -> tuple[list[Change], str]:
        await self._ensure_auth()
        async with httpx.AsyncClient(timeout=60.0) as client:
            await self._resolve_site_and_folder(client)

            url = cursor or f"{GRAPH_BASE}/drives/{self._drive_id}/items/{self._folder_item_id}/delta"

            changes: list[Change] = []
            new_cursor = cursor or ""

            while url:
                resp = await client.get(url, headers=self._headers())
                resp.raise_for_status()
                payload = resp.json()

                for item in payload.get("value", []):
                    change = self._to_change(item)
                    if change is not None:
                        changes.append(change)

                if "@odata.nextLink" in payload:
                    url = payload["@odata.nextLink"]
                elif "@odata.deltaLink" in payload:
                    new_cursor = payload["@odata.deltaLink"]
                    url = None
                else:
                    url = None

            return changes, new_cursor

    def _to_change(self, item: dict) -> Change | None:
        if "folder" in item:
            return None

        doc_id = item["id"]

        if item.get("deleted"):
            return Change(doc_id=doc_id, change_type=ChangeType.DELETED)

        name = item.get("name", "")
        extension = name.rsplit(".", 1)[-1].lower() if "." in name else ""
        if not self._is_allowed_extension(extension):
            return None

        return Change(
            doc_id=doc_id,
            change_type=ChangeType.MODIFIED,
            extension=extension,
            metadata={"name": name, "web_url": item.get("webUrl", "")},
        )

    async def get_content(self, doc_id: str) -> AsyncIterator[bytes]:
        await self._ensure_auth()
        url = f"{GRAPH_BASE}/drives/{self._drive_id}/items/{doc_id}/content"
        async with httpx.AsyncClient(timeout=120.0) as client:
            async with client.stream("GET", url, headers=self._headers()) as response:
                response.raise_for_status()
                async for piece in response.aiter_bytes(65536):
                    yield piece
