#!/usr/bin/with-contenv bashio
# Startet die Dokumentenverwaltung als Home-Assistant-Add-on.

DATA=/data
if bashio::config.true 'ablage_im_share'; then
    DATA=/share/dokumentenverwaltung
fi
mkdir -p "${DATA}"
bashio::log.info "Datenbank und Ablage: ${DATA}"

if bashio::config.has_value 'passwort'; then
    DMS_PASSWORD="$(bashio::config 'passwort')"
    export DMS_PASSWORD
fi
# Zugriffe über die Home-Assistant-Oberfläche (Ingress) sind bereits angemeldet.
export DMS_TRUSTED_IPS=172.30.32.2

exec /opt/venv/bin/python /app/run.py --host 0.0.0.0 --port 8099 --data "${DATA}"
