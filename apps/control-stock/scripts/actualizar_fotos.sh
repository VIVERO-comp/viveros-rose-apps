#!/bin/sh
# Refresca la copia local del mapa de fotos (sku -> hashes de Cloudinary)
# desde el repo del catálogo. Correr antes de cada deploy de control-stock:
# las fotos nuevas del catálogo aparecen en inventario recién con esto.
set -eu

AQUI="$(cd "$(dirname "$0")/.." && pwd)"
ORIGEN="${FOTOS_JSON:-$AQUI/../../../viveros-rose-frontend/src/data/fotos.json}"

cp "$ORIGEN" "$AQUI/app/datos_fotos/fotos.json"
echo "Copiado $ORIGEN -> app/datos_fotos/fotos.json"
