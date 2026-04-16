#!/bin/sh
set -eu

APP_UID="${APP_UID:-1000}"
APP_GID="${APP_GID:-1000}"
DATA_DIR="${DATA_DIR:-/data}"

if ! getent group appgroup >/dev/null 2>&1; then
    addgroup --gid "$APP_GID" appgroup >/dev/null 2>&1 || true
fi

if ! id appuser >/dev/null 2>&1; then
    adduser --system --uid "$APP_UID" --ingroup appgroup --home /nonexistent --shell /usr/sbin/nologin appuser >/dev/null 2>&1 || true
fi

mkdir -p "$DATA_DIR" "$DATA_DIR/uploads" "$DATA_DIR/qr" /app/instance
chown -R "$APP_UID":"$APP_GID" "$DATA_DIR" /app/instance
chmod 750 "$DATA_DIR" "$DATA_DIR/uploads" "$DATA_DIR/qr"

export DATA_DIR

exec gosu "$APP_UID":"$APP_GID" "$@"
