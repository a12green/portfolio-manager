FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install -r requirements.txt

# Run as an unprivileged user. Only /data and /app/reports are writable.
RUN useradd --system --uid 10001 --home-dir /app --shell /usr/sbin/nologin app \
    && mkdir -p /data /app/reports \
    && chown app:app /data /app/reports

COPY . .

ENV WEB_HOST=0.0.0.0 \
    WEB_PORT=8080 \
    STORAGE_DB_PATH=/data/portfolio_history.sqlite3

USER app

EXPOSE 8080

VOLUME ["/data"]

CMD ["python", "-m", "web"]
