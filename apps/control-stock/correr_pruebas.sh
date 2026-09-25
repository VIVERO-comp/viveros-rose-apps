#!/bin/bash
# Las pruebas de ESTE worktree, no las del repo original.
#
# La .venv vive en el checkout principal y trae el paquete `app` instalado
# apuntando ALLA. Sin forzar PYTHONPATH, pytest recoge los tests de aqui
# pero importa el codigo de alla: los tests nuevos corren contra el codigo
# viejo y fallan por nada. (Paso el 25/09/2026 y costo un rato.)
cd "$(dirname "$0")"
PYTHONPATH="$PWD" \
  /Users/abrahamkortovich/proyectos/viveros-rose-apps/apps/control-stock/.venv/bin/python \
  -m pytest "$@"
