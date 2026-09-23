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


# ---------------------------------------------------------------------------
# Calendario (pestana Calendario de control-stock). Los 13 tipos del grupo
# "Tipo de actividad" y los 4 filtros estilo Google Calendar.
# Valores actuales tal cual (Fase 3: los tipos con label en Twenty toman su
# familia; reunion/visita/cotizacion/seguimiento/compra/administrativo/otro
# son internos y se quedan).
# ---------------------------------------------------------------------------

TIPOS_CALENDARIO = [
    {"clave": "alquiler",       "nombre": "Alquiler",       "color": "#f97316"},  # naranja (dueño, 22/09/2026)
    {"clave": "mantenimiento",  "nombre": "Mantenimiento",  "color": "#2563eb"},  # azul (dueño, 22/09/2026)
    {"clave": "entrega",        "nombre": "Entrega",        "color": "#c9924f"},
    {"clave": "recogida",       "nombre": "Recogida",       "color": "#a9552f"},
    {"clave": "instalacion",    "nombre": "Instalación",    "color": "#3c6ea6"},
    {"clave": "proyecto",       "nombre": "Proyecto",       "color": "#5b55a6"},
    {"clave": "reunion",        "nombre": "Reunión",        "color": "#2f7d86"},
    {"clave": "visita",         "nombre": "Visita",         "color": "#8d6b3f"},
    {"clave": "cotizacion",     "nombre": "Cotización",     "color": "#9b5a86"},
    {"clave": "seguimiento",    "nombre": "Seguimiento",    "color": "#587a99"},
    {"clave": "compra",         "nombre": "Compra",         "color": "#5b7f6a"},
    {"clave": "administrativo", "nombre": "Administrativo", "color": "#6f6a5e"},
    {"clave": "otro",           "nombre": "Otro",           "color": "#8a8477"},
]

FILTROS_CALENDARIO = [
    {"clave": "eventos",        "nombre": "Eventos",        "tipos": ("alquiler", "recogida"),      "color": "#f97316"},
    {"clave": "paisajismo",     "nombre": "Paisajismo",     "tipos": ("proyecto", "instalacion"),   "color": "#5b55a6"},
    {"clave": "mantenimiento",  "nombre": "Mantenimiento",  "tipos": ("mantenimiento",),            "color": "#2563eb"},
    {"clave": "entrega-retail", "nombre": "Entrega retail", "tipos": ("entrega",),                  "color": "#c9924f"},
]

# ---------------------------------------------------------------------------
# Retail (kanban de leads retail). Valores actuales (Fase 3: Mayorista pasa
# a la familia orange).
# ---------------------------------------------------------------------------

COLORES_RETAIL = {"retail": "#16a34a", "mayorista": "#dc2626"}

# ---------------------------------------------------------------------------
# Calendario con piel de Twenty (/crm/calendario). El color de cada tipo y
# de cada etiqueta como los pinta Twenty. Valores actuales (Fase 3 corrige
# Mayorista, Bodas y Ferias con su familia real).
# ---------------------------------------------------------------------------

COLORES_CRM_CALENDARIO = {
    "alquiler": "#dc2626",   # rojo del label "Eventos · Alquiler"
    "recogida": "#7f1d1d",   # la recogida es parte del alquiler: mismo rojo, oscuro
    "entrega": "#16a34a",    # verde del label "Plantas retail"
    # mantenimiento ya es #2563eb (el azul del label) en TIPOS_CALENDARIO
}

ETIQUETAS_CHIP_CRM = {
    "Eventos · Alquiler": "#dc2626",
    "Eventos · Bodas": "#db2777",
    "Eventos · Ferias": "#d97706",
    "Mantenimiento": "#2563eb",
    "Plantas retail": "#16a34a",
    "Mayorista": "#7c3aed",
}
CHIP_CRM_SIN_ETIQUETA = "#6b6b70"
