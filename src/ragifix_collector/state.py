"""State store de ragifix-collector.

Ne retient QUE les cursors de synchronisation par source. Aucune table de
chunk_ids ici : cette information appartient entièrement au registre
interne de ragifix, que ragifix-collector n'a jamais besoin de consulter.
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Protocol, runtime_checkable


@runtime_checkable
class StateStore(Protocol):
    def get_cursor(self, source_name: str) -> str | None: ...

    def set_cursor(self, source_name: str, cursor: str) -> None: ...

    def get_failed(self, source_name: str, max_retries: int) -> list[tuple[str, int, str | None, str | None]]: ...

    def record_failure(
        self, source_name: str, doc_id: str, error_msg: str, error_count: int, origin: str | None = None
    ) -> None: ...

    def clear_success(self, source_name: str, doc_id: str) -> None: ...

    def close(self) -> None: ...


class SqliteStateStore:
    def __init__(self, path: str):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS cursors (
                    source_name TEXT PRIMARY KEY,
                    cursor TEXT NOT NULL
                )
                """
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS failed_documents (
                    source_name TEXT NOT NULL,
                    doc_id TEXT NOT NULL,
                    error_count INTEGER NOT NULL DEFAULT 1,
                    last_error TEXT,
                    origin TEXT,
                    PRIMARY KEY (source_name, doc_id)
                )
                """
            )
            # Migration légère pour les bases existantes créées avant l'ajout
            # de la colonne `origin` (préserve le lien d'origine à travers un retry).
            columns = {row[1] for row in self._conn.execute("PRAGMA table_info(failed_documents)")}
            if "origin" not in columns:
                self._conn.execute("ALTER TABLE failed_documents ADD COLUMN origin TEXT")

    def get_cursor(self, source_name: str) -> str | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT cursor FROM cursors WHERE source_name = ?", (source_name,)
            ).fetchone()
            return row[0] if row else None

    def set_cursor(self, source_name: str, cursor: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO cursors (source_name, cursor) VALUES (?, ?)
                ON CONFLICT(source_name) DO UPDATE SET cursor = excluded.cursor
                """,
                (source_name, cursor),
            )

    def get_failed(self, source_name: str, max_retries: int) -> list[tuple[str, int, str | None, str | None]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT doc_id, error_count, last_error, origin FROM failed_documents "
                "WHERE source_name = ? AND error_count < ?",
                (source_name, max_retries),
            ).fetchall()
            return [(row[0], row[1], row[2], row[3]) for row in rows]

    def record_failure(
        self, source_name: str, doc_id: str, error_msg: str, error_count: int, origin: str | None = None
    ) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO failed_documents (source_name, doc_id, error_count, last_error, origin)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(source_name, doc_id) DO UPDATE SET
                    error_count = excluded.error_count,
                    last_error = excluded.last_error,
                    origin = COALESCE(excluded.origin, failed_documents.origin)
                """,
                (source_name, doc_id, error_count, error_msg, origin),
            )

    def clear_success(self, source_name: str, doc_id: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "DELETE FROM failed_documents WHERE source_name = ? AND doc_id = ?",
                (source_name, doc_id),
            )

    def close(self) -> None:
        self._conn.close()


def build_state_store(backend: str, sqlite_path: str) -> StateStore:
    if backend == "sqlite":
        return SqliteStateStore(sqlite_path)
    raise ValueError(f"Backend de state store inconnu: {backend}")
