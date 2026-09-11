"""Orchestrateur de synchronisation.

Pour chaque source : récupère les changements via son connecteur, puis les
pousse vers l'API de ragifix (aucun traitement de contenu ici — ni
parsing, ni chunking, ni embedding : ragifix-collector ne fait que relayer
des octets et des suppressions).

Le doc_id envoyé à l'API est namespacé par le nom de la source
("{source_name}:{doc_id_interne}"), pour garantir l'unicité globale côté
ragifix même si plusieurs sources (ou plusieurs instances de
ragifix-collector) alimentent le même service.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from .api_client import RagifixClient
from .config import SourceConfig
from .connectors.base import Change, ChangeType, Connector
from .state import StateStore

logger = logging.getLogger(__name__)


def namespaced_doc_id(source_name: str, doc_id: str) -> str:
    return f"{source_name}:{doc_id}"


def get_extension(doc_id: str) -> str:
    """Extrait l'extension du doc_id (ex: '/path/to/file.pdf' -> 'pdf')."""
    return Path(doc_id).suffix.lstrip(".").lower()


class Syncer:
    def __init__(self, client: RagifixClient, state_store: StateStore, max_retries: int = 5):
        self._client = client
        self._state = state_store
        self._max_retries = max_retries

    async def sync_source(self, source_name: str, connector: Connector) -> None:
        """Exécute un cycle de synchronisation complet pour une source.

        À chaque run :
        1. Retente les documents échoués des runs précédents (si error_count < max_retries)
        2. Traite les nouveaux changements depuis le cursor
        3. Avance le cursor même en cas d'échecs (il marque la position dans le flux)
        4. Si un document atteint max_retries, on l'abandonne (suppression de la table)
        """
        cursor = self._state.get_cursor(source_name)

        # 1. Retente les documents échoués avant de traiter les nouveaux
        failed_docs = self._state.get_failed(source_name, self._max_retries)
        if failed_docs:
            logger.info(
                "Source '%s': %d document(s) en attente de retentative",
                source_name,
                len(failed_docs),
            )
            for doc_id, error_count, last_error, origin, extension, metadata in failed_docs:
                await self._retry_document(source_name, connector, doc_id, error_count, origin, extension, metadata)

        # 2. Traite les nouveaux changements
        changes, new_cursor = await connector.list_changes(cursor)
        logger.info("Source '%s': %d changement(s) détecté(s)", source_name, len(changes))

        success_count = 0
        failure_count = 0

        for change in changes:
            try:
                await self._apply_change(source_name, connector, change)
                self._state.clear_success(source_name, change.doc_id)
                success_count += 1
                await asyncio.sleep(3)
            except Exception as exc:
                failure_count += 1
                current = self._state.get_failed(source_name, self._max_retries + 1)
                current_count = next((c for d, c, *_ in current if d == change.doc_id), 0)
                new_error_count = current_count + 1
                origin_json = json.dumps(change.metadata["origin"]) if change.metadata.get("origin") else None
                self._state.record_failure(
                    source_name, change.doc_id, str(exc), new_error_count, origin_json, change.extension, change.metadata
                )
                if new_error_count >= self._max_retries:
                    logger.error(
                        "Source '%s': abandon après %d échec(s) pour '%s'",
                        source_name,
                        new_error_count,
                        change.doc_id,
                    )
                else:
                    logger.exception(
                        "Source '%s': échec pour '%s' (%d/%d) — sera retenté",
                        source_name,
                        change.doc_id,
                        new_error_count,
                        self._max_retries,
                    )

        # 3. Avance le cursor même en cas d'échecs
        self._state.set_cursor(source_name, new_cursor)
        logger.info(
            "Source '%s': run terminé (%d succès, %d échec(s))",
            source_name,
            success_count,
            failure_count,
        )

    async def sync_all_sources(self, sources: list[SourceConfig]) -> None:
        """Met à jour la liste des sources dans ragifix."""
        source_list = []
        for source in sources:
            description = source.description or f"Source {source.type}"
            source_list.append({"name": source.name, "description": description, "enabled": source.enabled})
        try:
            await asyncio.to_thread(self._client.set_sources, source_list)
            logger.debug("Sources mises à jour dans ragifix (%d source(s))", len(source_list))
        except Exception:
            logger.exception("Échec de la mise à jour des sources dans ragifix")

    async def _retry_document(
        self,
        source_name: str,
        connector: Connector,
        doc_id: str,
        error_count: int,
        origin: str | None,
        extension: str | None,
        metadata: str | None,
    ) -> None:
        """Retente l'envoi d'un document échoué.

        `origin`, `extension` et `metadata` (JSON sérialisé) sont lus tels quels
        depuis `failed_documents` : on réutilise EXACTEMENT l'extension et les
        métadonnées de la première tentative, afin qu'un retry renvoie les mêmes
        données qu'au premier appel. Pour les bases créées avant cette
        correction, ces valeurs peuvent être vaines : on retombe alors sur le
        recalcul de l'extension depuis le doc_id (valide pour local_fs, jamais
        pour un connecteur dont le doc_id n'est pas un chemin, ex. csv_events /
        sharepoint).
        """
        api_doc_id = namespaced_doc_id(source_name, doc_id)

        if not extension:
            extension = get_extension(doc_id)
        reconstructed = json.loads(metadata) if metadata else {}
        reconstructed = {**reconstructed, "retry": True, "source": source_name}
        if origin:
            reconstructed["origin"] = json.loads(origin)

        try:
            content = bytearray()
            async for piece in connector.get_content(doc_id):
                content.extend(piece)
            await asyncio.to_thread(
                self._client.put_document,
                api_doc_id,
                bytes(content),
                extension,
                reconstructed,
            )
            self._state.clear_success(source_name, doc_id)
            logger.info(
                "Source '%s': retentative réussie pour '%s'",
                source_name,
                doc_id,
            )
        except Exception as exc:
            new_error_count = error_count + 1
            self._state.record_failure(
                source_name, doc_id, str(exc), new_error_count, origin, extension, reconstructed
            )
            if new_error_count >= self._max_retries:
                logger.error(
                    "Source '%s': abandon après %d échec(s) pour '%s'",
                    source_name,
                    new_error_count,
                    doc_id,
                )
            else:
                logger.exception(
                    "Source '%s': retentative échouée pour '%s' (%d/%d) — %s",
                    source_name,
                    doc_id,
                    new_error_count,
                    self._max_retries,
                    exc,
                )

    async def _apply_change(self, source_name: str, connector: Connector, change: Change) -> None:
        api_doc_id = namespaced_doc_id(source_name, change.doc_id)

        if change.change_type == ChangeType.DELETED:
            await asyncio.to_thread(self._client.delete_document, api_doc_id)
            return

        content = bytearray()
        async for piece in connector.get_content(change.doc_id):
            content.extend(piece)

        await asyncio.to_thread(
            self._client.put_document,
            api_doc_id,
            bytes(content),
            change.extension,
            {**change.metadata, "source": source_name},
        )
