# RiderMusic — production image
#
# Follows the same principles as MusicMind's Dockerfile: pin to
# Ubuntu 24.04 for native Python 3.12, force UTF-8 locale, and never
# bake in config.py — it holds the Plex token and admin password, so
# it's provided at container-start via bind mount, not built into
# the image.

FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive
ENV LANG=C.UTF-8
ENV LC_ALL=C.UTF-8

RUN apt-get update && apt-get install -y \
    python3.12 \
    python3-pip \
    python3.12-venv \
    sqlite3 \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN python3.12 -m pip install --break-system-packages --no-cache-dir \
    -r requirements.txt gunicorn

COPY . .

# config.py and ridermusic.db are intentionally NOT copied in here —
# see .dockerignore. Provide config.py via bind mount at runtime;
# the db is created fresh by init_db.py on first start (mount a
# volume over /app if you want it to persist across restarts).

EXPOSE 6869

CMD ["sh", "-c", "python3.12 init_db.py && gunicorn --bind 0.0.0.0:6869 --workers 2 app:app"]
