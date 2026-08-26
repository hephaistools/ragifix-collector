"""Daemon ragifix-collector.

Boucle interne planifiant les cycles (sync.interval_seconds), mode
one-shot si interval_seconds=0, arrêt propre sur SIGTERM/SIGINT.
"""

from __future__ import annotations

import asyncio
import logging
import signal

from .api_client import RagifixClient
from .config import AppConfig, SourceConfig
from .connectors.registry import build_connector
from .state import build_state_store
from .syncer import Syncer

logger = logging.getLogger(__name__)


class CollectorDaemon:
    def __init__(self, config: AppConfig):
        self._config = config
        self._stop_event = asyncio.Event()

        self._client = RagifixClient(
            base_url=config.ragifix.base_url,
            token=config.ragifix.api_token,
            timeout=config.ragifix.timeout_seconds,
        )
        self._state_store = build_state_store(config.state_store.backend, config.state_store.sqlite.path)
        self._syncer = Syncer(self._client, self._state_store, max_retries=config.sync.max_retries)
        self._sources = [s for s in config.sources if s.enabled]

        if not self._sources:
            logger.warning("Aucune source activée dans la configuration (sources[].enabled)")

    def _install_signal_handlers(self) -> None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, self._stop_event.set)
            except NotImplementedError:
                pass

    async def _run_cycle(self) -> None:
        is_healthy = await asyncio.to_thread(self._client.health)
        if not is_healthy:
            logger.warning(
                "ragifix injoignable (%s) — cycle ignoré, nouvelle tentative au prochain run",
                self._config.ragifix.base_url,
            )
            return

        for source in self._sources:
            try:
                connector = build_connector(source)
            except Exception:
                logger.exception(
                    "Impossible d'initialiser la source '%s' — cycle ignoré pour cette source",
                    source.name,
                )
                continue

            try:
                await self._syncer.sync_source(source.name, connector)
            except Exception:
                logger.exception("Échec du cycle de synchronisation pour la source '%s'", source.name)

        # Mettre à jour la liste des sources dans ragifix
        await self._syncer.sync_all_sources(self._sources)

    async def run(self) -> None:
        self._install_signal_handlers()
        interval = self._config.sync.interval_seconds

        try:
            if interval <= 0:
                logger.info("Mode one-shot (sync.interval_seconds=0): exécution d'un seul cycle")
                await self._run_cycle()
                return

            logger.info("Démarrage du daemon ragifix-collector (intervalle: %ds)", interval)
            while not self._stop_event.is_set():
                await self._run_cycle()
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=interval)
                except asyncio.TimeoutError:
                    pass
            logger.info("Arrêt demandé (signal reçu), fin propre du daemon")
        finally:
            self._state_store.close()
            self._client.close()
