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

# Emoji de las tarjetas por categoria (los del prototipo). Se busca por
# fragmento para no depender de mayusculas o acentos exactos de Odoo.
_EMOJIS_CATEGORIA = [
    ("aromatic", "🌿"), ("floral", "🌺"), ("frutal", "🍋"),
    ("interior", "🪴"), ("exterior", "🌳"),
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


def estado(disponible, umbral):
    """agotada | critico | bajo | ok, sobre el disponible (lo vendible)."""
    if disponible <= 0:
        return "agotada"
    if disponible < umbral:
        return "critico"
    if disponible < umbral * 2:
        return "bajo"
    return "ok"


def clasificar(inventario, umbral):
    """Cuenta el inventario por estado. Devuelve un dict con los totales que
    usan el score y la pantalla de inicio."""
    cuentas = {"agotadas": 0, "criticos": 0, "bajos": 0, "ok": 0}
    unidades = 0
    for producto in inventario:
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
