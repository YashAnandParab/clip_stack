FROM python:3.12-slim

# gosu lets the entrypoint drop from root to your UID, so files written into
# the mounted vault are owned by you and not by root.
RUN apt-get update \
    && apt-get install -y --no-install-recommends gosu curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /srv

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENV CLIP_VAULT=/vault \
    CLIP_CONFIG=/config/config.yaml \
    CLIP_TEMPLATES=/config/templates \
    PYTHONUNBUFFERED=1

EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD curl -fsS http://localhost:8765/health || exit 1

ENTRYPOINT ["/entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8765"]
