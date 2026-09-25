#!/bin/bash
# Mantiene chico el almacen de WAHA. Corre cada hora por cron.
#
# Dos cosas crecen y ninguna nos sirve:
#
#   1. `gows_messages` — los mensajes que WhatsApp manda al vincular y al
#      llegar. El sincronizador NO los usa: solo pone etiquetas y pregunta
#      si un numero existe. Se borran los de mas de 1 dia.
#   2. `gows.db-wal` — el log de SQLite. Si nadie lo consolida llega a 60 MB
#      sin que haya datos nuevos (paso el 25/09: 71 MB de los cuales 61 eran
#      WAL). Un checkpoint lo pliega a la base.
#
# NO se toca ninguna tabla `whatsmeow_*`: ahi viven la identidad del
# dispositivo y las claves de cifrado. Borrar eso obliga a escanear el QR
# otra vez, con el telefono del negocio en la mano.
set -uo pipefail
DB=/s/gows/vivero/gows.db
LOG=/home/hermes/waha/almacen.log

ANTES=$(du -sm /home/hermes/waha/.sessions | cut -f1)

docker run --rm -v /home/hermes/waha/.sessions:/s alpine sh -c "
  apk add --no-cache sqlite >/dev/null 2>&1
  sqlite3 '$DB' \"DELETE FROM gows_messages WHERE timestamp < strftime('%s','now','-1 day');\"
  sqlite3 '$DB' 'PRAGMA wal_checkpoint(TRUNCATE);' >/dev/null
  sqlite3 '$DB' \"SELECT count(*) FROM gows_messages;\"
" > /tmp/waha_quedan.$$ 2>/dev/null

QUEDAN=$(tail -1 /tmp/waha_quedan.$$ 2>/dev/null); rm -f /tmp/waha_quedan.$$
DESPUES=$(du -sm /home/hermes/waha/.sessions | cut -f1)

echo "$(date '+%F %T') · almacen ${ANTES} MB -> ${DESPUES} MB · quedan ${QUEDAN:-?} mensajes" >> "$LOG"

# Si aun asi se dispara, que quede dicho en el log bien fuerte.
if [ "${DESPUES:-0}" -gt 20 ]; then
  echo "$(date '+%F %T') · OJO: el almacen sigue en ${DESPUES} MB (lo sano es ~2). Revisar los limites de historial." >> "$LOG"
fi
