"""ragifix-collector — mini ETL qui alimente ragifix.

Détecte les changements sur des sources configurables (fichiers locaux,
SharePoint via l'extra [sharepoint], ou tout connecteur tiers via
entry_points) et les pousse vers l'API HTTP de ragifix. Ne fait
absolument aucun traitement de contenu (pas de parsing, pas de chunking,
pas d'embedding).
"""

__version__ = "0.1.0"
