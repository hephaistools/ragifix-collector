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
import logging

from .api_client import RagifixClient
from .connectors.base import Change, ChangeType, Connector
from .state import StateStore

logger = logging.getLogger(__name__)


def namespaced_doc_id(source_name: str, doc_id: str) -> str:
    return f"{source_name}:{doc_id}"


class Syncer:
    def __init__(self, client: RagifixClient, state_store: StateStore):
        self._client = client
        self._state = state_store

    async def sync_source(self, source_name: str, connector: Connector) -> None:
        """Exécute un cycle de synchronisation complet pour une source.

        En cas d'échec sur un document individuel : log et abandon pour ce
        document (il sera retenté au run suivant), sans bloquer le
        traitement des autres documents. Le cursor est avancé en fin de run
        même en cas d'échecs partiels.
        """
        cursor = self._state.get_cursor(source_name)
        changes, new_cursor = await connector.list_changes(cursor)
        logger.info("Source '%s': %d changement(s) détecté(s)", source_name, len(changes))

        failures = 0
        for change in changes:
            try:
                await self._apply_change(source_name, connector, change)
            except Exception:
                failures += 1
                logger.exception(
                    "Échec de la synchronisation du document '%s' (source '%s') — "
                    "sera retenté au prochain run",
                    change.doc_id,
                    source_name,
                )

        self._state.set_cursor(source_name, new_cursor)

        if failures:
            logger.warning(
                "Source '%s': run terminé avec %d échec(s) sur %d changement(s)",
                source_name,
                failures,
                len(changes),
            )
        else:
            logger.info("Source '%s': run terminé sans erreur", source_name)

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
