"""La paleta unica de colores de las apps internas.

Fuente de verdad: paleta.json (este directorio), copia identica de la de
viveros-rose-frontend/src/data/paleta.json — las familias de color de
Twenty v2.39.5 y que cosa del negocio lleva cada familia (ver
docs/plan-colores.md). scripts/verificar_paleta.py comprueba que las dos
copias no se desalineen.

Ademas de la paleta, aqui viven TODOS los mapas de color de las pantallas
(antes sueltos en calendario.py, retail.py y crm_twenty.py). En la Fase 1
los valores quedan COMO ESTABAN — alinear cada pantalla a su familia es la
Fase 3 del plan; este modulo solo junta todo en un lugar.
"""

import json
import re
from pathlib import Path

_RUTA_PALETA = Path(__file__).with_name("paleta.json")

PALETA = json.loads(_RUTA_PALETA.read_text())
FAMILIAS = PALETA["familias"]
ASIGNACIONES = PALETA["asignaciones"]


def familia(nombre):
    """Los 5 colores de una familia (bg/texto claro y oscuro + solido)."""
    return FAMILIAS[nombre]


def solido(nombre):
    """El tono fuerte de una familia (puntos, bordes, chips solidos)."""
    return FAMILIAS[nombre]["solido"]


def hex_de(valor_p3):
    """Un color display-p3 de la paleta como hex sRGB.

    La misma conversion colorimetrica exacta con que se derivo solido_hex
    (sRGB transfer, matrices P3->XYZ D65->sRGB, recorte al gamut). Aqui se
    usa para los tonos de TEXTO, que _tinta() y los estilos inline de las
    plantillas necesitan en hex.
    """
    r, g, b = (float(x) for x in re.match(
        r"color\(display-p3 ([\d.]+) ([\d.]+) ([\d.]+)\)", valor_p3).groups())

    def dec(c):
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    def enc(c):
        c = min(1.0, max(0.0, c))
        return 12.92 * c if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055

    rl, gl, bl = dec(r), dec(g), dec(b)
    x = 0.4865709 * rl + 0.2656677 * gl + 0.1982173 * bl
    y = 0.2289746 * rl + 0.6917385 * gl + 0.0792869 * bl
    z = 0.0451134 * gl + 1.0439444 * bl
    rs = 3.2404542 * x - 1.5371385 * y - 0.4985314 * z
    gs = -0.9692660 * x + 1.8760108 * y + 0.0415560 * z
    bs = 0.0556434 * x - 0.2040259 * y + 1.0572252 * z
    return "#%02x%02x%02x" % tuple(round(enc(c) * 255) for c in (rs, gs, bs))


def texto_hex(nombre):
    """El tono de TEXTO claro de una familia, en hex (legible sobre tinta)."""
    return hex_de(FAMILIAS[nombre]["texto_claro"])


# ---------------------------------------------------------------------------
# Calendario (pestana Calendario de control-stock). Los 13 tipos del grupo
# "Tipo de actividad" y los 4 filtros estilo Google Calendar.
# Fase 3 del plan: los tipos con label en Twenty llevan el tono de TEXTO de
# su familia (legible sobre la tinta que hornea _tinta); la recogida es la
# cara oscura del rojo del alquiler (acordado 22/09/2026). Los tipos
# internos (reunion, visita, cotizacion, seguimiento, compra,
# administrativo, otro) no tienen label en Twenty y conservan sus tierras.
# ---------------------------------------------------------------------------

ROJO_RECOGIDA = "#8f2b28"  # familia red, mas oscuro que el texto del alquiler

TIPOS_CALENDARIO = [
    {"clave": "alquiler",       "nombre": "Alquiler",       "color": texto_hex("red")},
    {"clave": "mantenimiento",  "nombre": "Mantenimiento",  "color": texto_hex("blue")},
    {"clave": "entrega",        "nombre": "Entrega",        "color": texto_hex("green")},
    {"clave": "recogida",       "nombre": "Recogida",       "color": ROJO_RECOGIDA},
    {"clave": "instalacion",    "nombre": "Instalación",    "color": texto_hex("purple")},
    {"clave": "proyecto",       "nombre": "Proyecto",       "color": texto_hex("sky")},
    {"clave": "reunion",        "nombre": "Reunión",        "color": "#2f7d86"},
    {"clave": "visita",         "nombre": "Visita",         "color": "#8d6b3f"},
    {"clave": "cotizacion",     "nombre": "Cotización",     "color": "#9b5a86"},
    {"clave": "seguimiento",    "nombre": "Seguimiento",    "color": "#587a99"},
    {"clave": "compra",         "nombre": "Compra",         "color": "#5b7f6a"},
    {"clave": "administrativo", "nombre": "Administrativo", "color": "#6f6a5e"},
    {"clave": "otro",           "nombre": "Otro",           "color": "#8a8477"},
]

FILTROS_CALENDARIO = [
    {"clave": "eventos",        "nombre": "Eventos",        "tipos": ("alquiler", "recogida"),      "color": texto_hex("red")},
    {"clave": "paisajismo",     "nombre": "Paisajismo",     "tipos": ("proyecto", "instalacion"),   "color": texto_hex("turquoise")},
    {"clave": "mantenimiento",  "nombre": "Mantenimiento",  "tipos": ("mantenimiento",),            "color": texto_hex("blue")},
    {"clave": "entrega-retail", "nombre": "Entrega retail", "tipos": ("entrega",),                  "color": texto_hex("green")},
]

# ---------------------------------------------------------------------------
# Retail (kanban de leads retail): el tono FUERTE de la familia, para el
# punto de la tarjeta y el chip solido con texto blanco de la ficha.
# Fase 3: Mayorista deja el rojo (chocaba con Eventos · Alquiler) y toma su
# naranja de Twenty.
# ---------------------------------------------------------------------------

COLORES_RETAIL = {
    "retail": FAMILIAS["green"]["solido_hex"],
    "mayorista": FAMILIAS["orange"]["solido_hex"],
}

# ---------------------------------------------------------------------------
# Calendario con piel de Twenty (/crm/calendario). Desde la Fase 3 el
# calendario de inventario YA lleva los colores de los labels, asi que la
# piel no repinta ningun tipo: mismo color en las dos caras.
# ---------------------------------------------------------------------------

COLORES_CRM_CALENDARIO = {}

# El chip de cada etiqueta del equipo LEAD, con el tono de texto de su
# familia (el CSS le pone el fondo con color-mix al 12%).
ETIQUETAS_CHIP_CRM = {
    "Eventos · Alquiler": texto_hex("red"),
    "Eventos · Bodas": texto_hex("yellow"),
    "Eventos · Ferias": texto_hex("gray"),
    "Mantenimiento": texto_hex("blue"),
    "Plantas retail": texto_hex("green"),
    "Mayorista": texto_hex("orange"),
}
CHIP_CRM_SIN_ETIQUETA = "#6b6b70"
