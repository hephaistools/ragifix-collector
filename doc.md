# ragifix-collector — Daemon de synchronisation

## Vue d'overview

**ragifix-collector** est un daemon qui surveille des sources de fichiers et pousse les changements vers ragifix.

Il ne fait **aucun traitement de contenu** (ni parsing, ni chunking, ni embedding) — il se contente de :
- Détecter les changements via des connecteurs
- Récupérer le contenu brut
- Pousser vers l'API ragifix

## Architecture interne

```
┌─────────────────────────────────────────────────────────┐
│              ragifix-collector (Daemon)                   │
├─────────────────────────────────────────────────────────┤
│  daemon.py              │  Boucle de synchronisation     │
│                         │  - Intervalle configurable     │
│                         │  - Mode one-shot (interval=0)  │
│                         │  - Arrêt propre (SIGTERM)      │
├─────────────────────────────────────────────────────────┤
│  syncer.py              │  Orchestrateur par source      │
│                         │  - Retry logic (max_retries)   │
│                         │  - Cursor management           │
│                         │  - Sync sources vers ragifix   │
├─────────────────────────────────────────────────────────┤
│  connectors/            │  Connecteurs de sources        │
│  ├─ base.py             │  - Interface commune           │
│  ├─ local_fs.py         │  - Fichiers locaux             │
│  └─ sharepoint.py       │  - SharePoint (optionnel)      │
├─────────────────────────────────────────────────────────┤
│  state.py               │  State store SQLite            │
│                         │  - cursors (position flux)     │
│                         │  - failed_documents (retry)    │
├─────────────────────────────────────────────────────────┤
│  api_client.py          │  Client HTTP vers ragifix      │
│                         │  - put_document                │
│                         │  - delete_document             │
│                         │  - set_sources                 │
└─────────────────────────────────────────────────────────┘
```

## Points clés

### 1. Cycle de synchronisation

Pour chaque source, à chaque intervalle :

1. **Récupérer les changements** via le connecteur (`list_changes(cursor)`)
2. **Retenter les documents échoués** (si `error_count < max_retries`)
3. **Appliquer les changements** (`_apply_change`)
4. **Avancer le cursor** même en cas d'échecs
5. **Mettre à jour les sources** dans ragifix (`POST /sources`)

### 2. Gestion des erreurs et retry

- Table `failed_documents` : `(source_name, doc_id, error_count, last_error, origin)`
- Après chaque échec : `error_count += 1`
- Si `error_count >= max_retries` : abandon (suppression de la table)
- Au prochain run : retente les documents avec `error_count < max_retries`

**Note** : Le cursor avance même en cas d'échecs — les documents échoués sont retentés via la table `failed_documents`, pas via un resync complet.

### 3. State store

Deux tables :

**`cursors`** :
```sql
(source_name TEXT PRIMARY KEY, cursor TEXT NOT NULL)
```
Position dans le flux de changements de chaque source.

**`failed_documents`** :
```sql
(source_name TEXT, doc_id TEXT, error_count INTEGER, last_error TEXT, origin TEXT, PRIMARY KEY (source_name, doc_id))
```
Documents qui ont échoué et doivent être retentés. `origin` (JSON,
nullable) conserve `metadata["origin"]` capturé au moment de l'échec —
sans cela, un retry perdrait ce lien puisque `get_content(doc_id)` ne
renvoie que des octets, pas les métadonnées du connecteur.

### 4. Connecteurs

Interface commune :
```python
class Connector(Protocol):
    async def list_changes(cursor: str | None) -> tuple[list[Change], str]: ...
    async def get_content(doc_id: str) -> AsyncIterator[bytes]: ...
```

Changement :
```python
class Change:
    doc_id: str
    change_type: ChangeType  # ADDED | UPDATED | DELETED
    extension: str
    metadata: dict
```

Convention `metadata["origin"]` (optionnelle) : `{"kind": "https"|"file",
"uri": "...", "label": "..."}` — le lien ou chemin le plus rapide pour
qu'un humain retrouve le document source (lien SharePoint `webUrl`, chemin
local...). Un connecteur qui connaît ce lien le renseigne ici ; ragifix la
remonte telle quelle, typée, dans les réponses de son API (voir sa doc).

Connecteurs disponibles :
- `local_fs` : fichiers locaux (natif)
- `sharepoint` : SharePoint (optionnel, `pip install ragifix-collector[sharepoint]`)

### 5. Sync des sources

Après chaque cycle, le collector met à jour la liste des sources dans ragifix :

```python
sources = [
    {"name": source.name, "description": source.description, "enabled": source.enabled}
    for source in config.sources
]
client.set_sources(sources)
```

Cela permet au MCP de savoir quelles sources existent et leur statut.

### 6. Configuration

| Champ | Description |
|-------|-------------|
| `sync.interval_seconds` | Intervalle entre deux cycles (0 = one-shot) |
| `sync.max_retries` | Nombre maximal de tentatives avant abandon |
| `sources[].name` | Nom unique de la source |
| `sources[].type` | Type de connecteur (`local_fs`, `sharepoint`) |
| `sources[].enabled` | Activer/désactiver la collecte |
| `sources[].description` | Description pour l'exposition MCP |
| `state_store.sqlite.path` | Chemin du state store SQLite |

---

## Déploiement

### Docker
```bash
docker build -f deploy/docker/Dockerfile -t ragifix-collector .
docker run -d --name ragifix-collector --network host -v "$(pwd)/config.yaml:/etc/ragifix-collector/config.yaml:ro" ragifix-collector
```

### Développement
```bash
python3 -m venv venv && source venv/bin/activate
pip install -e .
ragifix-collector --config ./config.yaml
```

---

## Limitations connues

- Pas de retry exponentiel (retry linéaire au prochain cycle)
- Pas de gestion des changements concurrents (un seul collector par source)
- Connecteur SharePoint optionnel (non inclus par défaut)
