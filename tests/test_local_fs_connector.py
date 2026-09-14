"""Tests du connecteur fichiers locaux (détection par mtime)."""

from __future__ import annotations

import asyncio
import json
import os

import pytest

from ragifix_collector.config import ExtensionsFilter
from ragifix_collector.connectors.base import ChangeType
from ragifix_collector.connectors.local_fs import LocalFsConnector


def _allow_all(_ext: str) -> bool:
    return True


def test_init_raises_without_paths():
    with pytest.raises(ValueError):
        LocalFsConnector(paths=[], is_allowed_extension=_allow_all)


def test_first_scan_reports_all_files_as_created(tmp_path):
    (tmp_path / "a.txt").write_text("a")
    (tmp_path / "b.txt").write_text("b")

    connector = LocalFsConnector(paths=[str(tmp_path)], is_allowed_extension=_allow_all)
    changes, cursor = asyncio.run(connector.list_changes(None))

    assert {c.doc_id for c in changes} == {
        str((tmp_path / "a.txt").resolve()),
        str((tmp_path / "b.txt").resolve()),
    }
    assert all(c.change_type == ChangeType.CREATED for c in changes)
    state = json.loads(cursor)
    assert len(state["known_paths"]) == 2


def test_second_scan_with_no_change_reports_nothing(tmp_path):
    (tmp_path / "a.txt").write_text("a")
    connector = LocalFsConnector(paths=[str(tmp_path)], is_allowed_extension=_allow_all)
    _, cursor = asyncio.run(connector.list_changes(None))
    changes, _ = asyncio.run(connector.list_changes(cursor))
    assert changes == []


def test_modified_file_detected_after_mtime_bump(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("a")
    connector = LocalFsConnector(paths=[str(tmp_path)], is_allowed_extension=_allow_all)
    _, cursor = asyncio.run(connector.list_changes(None))

    # Avance le mtime pour simuler une modification.
    new_time = os.stat(f).st_mtime + 5
    os.utime(f, (new_time, new_time))

    changes, _ = asyncio.run(connector.list_changes(cursor))
    assert len(changes) == 1
    assert changes[0].change_type == ChangeType.MODIFIED
    assert changes[0].doc_id == str(f.resolve())


def test_deleted_file_detected(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("a")
    connector = LocalFsConnector(paths=[str(tmp_path)], is_allowed_extension=_allow_all)
    _, cursor = asyncio.run(connector.list_changes(None))

    f.unlink()

    changes, _ = asyncio.run(connector.list_changes(cursor))
    assert len(changes) == 1
    assert changes[0].change_type == ChangeType.DELETED
    assert changes[0].doc_id == str(f.resolve())


def test_extension_filter_excludes_disallowed_files(tmp_path):
    (tmp_path / "a.txt").write_text("a")
    (tmp_path / "b.exe").write_text("b")

    filt = ExtensionsFilter(method="allow", list=["txt"])
    connector = LocalFsConnector(paths=[str(tmp_path)], is_allowed_extension=filt.is_allowed)
    changes, _ = asyncio.run(connector.list_changes(None))

    assert len(changes) == 1
    assert changes[0].doc_id.endswith("a.txt")


def test_metadata_contains_file_info(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("a")
    connector = LocalFsConnector(paths=[str(tmp_path)], is_allowed_extension=_allow_all)
    changes, _ = asyncio.run(connector.list_changes(None))

    metadata = changes[0].metadata
    assert metadata["collector_type"] == "file"
    assert metadata["filename"] == "a.txt"
    assert metadata["uri"].startswith("file://")
    assert metadata["path"] == changes[0].doc_id
    assert metadata["modified_at"]  # ISO 8601, non vide


def test_nonexistent_root_is_skipped_gracefully(tmp_path):
    connector = LocalFsConnector(
        paths=[str(tmp_path / "does-not-exist")], is_allowed_extension=_allow_all
    )
    changes, cursor = asyncio.run(connector.list_changes(None))
    assert changes == []
    assert json.loads(cursor)["known_paths"] == []


def test_symlink_escaping_root_is_excluded(tmp_path):
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    outside_file = outside_dir / "secret.txt"
    outside_file.write_text("secret")

    root = tmp_path / "root"
    root.mkdir()
    link = root / "escape.txt"
    try:
        link.symlink_to(outside_file)
    except OSError:
        pytest.skip("symlinks non supportés sur cette plateforme")

    connector = LocalFsConnector(paths=[str(root)], is_allowed_extension=_allow_all)
    changes, _ = asyncio.run(connector.list_changes(None))
    assert changes == []


def test_get_content_reads_full_bytes(tmp_path):
    f = tmp_path / "a.bin"
    payload = b"x" * 200_000  # > 65536 pour exercer plusieurs itérations de lecture.
    f.write_bytes(payload)

    connector = LocalFsConnector(paths=[str(tmp_path)], is_allowed_extension=_allow_all)

    async def _read():
        chunks = []
        async for piece in connector.get_content(str(f.resolve())):
            chunks.append(piece)
        return b"".join(chunks)

    assert asyncio.run(_read()) == payload


def test_get_content_outside_roots_raises_permission_error(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("x")

    connector = LocalFsConnector(paths=[str(root)], is_allowed_extension=_allow_all)

    async def _read():
        async for _ in connector.get_content(str(outside)):
            pass

    with pytest.raises(PermissionError):
        asyncio.run(_read())
