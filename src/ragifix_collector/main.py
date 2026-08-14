from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from .config import ConfigError, load_config
from .daemon import CollectorDaemon


def main() -> None:
    parser = argparse.ArgumentParser(description="ragifix-collector — ETL de sources vers ragifix")
    parser.add_argument("--config", required=True, help="Chemin vers le fichier config.yaml")
    args = parser.parse_args()

    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(f"Erreur de configuration: {exc}", file=sys.stderr)
        sys.exit(1)

    logging.basicConfig(
        level=config.logging.level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
    )

    try:
        daemon = CollectorDaemon(config)
    except Exception:
        logging.getLogger(__name__).exception("Échec de l'initialisation de ragifix-collector")
        sys.exit(1)

    asyncio.run(daemon.run())


if __name__ == "__main__":
    main()
