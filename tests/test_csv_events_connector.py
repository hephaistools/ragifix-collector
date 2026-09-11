"""Tests du connecteur CSV d'événements (csv_events)."""

from __future__ import annotations

import asyncio
import json
import os

import pytest

from ragifix_collector.connectors.base import ChangeType
from ragifix_collector.connectors.csv_events import CsvEventsConnector, strip_html

_CSV_HEADER = "uid,title,description,long_description,status,date,city,department,region,address"


def _write_csv(path, rows: list[list[str]], header: str = _CSV_HEADER) -> None:
    lines = [header]
    lines.extend(",".join(row) for row in rows)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


@pytest.fixture
def csv_path(tmp_path):
    p = tmp_path / "events_propres.csv"
    _write_csv(
        p,
        [
            ["1", "Événement A", "desc courte", "<h2>Desc longue</h2><p>Première<br>Deuxième</p>", "Programmé", "2025-10-16T07:30:00+00:00", "Marseille", "Bouches-du-Rhône", "Provence-Alpes-Côte d'Azur", "12 rue de la République"],
            ["2", "Événement B", "seulement description", "", "Annulé", "2025-11-01T18:00:00+00:00", "Aix-en-Provence", "Bouches-du-Rhône", "Provence-Alpes-Côte d'Azur", ""],
            ["", "Sans uid", "ligne sans identifiant", "corps", "Programmé", "2025-12-01T09:00:00+00:00", "Marseille", "Bouches-du-Rhône", "Provence-Alpes-Côte d'Azur", "1 rue vide"],
        ],
    )
    return p


def test_first_scan_reports_all_rows_as_created(csv_path):
    connector = CsvEventsConnector(str(csv_path))
    changes, cursor = asyncio.run(connector.list_changes(None))

    # La ligne sans uid est ignorée.
    assert {c.doc_id for c in changes} == {"1", "2"}
    assert all(c.change_type == ChangeType.CREATED for c in changes)
    assert all(c.extension == "md" for c in changes)
    assert json.loads(cursor)["mtime"] > 0


def test_second_scan_with_no_change_reports_nothing(csv_path):
    connector = CsvEventsConnector(str(csv_path))
    _, cursor = asyncio.run(connector.list_changes(None))
    changes, _ = asyncio.run(connector.list_changes(cursor))
    assert changes == []


def test_modified_file_reports_rows_as_modified(csv_path):
    connector = CsvEventsConnector(str(csv_path))
    _, cursor = asyncio.run(connector.list_changes(None))

    new_time = csv_path.stat().st_mtime + 5
    csv_path.chmod(0o600)
    os.utime(csv_path, (new_time, new_time))

    changes, _ = asyncio.run(connector.list_changes(cursor))
    assert {c.doc_id for c in changes} == {"1", "2"}
    assert all(c.change_type == ChangeType.MODIFIED for c in changes)


def test_get_content_builds_markdown_fragment_with_stripped_html(csv_path):
    connector = CsvEventsConnector(str(csv_path))
    async def _content():
        async for piece in connector.get_content("1"):
            return piece.decode("utf-8")
    fragment = asyncio.run(_content())

    assert fragment.startswith("## Titre de l'évène\nÉvénement A")
    assert "<h2>" not in fragment and "<p>" not in fragment
    # Les <br> portent la structure en lignes : conservés.
    assert "Première\nDeuxième" in fragment


def test_get_content_uses_long_description_fallback_to_description(csv_path):
    # L'événement 2 n'a pas de long_description : on utilise `description`.
    connector = CsvEventsConnector(str(csv_path))

    async def _content():
        async for piece in connector.get_content("2"):
            return piece.decode("utf-8")
    fragment = asyncio.run(_content())

    assert "seulement description" in fragment
    assert "Desc longue" not in fragment


def test_get_content_omits_description_section_when_absent(tmp_path):
    p = tmp_path / "sans_desc.csv"
    _write_csv(
        p,
        [["7", "Sans description", "", "", "Programmé", "2025-10-16T07:30:00+00:00", "Marseille", "Bouches-du-Rhône", "Provence-Alpes-Côte d'Azur", "adresse"]],
    )
    connector = CsvEventsConnector(str(p))

    async def _content():
        async for piece in connector.get_content("7"):
            return piece.decode("utf-8")
    fragment = asyncio.run(_content())

    assert "## Description de l'évène" not in fragment
    # Le titre est toujours présent.
    assert fragment.startswith("## Titre de l'évène")


def test_metadata_structure(csv_path):
    connector = CsvEventsConnector(str(csv_path))
    changes, _ = asyncio.run(connector.list_changes(None))
    row1 = next(c for c in changes if c.doc_id == "1")
    meta = row1.metadata

    assert meta["uid"] == 1
    assert meta["city"] == "Marseille"
    assert meta["department"] == "Bouches-du-Rhône"
    assert meta["region"] == "Provence-Alpes-Côte d'Azur"
    assert meta["status"] == "Programmé"
    assert meta["date"] == "2025-10-16T07:30:00+00:00"
    # L'adresse est embarquée dans le fragment, pas en métadonnée.
    assert "address" not in meta
    # Lien vers le fichier source.
    assert meta["origin"]["kind"] == "file"
    assert meta["origin"]["uri"].startswith("file://")


def test_get_content_unknown_uid_raises(csv_path):
    connector = CsvEventsConnector(str(csv_path))

    async def _read():
        async for _ in connector.get_content("n-existe-pas"):
            pass

    with pytest.raises(KeyError):
        asyncio.run(_read())


def test_missing_file_reports_nothing(tmp_path):
    connector = CsvEventsConnector(str(tmp_path / "absent.csv"))
    changes, cursor = asyncio.run(connector.list_changes(None))
    assert changes == []
    assert cursor == ""


def test_strip_html_preserves_br_and_unescapes_entities():
    assert strip_html("<p>Paul &amp; et&nbsp;Jacques</p>") == "Paul & et Jacques"
    assert strip_html("<h2>Titre</h2><br>suite") == "Titre\nsuite"
