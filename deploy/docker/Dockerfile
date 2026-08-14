# A lancer depuis la racine de ce dépôt :
#   docker build -f deploy/docker/Dockerfile -t ragifix-collector .
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd --system ragifix-collector && useradd --system --gid ragifix-collector --create-home --home-dir /home/ragifix-collector ragifix-collector

WORKDIR /opt/ragifix-collector

COPY . .

# L'extra [sharepoint] est installé par défaut dans l'image Docker (coût
# négligeable, une seule dépendance : msal). Pour une image plus légère
# n'embarquant que local_fs, remplacer par `pip install .`.
RUN pip install --no-cache-dir ".[sharepoint]"

RUN mkdir -p /var/lib/ragifix-collector && chown -R ragifix-collector:ragifix-collector /var/lib/ragifix-collector /opt/ragifix-collector

USER ragifix-collector

ENTRYPOINT ["ragifix-collector"]
CMD ["--config", "/etc/ragifix-collector/config.yaml"]
