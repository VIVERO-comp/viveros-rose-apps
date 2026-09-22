"""La pestaña Retail: a quién falta cotizar, facturar o entregar.

Kanban de los leads de venta (etiquetas "Plantas retail" y "Mayorista" del
equipo LEAD de Linear — los de SERVICIO no van aquí: esos viven en el log
del calendario). Diseño aprobado en el artefacto "Pestaña Retail"
(22/09/2026).

Qué guarda la app (SQLite de control-stock) y qué no:

- La ETAPA de cada lead (cotizar → facturar → entregar → entregado) y su
  FECHA DE ENTREGA. En el plan final la etapa se deriva de hechos reales
  (existe orden vinculada, existe factura); mientras ese amarre llega, la
  etapa se mueve con el drag del tablero y queda aquí.
- Nada más: los leads viven en Linear y las ventas en Odoo.

Al poner la fecha de entrega se crea la actividad **Entrega** en el
calendario (CALENDARIO ROSE real, o el de muestra en desarrollo): así el
cliente aparece en la semana y en el bloque "Por entregar" del menú del
calendario. La actividad se crea UNA vez (se recuerda su id).

Sin credenciales de Linear la pestaña corre con leads de muestra, igual
que el calendario.
"""

import time
from datetime import datetime

from . import calendario
from .datos import ZONA_PANAMA, _db

# Etiqueta del equipo LEAD -> clave de tipo (color de la pantalla).
ETIQUETAS_RETAIL = {"Plantas retail": "retail", "Mayorista": "mayorista"}
COLORES = {"retail": "#16a34a", "mayorista": "#dc2626"}
NOMBRES_TIPO = {"retail": "Plantas retail", "mayorista": "Mayorista"}

ETAPAS = [
    {"clave": "cotizar",   "titulo": "Por cotizar",
     "pie": "Escribieron y nadie les ha pasado precio."},
    {"clave": "facturar",  "titulo": "Cotizado · por facturar",
     "pie": "Tienen cotización; falta cerrar la venta."},
    {"clave": "entregar",  "titulo": "Facturado · por entregar",
     "pie": "Ya pagaron: esta columna no se puede olvidar."},
    {"clave": "entregado", "titulo": "Entregado",
     "pie": "Cerrados; aquí descansan."},
]
CLAVES_ETAPA = {e["clave"] for e in ETAPAS}

TTL_LEADS = 120

CONSULTA_RETAIL = """
query { issues(first: 80, filter: {
    team: { key: { eq: "LEAD" } }
    state: { type: { nin: ["completed", "canceled"] } }
  }) { nodes { id identifier title url createdAt labels { nodes { name } } } }
}
"""

_cache = {"en": 0, "dato": None}


def iniciar_tablas():
    with _db() as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS retail_etapas (
                ref TEXT PRIMARY KEY,
                etapa TEXT NOT NULL,
                entrega TEXT,
                actividad TEXT,
                actualizado TEXT NOT NULL
            )
        """)


# ---------------------------------------------------------------------------
# Lectura de leads (Linear o muestra), con el estado guardado ya pegado
# ---------------------------------------------------------------------------

_MUESTRA = [
    {"ref": "LEAD-48", "nombre": "Laura Porcell",      "tipo": "retail",    "dias": 1, "cel": "6512-8890", "url": ""},
    {"ref": "LEAD-47", "nombre": "Andrés",             "tipo": "retail",    "dias": 1, "cel": "6033-2211", "url": ""},
    {"ref": "LEAD-46", "nombre": "Jasmin",             "tipo": "retail",    "dias": 2, "cel": "6788-4102", "url": ""},
    {"ref": "LEAD-45", "nombre": "Kev",                "tipo": "retail",    "dias": 3, "cel": "6209-7754", "url": ""},
    {"ref": "LEAD-44", "nombre": "Soledad",            "tipo": "retail",    "dias": 3, "cel": "6455-1832", "url": ""},
    {"ref": "LEAD-43", "nombre": "Diana Caballero",    "tipo": "retail",    "dias": 4, "cel": "6114-9077", "url": ""},
    {"ref": "LEAD-42", "nombre": "Monica Gama",        "tipo": "retail",    "dias": 4, "cel": "6620-3348", "url": ""},
    {"ref": "LEAD-50", "nombre": "Diego Armando",      "tipo": "mayorista", "dias": 0, "cel": "6987-5510", "url": ""},
    {"ref": "LEAD-34", "nombre": "NC Renovando Vidas", "tipo": "mayorista", "dias": 8, "cel": "6740-0923", "url": ""},
]


def _crudos():
    """Los leads retail/mayorista vivos, sin el estado de la app todavía."""
    if not calendario.configurado():
        return [dict(l) for l in _MUESTRA]
    if _cache["dato"] is not None:
        if time.time() - _cache["en"] >= TTL_LEADS:
            calendario._en_fondo("retail", _buscar)
        return [dict(l) for l in _cache["dato"]]
    try:
        return [dict(l) for l in _buscar()]
    except calendario.ErrorCalendario:
        return []


def _buscar():
    filas = []
    for issue in calendario._pedir(CONSULTA_RETAIL)["issues"]["nodes"]:
        etiquetas = [l["name"] for l in issue["labels"]["nodes"]]
        etiqueta = next((e for e in etiquetas if e in ETIQUETAS_RETAIL), None)
        if not etiqueta:
            continue  # servicio y demás: no son de esta pestaña
        titulo = issue["title"] or ""
        nombre = titulo.split(" (PP-")[0].strip() or titulo
        dias = 0
        try:
            dias = (datetime.now(ZONA_PANAMA).date()
                    - datetime.fromisoformat(issue["createdAt"].replace("Z", "+00:00"))
                      .astimezone(ZONA_PANAMA).date()).days
        except (ValueError, KeyError, TypeError):
            pass
        filas.append({"ref": issue["identifier"], "nombre": nombre,
                      "tipo": ETIQUETAS_RETAIL[etiqueta], "dias": dias,
                      "cel": "", "url": issue.get("url") or ""})
    _cache.update({"en": time.time(), "dato": filas})
    return filas


def _estados():
    iniciar_tablas()  # cada base (la real o la fresca de una prueba) la trae
    with _db() as con:
        filas = con.execute(
            "SELECT ref, etapa, entrega, actividad FROM retail_etapas").fetchall()
    return {f[0]: {"etapa": f[1], "entrega": f[2], "actividad": f[3]} for f in filas}


def tablero():
    """[{clave, titulo, pie, leads: [...]}] con todo resuelto en Python."""
    estados = _estados()
    leads = []
    for lead in _crudos():
        estado = estados.get(lead["ref"], {})
        lead["etapa"] = estado.get("etapa") or "cotizar"
        lead["entrega"] = estado.get("entrega") or ""
        lead["color"] = COLORES[lead["tipo"]]
        lead["tipo_nombre"] = NOMBRES_TIPO[lead["tipo"]]
        lead["hace"] = ("hoy" if lead["dias"] <= 0
                        else f"hace {lead['dias']} día" + ("s" if lead["dias"] > 1 else ""))
        leads.append(lead)
    leads.sort(key=lambda l: -l["dias"])
    columnas = []
    for etapa in ETAPAS:
        columnas.append(dict(etapa, leads=[l for l in leads if l["etapa"] == etapa["clave"]]))
    return columnas, {l["ref"]: l for l in leads}


def mover(ref, etapa):
    if etapa not in CLAVES_ETAPA:
        return
    iniciar_tablas()
    ahora = datetime.now(ZONA_PANAMA).isoformat()
    with _db() as con:
        con.execute("""
            INSERT INTO retail_etapas (ref, etapa, actualizado) VALUES (?, ?, ?)
            ON CONFLICT(ref) DO UPDATE SET etapa = excluded.etapa,
                actualizado = excluded.actualizado
        """, (ref, etapa, ahora))


def poner_fecha(ref, nombre, entrega):
    """Guarda la fecha y crea (una sola vez) la actividad Entrega del
    calendario; si ya existía, la mueve a la fecha nueva."""
    iniciar_tablas()
    ahora = datetime.now(ZONA_PANAMA).isoformat()
    with _db() as con:
        fila = con.execute(
            "SELECT actividad FROM retail_etapas WHERE ref = ?", (ref,)).fetchone()
    actividad = fila[0] if fila else None
    try:
        if actividad:
            calendario.mover(actividad, entrega, "10:00")
        else:
            creada = calendario.crear(
                tipo="entrega", cliente=nombre, fecha=entrega, hora="10:00",
                dur=60, lugar="", resp_id="", prioridad=3,
                nota=f"Entrega del lead {ref} (pestaña Retail).")
            # En modo muestra crear() no devuelve el id del issue: se busca
            # por la referencia recién creada.
            actividad = _actividad_por_ref(creada.get("ref", ""))
    except calendario.ErrorCalendario:
        actividad = actividad  # sin calendario igual queda la fecha
    with _db() as con:
        con.execute("""
            INSERT INTO retail_etapas (ref, etapa, entrega, actividad, actualizado)
            VALUES (?, 'entregar', ?, ?, ?)
            ON CONFLICT(ref) DO UPDATE SET entrega = excluded.entrega,
                actividad = COALESCE(excluded.actividad, retail_etapas.actividad),
                etapa = 'entregar', actualizado = excluded.actualizado
        """, (ref, entrega, actividad, ahora))


def _actividad_por_ref(ref_actividad):
    if not ref_actividad:
        return None
    try:
        for a in calendario.listar("1970-01-01", "2100-01-01"):
            if a["ref"] == ref_actividad:
                return a["id"]
    except calendario.ErrorCalendario:
        pass
    return None


def por_entregar():
    """El bloque del menú del calendario: TODOS los facturados por
    entregar — sin fecha primero (en rojo), luego por fecha."""
    _columnas, por_ref = tablero()
    filas = [l for l in por_ref.values() if l["etapa"] == "entregar"]
    filas.sort(key=lambda l: (l["entrega"] or "0000", l["nombre"]))
    return filas
