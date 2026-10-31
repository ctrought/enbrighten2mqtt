FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY enbrighten2mqtt ./enbrighten2mqtt
VOLUME /data
WORKDIR /data
ENTRYPOINT ["python", "-m", "enbrighten2mqtt"]
CMD ["serve", "--config", "/data/config.yaml"]
