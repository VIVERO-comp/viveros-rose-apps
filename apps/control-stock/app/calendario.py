"""Calendario del equipo: la cara de un proyecto de Linear dentro de la app.

Linear es el dueño de los datos (proyecto CALENDARIO ROSE, equipo
Viverorose): cada actividad es un issue, su tipo es la etiqueta del grupo
"Tipo de actividad", el responsable es el asignado y la fecha es el
`dueDate`. Aquí se lee ese proyecto, se normaliza para la pantalla y se
escriben de vuelta los cambios (crear, mover, cambiar de estado, reasignar,
comentar). La app NUNCA borra un issue: cancelar lo mueve al estado
Canceled y ahí queda con su historial.

Tres modos, según el `.env`:

- Sin `LINEAR_API_KEY` (o sin proyecto): **modo muestra**. La pantalla corre
  con actividades de ejemplo en memoria y se puede tocar todo sin que nada
  salga del droplet. Es el modo para desarrollo local.
- Con clave y `CALENDARIO_ESCRITURA` apagado: **solo lectura** del calendario
  real. Las acciones responden un error claro en vez de escribir.
- Con clave y `CALENDARIO_ESCRITURA=1`: lectura y escritura sobre el
  CALENDARIO ROSE de verdad.

La hora y la duración no existen en Linear (el `dueDate` es solo fecha), así
que viajan en una marca dentro de la descripción del issue:

    <!-- rose hora=09:00 dur=120 lugar=Obarrio -->

Se lee con `_leer_marca` y se reescribe con `_poner_marca`, de modo que el
texto que escribió la persona en Linear nunca se pisa.
"""

import os
import re
import threading
import time
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx

ZONA_PANAMA = ZoneInfo("America/Panama")
API = "https://api.linear.app/graphql"
TTL_LISTA = 60          # segundos de caché de las lecturas
TTL_CATALOGO = 900      # etiquetas, estados y gente cambian poquísimo

HORA_POR_DEFECTO = "09:00"
DURACION_POR_DEFECTO = 60


class ErrorCalendario(Exception):
    """Falla al hablar con Linear, con el texto que se le muestra al empleado."""


# ---------------------------------------------------------------------------
# Los 13 tipos del grupo "Tipo de actividad", con el color que usa la
# pantalla. El nombre tiene que coincidir con la etiqueta en Linear: es lo
# que amarra un tipo de aquí con su etiqueta de allá.
# ---------------------------------------------------------------------------
TIPOS = [
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
POR_NOMBRE = {t["nombre"].lower(): t["clave"] for t in TIPOS}
POR_CLAVE = {t["clave"]: t for t in TIPOS}

# Los 4 filtros del calendario (dueño, 22/09/2026: "4 filtros: eventos,
# paisajismo, mantenimiento, entrega retail"): se prenden y apagan como los
# calendarios de Google Calendar. Cada filtro agrupa tipos de actividad;
# los tipos que no caen en ningún grupo (reunión, visita, cotización…)
# se ven siempre. El color es el del tipo que representa al grupo.
FILTROS = [
    {"clave": "eventos",        "nombre": "Eventos",        "tipos": ("alquiler", "recogida"),      "color": "#f97316"},
    {"clave": "paisajismo",     "nombre": "Paisajismo",     "tipos": ("proyecto", "instalacion"),   "color": "#5b55a6"},
    {"clave": "mantenimiento",  "nombre": "Mantenimiento",  "tipos": ("mantenimiento",),            "color": "#2563eb"},
    {"clave": "entrega-retail", "nombre": "Entrega retail", "tipos": ("entrega",),                  "color": "#c9924f"},
]


def filtros_del_calendario(apagados):
    """Los 4 filtros con su estado y el conjunto de apagados que deja cada
    toque. Encendido = ninguno de sus tipos está apagado; el enlace con el
    conjunto nuevo lo arma main.py (_liga), la plantilla solo pinta."""
    filas = []
    for filtro in FILTROS:
        tipos = set(filtro["tipos"])
        encendido = not (tipos & set(apagados))
        nuevos = (set(apagados) | tipos) if encendido else (set(apagados) - tipos)
        filas.append({**filtro, "encendido": encendido, "apagados": nuevos})
    return filas

# Estado de la pantalla <-> tipo de estado en Linear.
ESTADOS = {
    "pend": ("unstarted", "backlog"),
    "curso": ("started",),
    "hecha": ("completed",),
    "cancel": ("canceled",),
}


# ---------------------------------------------------------------------------
# Configuración
# ---------------------------------------------------------------------------

def _var(nombre):
    valor = (os.environ.get(nombre) or "").strip()
    return valor or None


def configurado():
    """¿Hay credenciales para hablar con el CALENDARIO ROSE real?"""
    return bool(_var("LINEAR_API_KEY") and _var("LINEAR_PROJECT_CALENDARIO_ID"))


def escritura_activa():
    """Interruptor explícito: sin él la pantalla es de solo lectura.

    Existe porque no hay un Linear de pruebas: la instancia de pruebas de la
    app puede leer el calendario real sin riesgo de crear issues de verdad.
    """
    return configurado() and _var("CALENDARIO_ESCRITURA") == "1"


def modo():
    if not configurado():
        return "muestra"
    return "escritura" if escritura_activa() else "lectura"


def hoy():
    return datetime.now(ZONA_PANAMA).date()


# ---------------------------------------------------------------------------
# GraphQL
# ---------------------------------------------------------------------------

def _pedir(consulta, variables=None):
    clave = _var("LINEAR_API_KEY")
    if not clave:
        raise ErrorCalendario("El calendario no está conectado a Linear.")
    try:
        respuesta = httpx.post(
            API, timeout=15,
            headers={"Content-Type": "application/json", "Authorization": clave},
            json={"query": consulta, "variables": variables or {}},
        )
        cuerpo = respuesta.json()
    except httpx.HTTPError as error:
        raise ErrorCalendario(f"No se pudo hablar con Linear: {error}") from error
    except ValueError as error:
        raise ErrorCalendario("Linear respondió algo que no se entiende.") from error
    if respuesta.status_code >= 400 or cuerpo.get("errors"):
        detalle = cuerpo.get("errors") or respuesta.status_code
        raise ErrorCalendario(f"Linear rechazó la consulta: {str(detalle)[:200]}")
    return cuerpo.get("data") or {}


# ---------------------------------------------------------------------------
# Catálogo del equipo (etiquetas, estados, gente), cacheado
# ---------------------------------------------------------------------------

_catalogo_cache = {"en": 0, "dato": None}

CONSULTA_CATALOGO = """
query($equipo: String!) {
  team(id: $equipo) {
    states(first: 30) { nodes { id name type position } }
    labels(first: 80) { nodes { id name isGroup parent { name } } }
    members(first: 50) { nodes { id name displayName email active } }
  }
}
"""


def catalogo(refrescar=False):
    if _catalogo_cache["dato"] and not refrescar:
        # Nunca se espera a Linear si ya hay catálogo: viejo => refresco en fondo.
        if time.time() - _catalogo_cache["en"] >= TTL_CATALOGO:
            _en_fondo("catalogo", lambda: catalogo(refrescar=True))
        return _catalogo_cache["dato"]
    equipo = _var("LINEAR_TEAM_CALENDARIO_ID") or _var("LINEAR_TEAM_ID")
    datos = _pedir(CONSULTA_CATALOGO, {"equipo": equipo})["team"]

    etiquetas = {}
    for etiqueta in datos["labels"]["nodes"]:
        if etiqueta["isGroup"]:
            continue
        clave = POR_NOMBRE.get((etiqueta["name"] or "").lower())
        # Solo las del grupo "Tipo de actividad": un "Bug" llamado igual que
        # un tipo no debe secuestrar el color de la pantalla.
        padre = (etiqueta.get("parent") or {}).get("name", "")
        if clave and padre.lower().startswith("tipo"):
            etiquetas[clave] = etiqueta["id"]

    estados = {}
    for clave, tipos_linear in ESTADOS.items():
        candidatos = [e for e in datos["states"]["nodes"] if e["type"] in tipos_linear]
        # Dentro del mismo tipo, el de menor posición es el "natural"
        # (Todo antes que Backlog, In Progress antes que In Validation).
        orden = {t: i for i, t in enumerate(tipos_linear)}
        candidatos.sort(key=lambda e: (orden.get(e["type"], 9), e.get("position") or 0))
        if candidatos:
            estados[clave] = candidatos[0]["id"]

    gente = [
        {"id": m["id"], "nombre": m.get("displayName") or m["name"],
         "email": (m.get("email") or "").lower()}
        for m in datos["members"]["nodes"] if m.get("active")
    ]
    gente.sort(key=lambda g: g["nombre"].lower())

    dato = {"etiquetas": etiquetas, "estados": estados, "gente": gente}
    _catalogo_cache.update({"en": time.time(), "dato": dato})
    return dato


def responsables():
    """La gente del equipo, para los selectores de la pantalla."""
    if not configurado():
        return [{"id": n.lower(), "nombre": n, "email": ""} for n in _GENTE_MUESTRA]
    try:
        return catalogo()["gente"]
    except ErrorCalendario:
        return []


# ---------------------------------------------------------------------------
# La marca de hora/duración/lugar dentro de la descripción
# ---------------------------------------------------------------------------

MARCA = re.compile(r"<!--\s*rose\s+([^>]*?)-->\s*", re.I)


def _leer_marca(descripcion):
    texto = descripcion or ""
    encontrada = MARCA.search(texto)
    datos = {"hora": HORA_POR_DEFECTO, "dur": DURACION_POR_DEFECTO, "lugar": ""}
    if encontrada:
        for parte in encontrada.group(1).split("|"):
            if "=" in parte:
                llave, _, valor = parte.partition("=")
                datos[llave.strip()] = valor.strip()
        texto = MARCA.sub("", texto).strip()
    hora = str(datos.get("hora") or HORA_POR_DEFECTO)
    if not re.fullmatch(r"[0-2]\d:[0-5]\d", hora):
        hora = HORA_POR_DEFECTO
    try:
        dur = int(datos.get("dur") or DURACION_POR_DEFECTO)
    except (TypeError, ValueError):
        dur = DURACION_POR_DEFECTO
    return {"hora": hora, "dur": max(15, min(dur, 600)),
            "lugar": str(datos.get("lugar") or ""), "nota": texto}


def _poner_marca(nota, hora, dur, lugar):
    marca = f"<!-- rose hora={hora}|dur={int(dur)}|lugar={(lugar or '').replace('|', ' ')} -->"
    nota = (nota or "").strip()
    return f"{marca}\n\n{nota}".strip()


# ---------------------------------------------------------------------------
# Lectura
# ---------------------------------------------------------------------------

CAMPOS = """
  id identifier title description dueDate url priority
  state { name type }
  assignee { id name displayName }
  labels(first: 10) { nodes { id name } }
"""

# Una sola consulta con alias: el rango visible y las atrasadas viajan en
# UN viaje a Linear (antes eran dos, en fila — la mitad de la espera).
CONSULTA_LISTA = """
query($proyecto: ID!, $desde: TimelessDateOrDuration!, $hasta: TimelessDateOrDuration!, $hoy: TimelessDateOrDuration!) {
  rango: issues(first: 250, filter: {
    project: { id: { eq: $proyecto } }
    dueDate: { gte: $desde, lte: $hasta }
  }) { nodes { %s } }
  atrasadas: issues(first: 100, filter: {
    project: { id: { eq: $proyecto } }
    dueDate: { lt: $hoy }
    state: { type: { nin: ["completed", "canceled"] } }
  }) { nodes { %s } }
}
""" % (CAMPOS, CAMPOS)


def _tipo_de(issue):
    for etiqueta in (issue.get("labels") or {}).get("nodes", []):
        clave = POR_NOMBRE.get((etiqueta.get("name") or "").lower())
        if clave:
            return clave
    return "otro"


def _estado_de(issue):
    tipo = ((issue.get("state") or {}).get("type") or "").lower()
    for clave, tipos_linear in ESTADOS.items():
        if tipo in tipos_linear:
            return clave
    return "pend"


def _cliente_de(titulo, tipo):
    """El título se escribe "Tipo — Cliente"; la pantalla muestra el cliente."""
    nombre_tipo = POR_CLAVE[tipo]["nombre"]
    limpio = (titulo or "").strip()
    for separador in ("—", "-", "–"):
        prefijo = f"{nombre_tipo} {separador} "
        if limpio.lower().startswith(prefijo.lower()):
            return limpio[len(prefijo):].strip() or limpio
    return limpio


def _normalizar(issue):
    tipo = _tipo_de(issue)
    marca = _leer_marca(issue.get("description"))
    asignado = issue.get("assignee") or {}
    prioridad = issue.get("priority") or 3
    return {
        "id": issue["id"],
        "ref": issue.get("identifier") or "",
        "url": issue.get("url") or "",
        "tipo": tipo,
        "cliente": _cliente_de(issue.get("title"), tipo),
        "titulo": issue.get("title") or "",
        "lugar": marca["lugar"],
        "nota": marca["nota"],
        "hora": marca["hora"],
        "dur": marca["dur"],
        "fecha": issue.get("dueDate") or "",
        "estado": _estado_de(issue),
        "prioridad": 3 if prioridad in (0, 4) else prioridad,
        "resp": asignado.get("displayName") or asignado.get("name") or "Sin asignar",
        "resp_id": asignado.get("id") or "",
    }


_lista_cache = {}
# Refrescos en fondo por llave: la pantalla sirve lo guardado al instante
# aunque el TTL haya vencido, y Linear se consulta por detrás (pedido del
# dueño, 22/09/2026: cambiar de pestaña sin esperar).
_refrescos_lista = set()
_candado_lista = threading.Lock()


def listar(desde, hasta, refrescar=False):
    """Actividades con fecha entre `desde` y `hasta` (ISO), más las atrasadas.

    Las atrasadas viajan siempre: la pantalla las muestra arriba en la franja
    roja aunque estés mirando otro mes.
    """
    if not configurado():
        return _muestra_listar(desde, hasta)

    llave = f"{desde}:{hasta}"
    guardado = _lista_cache.get(llave)
    if guardado and not refrescar:
        # Lo guardado sale YA; si venció el TTL, Linear se consulta por
        # detrás y la próxima pintada trae lo nuevo.
        if time.time() - guardado["en"] >= TTL_LISTA:
            _refrescar_lista_en_fondo(llave, desde, hasta)
        return guardado["dato"]
    return _buscar_lista(llave, desde, hasta)


def _buscar_lista(llave, desde, hasta):
    """La consulta real (bloqueante) a Linear; actualiza el caché."""
    proyecto = _var("LINEAR_PROJECT_CALENDARIO_ID")
    datos = _pedir(CONSULTA_LISTA, {"proyecto": proyecto, "desde": desde,
                                    "hasta": hasta, "hoy": hoy().isoformat()})

    porid = {}
    for issue in datos["rango"]["nodes"] + datos["atrasadas"]["nodes"]:
        if issue.get("dueDate"):
            porid[issue["id"]] = _normalizar(issue)
    dato = sorted(porid.values(), key=lambda a: (a["fecha"], a["hora"]))
    _lista_cache[llave] = {"en": time.time(), "dato": dato}
    return dato


def _en_fondo(clave, tarea):
    """Corre `tarea` en un hilo, una sola vez por clave a la vez."""
    with _candado_lista:
        if clave in _refrescos_lista:
            return
        _refrescos_lista.add(clave)

    def correr():
        try:
            tarea()
        except Exception:
            pass  # era un refresco: lo guardado sigue sirviendo
        finally:
            with _candado_lista:
                _refrescos_lista.discard(clave)

    threading.Thread(target=correr, daemon=True).start()


def _refrescar_lista_en_fondo(llave, desde, hasta):
    _en_fondo("lista:" + llave, lambda: _buscar_lista(llave, desde, hasta))


def calentar_en_fondo():
    """Al arrancar el proceso: catálogo, el mes en curso, los leads de
    servicio y el tablero de Retail quedan calientes, para que ni la
    PRIMERA visita después de un deploy espere a Linear (el dueño la
    sintió lenta el 22/09/2026: leads y Retail no se precalentaban y la
    primera /calendario los esperaba en línea). El rango es el mismo que
    arma la pantalla: del 1° del mes, 10 días atrás y 52 adelante."""
    if not configurado():
        return

    def tarea():
        catalogo()
        primero = hoy().replace(day=1)
        desde = (primero - timedelta(days=10)).isoformat()
        hasta = (primero + timedelta(days=52)).isoformat()
        listar(desde, hasta)
        leads_de_servicio()
        # Import tardío: retail importa calendario (sería circular arriba).
        from . import retail
        retail.por_entregar()

    _en_fondo("calentar", tarea)


# ---------------------------------------------------------------------------
# Leads de servicio (equipo LEAD de Linear): el "log" del menú lateral.
#
# Pedido del dueño (22/09/2026): en el espacio libre del menú del calendario
# va un log de leads de alquiler/mantenimiento — todo lo que sea SERVICIO,
# nada de venta retail ni mayorista — que todavía no tienen su actividad
# agendada. Un toque en el lead abre "Nueva actividad" prellenada con su
# tipo y su nombre. El log se limpia solo: cuando el lead se factura o se
# pierde, su issue sale de los estados vivos y desaparece de aquí.
# ---------------------------------------------------------------------------

# Etiqueta del equipo LEAD -> tipo del calendario (para el color).
ETIQUETAS_LEADS_SERVICIO = {
    "Mantenimiento": "mantenimiento",
    "Eventos · Alquiler": "alquiler",
    "Eventos · Bodas": "alquiler",
    "Eventos · Ferias": "alquiler",
}
# La label que pone el barrido nocturno de Odoo cuando un lead lleva 15 días
# en "Nuevo" sin respuesta del cliente (regla del dueño, 22/09/2026): un lead
# desactivado NO sale en el log — no hay a quién agendarle nada. El barrido
# no cambia el estado del issue (sigue "vivo" en Linear a propósito, para
# revivir sin ruido si el cliente vuelve a escribir), por eso se filtra por
# la label y no por el estado.
LABEL_LEAD_DESACTIVADO = "Desactivado"
TTL_LEADS = 120

CONSULTA_LEADS = """
query { issues(first: 50, filter: {
    team: { key: { eq: "LEAD" } }
    state: { type: { nin: ["completed", "canceled"] } }
  }) { nodes { id identifier title url createdAt labels { nodes { name } } } }
}
"""

_leads_cache = {"en": 0, "dato": None}


def leads_de_servicio():
    """[{ref, nombre, tipo, etiqueta, hace, url}] de leads de servicio vivos.

    Mismo patrón de velocidad que el resto: se sirve lo guardado al
    instante y, si venció el TTL, Linear se consulta por detrás.
    """
    if not configurado():
        return _muestra_leads()
    if _leads_cache["dato"] is not None:
        if time.time() - _leads_cache["en"] >= TTL_LEADS:
            _en_fondo("leads", _buscar_leads)
        return _leads_cache["dato"]
    try:
        return _buscar_leads()
    except ErrorCalendario:
        return []  # el log es un extra: sin Linear no tumba la pantalla


def _buscar_leads():
    filas = []
    for issue in _pedir(CONSULTA_LEADS)["issues"]["nodes"]:
        etiquetas = [l["name"] for l in issue["labels"]["nodes"]]
        if LABEL_LEAD_DESACTIVADO in etiquetas:
            continue  # desactivado por inactividad: no hay a quién agendar
        etiqueta = next((e for e in etiquetas if e in ETIQUETAS_LEADS_SERVICIO), None)
        if not etiqueta:
            continue  # retail, mayorista y demás: no son servicio
        titulo = issue["title"] or ""
        # "Laura Porcell (PP-WATHA)" -> el nombre pelado para prellenar.
        nombre = titulo.split(" (PP-")[0].strip() or titulo
        dias = 0
        try:
            dias = (datetime.now(ZONA_PANAMA).date()
                    - datetime.fromisoformat(issue["createdAt"].replace("Z", "+00:00"))
                      .astimezone(ZONA_PANAMA).date()).days
        except (ValueError, KeyError, TypeError):
            pass
        # "Laura Porcell (PP-WATHA)" -> PP-WATHA, para casar el lead en Twenty.
        pp = re.search(r"\((PP-[A-Z0-9]+)\)", titulo)
        filas.append({
            "id": issue["id"], "ref": issue["identifier"], "nombre": nombre,
            "pp": pp.group(1) if pp else "",
            "tipo": ETIQUETAS_LEADS_SERVICIO[etiqueta], "etiqueta": etiqueta,
            "hace": "hoy" if dias <= 0 else (f"hace {dias} día" + ("s" if dias > 1 else "")),
            "dias": dias, "url": issue.get("url") or "",
        })
    filas.sort(key=lambda f: -f["dias"])  # el más viejo arriba: es el urgente
    _leads_cache.update({"en": time.time(), "dato": filas})
    return filas


def _muestra_leads():
    """Dos leads de ejemplo para el modo muestra (diseño y pruebas)."""
    return [
        {"id": "muestra-90", "ref": "LEAD-90", "nombre": "Hotel Bristol",
         "pp": "PP-MU3ST", "tipo": "mantenimiento",
         "etiqueta": "Mantenimiento", "hace": "hace 3 días", "dias": 3, "url": ""},
        {"id": "muestra-91", "ref": "LEAD-91", "nombre": "Boda Las Nubes",
         "pp": "PP-MU3SU", "tipo": "alquiler",
         "etiqueta": "Eventos · Bodas", "hace": "hoy", "dias": 0, "url": ""},
    ]


def invalidar_cache():
    _lista_cache.clear()


def resumen(actividades, dia=None):
    """Los números del Inicio, calculados aquí y no en el navegador."""
    dia = dia or hoy().isoformat()
    lunes = date.fromisoformat(dia) - timedelta(days=date.fromisoformat(dia).weekday())
    semana = {(lunes + timedelta(days=i)).isoformat() for i in range(7)}
    vivas = [a for a in actividades if a["estado"] != "cancel"]
    del_dia = [a for a in vivas if a["fecha"] == dia]
    de_semana = [a for a in vivas if a["fecha"] in semana]
    atrasadas = [a for a in vivas if a["fecha"] < dia and a["estado"] != "hecha"]
    hechas = [a for a in de_semana if a["estado"] == "hecha"]
    return {
        "dia": dia,
        "hoy": len(del_dia),
        "pendientes_hoy": len([a for a in del_dia if a["estado"] != "hecha"]),
        "atrasadas": len(atrasadas),
        "semana": len(de_semana),
        "hechas_semana": len(hechas),
        "avance": round(len(hechas) / len(de_semana) * 100) if de_semana else 0,
    }


# ---------------------------------------------------------------------------
# Escritura
# ---------------------------------------------------------------------------

def _exigir_escritura():
    if not configurado():
        return  # modo muestra: se escribe en memoria
    if not escritura_activa():
        raise ErrorCalendario(
            "Esta instancia está en solo lectura: mira el calendario real pero no "
            "escribe en Linear. Se enciende con CALENDARIO_ESCRITURA=1.")


MUTACION_CREAR = """
mutation($datos: IssueCreateInput!) {
  issueCreate(input: $datos) { success issue { id identifier url } }
}
"""

MUTACION_ACTUALIZAR = """
mutation($id: String!, $datos: IssueUpdateInput!) {
  issueUpdate(id: $id, input: $datos) { success }
}
"""

MUTACION_COMENTAR = """
mutation($id: String!, $texto: String!) {
  commentCreate(input: { issueId: $id, body: $texto }) { success }
}
"""


def crear(tipo, cliente, fecha, hora=HORA_POR_DEFECTO, dur=DURACION_POR_DEFECTO,
          lugar="", resp_id="", prioridad=3, nota=""):
    """Crea la actividad. Devuelve {ref, url}."""
    _exigir_escritura()
    if tipo not in POR_CLAVE:
        raise ErrorCalendario("Ese tipo de actividad no existe.")
    cliente = (cliente or "").strip()
    if not cliente:
        raise ErrorCalendario("Falta el cliente o el nombre del trabajo.")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", fecha or ""):
        raise ErrorCalendario("Falta la fecha.")
    if not configurado():
        return _muestra_crear(tipo, cliente, fecha, hora, dur, lugar, resp_id, prioridad, nota)

    cat = catalogo()
    etiqueta = cat["etiquetas"].get(tipo)
    datos = {
        "teamId": _var("LINEAR_TEAM_CALENDARIO_ID") or _var("LINEAR_TEAM_ID"),
        "projectId": _var("LINEAR_PROJECT_CALENDARIO_ID"),
        "title": f"{POR_CLAVE[tipo]['nombre']} — {cliente}",
        "description": _poner_marca(nota, hora, dur, lugar),
        "dueDate": fecha,
        "priority": int(prioridad),
    }
    if etiqueta:
        datos["labelIds"] = [etiqueta]
    if resp_id:
        datos["assigneeId"] = resp_id
    if cat["estados"].get("pend"):
        datos["stateId"] = cat["estados"]["pend"]
    hecho = _pedir(MUTACION_CREAR, {"datos": datos})["issueCreate"]
    if not hecho.get("success"):
        raise ErrorCalendario("Linear no pudo crear la actividad.")
    invalidar_cache()
    return {"ref": hecho["issue"]["identifier"], "url": hecho["issue"]["url"]}


def _actualizar(id_issue, datos):
    hecho = _pedir(MUTACION_ACTUALIZAR, {"id": id_issue, "datos": datos})["issueUpdate"]
    if not hecho.get("success"):
        raise ErrorCalendario("Linear no pudo guardar el cambio.")
    invalidar_cache()


def _una(id_issue):
    dato = _pedir("query($id: String!) { issue(id: $id) { %s } }" % CAMPOS, {"id": id_issue})
    issue = dato.get("issue")
    if not issue:
        raise ErrorCalendario("Esa actividad ya no existe en Linear.")
    return issue


def mover(id_issue, fecha, hora=None):
    """Cambia el día (y opcionalmente la hora) de una actividad."""
    _exigir_escritura()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", fecha or ""):
        raise ErrorCalendario("Esa fecha no se entiende.")
    if not configurado():
        return _muestra_editar(id_issue, fecha=fecha, hora=hora)
    datos = {"dueDate": fecha}
    if hora:
        issue = _una(id_issue)
        marca = _leer_marca(issue.get("description"))
        datos["description"] = _poner_marca(marca["nota"], hora, marca["dur"], marca["lugar"])
    _actualizar(id_issue, datos)


def cambiar_estado(id_issue, clave):
    """pend / curso / hecha / cancel. Cancelar NO borra: mueve a Canceled."""
    _exigir_escritura()
    if clave not in ESTADOS:
        raise ErrorCalendario("Ese estado no existe.")
    if not configurado():
        return _muestra_editar(id_issue, estado=clave)
    estado = catalogo()["estados"].get(clave)
    if not estado:
        raise ErrorCalendario("El equipo de Linear no tiene un estado para eso.")
    _actualizar(id_issue, {"stateId": estado})


def reasignar(id_issue, resp_id):
    _exigir_escritura()
    if not configurado():
        return _muestra_editar(id_issue, resp_id=resp_id)
    _actualizar(id_issue, {"assigneeId": resp_id or None})


def cambiar_detalle(id_issue, hora=None, dur=None, lugar=None, prioridad=None):
    """Hora, duración, lugar y prioridad (lo que vive en la marca)."""
    _exigir_escritura()
    if not configurado():
        return _muestra_editar(id_issue, hora=hora, dur=dur, lugar=lugar, prioridad=prioridad)
    issue = _una(id_issue)
    marca = _leer_marca(issue.get("description"))
    datos = {"description": _poner_marca(
        marca["nota"],
        hora or marca["hora"],
        dur or marca["dur"],
        marca["lugar"] if lugar is None else lugar,
    )}
    if prioridad:
        datos["priority"] = int(prioridad)
    _actualizar(id_issue, datos)


def agregar_nota(id_issue, texto, autor=""):
    """Una nota de la pantalla es un comentario del issue."""
    _exigir_escritura()
    texto = (texto or "").strip()
    if not texto:
        raise ErrorCalendario("Escribí la nota primero.")
    if not configurado():
        return _muestra_nota(id_issue, texto, autor)
    firma = f"{texto}\n\n_— {autor} desde Control Viverorose_" if autor else texto
    hecho = _pedir(MUTACION_COMENTAR, {"id": id_issue, "texto": firma})["commentCreate"]
    if not hecho.get("success"):
        raise ErrorCalendario("Linear no pudo guardar la nota.")
    invalidar_cache()


def comentarios(id_issue):
    if not configurado():
        return _MUESTRA_NOTAS.get(id_issue, [])
    dato = _pedir(
        "query($id: String!) { issue(id: $id) { comments(first: 20) "
        "{ nodes { body createdAt user { displayName name } } } } }",
        {"id": id_issue})
    issue = dato.get("issue") or {}
    salida = []
    for c in (issue.get("comments") or {}).get("nodes", []):
        usuario = c.get("user") or {}
        salida.append({
            "quien": usuario.get("displayName") or usuario.get("name") or "Alguien",
            "cuando": (c.get("createdAt") or "")[:10],
            "texto": c.get("body") or "",
        })
    return salida


# ---------------------------------------------------------------------------
# Modo muestra (sin Linear): todo vive en memoria del proceso.
# ---------------------------------------------------------------------------

_GENTE_MUESTRA = ["Abraham", "Juan", "Pedro", "María"]
_MUESTRA = None
_MUESTRA_NOTAS = {}


def _semilla():
    base = hoy()

    def dia(desplazamiento):
        return (base + timedelta(days=desplazamiento)).isoformat()

    crudo = [
        (-11, "08:30", 120, "mantenimiento", "Hotel Bristol", "Obarrio", "Juan", "hecha", 3),
        (-10, "10:00", 60, "entrega", "Torres del Este", "Costa del Este", "Pedro", "hecha", 3),
        (-8, "11:00", 60, "reunion", "Super Xtra", "Vía España", "Abraham", "hecha", 2),
        (-4, "08:30", 120, "mantenimiento", "Hotel Bristol", "Obarrio", "Juan", "hecha", 3),
        (-3, "09:00", 120, "alquiler", "Banco General · evento", "Marbella", "Juan", "hecha", 2),
        (-3, "15:00", 60, "cotizacion", "Torre Financiera", "Calle 50", "Abraham", "pend", 2),
        (-2, "09:00", 120, "mantenimiento", "Clínica Punta Pacífica", "Punta Pacífica", "María", "pend", 3),
        (-1, "16:00", 60, "recogida", "Banco General · evento", "Marbella", "Pedro", "hecha", 3),
        (0, "07:00", 120, "compra", "Vivero mayorista Chiriquí", "Carga", "Abraham", "curso", 3),
        (0, "08:00", 120, "mantenimiento", "Embajada de Colombia", "Bella Vista", "Juan", "curso", 2),
        (0, "10:00", 90, "entrega", "Hotel Bristol", "Obarrio", "Pedro", "pend", 3),
        (0, "14:00", 60, "seguimiento", "Residencia Him", "Llamada", "María", "pend", 3),
        (0, "16:30", 60, "visita", "Terraza Villa Zaita", "Villa Zaita", "Abraham", "pend", 3),
        (1, "09:00", 120, "visita", "Residencia Costa del Este", "Costa del Este", "Abraham", "pend", 3),
        (2, "10:00", 60, "entrega", "Quinta Paraíso", "Villa Zaita", "Pedro", "pend", 3),
        (3, "08:30", 120, "mantenimiento", "Oficinas Duarte", "El Cangrejo", "María", "pend", 3),
        (4, "10:00", 90, "entrega", "Hotel Riu", "Paitilla", "Pedro", "pend", 3),
        (5, "08:00", 240, "proyecto", "Jardín Oficina ABC", "Costa del Este", "Juan", "pend", 2),
        (6, "13:00", 120, "alquiler", "Boda Salón Las Nubes", "Clayton", "Pedro", "pend", 1),
        (7, "09:00", 60, "recogida", "Boda Salón Las Nubes", "Clayton", "Pedro", "pend", 2),
        (9, "09:00", 120, "administrativo", "Planilla y pagos", "Oficina", "Abraham", "pend", 3),
        (11, "08:00", 240, "proyecto", "Jardín Oficina ABC", "Costa del Este", "Juan", "pend", 2),
    ]
    salida = []
    for i, (desp, hora, dur, tipo, cliente, lugar, resp, estado, prioridad) in enumerate(crudo):
        salida.append({
            "id": f"muestra-{i}", "ref": f"VIV-{201 + i}", "url": "",
            "tipo": tipo, "cliente": cliente, "titulo": f"{POR_CLAVE[tipo]['nombre']} — {cliente}",
            "lugar": lugar, "nota": "", "hora": hora, "dur": dur, "fecha": dia(desp),
            "estado": estado, "prioridad": prioridad, "resp": resp, "resp_id": resp.lower(),
        })
    return salida


def _muestra():
    global _MUESTRA
    if _MUESTRA is None:
        _MUESTRA = _semilla()
    return _MUESTRA


def reiniciar_muestra():
    global _MUESTRA
    _MUESTRA = _semilla()
    _MUESTRA_NOTAS.clear()


def _muestra_listar(desde, hasta):
    dia_hoy = hoy().isoformat()
    return sorted(
        [a for a in _muestra()
         if desde <= a["fecha"] <= hasta
         or (a["fecha"] < dia_hoy and a["estado"] not in ("hecha", "cancel"))],
        key=lambda a: (a["fecha"], a["hora"]))


def _muestra_buscar(id_actividad):
    for a in _muestra():
        if a["id"] == id_actividad:
            return a
    raise ErrorCalendario("Esa actividad ya no está.")


def _muestra_crear(tipo, cliente, fecha, hora, dur, lugar, resp_id, prioridad, nota):
    lista = _muestra()
    numero = 201 + len(lista)
    nombre = next((g for g in _GENTE_MUESTRA if g.lower() == (resp_id or "").lower()), "Sin asignar")
    actividad = {
        "id": f"muestra-{len(lista)}-{int(time.time())}", "ref": f"VIV-{numero}", "url": "",
        "tipo": tipo, "cliente": cliente, "titulo": f"{POR_CLAVE[tipo]['nombre']} — {cliente}",
        "lugar": lugar, "nota": nota, "hora": hora, "dur": int(dur), "fecha": fecha,
        "estado": "pend", "prioridad": int(prioridad), "resp": nombre,
        "resp_id": (resp_id or "").lower(),
    }
    lista.append(actividad)
    return {"ref": actividad["ref"], "url": ""}


def _muestra_editar(id_actividad, **cambios):
    actividad = _muestra_buscar(id_actividad)
    for llave, valor in cambios.items():
        if valor is None:
            continue
        if llave == "resp_id":
            actividad["resp_id"] = (valor or "").lower()
            actividad["resp"] = next(
                (g for g in _GENTE_MUESTRA if g.lower() == actividad["resp_id"]), "Sin asignar")
        elif llave == "dur":
            actividad["dur"] = int(valor)
        elif llave == "prioridad":
            actividad["prioridad"] = int(valor)
        else:
            actividad[llave] = valor


def _muestra_nota(id_actividad, texto, autor):
    _muestra_buscar(id_actividad)
    _MUESTRA_NOTAS.setdefault(id_actividad, []).insert(0, {
        "quien": autor or "Alguien", "cuando": hoy().isoformat(), "texto": texto,
    })


# ---------------------------------------------------------------------------
# Armado de la pantalla.
#
# TODO el layout se calcula aquí, en Python: qué días entran en la vista, en
# qué posición va cada bloque de la semana, cómo se reparten los que chocan a
# la misma hora, qué celdas tiene el mes y cómo se agrupa la lista. La
# plantilla solo recorre estas estructuras y el navegador no arma nada.
# ---------------------------------------------------------------------------

# 6 a 19 h. Una hora con trabajo mide 46 px; una hora vacía se encoge a 20
# (pedido del dueño, 18/09/2026: "en las horas que no hay nada que la fila se
# ponga más chica así cabe todo"). Por eso la altura NO es lineal y hay que
# convertir minutos a píxeles con el mapa de filas, nunca multiplicando.
HORA_INICIO, HORA_FIN, ALTO_HORA, ALTO_HORA_VACIA = 6, 19, 46, 27

DOW = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"]
DOW_LARGO = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]
MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
         "agosto", "septiembre", "octubre", "noviembre", "diciembre"]
VISTAS = ("semana", "dia", "mes", "lista")


def _dia(iso_texto):
    return date.fromisoformat(iso_texto)


def _lunes(fecha):
    return fecha - timedelta(days=fecha.weekday())


def dmy(iso_texto):
    """Fechas a la panameña: 18/09/2026."""
    if not iso_texto:
        return ""
    partes = iso_texto.split("-")
    return f"{partes[2]}/{partes[1]}/{partes[0]}"


def _minutos(hora):
    try:
        h, m = hora.split(":")
        return int(h) * 60 + int(m)
    except (ValueError, AttributeError):
        return 9 * 60


def hora_bonita(hora):
    """09:00 -> 9 am, 16:30 -> 4:30 pm."""
    minutos = _minutos(hora)
    h, m = divmod(minutos, 60)
    sufijo = "am" if h < 12 else "pm"
    h12 = h % 12 or 12
    return f"{h12}{'' if m == 0 else ':%02d' % m} {sufijo}"


def esta_atrasada(actividad, dia_hoy=None):
    dia_hoy = dia_hoy or hoy().isoformat()
    return (actividad["estado"] not in ("hecha", "cancel")
            and actividad["fecha"] < dia_hoy)


def color_de(tipo):
    return POR_CLAVE.get(tipo, POR_CLAVE["otro"])["color"]


def nombre_de_tipo(tipo):
    return POR_CLAVE.get(tipo, POR_CLAVE["otro"])["nombre"]


def nombre_de_estado(estado):
    return {"hecha": "Terminada", "curso": "En curso",
            "cancel": "Cancelada"}.get(estado, "Pendiente")


def nombre_de_prioridad(prioridad):
    return {1: "Urgente", 2: "Alta"}.get(prioridad, "Normal")


def _tinta(hex_color, alfa):
    """El color del tipo mezclado con blanco, ya resuelto y OPACO.

    Opaco a propósito: con rgba() los bloques que se enciman a la misma hora
    se transparentaban entre sí y el texto quedaba ilegible.
    """
    r, g, b = (int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
    mezcla = tuple(round(255 + (canal - 255) * alfa) for canal in (r, g, b))
    return "#%02x%02x%02x" % mezcla


def filtrar(actividades, tipos_apagados=(), quien="", texto="", ver_hechas=True,
            solo_de=""):
    """Los filtros de la barra, aplicados en el servidor."""
    texto = (texto or "").strip().lower()
    salida = []
    for a in actividades:
        if solo_de and a["resp_id"] != solo_de:
            continue
        if a["tipo"] in tipos_apagados:
            continue
        if quien and a["resp_id"] != quien:
            continue
        if not ver_hechas and a["estado"] in ("hecha", "cancel"):
            continue
        if texto:
            bolsa = " ".join([a["cliente"], a["lugar"], a["resp"],
                              nombre_de_tipo(a["tipo"]), a["ref"], a.get("nota", "")]).lower()
            if texto not in bolsa:
                continue
        salida.append(a)
    return salida


def dias_de(vista, ancla):
    """Los días que se ven en cada vista."""
    if vista == "dia":
        return [ancla.isoformat()]
    if vista == "semana":
        inicio = _lunes(ancla)
        return [(inicio + timedelta(days=i)).isoformat() for i in range(7)]
    if vista == "mes":
        inicio = _lunes(ancla.replace(day=1))
        return [(inicio + timedelta(days=i)).isoformat() for i in range(42)]
    primero = ancla.replace(day=1)
    ultimo = (primero + timedelta(days=32)).replace(day=1) - timedelta(days=1)
    return [(primero + timedelta(days=i)).isoformat() for i in range(ultimo.day)]


def titulo_de(vista, ancla):
    if vista == "dia":
        return f"{DOW_LARGO[ancla.weekday()]} {dmy(ancla.isoformat())}"
    if vista == "semana":
        inicio = _lunes(ancla)
        fin = inicio + timedelta(days=6)
        mes_i, mes_f = MESES[inicio.month - 1], MESES[fin.month - 1]
        izquierda = f"{inicio.day}" + ("" if mes_i == mes_f else f" {mes_i}")
        return f"{izquierda} – {fin.day} {mes_f} {fin.year}"
    return f"{MESES[ancla.month - 1].capitalize()} {ancla.year}"


def _del_dia(actividades, dia_iso):
    return sorted([a for a in actividades if a["fecha"] == dia_iso],
                  key=lambda a: _minutos(a["hora"]))


def _repartir(actividades):
    """Reparte en columnas las actividades que chocan a la misma hora."""
    orden = sorted(actividades, key=lambda a: _minutos(a["hora"]))
    grupos, actual, fin_actual = [], [], -1
    for a in orden:
        inicio = _minutos(a["hora"])
        if actual and inicio >= fin_actual:
            grupos.append(actual)
            actual, fin_actual = [], -1
        actual.append(a)
        fin_actual = max(fin_actual, inicio + a["dur"])
    if actual:
        grupos.append(actual)

    repartidas = []
    for grupo in grupos:
        columnas = []
        puestos = []
        for a in grupo:
            inicio = _minutos(a["hora"])
            elegida = None
            for i, libre_desde in enumerate(columnas):
                if libre_desde <= inicio:
                    columnas[i] = inicio + a["dur"]
                    elegida = i
                    break
            if elegida is None:
                columnas.append(inicio + a["dur"])
                elegida = len(columnas) - 1
            puestos.append((a, elegida))
        for a, columna in puestos:
            repartidas.append((a, columna, len(columnas)))
    return repartidas


def _bloque(actividad, columna, columnas, indice, total_columnas, dia_hoy, filas=()):
    """Un bloque del carril con su posición ya resuelta en CSS.

    Las actividades que chocan a la misma hora se parten la columna en
    partes IGUALES, lado a lado, sin taparse (pedido del dueño, 22/09/2026,
    al ver dos tarjetas encimadas el martes 22: "mira el 22"). Antes se
    escalonaban una sobre otra y la de atrás quedaba tapada.
    """
    ancho = f"((100% - 46px) / {total_columnas})"
    izquierda = f"(46px + (100% - 46px) * {indice / total_columnas})"
    inicio = _minutos(actividad["hora"])
    arriba = _y_de(inicio, filas)
    # En % del carril; el piso de 24px para que siempre se lea lo pone el
    # CSS (min-height), porque el alto real depende de la pantalla.
    alto = _y_de(inicio + actividad["dur"], filas) - arriba
    color = color_de(actividad["tipo"])
    parte = 1 / columnas
    corrido = parte * columna
    return {
        "a": actividad,
        "color": color,
        "fondo": _tinta(color, 0.07),
        "borde": _tinta(color, 0.3),
        "estilo": (f"top:calc({arriba:.4f}% + 2px);height:calc({alto:.4f}% - 6px);"
                   f"left:calc({izquierda} + {ancho} * {corrido:.4f} + 2px);"
                   f"width:calc({ancho} * {parte:.4f} - 5px);"
                   f"z-index:{2 + columna};"
                   f"background:{_tinta(color, 0.07)};border-color:{_tinta(color, 0.3)};"
                   f"border-left-color:{color}"),
        # Con la columna partida (o poco alto) el bloque solo dice el tipo:
        # más vale una línea legible que tres cortadas.
        "ver_cliente": actividad["dur"] >= 45 and columnas < 2,
        "ver_pie": actividad["dur"] >= 90 and columnas < 2,
        "atrasada": esta_atrasada(actividad, dia_hoy),
    }


def rango_horas(actividades):
    """De qué hora a qué hora dibujar los carriles.

    En vez de pintar siempre de 6 a 19 (catorce filas, casi todas vacías), se
    ajusta a lo que hay ese día, con una hora de aire arriba y abajo. Así la
    semana entra completa en la pantalla sin bajar con la rueda.
    """
    if not actividades:
        return 7, 17
    inicios = [_minutos(a["hora"]) for a in actividades]
    finales = [_minutos(a["hora"]) + a["dur"] for a in actividades]
    desde = max(HORA_INICIO, min(inicios) // 60 - 1)
    hasta = min(HORA_FIN, (max(finales) + 59) // 60 + 1)
    if hasta - desde < 7:  # una tira muy corta se ve rara: mínimo 8 filas
        hasta = min(HORA_FIN, desde + 7)
        desde = max(HORA_INICIO, hasta - 7)
    return desde, hasta


def filas_de_horas(actividades, desde, hasta):
    """Las filas del carril, cada una con su alto según tenga trabajo o no."""
    ocupadas = set()
    for a in actividades:
        inicio, fin = _minutos(a["hora"]), _minutos(a["hora"]) + a["dur"]
        for h in range(desde, hasta + 1):
            if inicio < (h + 1) * 60 and fin > h * 60:
                ocupadas.add(h)
    filas, y = [], 0
    for h in range(desde, hasta + 1):
        alto = ALTO_HORA if h in ocupadas else ALTO_HORA_VACIA
        filas.append({"h": h, "etiqueta": hora_bonita(f"{h:02d}:00"),
                      "alto": alto, "top": y, "vacia": h not in ocupadas})
        y += alto
    # El carril llena el alto disponible, como la vista de mes (pedido del
    # dueño, 22/09/2026): alto y top van en PORCENTAJE del total, la fila
    # con trabajo pesa el doble que la vacía, y el CSS estira el lienzo.
    # Así no queda el hueco blanco debajo de la última hora.
    total = y or 1
    for fila in filas:
        fila["alto"] = round(fila["alto"] / total * 100, 4)
        fila["top"] = round(fila["top"] / total * 100, 4)
    return filas


def _y_de(minuto, filas):
    """En qué porcentaje del carril cae un minuto del día (filas de alto
    distinto); las filas ya vienen en % del total."""
    if not filas:
        return 0
    if minuto <= filas[0]["h"] * 60:
        return 0
    for fila in filas:
        inicio = fila["h"] * 60
        if inicio <= minuto < inicio + 60:
            return fila["top"] + (minuto - inicio) / 60 * fila["alto"]
    return filas[-1]["top"] + filas[-1]["alto"]


def linea_ahora(filas):
    """A qué altura va la rayita roja de la hora actual (None si está fuera)."""
    if not filas:
        return None
    ahora = datetime.now(ZONA_PANAMA)
    minutos = ahora.hour * 60 + ahora.minute
    if minutos < filas[0]["h"] * 60 or minutos > (filas[-1]["h"] + 1) * 60:
        return None
    return round(_y_de(minutos, filas), 2)


def carril_semana(actividades, dias, dia_hoy):
    del_rango = [a for a in actividades if a["fecha"] in dias]
    desde, hasta = rango_horas(del_rango)
    filas = filas_de_horas(del_rango, desde, hasta)
    columnas, bloques = [], []
    for i, dia_iso in enumerate(dias):
        del_dia = _del_dia(actividades, dia_iso)
        fecha = _dia(dia_iso)
        columnas.append({
            "iso": dia_iso, "dow": DOW[i], "num": fecha.day,
            "hoy": dia_iso == dia_hoy, "finde": i > 4, "cant": len(del_dia),
            "estilo": (f"left:calc(46px + (100% - 46px) * {i / 7});"
                       f"width:calc((100% - 46px) / 7)"),
        })
        for actividad, columna, total in _repartir(del_dia):
            bloques.append(_bloque(actividad, columna, total, i, 7, dia_hoy, filas))
    return {"columnas": columnas, "bloques": bloques, "horas": filas,
            "ahora": linea_ahora(filas) if dia_hoy in dias else None}


def carril_dia(actividades, dia_iso, gente, dia_hoy):
    if not gente:
        gente = [{"id": "", "nombre": "Sin asignar"}]
    total = len(gente)
    columnas, bloques = [], []
    del_dia = _del_dia(actividades, dia_iso)
    desde, hasta = rango_horas(del_dia)
    filas = filas_de_horas(del_dia, desde, hasta)
    for i, persona in enumerate(gente):
        suyas = [a for a in del_dia if a["resp_id"] == persona["id"]]
        columnas.append({
            "iso": dia_iso, "dow": persona["nombre"], "num": "",
            "persona": persona, "hoy": False, "finde": False, "cant": len(suyas),
            "estilo": (f"left:calc(46px + (100% - 46px) * {i / total});"
                       f"width:calc((100% - 46px) / {total})"),
        })
        for actividad, columna, cuantas in _repartir(suyas):
            bloques.append(_bloque(actividad, columna, cuantas, i, total, dia_hoy, filas))
    return {"columnas": columnas, "bloques": bloques, "horas": filas,
            "ahora": linea_ahora(filas) if dia_iso == dia_hoy else None}


def rejilla_mes(actividades, ancla, dia_elegido, dia_hoy):
    celdas = []
    for dia_iso in dias_de("mes", ancla):
        fecha = _dia(dia_iso)
        del_dia = _del_dia(actividades, dia_iso)
        celdas.append({
            "iso": dia_iso, "num": fecha.day,
            "hoy": dia_iso == dia_hoy,
            "elegido": dia_iso == dia_elegido,
            "otro_mes": fecha.month != ancla.month,
            "atrasadas": len([a for a in del_dia if esta_atrasada(a, dia_hoy)]),
            "barras": [{"a": a, "color": color_de(a["tipo"]),
                        "fondo": _tinta(color_de(a["tipo"]), 0.12)}
                       for a in del_dia[:3]],
            "resto": max(0, len(del_dia) - 3),
        })
    return [celdas[i:i + 7] for i in range(0, len(celdas), 7)]


def grupos_lista(actividades, dias, dia_hoy):
    atrasadas = sorted([a for a in actividades if esta_atrasada(a, dia_hoy)],
                       key=lambda a: (a["fecha"], a["hora"]))
    grupos = []
    if atrasadas:
        grupos.append({"titulo": "Atrasadas", "es_atraso": True,
                       "actividades": atrasadas})
    for dia_iso in dias:
        del_dia = [a for a in _del_dia(actividades, dia_iso)
                   if not esta_atrasada(a, dia_hoy)]
        if not del_dia:
            continue
        fecha = _dia(dia_iso)
        etiqueta = (("HOY · " if dia_iso == dia_hoy else "")
                    + f"{DOW[fecha.weekday()].upper()} {fecha.day} {MESES[fecha.month - 1]}")
        grupos.append({"titulo": etiqueta, "es_atraso": False, "actividades": del_dia})
    return grupos


def vista_movil(actividades, dia_iso, dia_hoy):
    """La pantalla del teléfono: un día a la vez (pedido del dueño, 22/09/2026).

    En el celular no caben siete columnas angostas: se muestra UNA agenda del
    día elegido, con una tira arriba para saltar entre los días de la semana.
    Como todo lo demás, se arma aquí: la tira, el título del mes, las filas
    de hora y qué actividad cae en cada una. La plantilla solo recorre y los
    enlaces (que arma main.py) llevan el estado; el navegador no calcula nada.
    """
    fecha = _dia(dia_iso)
    inicio_semana = _lunes(fecha)
    tira = []
    for i in range(7):
        dia = inicio_semana + timedelta(days=i)
        iso = dia.isoformat()
        tira.append({
            "iso": iso, "dow": DOW[i], "num": dia.day,
            "hoy": iso == dia_hoy, "sel": iso == dia_iso,
            "con_trabajo": bool(_del_dia(actividades, iso)),
        })

    del_dia = _del_dia(actividades, dia_iso)
    desde, hasta = rango_horas(del_dia)
    ahora = datetime.now(ZONA_PANAMA)
    horas = []
    for h in range(desde, hasta + 1):
        # Cada actividad cae en la fila de la hora en que EMPIEZA (las que
        # duran más lo dicen en su propio texto con el rango completo). Las
        # de la misma hora se apilan una debajo de otra: nunca se enciman.
        if h == desde:  # una actividad más temprana que la primera fila
            suyas = [a for a in del_dia if _minutos(a["hora"]) // 60 <= h]
        elif h == hasta:  # o más tarde que la última
            suyas = [a for a in del_dia if _minutos(a["hora"]) // 60 >= h]
        else:
            suyas = [a for a in del_dia if _minutos(a["hora"]) // 60 == h]
        horas.append({
            "h": h, "etiqueta": hora_bonita(f"{h:02d}:00"),
            "actividades": [dict(a, rango=_rango_bonito(a),
                                 atrasada=esta_atrasada(a, dia_hoy),
                                 color=color_de(a["tipo"]),
                                 fondo=_tinta(color_de(a["tipo"]), 0.07))
                            for a in suyas],
            "vacia": not suyas,
            "ahora": dia_iso == dia_hoy and ahora.hour == h,
        })
    return {
        "titulo": f"{MESES[fecha.month - 1].capitalize()} {fecha.year}",
        "subtitulo": ("Hoy" if dia_iso == dia_hoy else DOW_LARGO[fecha.weekday()])
                     + " " + dmy(dia_iso),
        "tira": tira, "horas": horas, "total": len(del_dia),
        "semana_ant": (inicio_semana - timedelta(days=7)).isoformat(),
        "semana_sig": (inicio_semana + timedelta(days=7)).isoformat(),
    }


def _rango_bonito(actividad):
    """9 am – 10:30 am, ya resuelto para la tarjeta del teléfono."""
    inicio = _minutos(actividad["hora"])
    fin_min = min(inicio + actividad["dur"], 23 * 60 + 59)  # no pasa de medianoche
    fin = f"{fin_min // 60:02d}:{fin_min % 60:02d}"
    return f"{hora_bonita(actividad['hora'])} – {hora_bonita(fin)}"


def mini_calendario(mes_ancla, dia_elegido, dias_en_vista, actividades, dia_hoy):
    inicio = _lunes(mes_ancla.replace(day=1))
    celdas = []
    for i in range(42):
        fecha = inicio + timedelta(days=i)
        dia_iso = fecha.isoformat()
        puntos, vistos = [], set()
        for a in _del_dia(actividades, dia_iso)[:3]:
            if a["tipo"] in vistos:
                continue
            vistos.add(a["tipo"])
            puntos.append(color_de(a["tipo"]))
        celdas.append({
            "iso": dia_iso, "num": fecha.day,
            "otro_mes": fecha.month != mes_ancla.month,
            "hoy": dia_iso == dia_hoy,
            "elegido": dia_iso == dia_elegido,
            "en_rango": dia_iso in dias_en_vista,
            "puntos": puntos,
        })
    return {"titulo": f"{MESES[mes_ancla.month - 1].capitalize()} {mes_ancla.year}",
            "celdas": celdas,
            "anterior": (mes_ancla.replace(day=1) - timedelta(days=1)).isoformat(),
            "siguiente": (mes_ancla.replace(day=1) + timedelta(days=32)).replace(day=1).isoformat()}


def resumen_dia(actividades, dia_iso, dia_hoy):
    del_dia = _del_dia(actividades, dia_iso)
    atrasadas = [a for a in del_dia if esta_atrasada(a, dia_hoy)]
    hechas = [a for a in del_dia if a["estado"] == "hecha"]
    pendientes = [a for a in del_dia
                  if a["estado"] in ("pend", "curso") and a not in atrasadas]
    fecha = _dia(dia_iso)
    return {
        "iso": dia_iso,
        "titulo": ("Hoy" if dia_iso == dia_hoy else DOW_LARGO[fecha.weekday()]) + " · " + dmy(dia_iso),
        "total": len(del_dia), "atrasadas": len(atrasadas),
        "pendientes": len(pendientes), "hechas": len(hechas),
        "primeras": del_dia[:6],
        # Cuatro cajones con "0" adentro no dicen nada: la pantalla pinta
        # solo los números que existen, y si no hay ninguno dice "Día libre".
        "marcadores": [m for m in (
            {"v": len(del_dia), "e": "del día", "clase": ""},
            {"v": len(atrasadas), "e": "atrasadas", "clase": "at"},
            {"v": len(hechas), "e": "terminadas", "clase": "ok"},
        ) if m["v"]],
    }


def carga_equipo(actividades, dias, gente, quien):
    filas = [{"id": "", "nombre": "Todo el equipo", "elegido": not quien,
              "cuenta": len([a for a in actividades
                             if a["fecha"] in dias and a["estado"] != "cancel"])}]
    for persona in gente:
        filas.append({
            "id": persona["id"], "nombre": persona["nombre"],
            "elegido": quien == persona["id"],
            "cuenta": len([a for a in actividades
                           if a["fecha"] in dias and a["resp_id"] == persona["id"]
                           and a["estado"] != "cancel"]),
        })
    return filas


def chips(actividades, tipos_apagados):
    return [{"clave": t["clave"], "nombre": t["nombre"], "color": t["color"],
             "encendido": t["clave"] not in tipos_apagados,
             "cuenta": len([a for a in actividades if a["tipo"] == t["clave"]])}
            for t in TIPOS]


def chips_a_la_vista(fichas):
    """Los chips que vale la pena pintar.

    Son trece tipos de actividad y una semana normal usa tres o cuatro: pintar
    los trece dejaba dos filas de ceros arriba del calendario. Se muestran los
    que aparecen en lo que se está viendo, y los que el empleado apagó aunque
    estén en cero (si no, no habría cómo volver a prenderlos).
    """
    return [c for c in fichas if c["cuenta"] or not c["encendido"]]


def paso_de_vista(vista, ancla, direccion):
    """El ‹ y el › de la barra, según la vista."""
    if vista == "semana":
        return ancla + timedelta(days=7 * direccion)
    if vista == "dia":
        return ancla + timedelta(days=direccion)
    if direccion < 0:
        return (ancla.replace(day=1) - timedelta(days=1)).replace(day=1)
    return (ancla.replace(day=1) + timedelta(days=32)).replace(day=1)


def panel_inicio(actividades, dia_hoy=None):
    """Lo que la pestaña Inicio muestra del calendario, ya masticado.

    Números del día y de la semana, la agenda de hoy, lo atrasado y la carga
    de los próximos siete días (con la altura de cada barra en porcentaje).
    El navegador no calcula nada de esto.
    """
    dia_hoy = dia_hoy or hoy().isoformat()
    numeros = resumen(actividades, dia_hoy)
    vivas = [a for a in actividades if a["estado"] != "cancel"]
    agenda = [a for a in _del_dia(vivas, dia_hoy)][:5]
    atrasadas = sorted([a for a in vivas if esta_atrasada(a, dia_hoy)],
                       key=lambda a: a["fecha"])[:3]

    dias = [(_dia(dia_hoy) + timedelta(days=i)).isoformat() for i in range(7)]
    cuentas = {d: len(_del_dia(vivas, d)) for d in dias}
    tope = max(cuentas.values()) if cuentas else 0
    barras = []
    for dia_iso in dias:
        fecha = _dia(dia_iso)
        cuantas = cuentas[dia_iso]
        barras.append({
            "iso": dia_iso,
            "dow": "HOY" if dia_iso == dia_hoy else DOW[fecha.weekday()],
            "num": fecha.day,
            "cuenta": cuantas,
            "hoy": dia_iso == dia_hoy,
            # En porcentaje para que la barra no dependa de píxeles.
            "alto": round(cuantas / tope * 100) if tope else 0,
            "colores": [color_de(a["tipo"]) for a in _del_dia(vivas, dia_iso)[:4]],
        })
    return {"numeros": numeros, "agenda": agenda, "atrasadas": atrasadas,
            "barras": barras, "dia": dia_hoy}
