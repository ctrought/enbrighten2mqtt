FROM python:3.12-slim
WORKDIR /app

COPY pyproject.toml requirements.txt README.md ./
COPY enbrighten2mqtt ./enbrighten2mqtt
RUN pip install --no-cache-dir .

# Everything the bridge reads/writes lives on the mounted /data volume:
#   /data/secrets/oem.json   (Enbrighten app constants; you provide this)
#   /data/devices.json       (key cache; written automatically on first login)
#   /data/config.yaml        (optional; env vars below cover the common case)
ENV OEM_CREDS=/data/secrets/oem.json \
    DEVICES_FILE=/data/devices.json

VOLUME /data
WORKDIR /data

# Needs host networking at runtime for Tuya LAN discovery/control:
#   docker run --network host \
#     -e ENBRIGHTEN_EMAIL=you@example.com -e ENBRIGHTEN_PASSWORD=... \
#     -e MQTT_HOST=your-broker-host \
#     -v /path/to/data:/data  ghcr.io/<you>/enbrighten2mqtt
ENTRYPOINT ["enbrighten2mqtt"]
CMD ["serve"]
