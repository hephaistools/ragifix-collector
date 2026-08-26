"""Modèle de configuration de ragifix-collector.

Fichier YAML unique, indépendant de celui de ragifix. ragifix-collector ne
connaît rien du chunking, de l'embedding ni de la base vectorielle : il ne
fait que détecter des changements de fichiers et les pousser vers l'API de
ragifix.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


class ConfigError(Exception):
    """Erreur de configuration (fichier invalide ou secret manquant)."""


# ------------------------------------------------------------------------- #
# Connexion à ragifix
# ------------------------------------------------------------------------- #

class RagifixConfig(BaseModel):
    base_url: str = "http://127.0.0.1:8421"
    api_token_env: str
    timeout_seconds: float = 60.0

    @property
    def api_token(self) -> str:
        value = os.environ.get(self.api_token_env)
        if not value:
            raise ConfigError(f"Variable d'environnement manquante: {self.api_token_env}")
        return value


# ------------------------------------------------------------------------- #
# Sources
# ------------------------------------------------------------------------- #

class ExtensionsFilter(BaseModel):
    # Le champ Python s'appelle `extensions_list` (pas `list`) : nommer un
    # champ Pydantic `list` entre en conflit avec le type builtin `list[str]`
    # utilisé dans les annotations de ce fichier. L'alias préserve la clé
    # YAML `list` demandée côté config.
    model_config = ConfigDict(populate_by_name=True)

    method: Literal["allow", "deny"] = "deny"
    extensions_list: list[str] = Field(default_factory=list, alias="list")

    def is_allowed(self, extension: str) -> bool:
        ext = extension.lower().lstrip(".")
        normalized = {e.lower().lstrip(".") for e in self.extensions_list}
        if self.method == "allow":
            return ext in normalized
        return ext not in normalized


class SourceConfig(BaseModel):
    """Configuration générique d'une source.

    Volontairement permissive (`extra="allow"`) : chaque type de connecteur
    a ses propres champs spécifiques (ex: `paths` pour local_fs, `tenant_id`
    pour sharepoint). ragifix-collector ne connaît que le socle commun ; le
    reste est transmis tel quel au connecteur concerné.
    """

    model_config = ConfigDict(extra="allow")

    name: str
    type: str
    enabled: bool = True
    description: str | None = None
    extensions: ExtensionsFilter = Field(default_factory=ExtensionsFilter)


# ------------------------------------------------------------------------- #
# Synchronisation
# ------------------------------------------------------------------------- #

class SyncConfig(BaseModel):
    interval_seconds: int = 300
    max_retries: int = 5


# ------------------------------------------------------------------------- #
# State store (cursors uniquement)
# ------------------------------------------------------------------------- #

class SqliteStateConfig(BaseModel):
    path: str = "/var/lib/ragifix-collector/state.db"


class StateStoreConfig(BaseModel):
    backend: Literal["sqlite"] = "sqlite"
    sqlite: SqliteStateConfig = Field(default_factory=SqliteStateConfig)


# ------------------------------------------------------------------------- #
# Logging
# ------------------------------------------------------------------------- #

class LoggingConfig(BaseModel):
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"


# ------------------------------------------------------------------------- #
# Racine
# ------------------------------------------------------------------------- #

class AppConfig(BaseModel):
    ragifix: RagifixConfig
    sync: SyncConfig = Field(default_factory=SyncConfig)
    sources: list[SourceConfig]
    state_store: StateStoreConfig = Field(default_factory=StateStoreConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)

    @model_validator(mode="after")
    def _check_unique_source_names(self) -> "AppConfig":
        names = [s.name for s in self.sources]
        if len(names) != len(set(names)):
            raise ValueError("Les noms de sources (sources[].name) doivent être uniques")
        return self


def load_config(path: str | Path) -> AppConfig:
    path = Path(path)
    if not path.is_file():
        raise ConfigError(f"Fichier de configuration introuvable: {path}")
    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    if raw is None:
        raise ConfigError(f"Fichier de configuration vide: {path}")
    try:
        return AppConfig.model_validate(raw)
    except Exception as exc:
        raise ConfigError(f"Configuration invalide ({path}): {exc}") from exc
