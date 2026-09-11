"""Tests du state store SQLite (cursors + file d'attente de retentatives)."""

from __future__ import annotations

import sqlite3

import pytest

from ragifix_collector.state import SqliteStateStore, build_state_store


def test_get_cursor_on_empty_returns_none(tmp_path):
    store = SqliteStateStore(str(tmp_path / "state.db"))
    assert store.get_cursor("src1") is None
    store.close()


def test_set_then_get_cursor_roundtrip(tmp_path):
    store = SqliteStateStore(str(tmp_path / "state.db"))
    store.set_cursor("src1", "cursor-a")
    assert store.get_cursor("src1") == "cursor-a"
    store.close()


def test_set_cursor_overwrites_existing(tmp_path):
    store = SqliteStateStore(str(tmp_path / "state.db"))
    store.set_cursor("src1", "cursor-a")
    store.set_cursor("src1", "cursor-b")
    assert store.get_cursor("src1") == "cursor-b"
    store.close()


def test_cursors_are_scoped_per_source(tmp_path):
    store = SqliteStateStore(str(tmp_path / "state.db"))
    store.set_cursor("src1", "cursor-a")
    store.set_cursor("src2", "cursor-b")
    assert store.get_cursor("src1") == "cursor-a"
    assert store.get_cursor("src2") == "cursor-b"
    store.close()


def test_get_failed_on_empty_returns_empty_list(tmp_path):
    store = SqliteStateStore(str(tmp_path / "state.db"))
    assert store.get_failed("src1", max_retries=5) == []
    store.close()


def test_record_failure_then_get_failed(tmp_path):
    store = SqliteStateStore(str(tmp_path / "state.db"))
    store.record_failure("src1", "doc1", "boom", 1)
    failed = store.get_failed("src1", max_retries=5)
    assert failed == [("doc1", 1, "boom", None, "", None)]
    store.close()


def test_get_failed_excludes_docs_at_or_above_max_retries(tmp_path):
    store = SqliteStateStore(str(tmp_path / "state.db"))
    store.record_failure("src1", "doc1", "boom", 5)
    assert store.get_failed("src1", max_retries=5) == []
    assert store.get_failed("src1", max_retries=6) == [("doc1", 5, "boom", None, "", None)]
    store.close()


def test_record_failure_updates_error_count_and_message(tmp_path):
    store = SqliteStateStore(str(tmp_path / "state.db"))
    store.record_failure("src1", "doc1", "first", 1)
    store.record_failure("src1", "doc1", "second", 2)
    failed = store.get_failed("src1", max_retries=5)
    assert failed == [("doc1", 2, "second", None, "", None)]
    store.close()


def test_record_failure_preserves_origin_when_not_resupplied(tmp_path):
    store = SqliteStateStore(str(tmp_path / "state.db"))
    store.record_failure("src1", "doc1", "first", 1, origin='{"kind": "file"}')
    # Deuxième échec sans origin explicite : l'origine précédente doit être conservée.
    store.record_failure("src1", "doc1", "second", 2, origin=None)
    failed = store.get_failed("src1", max_retries=5)
    assert failed == [("doc1", 2, "second", '{"kind": "file"}', "", None)]
    store.close()


def test_record_failure_updates_origin_when_resupplied(tmp_path):
    store = SqliteStateStore(str(tmp_path / "state.db"))
    store.record_failure("src1", "doc1", "first", 1, origin='{"kind": "file"}')
    store.record_failure("src1", "doc1", "second", 2, origin='{"kind": "https"}')
    failed = store.get_failed("src1", max_retries=5)
    assert failed == [("doc1", 2, "second", '{"kind": "https"}', "", None)]
    store.close()


def test_clear_success_removes_failed_entry(tmp_path):
    store = SqliteStateStore(str(tmp_path / "state.db"))
    store.record_failure("src1", "doc1", "boom", 1)
    store.clear_success("src1", "doc1")
    assert store.get_failed("src1", max_retries=5) == []
    store.close()


def test_clear_success_on_missing_entry_is_noop(tmp_path):
    store = SqliteStateStore(str(tmp_path / "state.db"))
    store.clear_success("src1", "missing")  # ne doit pas lever
    store.close()


def test_failed_documents_scoped_per_source(tmp_path):
    store = SqliteStateStore(str(tmp_path / "state.db"))
    store.record_failure("src1", "doc1", "boom", 1)
    store.record_failure("src2", "doc1", "boom", 1)
    store.clear_success("src1", "doc1")
    assert store.get_failed("src1", max_retries=5) == []
    assert store.get_failed("src2", max_retries=5) == [("doc1", 1, "boom", None, "", None)]
    store.close()


def test_schema_migration_adds_origin_column(tmp_path):
    """Une base créée avant l'ajout de la colonne `origin` doit être migrée
    automatiquement à l'ouverture (ALTER TABLE ADD COLUMN)."""
    db_path = tmp_path / "legacy.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """
        CREATE TABLE failed_documents (
            source_name TEXT NOT NULL,
            doc_id TEXT NOT NULL,
            error_count INTEGER NOT NULL DEFAULT 1,
            last_error TEXT,
            PRIMARY KEY (source_name, doc_id)
        )
        """
    )
    conn.execute(
        "INSERT INTO failed_documents (source_name, doc_id, error_count, last_error) VALUES (?, ?, ?, ?)",
        ("src1", "doc1", 1, "old-error"),
    )
    conn.commit()
    conn.close()

    store = SqliteStateStore(str(db_path))
    failed = store.get_failed("src1", max_retries=5)
    assert failed == [("doc1", 1, "old-error", None, None, None)]
    store.close()


def test_build_state_store_sqlite(tmp_path):
    store = build_state_store("sqlite", str(tmp_path / "state.db"))
    assert isinstance(store, SqliteStateStore)
    store.close()


def test_build_state_store_unknown_backend_raises(tmp_path):
    with pytest.raises(ValueError):
        build_state_store("postgres", str(tmp_path / "state.db"))


def test_creates_parent_directories(tmp_path):
    nested = tmp_path / "a" / "b" / "state.db"
    store = SqliteStateStore(str(nested))
    assert nested.parent.is_dir()
    store.close()
