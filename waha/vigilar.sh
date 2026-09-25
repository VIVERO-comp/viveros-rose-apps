#!/bin/bash
# Vigilante de la convivencia OpenWA <-> WAHA (Fase W, 25/09/2026).
#
# Abraham autorizo de antemano: si OpenWA deja de recibir, se desvincula WAHA
# y se restaura su respaldo SIN preguntar, y se le avisa despues.
#
# Dos cosas se miran cada 30 segundos:
#   1. que OpenWA siga "healthy" y no se haya desconectado
#   2. que el almacen de WAHA no se dispare por la descarga de historial
#
# Uso: ~/waha/vigilar.sh   (deja el diario en ~/waha/vigilancia.log)
set -u
LOG=~/waha/vigilancia.log
RESPALDO=$(ls -t ~/backups/openwa-sesion-antes-de-waha-*.tar.gz 2>/dev/null | head -1)
TOPE_MB=60          # el almacen sano ronda 3 MB; 60 ya es historial bajando
FIN=$((SECONDS+3600))

di () { echo "[$(date +%H:%M:%S)] $*" | tee -a "$LOG"; }

almacen_mb () {
  docker run --rm -v /home/hermes/waha/.sessions:/s:ro alpine \
    du -sm /s 2>/dev/null | cut -f1
}
openwa_sano () {
  [ "$(docker inspect openwa-openwa-1 --format '{{.State.Health.Status}}' 2>/dev/null)" = "healthy" ] \
  && [ "$(docker inspect openwa-openwa-1 --format '{{.State.Status}}' 2>/dev/null)" = "running" ]
}

desvincular_waha () {
  cd ~/waha; set -a; . ./.env; set +a
  curl -s -o /dev/null -X POST -H "X-Api-Key: $WAHA_API_KEY" \
    http://127.0.0.1:3001/api/sessions/vivero/logout
  di "WAHA desvinculado."
}
restaurar_openwa () {
  [ -z "$RESPALDO" ] && { di "NO hay respaldo que restaurar."; return 1; }
  di "Restaurando OpenWA desde $(basename "$RESPALDO")"
  cd ~/openwa && docker compose down
  docker run --rm -v openwa_openwa-data:/d -v ~/backups:/b alpine \
    sh -c 'rm -rf /d/* /d/.[!.]* 2>/dev/null; tar xzf /b/'"$(basename "$RESPALDO")"' -C /d'
  cd ~/openwa && docker compose up -d
  di "OpenWA restaurado y levantado."
}

di "=== vigilancia iniciada. Respaldo: $(basename "${RESPALDO:-ninguno}") ==="
FALLOS=0
while [ $SECONDS -lt $FIN ]; do
  MB=$(almacen_mb)
  if openwa_sano; then
    FALLOS=0
  else
    FALLOS=$((FALLOS+1))
    di "OpenWA NO sano (fallo $FALLOS de 3)."
  fi

  if [ "${MB:-0}" -gt "$TOPE_MB" ]; then
    di "ALERTA: el almacen de WAHA va en ${MB} MB (tope $TOPE_MB). Esta bajando historial."
  fi

  if [ "$FALLOS" -ge 3 ]; then
    di "=== ROLLBACK AUTOMATICO: OpenWA lleva 90s caido ==="
    desvincular_waha
    sleep 60
    if openwa_sano; then
      di "OpenWA se recupero al quitar WAHA. No hizo falta restaurar."
    else
      restaurar_openwa
    fi
    di "=== fin del rollback. AVISAR A ABRAHAM. ==="
    exit 2
  fi
  sleep 30
done
di "=== una hora sin incidentes. OpenWA sano, almacen en ${MB:-?} MB. ==="
