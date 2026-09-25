#!/bin/bash
# El sincronizador de etiquetas, cada 2 minutos. Lo llama el cron.
#
# Solo deja el diario si hubo ALGO que hacer o si algo fallo: con 720
# pasadas al dia, anotar "0 cambios" cada vez llenaria el log de ruido y
# esconderia justo lo que hay que ver.
#
# El secreto no aparece nunca: el sincronizador lo lee del .env por su
# cuenta y este script no lo toca.
set -uo pipefail
LOG=/home/hermes/waha/sincronizador.log
SALIDA="$(/usr/bin/python3 /home/hermes/waha/sincronizador.py --aplicar 2>&1)"
CODIGO=$?
RESUMEN="$(echo "$SALIDA" | grep -E "^APLICADO ·" | tail -1)"

# Nada que hacer y sin errores: no se anota.
if [ "$CODIGO" = "0" ] && echo "$RESUMEN" | grep -q "0 chats etiquetados"; then
  exit 0
fi

{
  echo "$(date '+%F %T') · ${RESUMEN:-sin resumen (codigo $CODIGO)}"
  if [ "$CODIGO" != "0" ]; then
    echo "$SALIDA" | grep -E "ERROR|Traceback|Error" | head -8 | sed 's/^/    /'
  fi
} >> "$LOG"
