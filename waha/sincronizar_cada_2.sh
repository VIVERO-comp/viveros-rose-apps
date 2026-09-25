#!/bin/bash
# El sincronizador, cada 2 minutos. Lo llama el cron.
#
# Hace las DOS cosas en la misma corrida: las etiquetas (`--aplicar`) y los
# nombres de contacto (`--aplicar-nombres`). Las dos banderas son separadas
# a proposito -se puede aplicar una sin la otra- pero el cron pasa las dos,
# porque si nadie pasa `--aplicar-nombres` un lead nuevo nunca recibe su
# nombre solo, que es justamente para lo que se escribio.
#
# Convivir no se las pisa: los dos bloques leen los mismos leads una sola
# vez, comparten la cache de `@lid` (asi que los nombres no repiten ni un
# `check-exists`), escriben en endpoints distintos -las etiquetas en
# `labels/chats/...`, los nombres en `contacts/...`- y el unico archivo de
# estado (`sincronizador.estado.json`) lo toca solo el bloque de etiquetas.
# El orden tambien esta pensado: etiquetas primero, nombres despues, y el
# estado se guarda al final, cuando ya no queda nada que pueda reventar.
#
# ANOTAR O CALLARSE lo decide el CODIGO DE SALIDA del sincronizador:
#   0 = no hubo nada que hacer  -> no se anota (con 720 pasadas al dia, un
#       renglon «0 cambios» cada vez esconde justo lo que hay que ver)
#   2 = se cambio algo          -> se anota el resumen
#   1 = fallo algo              -> se anota el resumen y el detalle
# Antes esto se decidia buscando la cadena «0 chats etiquetados» en la
# salida, y el sincronizador imprime «0 chats de leads»: no calzaba nunca,
# el `exit 0` no ocurria jamas y el diario se llevo las 720 pasadas del
# dia. Por eso ahora no depende de ninguna frase.
#
# El secreto no aparece nunca: el sincronizador lo lee del .env por su
# cuenta y este script no lo toca. Nada de `set -x`.
set -uo pipefail
LOG=/home/hermes/waha/sincronizador.log
SALIDA="$(/usr/bin/python3 /home/hermes/waha/sincronizador.py \
    --aplicar --aplicar-nombres 2>&1)"
CODIGO=$?
RESUMEN="$(printf '%s\n' "$SALIDA" | grep -E "^APLICADO ·" | tail -1)"

# Nada que hacer y sin errores: no se anota.
if [ "$CODIGO" = "0" ]; then
  exit 0
fi

{
  echo "$(date '+%F %T') · ${RESUMEN:-sin resumen (codigo $CODIGO)}"
  # El detalle solo cuando fallo algo (cualquier codigo que no sea el 2 de
  # «hubo cambios»): un cambio limpio no necesita mas que su resumen.
  if [ "$CODIGO" != "2" ]; then
    printf '%s\n' "$SALIDA" | grep -E "ERROR|Traceback|Error" | head -8 | sed 's/^/    /'
  fi
} >> "$LOG"
