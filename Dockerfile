FROM python:3.12-slim
WORKDIR /app

COPY pyproject.toml requirements.txt README.md ./
COPY enbrighten2mqtt ./enbrighten2mqtt
RUN pip install --no-cache-dir .

# Bake the Enbrighten app constants (oem.json) into the image so it is turnkey:
# the only per-user settings are the account login + MQTT. secrets/ is in the
# build context (gitignored, so present only when the maintainer builds); on a
# clean checkout only .gitkeep is copied and OEM_CREDS is simply absent.
COPY secrets/ /app/secrets/
ENV OEM_CREDS=/app/secrets/oem.json \
    DEVICES_FILE=/data/devices.json

VOLUME /data
WORKDIR /data

# Runtime needs host networking for Tuya LAN discovery (UDP) + control (TCP):
#   docker run --network host \
#     -e ENBRIGHTEN_EMAIL=you@example.com -e ENBRIGHTEN_PASSWORD=... \
#     -e MQTT_HOST=your-broker-host \
#     -v /srv/enbrighten2mqtt:/data  ghcr.io/<owner>/enbrighten2mqtt
#
# The same image also runs as a Home Assistant add-on: it reads the add-on's
# /data/options.json and HA's MQTT service automatically (see hassio.py).
ENTRYPOINT ["enbrighten2mqtt"]
CMD ["serve"]
