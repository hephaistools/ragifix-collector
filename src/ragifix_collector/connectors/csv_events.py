"""Connecteur CSV d'événements (events_propres.csv).

Lit un fichier de la forme ``uid,title,description,long_description,status,
date,city,department,region,address`` et pousse un fragment Markdown par
événement vers ragifix, avec ses métadonnées structurées (voir
``datas/etude_decoupage.md``).

Contrairement aux connecteurs natifs, celui-ci applique une transformation de
contenu minimale et ciblée : ragifix ne sait pas parser un CSV, et le format
reçu (Markdown + métadonnées) est imposé par la spécification d'ingestion. Le
connecteur ne fait qu'un mini-parsing (détecteur de changements + construction
du fragment) ; ni chunking, ni embedding, ni aucun traitement supplémentaire.

Synchronisation one-shot : le cursor encode la ``mtime`` du fichier — si le
fichier n'a pas changé, aucun changement n'est émis.
"""

from __future__ import annotations

import csv
import html
import json
import re
from pathlib import Path
from typing import AsyncIterator

from .base import Change, ChangeType

# Titres de section du fragment, repris à l'identique de l'etude d'ingestion.
_HDR_TITRE = "## Titre de l'\u00e9v\u00e8ne"
_HDR_DATE = "## Date de l'\u00e9v\u00e8ne"
_HDR_ADRESSE = "## Adresse de l'\u00e9v\u00e8ne"
_HDR_DESCRIP = "## Description de l'\u00e9v\u00e8ne"

_MTIME_KEY = "mtime"
_UID_KEY = "uid"
_DATE_KEY = "date"
_CITY_KEY = "city"
_DEPARTMENT_KEY = "department"
_REGION_KEY = "region"
_STATUS_KEY = "status"

_EXTENSION = "md"


def strip_html(text: str) -> str:
    """Retire les balises HTML d'un texte en conservant la structure en lignes.

    Les sauts de ligne issues de ``<br>`` sont conservés (ils portent la
    structure du corps dans la source), le reste des balises est supprimé et
    les entités sont désencodées.
    """
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    text = text.replace("\xa0", " ")
    return text.strip()


class CsvEventsConnector:
    def __init__(self, path: str):
        self._path = Path(path).expanduser().resolve()
        # Cache uid -> ligne, rempli à la première lecture (list_changes, ou
        # get_content en dépannage). Il traverse list_changes et get_content
        # au sein d'un même cycle de synchronisation.
        self._rows: dict[str, dict] | None = None

    async def list_changes(self, cursor: str | None) -> tuple[list[Change], str]:
        try:
            mtime = self._path.stat().st_mtime
        except OSError:
            return [], cursor or ""

        stored = json.loads(cursor) if cursor else {}
        if float(stored.get(_MTIME_KEY, 0.0)) >= mtime:
            return [], cursor or json.dumps({_MTIME_KEY: mtime})

        self._rows = self._parse()
        change_type = ChangeType.CREATED if not cursor else ChangeType.MODIFIED
        changes = [
            Change(
                doc_id=uid,
                change_type=change_type,
                extension=_EXTENSION,
                metadata=self._metadata(row),
            )
            for uid, row in self._rows.items()
        ]
        return changes, json.dumps({_MTIME_KEY: mtime})

    async def get_content(self, doc_id: str) -> AsyncIterator[bytes]:
        if self._rows is None:
            self._rows = self._parse()
        row = self._rows.get(doc_id)
        if row is None:
            raise KeyError(f"Aucun événement avec uid '{doc_id}' dans {self._path}")
        yield self._build_fragment(row).encode("utf-8")

    def _parse(self) -> dict[str, dict]:
        rows: dict[str, dict] = {}
        with open(self._path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                uid = (row.get(_UID_KEY) or "").strip()
                if not uid:
                    continue
                rows[uid] = row
        return rows

    def _build_fragment(self, row: dict) -> str:
        sections: list[str] = []

        title = (row.get("title") or "").strip()
        if title:
            sections.append(f"{_HDR_TITRE}\n{title}")

        date = (row.get(_DATE_KEY) or "").strip()
        if date:
            sections.append(f"{_HDR_DATE}\n{date}")

        address = (row.get("address") or "").strip()
        if address:
            sections.append(f"{_HDR_ADRESSE}\n{address}")

        description = self._description(row)
        if description:
            sections.append(f"{_HDR_DESCRIP}\n{description}")

        return "\n\n".join(sections)

    def _description(self, row: dict) -> str:
        text = row.get("long_description") or row.get("description") or ""
        text = text.strip()
        if not text:
            return ""
        return strip_html(text)

    def _metadata(self, row: dict) -> dict:
        meta: dict = {}
        uid = (row.get(_UID_KEY) or "").strip()
        if uid:
            try:
                meta[_UID_KEY] = int(uid)
            except ValueError:
                meta[_UID_KEY] = uid

        for key in (_DATE_KEY, _CITY_KEY, _DEPARTMENT_KEY, _REGION_KEY, _STATUS_KEY):
            value = (row.get(key) or "").strip()
            if value:
                meta[key] = value

        meta["origin"] = {"kind": "file", "uri": self._path.as_uri(), "label": self._path.name}
        return meta
