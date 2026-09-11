"""Siembra fichas_producto con los textos que hoy tiene el sitio.

Se corre UNA vez al estrenar la pestaña Fichas (y es inofensivo repetirlo):
inserta la descripción y guía actuales del catálogo (datos_fichas/catalogo.json)
para cada SKU que todavía no tiene ficha, sin tocar las ya editadas. Así la
primera regeneración del catálogo no pierde ningún texto aunque nadie haya
curado nada aún.

En producción:  docker compose exec control-stock python -m app.sembrar_fichas
"""

from . import fichas


def sembrar():
    referencias = fichas.referencias()
    if not referencias:
        raise SystemExit("No hay datos_fichas/catalogo.json: nada que sembrar.")
    existentes = set(fichas.todas())
    sembradas = 0
    for sku, ref in sorted(referencias.items()):
        if sku in existentes or not ref.get("descripcion"):
            continue
        campos = fichas.limpiar(ref)
        fichas.guardar(sku, campos, "seed-catalogo")
        sembradas += 1
    print(f"Fichas sembradas: {sembradas} (ya existían: {len(existentes)})")


if __name__ == "__main__":
    sembrar()
