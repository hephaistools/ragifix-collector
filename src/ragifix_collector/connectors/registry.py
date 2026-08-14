"""Registre des connecteurs de sources.

local_fs est natif (aucune dépendance supplémentaire). sharepoint est
livré dans le paquet mais nécessite l'extra `pip install
ragifix-collector[sharepoint]` pour fonctionner (message d'erreur clair
sinon).

Un connecteur totalement tiers peut s'ajouter SANS modifier
ragifix-collector, via le mécanisme standard des entry points Python : il
déclare dans son propre pyproject.toml un point d'entrée dans le groupe
"ragifix_collector.connectors", et ragifix-collector le découvre
automatiquement au démarrage.
"""

from __future__ import annotations

import os
from importlib.metadata import entry_points
from typing import Callable

from ragifix_collector.config import ConfigError, SourceConfig

from .base import Connector
from .local_fs import LocalFsConnector

ENTRY_POINT_GROUP = "ragifix_collector.connectors"

_NATIVE_BUILDERS: dict[str, Callable[[SourceConfig], Connector]] = {}


def _register(type_name: str):
    def decorator(builder: Callable[[SourceConfig], Connector]):
        _NATIVE_BUILDERS[type_name] = builder
        return builder

    return decorator


@_register("local_fs")
def _build_local_fs(source: SourceConfig) -> Connector:
    paths = getattr(source, "paths", None)
    if not paths:
        raise ConfigError(f"Source '{source.name}' (local_fs): champ 'paths' manquant ou vide")
    return LocalFsConnector(paths=paths, is_allowed_extension=source.extensions.is_allowed)


@_register("sharepoint")
def _build_sharepoint(source: SourceConfig) -> Connector:
    required = ["tenant_id", "client_id", "client_secret_env", "site_url", "folder_path"]
    missing = [f for f in required if not getattr(source, f, None)]
    if missing:
        raise ConfigError(f"Source '{source.name}' (sharepoint): champ(s) manquant(s): {missing}")

    secret_env = source.client_secret_env
    secret = os.environ.get(secret_env)
    if not secret:
        raise ConfigError(f"Source '{source.name}': variable d'environnement manquante: {secret_env}")

    # L'instanciation (pas seulement l'import) doit être couverte par le
    # try/except : SharePointConnector.__init__ lève ImportError si l'extra
    # 'sharepoint' n'est pas installé (msal manquant).
    try:
        from .sharepoint import SharePointConnector

        return SharePointConnector(
            tenant_id=source.tenant_id,
            client_id=source.client_id,
            client_secret=secret,
            site_url=source.site_url,
            folder_path=source.folder_path,
            is_allowed_extension=source.extensions.is_allowed,
        )
    except ImportError as exc:
        raise ConfigError(f"Source '{source.name}' (sharepoint): {exc}") from exc


def _discover_plugins() -> dict[str, Callable]:
    return {ep.name: ep.load() for ep in entry_points(group=ENTRY_POINT_GROUP)}


def build_connector(source: SourceConfig) -> Connector:
    if source.type in _NATIVE_BUILDERS:
        return _NATIVE_BUILDERS[source.type](source)

    plugins = _discover_plugins()
    if source.type in plugins:
        factory = plugins[source.type]
        return factory(source)

    raise ConfigError(
        f"Type de connecteur inconnu: '{source.type}'. "
        f"Types natifs disponibles: {sorted(_NATIVE_BUILDERS)}. "
        f"Plugins installés: {sorted(plugins)}."
    )
