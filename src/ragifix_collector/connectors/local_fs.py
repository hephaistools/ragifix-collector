"""Connecteur fichiers locaux.

Détection des changements basée UNIQUEMENT sur la date de modification
(mtime) des fichiers — pas de hash de contenu. Le cursor encode à la fois
la date du dernier scan et l'ensemble des fichiers vus à ce moment-là, ce
qui permet de détecter les suppressions sans avoir besoin de hasher quoi
que ce soit.

Accès strictement en lecture seule aux répertoires sources.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import AsyncIterator, Callable

from .base import Change, ChangeType


class LocalFsConnector:
    def __init__(self, paths: list[str], is_allowed_extension: Callable[[str], bool]):
        if not paths:
            raise ValueError("local_fs: au moins un chemin doit être configuré (paths)")
        self._roots = [Path(p).expanduser().resolve() for p in paths]
        self._is_allowed_extension = is_allowed_extension

    async def list_changes(self, cursor: str | None) -> tuple[list[Change], str]:
        state = json.loads(cursor) if cursor else {"last_mtime": 0.0, "known_paths": []}
        since = float(state.get("last_mtime", 0.0))
        previously_known: set[str] = set(state.get("known_paths", []))

        current_files: dict[str, float] = {}
        for root in self._roots:
            if not root.is_dir():
                continue
            for path in root.rglob("*"):
                if not path.is_file():
                    continue
                extension = path.suffix.lstrip(".")
                if not self._is_allowed_extension(extension):
                    continue

                resolved = self._safe_resolve(path, root)
                if resolved is None:
                    continue  # hors du périmètre autorisé (ex: lien symbolique sortant)

                try:
                    mtime = resolved.stat().st_mtime
                except OSError:
                    continue
                current_files[str(resolved)] = mtime

        changes: list[Change] = []
        max_mtime = since

        for doc_id, mtime in current_files.items():
            if mtime > since:
                change_type = ChangeType.MODIFIED if doc_id in previously_known else ChangeType.CREATED
                extension = Path(doc_id).suffix.lstrip(".")
                changes.append(
                    Change(
                        doc_id=doc_id,
                        change_type=change_type,
                        extension=extension,
                        metadata={
                            "origin": {"kind": "file", "uri": Path(doc_id).as_uri(), "label": Path(doc_id).name},
                        },
                    )
                )
            max_mtime = max(max_mtime, mtime)

        for doc_id in previously_known - current_files.keys():
            changes.append(Change(doc_id=doc_id, change_type=ChangeType.DELETED))

        new_state = {"last_mtime": max_mtime, "known_paths": sorted(current_files.keys())}
        return changes, json.dumps(new_state)

    async def get_content(self, doc_id: str) -> AsyncIterator[bytes]:
        path = Path(doc_id)
        if not self._is_within_roots(path):
            raise PermissionError(f"local_fs: chemin hors du périmètre autorisé: {doc_id}")
        with open(path, "rb") as f:
            while True:
                data = f.read(65536)
                if not data:
                    break
                yield data

    def _safe_resolve(self, path: Path, root: Path) -> Path | None:
        resolved = path.resolve()
        if resolved != root and root not in resolved.parents:
            return None
        return resolved

    def _is_within_roots(self, path: Path) -> bool:
        resolved = path.resolve()
        return any(resolved == root or root in resolved.parents for root in self._roots)
