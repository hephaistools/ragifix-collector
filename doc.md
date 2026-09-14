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

- Table `failed_documents` : `(source_name, doc_id, error_count, last_error, extension, metadata)`
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
(source_name TEXT, doc_id TEXT, error_count INTEGER, last_error TEXT, extension TEXT, metadata TEXT,
 PRIMARY KEY (source_name, doc_id))
```
Documents qui ont échoué et doivent être retentés. `extension` et
`metadata` (JSON, nullables) conservent respectivement `change.extension`
et `change.metadata` capturés au moment de l'échec — sans cela, un retry
perdrait ces informations puisque `get_content(doc_id)` ne renvoie que des
octets, ni l'extension ni les métadonnées du connecteur.

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

Schéma `metadata` formalisé (clés à plat, posées par chaque connecteur) :

| Clé | Contenu | Exemple |
|---|---|---|
| `collector_type` | Type de connecteur (valeur fixe par connecteur) | `"file"`, `"sharepoint"` |
| `source` | Nom de la source (ajouté par `syncer.py`, pas par le connecteur) | `"docs_internes"` |
| `filename` | Nom du fichier | `"rapport.pdf"` |
| `uri` | Lien ou chemin le plus rapide pour ouvrir le document source | `file://...` ou `https://...` |
| `path` | Chemin du fichier dans son arborescence source (glob futur) | `/mnt/partage/rapport.pdf` |
| `modified_at` | Date de dernière modification du fichier (ISO 8601), fournie par le connecteur | `"2026-01-01T00:00:00Z"` |
| `retry` | Ajouté par `syncer.py` lors d'un retry (absent au premier envoi) | `true` |

`api_client.py` fusionne en plus la clé `extension` dans ce même objet
`metadata` avant l'envoi à ragifix (`PUT /documents/{doc_id}?metadata=...`)
— ragifix l'exige dans `metadata`, il n'y a plus de paramètre HTTP dédié.

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
