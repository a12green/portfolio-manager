FROM python:3.12-slim-bookworm

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV WEB_HOST=0.0.0.0
ENV WEB_PORT=8080
ENV STORAGE_DB_PATH=/data/portfolio_history.sqlite3

EXPOSE 8080

VOLUME ["/data"]

CMD ["python", "-m", "web"]
