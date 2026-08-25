#!/bin/sh
# Garante pastas do volume (EasyPanel monta /app/data vazio e em geral como root).
set -e
DATA="${FISCAL_DATA_DIR:-/app/data}"
mkdir -p "$DATA/XML" "$DATA/Certificados" "$DATA/logs"

if [ "$(id -u)" = "0" ]; then
  chown -R appuser:appgroup "$DATA" 2>/dev/null || true
  exec gosu appuser "$@"
fi

exec "$@"
