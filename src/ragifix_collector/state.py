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

    def close(self) -> None:
        self._conn.close()


def build_state_store(backend: str, sqlite_path: str) -> StateStore:
    if backend == "sqlite":
        return SqliteStateStore(sqlite_path)
    raise ValueError(f"Backend de state store inconnu: {backend}")
