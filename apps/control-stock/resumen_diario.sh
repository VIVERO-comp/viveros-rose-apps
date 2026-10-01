#!/bin/sh
# El resumen del día al celular del dueño, a las 19:00 de Panamá.
#
# ESTE ARCHIVO VIVE EN EL REPO A PROPOSITO. La primera version (25/09/2026)
# existia SOLO en el droplet, y el primer `rsync --delete` de un despliegue
# posterior la borro: el cron siguio corriendo, el log se lleno de
# "not found" y el dueño paso SEIS NOCHES sin su resumen sin que nadie se
# enterara (lo unico que fallaba era un archivo que no estaba en ninguna
# lista de excludes). Todo script que un cron del droplet llame tiene que
# estar en el repo, o el proximo despliegue se lo lleva igual.
#
# El secreto NO vive en el crontab: se lee del .env en cada corrida y se le
# pasa a `curl -K -` por entrada estandar, asi que no aparece ni en
# `crontab -l`, ni en `ps`, ni en el log. Nada de `set -x` ni `curl -v` en
# este script, por lo mismo.
#
# Crontab del droplet de apps (que corre en hora de Panama):
#   0 19 * * * /home/hermes/control-stock/resumen_diario.sh >> ~/control-stock/resumen.log 2>&1
set -eu

AQUI="$(cd "$(dirname "$0")" && pwd)"
cd "$AQUI"

SECRETO="$(grep -m1 '^RESUMEN_SECRETO=' .env 2>/dev/null | cut -d= -f2-)"
if [ -z "$SECRETO" ]; then
  echo "$(date '+%F %T') · falta RESUMEN_SECRETO en .env: no se mando nada."
  exit 0
fi

URL="${RESUMEN_URL:-http://127.0.0.1:8092/avisos/resumen}"

RESPUESTA="$(
  printf 'url = "%s"\nrequest = POST\nheader = "Authorization: Bearer %s"\nsilent\nshow-error\n' \
    "$URL" "$SECRETO" | curl -K -
)"

echo "$(date '+%F %T') · $RESPUESTA"
