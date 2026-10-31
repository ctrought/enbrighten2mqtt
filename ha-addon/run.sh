#!/usr/bin/with-contenv bashio
# Translate add-on options (and the Home Assistant MQTT service) into the
# environment variables enbrighten2mqtt reads, then start the bridge.
set -e

# --- MQTT: an explicit mqtt_host option wins, else use HA's MQTT service ---
if bashio::config.has_value 'mqtt_host'; then
    export MQTT_HOST="$(bashio::config 'mqtt_host')"
    export MQTT_PORT="$(bashio::config 'mqtt_port')"
    export MQTT_USER="$(bashio::config 'mqtt_user')"
    export MQTT_PASS="$(bashio::config 'mqtt_pass')"
elif bashio::services.available 'mqtt'; then
    export MQTT_HOST="$(bashio::services 'mqtt' 'host')"
    export MQTT_PORT="$(bashio::services 'mqtt' 'port')"
    export MQTT_USER="$(bashio::services 'mqtt' 'username')"
    export MQTT_PASS="$(bashio::services 'mqtt' 'password')"
else
    bashio::exit.nok "No MQTT broker found: install the Mosquitto add-on, or set mqtt_host."
fi

# --- Enbrighten account (auto-login on startup) ---
export ENBRIGHTEN_EMAIL="$(bashio::config 'enbrighten_email')"
export ENBRIGHTEN_PASSWORD="$(bashio::config 'enbrighten_password')"
export DEVICES_REFRESH="$(bashio::config 'device_refresh')"
export LOG_LEVEL="$(bashio::config 'log_level' | tr '[:lower:]' '[:upper:]')"

# --- OEM app constants: you place oem.json in the add-on's config folder ---
if bashio::fs.file_exists '/config/oem.json'; then
    export OEM_CREDS=/config/oem.json
elif bashio::fs.file_exists '/app/secrets/oem.json'; then
    export OEM_CREDS=/app/secrets/oem.json
else
    bashio::log.warning "oem.json not found in the add-on config folder; auto-login will be skipped."
fi

# Persist the fetched key cache in the add-on's /data volume.
export DEVICES_FILE=/data/devices.json

exec enbrighten2mqtt serve
