"""URLs de las fotos de producto en Cloudinary.

Las fotos las publica el pipeline del catálogo (viveros-rose-frontend,
scripts/fotos.py) con public_id `productos/{SKU}/{hash}`; el mapa sku → hashes
vive en el fotos.json de ese repo. Aquí se usa una copia local
(app/datos_fotos/fotos.json) que se refresca en cada deploy con
scripts/actualizar_fotos.sh: una foto nueva aparece en inventario recién en
el siguiente deploy, y eso está bien para una herramienta interna.

Sin foto (o sin la copia del json) se devuelve None y la pantalla cae al
emoji de siempre: nunca se rompe el layout por una foto que falta.
"""

import json
from functools import lru_cache
from pathlib import Path

RUTA_FOTOS = Path(__file__).parent / "datos_fotos" / "fotos.json"

# Mismas transformaciones que la tarjeta del catálogo pero en miniatura
# cuadrada: la caja .foto mide 46px, w_160 alcanza también para el modal
# en pantallas retina.
_TRANSFORMACION = "f_auto,q_auto,c_fill,g_auto,ar_1:1,w_160"


@lru_cache(maxsize=1)
def _datos():
    try:
        return json.loads(RUTA_FOTOS.read_text())
    except (OSError, ValueError):
        return {}


def reiniciar_cache_fotos():
    """Solo para pruebas."""
    _datos.cache_clear()


def _recorte(datos, hash_foto):
    """El recorte manual decidido en la página de revisión del catálogo se
    antepone como c_crop, igual que hace fotos.ts del frontend, para que
    inventario vea la misma foto ya recortada."""
    rec = (datos.get("recortesPorHash") or {}).get(hash_foto)
    if not rec:
        return ""
    x, y, w, h = rec
    return f"c_crop,x_{x},y_{y},w_{w},h_{h}/"


def url_foto(sku):
    """URL de la foto principal del SKU, o None si no tiene foto."""
    datos = _datos()
    hashes = (datos.get("porSku") or {}).get(sku) or []
    if not datos.get("cloud") or not hashes:
        return None
    principal = hashes[0]
    return (f"https://res.cloudinary.com/{datos['cloud']}/image/upload/"
            f"{_recorte(datos, principal)}{_TRANSFORMACION}/productos/{sku}/{principal}")
