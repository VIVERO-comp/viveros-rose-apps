#!/usr/bin/env python3
"""Refresca la copia local del catálogo (sku -> descripción y guía del sitio)
desde el products.ts del frontend. Correr antes de cada deploy de
control-stock, igual que actualizar_fotos.sh: la pestaña Fichas precarga
estos textos como referencia de lo que hoy dice el sitio.

Uso:  python scripts/actualizar_catalogo.py
      CATALOGO_TS=/otra/ruta/products.ts python scripts/actualizar_catalogo.py
"""

import json
import os
import re
import sys
from pathlib import Path

AQUI = Path(__file__).resolve().parent.parent
ORIGEN = Path(os.environ.get(
    "CATALOGO_TS",
    AQUI / ".." / ".." / ".." / "viveros-rose-frontend" / "src" / "data" / "products.ts",
)).resolve()
DESTINO = AQUI / "app" / "datos_fichas" / "catalogo.json"


def descomillar(s):
    return s.replace("\\'", "'").replace("\\\\", "\\")


def main():
    contenido = ORIGEN.read_text(encoding="utf-8")

    # Cada producto es un bloque del array generado; con los campos basta un
    # regex por bloque (products.ts lo escribe generar_catalogo.py con un
    # formato fijo, no es TS arbitrario).
    bloques = re.findall(r"\{\s*sku:.*?\n  \},", contenido, re.DOTALL)
    catalogo = {}
    for bloque in bloques:
        def campo(nombre):
            m = re.search(rf"{nombre}:\s*\n?\s*'((?:[^'\\]|\\.)*)'", bloque)
            return descomillar(m.group(1)) if m else ""

        cuidado = re.search(
            r"care:\s*\{\s*light:\s*'((?:[^'\\]|\\.)*)',\s*water:\s*'((?:[^'\\]|\\.)*)',"
            r"\s*difficulty:\s*'((?:[^'\\]|\\.)*)'",
            bloque,
        )
        sku = campo("sku")
        if not sku:
            continue
        catalogo[sku] = {
            "descripcion": campo("description"),
            "luz": descomillar(cuidado.group(1)) if cuidado else "",
            "riego": descomillar(cuidado.group(2)) if cuidado else "",
            "dificultad": descomillar(cuidado.group(3)) if cuidado else "",
        }

    if not catalogo:
        sys.exit(f"No se encontraron productos en {ORIGEN}")

    DESTINO.parent.mkdir(parents=True, exist_ok=True)
    DESTINO.write_text(
        json.dumps(catalogo, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"Copiados {len(catalogo)} productos: {ORIGEN} -> {DESTINO}")


if __name__ == "__main__":
    main()
