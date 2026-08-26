#!/bin/sh
# Garante pastas do volume (EasyPanel monta /app/data; em geral como root na 1a vez).
set -e
DATA="${FISCAL_DATA_DIR:-/app/data}"
# Caminho Windows vazado na env (C:/...) nao existe no Linux — cai no volume.
case "$DATA" in
  [A-Za-z]:*) DATA=/app/data ;;
esac
mkdir -p "$DATA/XML" "$DATA/Certificados" "$DATA/logs"
export FISCAL_DATA_DIR="$DATA"
XML="${FISCAL_XML_DIR:-$DATA/XML}"
case "$XML" in
  [A-Za-z]:*) XML="$DATA/XML" ;;
esac
export FISCAL_XML_DIR="$XML"

if [ "$(id -u)" = "0" ]; then
  # NAO usar chown -R no volume: com milhares de XML o boot demora, o healthcheck
  # do EasyPanel mata o processo e o log enche de "Serving on :8000".
  chown appuser:appgroup "$DATA" "$DATA/XML" "$DATA/Certificados" "$DATA/logs" 2>/dev/null || true
  chown appuser:appgroup "$DATA"/portal_fiscal.db "$DATA"/portal_fiscal.db-wal "$DATA"/portal_fiscal.db-shm 2>/dev/null || true
  exec gosu appuser "$@"
fi

exec "$@"
