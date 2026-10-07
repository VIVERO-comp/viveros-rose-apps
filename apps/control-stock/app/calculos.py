"""Cálculos puros de la app: estados, score de salud y emojis.

Sin I/O a propósito: todo lo que decide cómo se pinta el inventario vive
aquí y se prueba sin base ni red.
"""

import unicodedata

# El score parte de 100 y resta por lo que duele: un producto crítico casi
# se acabó (se pierde la venta), uno bajo va camino a eso, y un conteo
# quincenal vencido significa que los números ya no son confiables. Los
# agotados no restan: quedarse sin una planta puede ser a propósito (se
# vendió todo), y ya se ven en su propio contador.
PENALIZACION_CRITICO = 6
PENALIZACION_BAJO = 2
PENALIZACION_CONTEO_VENCIDO = 15
DIAS_CONTEO_QUINCENAL = 15

UMBRAL_DEFECTO = 3

# Emojis para la tarjeta del producto, como el prototipo: casos especiales
# por palabra clave (la misma idea del generador del catálogo del frontend)
# y una maceta como comodín.
_EMOJIS_ESPECIALES = [
    ("CACTUS", "🌵"), ("SUCULENTA", "🌵"), ("PALMA", "🌴"), ("ROSA", "🌹"),
    ("ROSITA", "🌹"), ("HELECHO", "🌿"), ("MENTA", "🌿"), ("ALBAHACA", "🌿"),
    ("OREGANO", "🌿"), ("ORÉGANO", "🌿"), ("ROMERO", "🌿"), ("TOMILLO", "🌿"),
    ("HIERBA", "🌿"), ("LIMON", "🍋"), ("LIMÓN", "🍋"), ("PAPAYA", "🍋"),
    ("IXORA", "🌺"), ("VERANERA", "🌺"), ("CROTON", "🌺"), ("CROTO", "🌺"),
]
EMOJI_DEFECTO = "🪴"

# Emoji de las tarjetas por categoria: las tres del sitio web (Interior,
# Exterior, Florales), que son las unicas que existen en Odoo. Se busca por
# fragmento para no depender de mayusculas o acentos exactos de Odoo.
_EMOJIS_CATEGORIA = [
    ("floral", "🌺"), ("interior", "🪴"), ("exterior", "🌳"),
]


def emoji_categoria(nombre):
    plano = "".join(c for c in unicodedata.normalize("NFD", (nombre or "").lower())
                    if unicodedata.category(c) != "Mn")
    for fragmento, emoji in _EMOJIS_CATEGORIA:
        if fragmento in plano:
            return emoji
    return EMOJI_DEFECTO


def emoji_de(nombre):
    mayus = (nombre or "").upper()
    for palabra, emoji in _EMOJIS_ESPECIALES:
        if palabra in mayus:
            return emoji
    return EMOJI_DEFECTO


# ---------------------------------------------------------------------------
# EL formateador de dinero de la app. UNO SOLO (7/10/2026)
#
# Hasta hoy convivían dos: el filtro `dinero` de las plantillas
# (main.dinero_venta, SIN coma de miles) y media docena de f-strings con
# `:,.2f` sueltas por los módulos. La misma pantalla llegó a pintar
# «$50,403.00» arriba y «$1522.50» tres renglones abajo — el mismo dinero
# con dos caras, que es justo lo que hace dudar de una cifra.
#
# Regla, para toda la app: **coma de miles y dos decimales, siempre.**
# Quien tenga que pintar un monto lo pasa por aquí: las plantillas con el
# filtro `dinero` (que es esta misma función, registrada en main.py) y
# Python llamándola directo. Nadie vuelve a escribir el formato a mano —
# hay una prueba que recorre el código y las pantallas y falla si aparece
# un monto sin coma (`tests/test_dinero_formato.py`).
#
# Vive en `calculos` porque es el único módulo sin un solo import de la
# casa: lo puede llamar cualquiera (control, finanzas, revisar, pedidos…)
# sin armar un ciclo.
# ---------------------------------------------------------------------------

def dinero(monto):
    """El ÚNICO formato de dinero de la app: `$1,522.50`.

    `None` sale como cadena vacía a propósito: «no se sabe» no es cero, y
    quien llama decide qué palabra poner en su lugar («sin dato»,
    «Todavía no»). Nunca inventa un $0.00.
    """
    if monto is None:
        return ""
    return f"${float(monto):,.2f}"


# Misma regla del catálogo: un precio menor a $1.00 es un marcador de
# "todavía sin precio real" en Odoo, no un precio.
_PRECIO_MINIMO_CENTAVOS = 100


def precio_online(centavos):
    """El list_price de Odoo tal cual, formateado "$X.XX". None si el
    producto aún no tiene precio real (0 o marcador menor a $1.00)."""
    if not centavos or centavos < _PRECIO_MINIMO_CENTAVOS:
        return None
    return dinero(centavos / 100)


def estado(disponible, umbral):
    """agotada | critico | bajo | ok, sobre el disponible (lo vendible)."""
    if disponible <= 0:
        return "agotada"
    if disponible < umbral:
        return "critico"
    if disponible < umbral * 2:
        return "bajo"
    return "ok"


def es_negativo(producto):
    """Físico negativo en Odoo: se vendió sin existencias registradas. Es un
    error de datos que el encargado debe corregir, así que cuenta como
    crítico (no como agotada) en score, alertas y tarjetas."""
    return producto.get("fisico", producto["disponible"]) < 0


def clasificar(inventario, umbral):
    """Cuenta el inventario por estado. Devuelve un dict con los totales que
    usan el score y la pantalla de inicio."""
    cuentas = {"agotadas": 0, "criticos": 0, "bajos": 0, "ok": 0}
    unidades = 0
    for producto in inventario:
        if es_negativo(producto):
            clave = "criticos"
        else:
            clave = {"agotada": "agotadas", "critico": "criticos",
                     "bajo": "bajos", "ok": "ok"}[estado(producto["disponible"], umbral)]
        cuentas[clave] += 1
        unidades += max(0, producto["disponible"])
    cuentas["unidades"] = unidades
    cuentas["con_stock"] = len(inventario) - cuentas["agotadas"]
    return cuentas


def score(criticos, bajos, conteo_vencido):
    """Salud del stock de 0 a 100."""
    puntos = 100
    puntos -= criticos * PENALIZACION_CRITICO
    puntos -= bajos * PENALIZACION_BAJO
    if conteo_vencido:
        puntos -= PENALIZACION_CONTEO_VENCIDO
    return max(0, min(100, puntos))
