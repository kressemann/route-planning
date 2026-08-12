# Laufzeit fuer die Tourenplanung: Flask-App als Dauerdienst.

FROM python:3.12-slim

ENV TZ=Europe/Berlin \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
 && apt-get install -y --no-install-recommends tzdata \
 && ln -snf "/usr/share/zoneinfo/$TZ" /etc/localtime \
 && echo "$TZ" > /etc/timezone \
 && rm -rf /var/lib/apt/lists/*

ARG UID=1000
ARG GID=1000
RUN groupadd --gid "$GID" routeplanning \
 && useradd --uid "$UID" --gid "$GID" --create-home --shell /bin/bash routeplanning

WORKDIR /app

COPY requirements.txt requirements-dev.txt ./
RUN pip install --no-cache-dir -r requirements-dev.txt

COPY routeplanning/ ./routeplanning/
COPY tests/ ./tests/
COPY wsgi.py ./

RUN mkdir -p /app/data \
 && chown -R "$UID:$GID" /app/data /app

USER routeplanning

EXPOSE 5000

CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "2", "wsgi:app"]
