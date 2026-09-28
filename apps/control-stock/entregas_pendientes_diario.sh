#!/bin/sh
# La pasada diaria de "Entrega pendiente" (28/09/2026), a las 7:00 a.m. de
# Panamá. Mismo patrón EXACTO que resumen_diario.sh: el secreto no vive en
# el crontab -este script lo lee del .env en cada corrida y lo pasa a
# `curl -K -` por entrada estándar-, así que no aparece ni en `crontab -l`
# ni en `ps` ni en el log. Nada de `set -x` ni `curl -v` en este script,
# por lo mismo.
#
# Crontab (lo pone Abraham en el droplet de apps, que corre en hora de
# Panamá):
#   0 7 * * * /home/hermes/control-stock/entregas_pendientes_diario.sh >> ~/control-stock/entregas_pendientes.log
set -eu

AQUI="$(cd "$(dirname "$0")" && pwd)"
cd "$AQUI"

SECRETO="$(grep -m1 '^ENTREGAS_PENDIENTES_SECRETO=' .env 2>/dev/null | cut -d= -f2-)"
if [ -z "$SECRETO" ]; then
  echo "$(date '+%F %T') · falta ENTREGAS_PENDIENTES_SECRETO en .env: no se corrió nada."
  exit 0
fi

URL="${ENTREGAS_PENDIENTES_URL:-http://127.0.0.1:8092/entregas-pendientes/revisar}"

RESPUESTA="$(
  printf 'url = "%s"\nrequest = POST\nheader = "Authorization: Bearer %s"\nsilent\nshow-error\n' \
    "$URL" "$SECRETO" | curl -K -
)"

echo "$(date '+%F %T') · $RESPUESTA"
