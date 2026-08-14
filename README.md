# ragifix-collector

Mini ETL : détecte les changements sur des sources configurables (fichiers
locaux par défaut, SharePoint via extension) et alimente un serveur
[ragifix](../ragifix) via son API HTTP. Ne fait aucun traitement de
contenu (pas de parsing, pas de chunking, pas d'embedding).

## Sommaire

- [Installation via Docker](#installation-via-docker)
- [Installation via paquet .deb](#installation-via-paquet-deb)
- [Installation en environnement de développement](#installation-en-environnement-de-développement)
- [Configuration](#configuration)
- [Ajouter un connecteur](#ajouter-un-connecteur)

---

## Installation via Docker

```bash
git clone <url-du-dépôt> ragifix-collector && cd ragifix-collector

cp config.example.yaml config.yaml                    # puis l'adapter
cp deploy/ragifix-collector.env.example ragifix-collector.env  # puis renseigner les secrets

docker build -f deploy/docker/Dockerfile -t ragifix-collector .

docker run -d \
  --name ragifix-collector \
  --network host \
  --env-file ragifix-collector.env \
  -v "$(pwd)/config.yaml:/etc/ragifix-collector/config.yaml:ro" \
  -v ragifix-collector-data:/var/lib/ragifix-collector \
  -v /srv/ragifix-data:/srv/ragifix-data:ro \
  ragifix-collector
```

`--network host` : `ragifix-collector` doit atteindre `ragifix` sur
`127.0.0.1` (voir le README de `ragifix`). Le montage de
`/srv/ragifix-data` n'est nécessaire que si une source `local_fs` est
configurée — adapter le chemin à vos sources réelles, en lecture seule.

L'extra `sharepoint` (dépendance `msal`) est installé par défaut dans
l'image Docker.

## Installation via paquet .deb

```bash
git clone <url-du-dépôt> ragifix-collector && cd ragifix-collector

sudo apt-get install -y devscripts debhelper python3-venv python3-pip
dpkg-buildpackage -us -uc -b

sudo apt install -y ../ragifix-collector_0.1.0-1_all.deb
```

L'installation (`postinst`) construit un environnement virtuel Python dans
`/opt/ragifix-collector/venv` et y installe les dépendances **depuis
PyPI** (extra `sharepoint` inclus) : accès réseau requis au moment de
`apt install`, service ensuite hors-ligne.

Le paquet crée un utilisateur système dédié, `/etc/ragifix-collector/config.yaml`
et `ragifix-collector.env` (depuis les exemples), et une unité systemd
`ragifix-collector.service`.

```bash
sudo nano /etc/ragifix-collector/config.yaml
sudo nano /etc/ragifix-collector/ragifix-collector.env
sudo systemctl enable --now ragifix-collector
journalctl -u ragifix-collector -f
```

```bash
sudo apt remove ragifix-collector    # conserve la config
sudo apt purge ragifix-collector     # supprime tout
```

## Installation en environnement de développement

```bash
git clone <url-du-dépôt> ragifix-collector && cd ragifix-collector

python3 -m venv venv
source venv/bin/activate
pip install -e ".[sharepoint]"   # omettre [sharepoint] si non utilisé

cp config.example.yaml config.yaml   # reste à côté du code, hors /etc

export RAGIFIX_API_TOKEN=dev-token          # même valeur que côté ragifix
export SHAREPOINT_PROJET_X_CLIENT_SECRET=... # si une source sharepoint est configurée

ragifix-collector --config ./config.yaml
```

## Configuration

Un seul fichier YAML (voir `config.example.yaml`), indépendant de celui de
`ragifix`. Aucun secret en clair : uniquement des références à des
variables d'environnement.

| Variable d'environnement | Rôle |
|---|---|
| `RAGIFIX_API_TOKEN` | Doit être identique à celui configuré côté `ragifix`. |
| `SHAREPOINT_<NOM>_CLIENT_SECRET` | Une variable par source SharePoint configurée. |

- `ragifix.base_url` : où joindre l'API `ragifix` (`http://127.0.0.1:8421`
  par défaut).
- `sync.interval_seconds` : `0` = un seul cycle puis arrêt (mode one-shot,
  pour être piloté par `cron`/un timer systemd plutôt que par la boucle
  interne) ; sinon boucle interne à cet intervalle.
- `sources[]` : liste des sources, chacune avec `name` (unique), `type`
  (`local_fs` ou `sharepoint`), `enabled`, `extensions` (`method:
  allow|deny` + `list`), et les champs propres au connecteur (`paths` pour
  `local_fs` ; `tenant_id`, `client_id`, `client_secret_env`, `site_url`,
  `folder_path` pour `sharepoint`).

Les documents sont poussés vers `ragifix` avec un `doc_id` préfixé par le
nom de la source (ex: `docs_internes:/srv/ragifix-data/docs/rapport.txt`),
pour éviter toute collision entre sources.

## Ajouter un connecteur

`local_fs` est natif. `sharepoint` nécessite son extra :

```bash
pip install "ragifix-collector[sharepoint]"
```

Un connecteur tiers (paquet séparé, sans modifier ce dépôt) s'ajoute via
un entry point Python :

```toml
# dans le pyproject.toml du paquet tiers
[project.entry-points."ragifix_collector.connectors"]
mon_connecteur = "mon_paquet.connector:build_connector"
# build_connector(source_config) -> Connector
# (implémente ragifix_collector.connectors.base.Connector :
#  list_changes + get_content)
```

Une fois ce paquet installé dans le même environnement (ou dans le venv
`/opt/ragifix-collector/venv` en production), le référencer dans
`config.yaml` via `type: mon_connecteur`.
