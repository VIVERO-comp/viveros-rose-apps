"""URLs de las fotos de producto en Cloudinary.

Las fotos las publica el pipeline del catálogo (viveros-rose-frontend,
scripts/fotos.py) con public_id `productos/{SKU}/{hash}`; el mapa sku → hashes
vive en el fotos.json de ese repo. Aquí se usa una copia local
(app/datos_fotos/fotos.json) que se refresca en cada deploy con
scripts/actualizar_fotos.sh: una foto nueva aparece en inventario recién en
el siguiente deploy, y eso está bien para una herramienta interna.

Hay ademas fotos SOLO de las apps internas (datos_fotos/fotos-apps.json):
fotos reales que el dueño no quiere en la tienda publica. Viven en
Cloudinary bajo `apps/{SKU}/{hash}` (namespace aparte, para que el pipeline
del catalogo nunca las publique) y aqui GANAN sobre la foto del catalogo.

Desde la pantalla tambien se puede CAMBIAR la foto (el pincel del modal):
la nueva sube a Cloudinary bajo `apps/{SKU}/{hash}` (siempre el namespace
interno: la tienda publica jamas la ve) y el puntero sku -> hash queda en la
base SQLite de la app (datos.fotos_subidas), que gana sobre los dos json.
La foto anterior no se borra de Cloudinary; solo se deja de apuntar.

Sin foto (o sin la copia del json) se devuelve None y la pantalla cae al
emoji de siempre: nunca se rompe el layout por una foto que falta.
"""

import hashlib
import json
import os
import time
from functools import lru_cache
from pathlib import Path

import httpx

RUTA_FOTOS = Path(__file__).parent / "datos_fotos" / "fotos.json"
RUTA_FOTOS_APPS = Path(__file__).parent / "datos_fotos" / "fotos-apps.json"

# Mismas transformaciones que la tarjeta del catálogo pero en miniatura
# cuadrada: la caja .foto mide 46px, w_160 alcanza también para el modal
# en pantallas retina.
_TRANSFORMACION = "f_auto,q_auto,c_fill,g_auto,ar_1:1,w_160"

# Para el modal de foto ampliada: la foto entera (sin recorte cuadrado),
# limitada a 900px para no bajar el original completo al teléfono.
_TRANSFORMACION_GRANDE = "f_auto,q_auto,c_limit,w_900"


@lru_cache(maxsize=1)
def _datos():
    try:
        return json.loads(RUTA_FOTOS.read_text())
    except (OSError, ValueError):
        return {}


@lru_cache(maxsize=1)
def _datos_apps():
    try:
        return json.loads(RUTA_FOTOS_APPS.read_text())
    except (OSError, ValueError):
        return {}


def reiniciar_cache_fotos():
    """Solo para pruebas."""
    _datos.cache_clear()
    _datos_apps.cache_clear()


def _recorte(datos, hash_foto):
    """El recorte manual decidido en la página de revisión del catálogo se
    antepone como c_crop, igual que hace fotos.ts del frontend, para que
    inventario vea la misma foto ya recortada."""
    rec = (datos.get("recortesPorHash") or {}).get(hash_foto)
    if not rec:
        return ""
    x, y, w, h = rec
    return f"c_crop,x_{x},y_{y},w_{w},h_{h}/"


def _fuente(sku, hash_subido=None):
    """(cloud, prefijo de recorte, public_id) de la foto que gana para el
    SKU, o None si no hay ninguna. Orden: subida desde la app > solo-apps
    (fotos-apps.json) > catálogo (fotos.json)."""
    if hash_subido:
        cloud = (os.environ.get("CLOUDINARY_CLOUD_NAME")
                 or _datos_apps().get("cloud") or _datos().get("cloud"))
        if cloud:
            return cloud, "", f"apps/{sku}/{hash_subido}"
    apps = _datos_apps()
    hashes_apps = (apps.get("porSku") or {}).get(sku) or []
    if apps.get("cloud") and hashes_apps:
        return apps["cloud"], "", f"apps/{sku}/{hashes_apps[0]}"
    datos = _datos()
    hashes = (datos.get("porSku") or {}).get(sku) or []
    if not datos.get("cloud") or not hashes:
        return None
    principal = hashes[0]
    return datos["cloud"], _recorte(datos, principal), f"productos/{sku}/{principal}"


def info_foto(sku, hash_subido=None):
    """URLs de la foto del SKU: {img: miniatura, grande: para el modal,
    descarga: original con Content-Disposition attachment}, o None.

    hash_subido es el puntero de datos.fotos_subidas() (la foto cambiada
    desde la propia app), que gana sobre los json."""
    fuente = _fuente(sku, hash_subido)
    if fuente is None:
        return None
    cloud, recorte, public_id = fuente
    base = f"https://res.cloudinary.com/{cloud}/image/upload/"
    return {
        "img": f"{base}{recorte}{_TRANSFORMACION}/{public_id}",
        "grande": f"{base}{recorte}{_TRANSFORMACION_GRANDE}/{public_id}",
        # fl_attachment fuerza la descarga con nombre {sku}.{ext}: es el
        # botón "Descargar" del modal (el dueño baja la foto en su compu).
        "descarga": f"{base}{recorte}fl_attachment:{sku}/{public_id}",
    }


def url_foto(sku, hash_subido=None):
    """URL de la foto principal del SKU (miniatura), o None si no tiene."""
    info = info_foto(sku, hash_subido)
    return info["img"] if info else None


# ---------------------------------------------------------------------------
# Subida a Cloudinary (el pincel del modal de foto)
# ---------------------------------------------------------------------------

def subida_configurada():
    """True si el .env trae las credenciales de Cloudinary; sin ellas el
    pincel no se ofrece y la pantalla queda solo de consulta/zoom."""
    return all(os.environ.get(v) for v in (
        "CLOUDINARY_CLOUD_NAME", "CLOUDINARY_API_KEY", "CLOUDINARY_API_SECRET"))


def subir_foto(contenido, sku):
    """Sube la foto a Cloudinary como apps/{sku}/{hash} y devuelve el hash.

    Mismo esquema que el pipeline del catálogo (scripts/fotos.py del
    frontend): public_id por sha1 del contenido (12 hex) y subida firmada
    sin SDK. Siempre bajo apps/: el pipeline del catálogo nunca publica ese
    namespace, así que la tienda no cambia. Errores suben como RuntimeError."""
    hash_foto = hashlib.sha1(contenido).hexdigest()[:12]
    public_id = f"apps/{sku}/{hash_foto}"
    marca = str(int(time.time()))
    base_firma = f"public_id={public_id}&timestamp={marca}"
    firma = hashlib.sha1(
        (base_firma + os.environ["CLOUDINARY_API_SECRET"]).encode()).hexdigest()
    try:
        respuesta = httpx.post(
            f"https://api.cloudinary.com/v1_1/{os.environ['CLOUDINARY_CLOUD_NAME']}/image/upload",
            data={
                "api_key": os.environ["CLOUDINARY_API_KEY"],
                "timestamp": marca,
                "public_id": public_id,
                "signature": firma,
            },
            files={"file": (sku, contenido)},
            timeout=60,
        )
    except Exception as error:
        raise RuntimeError(f"Sin conexión con Cloudinary: {error}") from error
    if respuesta.status_code != 200:
        raise RuntimeError(f"Cloudinary respondió {respuesta.status_code}: "
                           f"{respuesta.text[:200]}")
    return hash_foto
