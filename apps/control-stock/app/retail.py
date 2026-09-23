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

import re
import time
from datetime import datetime

from . import calendario, colores
from .datos import ZONA_PANAMA, _db

# Etiqueta del equipo LEAD -> clave de tipo (color de la pantalla).
# Los colores viven en colores.py (la paleta unica).
ETIQUETAS_RETAIL = {"Plantas retail": "retail", "Mayorista": "mayorista"}
COLORES = colores.COLORES_RETAIL
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
INDICE_ETAPA = {e["clave"]: indice for indice, e in enumerate(ETAPAS)}

TTL_LEADS = 120

CONSULTA_RETAIL = """
query { issues(first: 80, filter: {
    team: { key: { eq: "LEAD" } }
    state: { type: { nin: ["completed", "canceled"] } }
  }) { nodes { id identifier title description url createdAt labels { nodes { name } } } }
}
"""

_cache = {"en": 0, "dato": None}


def refrescar():
    """Olvida el caché de leads: la próxima vista trae Linear fresco. Lo
    llama Vender cuando el espejo del CRM acaba de abrir o mover un lead —
    una venta recién hecha no puede tardar 2 minutos (el TTL) en salir en
    su columna."""
    _cache.update({"en": 0, "dato": None})


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


def _cel_de(descripcion):
    """El celular del cliente, sacado de la tarjeta del issue (el link de
    WhatsApp o el bloque Cliente). Vuelve como "6999-9901", o "" si la
    tarjeta no trae teléfono."""
    texto = descripcion or ""
    encontrado = re.search(r"wa\.me/(\d{8,15})", texto)
    digitos = encontrado.group(1)[-8:] if encontrado else ""
    if not digitos:
        encontrado = re.search(r"\+507\s?(\d{4})[- ]?(\d{4})", texto)
        digitos = (encontrado.group(1) + encontrado.group(2)) if encontrado else ""
    return f"{digitos[:4]}-{digitos[4:]}" if len(digitos) == 8 else ""


def _digitos(texto):
    return re.sub(r"\D", "", texto or "")


def candidatas_para(lead, limite=6):
    """Las cotizaciones/ventas de Vender sin lead que PERTENECEN a este
    lead: mismo celular (últimos 8 dígitos) o, en su defecto, mismo nombre
    real. Corrección de Abraham (22/09/2026): la ficha nunca ofrece
    vincular cotizaciones de otros clientes, y un lead anónimo sin celular
    no ofrece nada."""
    from . import cotizaciones, ventas  # aquí abajo para no ciclar imports
    objetivo = _digitos(lead.get("cel"))[-8:]
    nombre = (lead.get("nombre") or "").strip().lower()
    if nombre in {n.lower() for n in NOMBRES_TIPO.values()}:
        nombre = ""  # provisional: el título era el tipo, no un cliente
    if not objetivo and not nombre:
        return []

    def es_suya(fila):
        cel = _digitos(fila.get("celular"))[-8:]
        if objetivo and cel and cel == objetivo:
            return True
        cliente = (fila.get("cliente") or "").strip().lower()
        return bool(nombre) and cliente == nombre

    filas = []
    for c in cotizaciones.sin_lead():
        if es_suya(c):
            filas.append({"clase": "servicio", "n": c["n"], "orden": c["orden"],
                          "cliente": c["cliente"], "total": c["total"],
                          "creado_en": c["creado_en"],
                          "etiqueta": cotizaciones.etiqueta_de(c["tipo"])})
    for v in ventas.sin_lead():
        if es_suya(v):
            filas.append({"clase": "venta", "n": v["n"],
                          "orden": v["orden"] or f"Venta {v['n']}",
                          "cliente": v["cliente"], "total": v["total"],
                          "creado_en": v["creado_en"], "etiqueta": "Venta local"})
    filas.sort(key=lambda f: f["creado_en"], reverse=True)
    return filas[:limite]


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
                      "cel": _cel_de(issue.get("description")),
                      "url": issue.get("url") or ""})
    _cache.update({"en": time.time(), "dato": filas})
    return filas


def _estados():
    iniciar_tablas()  # cada base (la real o la fresca de una prueba) la trae
    with _db() as con:
        filas = con.execute(
            "SELECT ref, etapa, entrega, actividad FROM retail_etapas").fetchall()
    return {f[0]: {"etapa": f[1], "entrega": f[2], "actividad": f[3]} for f in filas}


def _vinculos():
    """{LEAD-NN: [registros de Vender vinculados]} — cotizaciones de
    servicio y ventas locales, normalizadas para la ficha del lead. Todo
    sale de SQLite: la pantalla nunca espera a Odoo por esto."""
    from . import cotizaciones, ventas  # aquí abajo para no ciclar imports
    cotizaciones.iniciar_tablas()  # cada base (real o de prueba) las trae
    ventas.iniciar_tablas()
    juntos = {}
    for ref, filas in cotizaciones.vinculadas_por_lead().items():
        for f in filas:
            juntos.setdefault(ref, []).append({
                "clase": "servicio", "n": f["n"], "orden": f["orden"],
                "cliente": f["cliente"], "total": f["total"],
                "creado_en": f["creado_en"],
                "etiqueta": cotizaciones.etiqueta_de(f["tipo"]),
                "pdf": f"/venta/servicio/{f['n']}/propuesta.pdf",
                "facturada": False})
    for ref, filas in ventas.vinculadas_por_lead().items():
        for f in filas:
            facturada = bool(f.get("factura_id")) or f.get("estado") in ("facturada", "pagado")
            juntos.setdefault(ref, []).append({
                "clase": "venta", "n": f["n"], "orden": f["orden"] or f"Venta {f['n']}",
                "cliente": f["cliente"], "total": f["total"],
                "creado_en": f["creado_en"],
                "etiqueta": "Venta local",
                "pdf": (f"/venta/{f['n']}/factura.pdf" if facturada
                        else f"/venta/{f['n']}/cotizacion.pdf"),
                "facturada": facturada})
    for filas in juntos.values():
        filas.sort(key=lambda r: r["creado_en"], reverse=True)
    return juntos


def _etapa_derivada(registros):
    """La etapa que los hechos de Vender imponen como mínimo: con una
    cotización vinculada el lead ya está "Cotizado"; con una factura o un
    pago, "Facturado · por entregar". None si no hay nada vinculado."""
    if not registros:
        return None
    if any(r["facturada"] for r in registros):
        return "entregar"
    return "facturar"


def tablero():
    """[{clave, titulo, pie, leads: [...]}] con todo resuelto en Python."""
    estados = _estados()
    vinculos = _vinculos()
    leads = []
    for lead in _crudos():
        estado = estados.get(lead["ref"], {})
        lead["cotizaciones"] = vinculos.get(lead["ref"], [])
        # La etapa guardada (drag/botones) nunca queda ATRÁS de lo que los
        # hechos ya dicen: cotización vinculada = mínimo "Cotizado". Hacia
        # adelante (p. ej. marcar Entregado a mano) la guardada manda.
        guardada = estado.get("etapa") or "cotizar"
        derivada = _etapa_derivada(lead["cotizaciones"])
        if derivada and INDICE_ETAPA[derivada] > INDICE_ETAPA[guardada]:
            guardada = derivada
        lead["etapa"] = guardada
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
