"""Contrat commun à tous les connecteurs de sources.

Un connecteur ne fait QUE produire un flux de documents créés / modifiés /
supprimés, et fournir leur contenu en flux d'octets. Il ne parse rien, ne
chunk rien, n'embedde rien — ragifix-collector se contente de relayer ce
contenu brut vers l'API de ragifix, qui s'occupe de tout le reste.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import AsyncIterator, Protocol, runtime_checkable


class ChangeType(str, Enum):
    CREATED = "created"
    MODIFIED = "modified"
    DELETED = "deleted"


@dataclass(frozen=True)
class Change:
    """doc_id : identifiant stable et unique DANS SA SOURCE (pas encore
    namespacé — c'est ragifix_collector.syncer qui préfixe par le nom de la
    source avant l'appel à l'API, pour garantir l'unicité globale côté
    ragifix)."""

    doc_id: str
    change_type: ChangeType
    extension: str = ""
    metadata: dict = field(default_factory=dict)


@runtime_checkable
class Connector(Protocol):
    async def list_changes(self, cursor: str | None) -> tuple[list[Change], str]:
        """Retourne les changements depuis `cursor`, et le nouveau cursor.

        `cursor` est une chaîne opaque, sérialisable, entièrement définie et
        interprétée par le connecteur lui-même.
        """
        ...

    async def get_content(self, doc_id: str) -> AsyncIterator[bytes]:
        """Flux d'octets du contenu du document."""
        ...
