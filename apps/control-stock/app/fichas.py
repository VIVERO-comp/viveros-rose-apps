"""Fichas de producto: descripción y guía de cuidado curadas para la tienda.

La pestaña Fichas es la casa de verdad del contenido editorial del catálogo
(descripción y guía de cuidado por producto): lo que se guarda aquí lo lee
el generador del catálogo (viveros-rose-frontend, scripts/generar_catalogo.py)
la próxima vez que el dueño regenera el sitio. Odoo sigue mandando en nombre,
precio y stock; aquí vive solo la prosa. Guardar una ficha NO cambia el sitio
al instante.

Las fichas viven en la base `tienda` (Postgres del droplet, TIENDA_DSN, tabla
fichas_producto — migración 015 del order-api). Sin TIENDA_DSN (desarrollo y
pruebas) se usa una tabla igual en el SQLite de la app: misma pantalla, cero
servicios extra.

Solo editan los usuarios listados en FICHAS_EDITORES (separados por coma);
para el resto la pestaña ni aparece. La referencia precargada (lo que hoy
dice el sitio) viene de app/datos_fichas/catalogo.json, una copia del
products.ts del frontend que refresca scripts/actualizar_catalogo.py antes
de cada deploy (mismo patrón que las fotos).
"""

import json
import os
from functools import lru_cache
from pathlib import Path

from . import datos

RUTA_CATALOGO = Path(__file__).parent / "datos_fichas" / "catalogo.json"

# Las mismas dificultades que el tipo Product del frontend.
DIFICULTADES = ("Facil", "Media", "Exigente")

# Topes holgados: una descripción de ficha ronda 200 caracteres y la meta
# del sitio usa solo las primeras oraciones; esto es red, no formato.
TOPES = {"descripcion": 600, "luz": 80, "riego": 80, "nota": 600}

CAMPOS = ("descripcion", "luz", "riego", "dificultad", "nota")


def editoras():
    """Usuarios que pueden ver y editar Fichas (FICHAS_EDITORES, por coma)."""
    crudo = os.environ.get("FICHAS_EDITORES", "")
    return {u.strip().lower() for u in crudo.split(",") if u.strip()}


def es_editora(usuario):
    # "*" = todos los usuarios de la app (decisión del dueño, 11/09/2026);
    # una lista por coma limita a esos usuarios; sin la variable, nadie.
    if os.environ.get("FICHAS_EDITORES", "").strip() == "*":
        return True
    return usuario in editoras()


@lru_cache(maxsize=4)
def _leer_catalogo(ruta, mtime):
    with open(ruta, encoding="utf-8") as f:
        return json.load(f)


def referencias():
    """{sku: {descripcion, luz, riego, dificultad}} tal como está en el sitio.

    Sin la copia del catálogo (desarrollo recién clonado) devuelve {} y la
    pantalla precarga vacío: molesto pero nunca roto.
    """
    ruta = os.environ.get("FICHAS_CATALOGO", str(RUTA_CATALOGO))
    try:
        return _leer_catalogo(ruta, os.path.getmtime(ruta))
    except OSError:
        return {}


def validar(campos):
    """Devuelve un mensaje de error o None si la ficha se puede guardar."""
    for campo, tope in TOPES.items():
        if len(campos.get(campo, "")) > tope:
            return f"El campo {campo} pasa de {tope} caracteres."
    if campos.get("dificultad", "") not in ("",) + DIFICULTADES:
        return "La dificultad debe ser Facil, Media o Exigente."
    if not campos.get("descripcion", "").strip():
        return "La descripción no puede quedar vacía."
    return None


def limpiar(crudo):
    """Los campos del formulario, recortados y con solo las llaves conocidas."""
    return {c: str(crudo.get(c, "") or "").strip() for c in CAMPOS}


# ---------------------------------------------------------------------------
# Almacenamiento: Postgres (tienda) o SQLite local como respaldo de desarrollo
# ---------------------------------------------------------------------------

def _dsn():
    return os.environ.get("TIENDA_DSN")


def _con_postgres():
    # Import perezoso: psycopg solo hace falta donde hay TIENDA_DSN (el
    # droplet); las pruebas y el desarrollo local usan SQLite.
    import psycopg

    return psycopg.connect(_dsn())


def _asegurar_tabla_sqlite(con):
    # Espejo local de la migración 015 del order-api, solo para desarrollo.
    con.execute("""
        CREATE TABLE IF NOT EXISTS fichas_producto (
            sku TEXT PRIMARY KEY,
            descripcion TEXT NOT NULL DEFAULT '',
            luz TEXT NOT NULL DEFAULT '',
            riego TEXT NOT NULL DEFAULT '',
            dificultad TEXT NOT NULL DEFAULT '',
            nota TEXT NOT NULL DEFAULT '',
            actualizado_por TEXT NOT NULL,
            actualizado_en TEXT NOT NULL
        )""")


_UPSERT = """
    INSERT INTO fichas_producto
        (sku, descripcion, luz, riego, dificultad, nota, actualizado_por, actualizado_en)
    VALUES ({p}, {p}, {p}, {p}, {p}, {p}, {p}, {ahora})
    ON CONFLICT (sku) DO UPDATE SET
        descripcion = EXCLUDED.descripcion,
        luz = EXCLUDED.luz,
        riego = EXCLUDED.riego,
        dificultad = EXCLUDED.dificultad,
        nota = EXCLUDED.nota,
        actualizado_por = EXCLUDED.actualizado_por,
        actualizado_en = {ahora}
"""


def guardar(sku, campos, usuario):
    valores = (sku, campos["descripcion"], campos["luz"], campos["riego"],
               campos["dificultad"], campos["nota"], usuario)
    if _dsn():
        with _con_postgres() as con:
            con.execute(_UPSERT.format(p="%s", ahora="now()"), valores)
    else:
        with datos._db() as con:
            _asegurar_tabla_sqlite(con)
            # {ahora} aparece dos veces en el SQL (INSERT y UPDATE del upsert).
            ahora = datos.ahora_iso()
            con.execute(_UPSERT.format(p="?", ahora="?"), valores + (ahora, ahora))


def todas():
    """{sku: ficha} de todo lo guardado, para precargar la pantalla."""
    consulta = ("SELECT sku, descripcion, luz, riego, dificultad, nota, "
                "actualizado_por, actualizado_en FROM fichas_producto")
    if _dsn():
        with _con_postgres() as con:
            filas = con.execute(consulta).fetchall()
            columnas = ("sku", "descripcion", "luz", "riego", "dificultad",
                        "nota", "actualizado_por", "actualizado_en")
            fichas = [dict(zip(columnas, f)) for f in filas]
    else:
        with datos._db() as con:
            _asegurar_tabla_sqlite(con)
            fichas = [dict(f) for f in con.execute(consulta).fetchall()]
    for f in fichas:
        f["actualizado_en"] = str(f["actualizado_en"])
    return {f.pop("sku"): f for f in fichas}
