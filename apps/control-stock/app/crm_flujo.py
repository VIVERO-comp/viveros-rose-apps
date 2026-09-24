"""Lo que queda del espejo del CRM: la trastienda de la pestaña Retail.

**La pestaña CRM murió en la Fase 5** (24/09/2026): su kanban era el
embudo VIEJO (con Contactado y En conversación, que dejaron de existir) y
su trabajo lo hace ahora `control.py` leyendo y escribiendo el equipo LEAD
de Linear. Se fueron la pantalla (`/crm` y sus cuatro POST), la plantilla
`crm.html` y su JS.

Este módulo sigue vivo por UNA razón: `retail.py` lo usa. Retail casa sus
leads con los del espejo (`lead_por_ref`, `senales_retail`,
`_etapas_retail`), y mover una tarjeta de Retail escribe el estado por
aquí (`mover_estado`). El día que Retail se borre —está fuera del menú
desde el 24/09/2026 con `RETAIL_EN_MENU=0`, esperando el OK del dueño—
este archivo se va con él, y no antes: borrarlo hoy dejaría a Retail sin
piso.

Ojo con lo que hay acá adentro: los estados (`COLUMNAS`, `DESTINOS_DRAG`,
`_NOMBRES_LINEAR`) y los motivos (`MOTIVOS`) son el vocabulario VIEJO. Lo
nuevo vive en `linear_leads.py`, que es la única puerta al tablero. Nada
nuevo debería entrar por aquí.

Los leads salen del objeto `leads` del Twenty real. Sin TWENTY_API_KEY
corre con leads de muestra.
"""

import os
import re
import time
from urllib.parse import quote

import httpx

from . import calendario, colores, crm_leads, crm_twenty

TTL_LEADS = 120

# Mismo orden y nombres que COLUMNAS_LEADS del admin
# (viveros-rose-frontend/src/pages/api/crm/tablero.ts); el punto de la
# columna lleva el tono FUERTE de la familia del estado (paleta unica).
def _col(clave, titulo):
    # Con `.get`: desde el 24/09/2026 la paleta unica lleva los 8 estados
    # del embudo NUEVO, y estas columnas son las del embudo viejo (con
    # Contactado y En conversacion, que dejaron de existir). Esta pantalla
    # muere en la Fase 5; hasta entonces pinta en gris lo que ya no esta en
    # la paleta, en vez de reventar.
    familia = colores.ASIGNACIONES["estado_lead"].get(clave, "gray")
    return {"clave": clave, "titulo": titulo,
            "color": colores.FAMILIAS[familia]["solido_hex"]}


COLUMNAS = [
    _col("NUEVO", "Nuevo"),
    _col("CONTACTADO", "Contactado"),
    _col("EN_CONVERSACION", "En conversación"),
    _col("PEDIDO_PENDIENTE", "Pedido pendiente"),
    _col("GANADO", "Ganado"),
    _col("PERDIDO", "Perdido"),
]

# Vocabulario de chips compartido con el admin y los Chats
# (viveros-rose-frontend/src/lib/chips-lead.ts): mismo texto, y el color de
# la familia del tipo en la paleta unica (tono de texto: sirve como chip y
# como fondo solido con texto blanco en la ficha).
_NOMBRE_INTERES = {
    "PLANTAS_RETAIL": "Plantas retail",
    "MAYORISTA": "Mayorista",
    "EVENTOS": "Eventos",
    "EVENTOS_ALQUILER": "Eventos · Alquiler",
    "EVENTOS_BODAS": "Eventos · Bodas",
    "EVENTOS_FERIAS": "Eventos · Ferias",
    "MANTENIMIENTO": "Mantenimiento",
    "PAISAJISMO": "Paisajismo",
    "SERVICIOS_PROYECTOS": "Servicios · Proyectos",
    "SERVICIOS_INSTALACION": "Servicios · Instalación",
    "CONSTRUCCION": "Construcción",
}
ETIQUETA_INTERES = {
    clave: (nombre, colores.texto_hex(colores.ASIGNACIONES["tipo_interes"][clave]))
    for clave, nombre in _NOMBRE_INTERES.items()
}
# El mismo vocabulario, como catálogo del selector de la ficha: desde el
# 23/09/2026 el tipo de interés se CORRIGE desde aquí (pedido de Abraham),
# porque lo pone el clasificador leyendo el mensaje y a veces se equivoca.
INTERESES = dict(_NOMBRE_INTERES)

MOTIVOS = {
    "NO_CONTESTO": "No contestó",
    "DEJO_DE_RESPONDER": "Dejó de responder",
    "DIJO_QUE_NO": "Dijo que no / precio",
    "SOLO_PREGUNTABA": "Solo preguntaba",
}

_cache = {"en": 0, "dato": None}

# En modo muestra las escrituras (motivo) caen sobre estas filas, para que
# el tablero y Control se muevan igual que con el Twenty real.
_MUESTRA = [
    {"name": "PP-70211 · Tamara", "estado": "NUEVO", "tipoInteres": ["PLANTAS_RETAIL"],
     "motivoNoAvance": "", "createdAt": "2026-09-23T14:40:00+00:00", "id": "L1",
     "personaId": "p1", "leadWebId": "", "linearIssueUrl": ""},
    {"name": "PP-70208 · Kev", "estado": "EN_CONVERSACION", "tipoInteres": ["PLANTAS_RETAIL"],
     "motivoNoAvance": "", "createdAt": "2026-09-22T10:00:00+00:00", "id": "L2",
     "personaId": "p2", "leadWebId": "",
     "linearIssueUrl": "https://linear.app/viverorose/issue/LEAD-45/kev"},
    {"name": "PP-70202 · NC Renovando Vidas", "estado": "CONTACTADO", "tipoInteres": ["MAYORISTA"],
     "motivoNoAvance": "", "createdAt": "2026-09-21T09:30:00+00:00", "id": "L3",
     "personaId": "p3", "leadWebId": "",
     "linearIssueUrl": "https://linear.app/viverorose/issue/LEAD-34/nc-renovando-vidas"},
    {"name": "PP-70195 · Soledad", "estado": "GANADO", "tipoInteres": ["PLANTAS_RETAIL"],
     "motivoNoAvance": "", "createdAt": "2026-09-19T15:00:00+00:00", "id": "L4",
     "personaId": "p4", "leadWebId": "",
     "linearIssueUrl": "https://linear.app/viverorose/issue/LEAD-44/soledad"},
    {"name": "PP-70190 · Monica Gama", "estado": "CONTACTADO", "tipoInteres": ["PLANTAS_RETAIL"],
     "motivoNoAvance": "SOLO_PREGUNTABA", "createdAt": "2026-09-18T12:00:00+00:00", "id": "L5",
     "personaId": "p5", "leadWebId": "",
     "linearIssueUrl": "https://linear.app/viverorose/issue/LEAD-42/monica-gama"},
]


def _crudos():
    if not crm_twenty.twenty_configurado():
        return [dict(f) for f in _MUESTRA]
    if _cache["dato"] is not None:
        if time.time() - _cache["en"] >= TTL_LEADS:
            calendario._en_fondo("crm-flujo", _buscar)
        return [dict(f) for f in _cache["dato"]]
    try:
        return [dict(f) for f in _buscar()]
    except Exception:
        return []


def _buscar():
    filas = []
    cursor = None
    for _ in range(4):  # hasta 240 leads: de sobra para el tablero
        ruta = "leads?order_by=createdAt[DescNullsLast]&limit=60"
        if cursor:
            ruta += "&starting_after=" + cursor
        j = crm_twenty._twenty(ruta)
        filas.extend((j.get("data") or {}).get("leads") or [])
        pagina = j.get("pageInfo") or (j.get("data") or {}).get("pageInfo") or {}
        cursor = pagina.get("endCursor") if pagina.get("hasNextPage") else None
        if not cursor:
            break
    _cache.update({"en": time.time(), "dato": filas})
    return filas


def refrescar():
    _cache.update({"en": 0, "dato": None})


def _hace_bonito(iso_texto):
    from datetime import datetime
    from .datos import ZONA_PANAMA
    try:
        cuando = datetime.fromisoformat(str(iso_texto).replace("Z", "+00:00"))
        dias = (datetime.now(ZONA_PANAMA).date()
                - cuando.astimezone(ZONA_PANAMA).date()).days
    except (ValueError, TypeError):
        return ""
    if dias <= 0:
        return "hoy"
    return f"hace {dias} día" + ("s" if dias > 1 else "")


def _tarjeta(fila, etapas_retail=None):
    chips = []
    for tipo in (fila.get("tipoInteres") or []):
        if tipo in ETIQUETA_INTERES:
            texto, color = ETIQUETA_INTERES[tipo]
            chips.append({"texto": texto, "color": color})
    tipos = fila.get("tipoInteres") or []
    ref_retail = _ref_de(fila.get("linearIssueUrl") or "")
    return {
        "id": fila.get("id") or "",
        # La clave del tipo (no el texto): la usa el selector de la ficha
        # para marcar cuál está puesto.
        "interes": tipos[0] if tipos else "",
        "titulo": fila.get("name") or "Lead sin código",
        "estado": fila.get("estado") or "",
        "motivo": MOTIVOS.get(fila.get("motivoNoAvance") or "", ""),
        "motivo_clave": fila.get("motivoNoAvance") or "",
        "chips": chips,
        "hace": _hace_bonito(fila.get("fechaLead") or fila.get("createdAt") or ""),
        "canal": fila.get("canal") or "",
        "pagina": fila.get("paginaContacto") or fila.get("primeraPagina") or "",
        "persona_id": fila.get("personaId") or "",
        "twenty_url": (f"{crm_twenty.twenty_publico()}/object/lead/{fila['id']}"
                       if fila.get("id") else ""),
        "linear_url": fila.get("linearIssueUrl") or "",
        # El label chiquito "dónde está en Retail" (dueño, 23/09/2026).
        "retail_ref": ref_retail,
        "retail_etapa": (etapas_retail or {}).get(ref_retail, ""),
    }


def tablero():
    """([columnas], [inactivos]) igual repartido que el tablero del admin:
    un lead con motivo no va en columnas, va en Inactivos."""
    etapas_retail = _etapas_retail()
    tarjetas = [_tarjeta(f, etapas_retail) for f in _crudos()]
    inactivos = [t for t in tarjetas if t["motivo"]]
    activas = [t for t in tarjetas if not t["motivo"]]
    columnas = [dict(col, tarjetas=[t for t in activas if t["estado"] == col["clave"]])
                for col in COLUMNAS]
    return columnas, inactivos


def por_persona():
    """{personaId: fila cruda} — con esto Control casa cada chat con su
    lead (los mensajes de Twenty traen el personaId) y hereda Ganado y el
    motivo de la MISMA fuente que el admin."""
    filas = {}
    for f in _crudos():
        if f.get("personaId"):
            filas.setdefault(f["personaId"], f)
    return filas


# ---------------------------------------------------------------------------
# El amarre con la pestaña Retail (pedido de Abraham, 23/09/2026): los dos
# tableros son el mismo negocio, así que "Facturado · por entregar" es
# "Pedido pendiente" y "Entregado" es "Ganado". Retail casa sus leads
# (LEAD-NN) con estas filas por la URL del issue de Linear.
# ---------------------------------------------------------------------------

_REF_LINEAR = re.compile(r"/([A-Z]+-\d+)(?:/|$)")

# estado del CRM -> etapa MÍNIMA del kanban Retail que ese estado impone.
_ESTADO_A_ETAPA_RETAIL = {"PEDIDO_PENDIENTE": "entregar", "GANADO": "entregado"}


def _ref_de(url):
    """El identificador LEAD-NN dentro de una URL de issue de Linear."""
    encontrado = _REF_LINEAR.search(url or "")
    return encontrado.group(1) if encontrado else ""


def lead_por_ref(ref):
    """La fila cruda del lead cuyo issue de Linear es `ref` (LEAD-NN), o
    None si ningún lead del espejo apunta a ese issue."""
    ref = (ref or "").strip()
    if not ref:
        return None
    for f in _crudos():
        if _ref_de(f.get("linearIssueUrl") or "") == ref:
            return f
    return None


def senales_retail():
    """{LEAD-NN: {"piso", "inactivo"}}: lo que el espejo del CRM le dicta
    al tablero Retail — la etapa mínima que el estado impone y si el lead
    está inactivo (tiene motivo). En vivo Retail saca estas señales de su
    propia consulta a Linear (estado y label "Desactivado" del issue);
    esta función alimenta el modo muestra."""
    senales = {}
    for f in _crudos():
        ref = _ref_de(f.get("linearIssueUrl") or "")
        if ref:
            senales[ref] = {
                "piso": _ESTADO_A_ETAPA_RETAIL.get(f.get("estado") or ""),
                "inactivo": bool(f.get("motivoNoAvance")),
            }
    return senales


def _refrescar_retail():
    """Tras una escritura del CRM (drag, motivo) el tablero Retail no puede
    quedarse 2 minutos (su TTL) enseñando lo viejo: un lead recién ganado
    debe caer en Entregado y un inactivo debe salir del tablero ya."""
    from . import retail  # aquí abajo para no ciclar imports
    retail.refrescar()


# El label chiquito de la tarjeta (pedido del dueño, 23/09/2026): dónde
# está el lead dentro del kanban Retail.
ETAPA_RETAIL_ETIQUETA = {
    "cotizar": "Retail · por cotizar",
    "facturar": "Retail · por facturar",
    "entregar": "Retail · por entregar",
    "entregado": "Retail · entregado",
}


def _etapas_retail():
    """{LEAD-NN: etiqueta chiquita} — la etapa de cada lead en el kanban
    Retail. Best-effort: sin tablero Retail (Linear caído), sin labels."""
    from . import retail  # aquí abajo para no ciclar imports
    try:
        _cols, por_ref = retail.tablero()
    except Exception:
        return {}
    return {ref: ETAPA_RETAIL_ETIQUETA.get(l.get("etapa"), "")
            for ref, l in por_ref.items()}


# ---------------------------------------------------------------------------
# La ficha del lead (lo mismo que carga /api/crm/lead-detalle en el panel)
# ---------------------------------------------------------------------------

_MENSAJES_MUESTRA = {
    "p1": [{"texto": "¿Tienen calatheas grandes?", "salida": False,
            "cuando": "23/09 · 09:40"}],
    "p5": [{"texto": "Gracias, era solo por saber 🙏", "salida": False,
            "cuando": "18/09 · 12:00"}],
}


def _fila_por_id(lead_id):
    for f in _crudos():
        if f.get("id") == lead_id:
            return f
    return None


def _issue_de(fila):
    """El id del issue de Linear del lead (vía su leadWeb), o ""."""
    if not fila.get("leadWebId") or not crm_twenty.twenty_configurado():
        return ""
    try:
        j = crm_twenty._twenty(f"leadsWeb/{quote(fila['leadWebId'])}")
        return ((j.get("data") or {}).get("leadWeb") or {}).get("linearIssueId") or ""
    except Exception:
        return ""


def _conversacion(persona_id):
    """La conversación completa de WhatsApp de la Person, vieja→nueva."""
    mensajes = []
    cursor = None
    for _ in range(5):
        ruta = ('mensajesWhatsapp?filter=personaId[eq]:"' + quote(persona_id)
                + '"&order_by=fecha[AscNullsFirst]&limit=60')
        if cursor:
            ruta += "&starting_after=" + quote(cursor)
        j = crm_twenty._twenty(ruta)
        mensajes.extend((j.get("data") or {}).get("mensajesWhatsapp") or [])
        pagina = j.get("pageInfo") or (j.get("data") or {}).get("pageInfo") or {}
        cursor = pagina.get("endCursor") if pagina.get("hasNextPage") else None
        if not cursor:
            break
    mensajes.sort(key=lambda m: str(m.get("fecha") or ""))
    return [{"texto": m.get("texto") or "",
             "salida": (m.get("direccion") or "") == "SALIENTE",
             "cuando": crm_twenty._cuando_bonito(m.get("fecha") or m.get("createdAt") or "")}
            for m in mensajes[-300:]]


CONSULTA_NOTAS = """
query Comentarios($id: String!) {
  issue(id: $id) { comments(first: 50) { nodes { body createdAt user { name } } } }
}
"""


def _notas(issue_id):
    """Los comentarios del issue de Linear (las notas del panel viven ahí,
    firmadas), del más viejo al más nuevo. Best-effort."""
    if not issue_id:
        return []
    try:
        datos = calendario._pedir(CONSULTA_NOTAS, {"id": issue_id})
        nodos = ((datos.get("issue") or {}).get("comments") or {}).get("nodes") or []
    except Exception:
        return []
    notas = [{"texto": n.get("body") or "",
              "cuando": crm_twenty._cuando_bonito(n.get("createdAt") or ""),
              "fecha": n.get("createdAt") or "",
              "autor": (n.get("user") or {}).get("name") or ""}
             for n in nodos]
    notas.sort(key=lambda n: n["fecha"])
    return notas


def detalle(lead_id):
    """La ficha completa: fila + persona + notas + conversación. None si el
    lead no existe."""
    fila = _fila_por_id(lead_id)
    if fila is None:
        return None
    ficha = _tarjeta(fila, _etapas_retail())
    if not crm_twenty.twenty_configurado():
        ficha.update({"telefono": "", "wa": "", "notas": [],
                      "mensajes": _MENSAJES_MUESTRA.get(fila.get("personaId"), [])})
        return ficha
    telefono = wa = ""
    mensajes = []
    if fila.get("personaId"):
        try:
            p = (crm_twenty._twenty(f"people/{quote(fila['personaId'])}")
                 .get("data") or {}).get("person") or {}
            telefono = crm_twenty.telefono_legible(p.get("phones"))
            wa = crm_twenty.telefono_wame(p.get("phones"))
        except Exception:
            pass
        try:
            mensajes = _conversacion(fila["personaId"])
        except Exception:
            mensajes = []
    ficha.update({"telefono": telefono, "wa": wa,
                  "notas": _notas(_issue_de(fila)), "mensajes": mensajes})
    return ficha


# ---------------------------------------------------------------------------
# Escrituras EN SINCRONÍA con el admin: por las mismas rutas del panel
# (X-Clave-Admin), con respaldo directo si la clave no está configurada
# ---------------------------------------------------------------------------

def _admin_url():
    return (os.environ.get("CRM_ADMIN_URL") or "https://www.plantaspanama.com").rstrip("/")


def _admin_clave():
    return (os.environ.get("CRM_ADMIN_CLAVE") or "").strip()


def _ruta_admin(ruta, cuerpo):
    respuesta = httpx.post(
        f"{_admin_url()}{ruta}", json=cuerpo,
        headers={"X-Clave-Admin": _admin_clave()}, timeout=12.0)
    return respuesta.status_code < 300


def registrar_motivo(lead_id, motivo):
    """Pone (o con "" quita) el porqué del lead, como lo haría el panel.
    Con CRM_ADMIN_CLAVE va por /api/crm/lead-motivo (Twenty + labels de
    Linear + archivo en Odoo, todo el pipeline del admin); sin clave, el
    respaldo escribe motivoNoAvance directo en Twenty — los tableros
    quedan igual. Vuelve True si se escribió."""
    if motivo and motivo not in MOTIVOS:
        return False
    ok = False
    if not crm_twenty.twenty_configurado():
        fila = next((f for f in _MUESTRA if f["id"] == lead_id), None)
        if fila is not None:
            fila["motivoNoAvance"] = motivo
            ok = True
    elif _admin_clave():
        try:
            ok = _ruta_admin("/api/crm/lead-motivo",
                             {"leadId": lead_id, "motivo": motivo})
        except httpx.HTTPError:
            ok = False
    else:
        try:
            crm_twenty._twenty_patch(f"leads/{quote(lead_id)}",
                                     {"motivoNoAvance": motivo or None})
            ok = True
        except Exception:
            ok = False
    if ok:
        refrescar()
        _refrescar_retail()
    return ok


def cambiar_interes(lead_id, interes):
    """Corrige el tipo de interés del lead (Abraham, 23/09/2026).

    El tipo lo pone el sistema al nacer el lead leyendo su mensaje de
    WhatsApp, y de él cuelgan los dos tableros: la pestaña Retail solo lista
    los de venta y el log del calendario solo los de servicio. Por eso la
    corrección NO se escribe aquí a medias: va por el puente del frontend
    (/api/crm/lead-interes), que mueve Twenty, la label de Linear y la
    etiqueta de la oportunidad en Odoo de una sola vez.

    En modo muestra (sin Twenty) se cambia la fila de ejemplo, para poder
    probar la pantalla en local. Devuelve None si no se pudo, o un dict con
    lo que de verdad quedó escrito ({"en_linear", "en_odoo"}) para que el
    aviso de la pantalla no prometa de más."""
    if interes not in INTERESES:
        return None
    hecho = {"en_linear": False, "en_odoo": False}
    if not crm_twenty.twenty_configurado():
        fila = next((f for f in _MUESTRA if f["id"] == lead_id), None)
        if fila is None:
            return None
        fila["tipoInteres"] = [interes]
    else:
        cuerpo = crm_leads.cambiar_tipo_de_interes(lead_id, interes)
        if not cuerpo:
            return None
        hecho = {"en_linear": bool(cuerpo.get("enLinear")),
                 "en_odoo": bool(cuerpo.get("enOdoo"))}
    refrescar()
    _refrescar_retail()
    # El log de leads de servicio del calendario se arma con las labels de
    # Linear: se tira su caché para que el lead aparezca (o desaparezca) de
    # una vez, sin esperar los 2 minutos del TTL.
    calendario._leads_cache.update({"en": 0, "dato": None})
    return hecho


# ---------------------------------------------------------------------------
# El drag del kanban (decisión del dueño, 23/09/2026): las tarjetas se
# arrastran, pero SOLO caen en En conversación, Pedido pendiente y Ganado —
# Nuevo lo pone el sistema al nacer el lead, Contactado lo pone el flujo al
# responderle, y Perdido va por el porqué. El movimiento va a Linear (la
# fuente del pipeline) y se espeja a Twenty al instante, igual que hace la
# ruta /api/crm/lead-estado del panel.
# ---------------------------------------------------------------------------

DESTINOS_DRAG = {"EN_CONVERSACION", "PEDIDO_PENDIENTE", "GANADO"}

# nombre del estado en Linear (sin acentos, minúsculas) -> valor de Twenty;
# el mismo mapa de lib/server/estados.ts del frontend.
_NOMBRES_LINEAR = {
    "nuevo": "NUEVO", "contactado": "CONTACTADO",
    "en conversacion": "EN_CONVERSACION",
    "pedido pendiente": "PEDIDO_PENDIENTE",
    "ganado": "GANADO", "perdido": "PERDIDO",
}

CONSULTA_ESTADOS_LEAD = """
query { teams(filter: { key: { eq: "LEAD" } })
  { nodes { states { nodes { id name } } } } }
"""

MUTACION_ESTADO = """
mutation Mover($id: String!, $state: String!) {
  issueUpdate(id: $id, input: { stateId: $state }) { success }
}
"""


def _sin_acentos(texto):
    import unicodedata
    plano = unicodedata.normalize("NFD", texto or "")
    return "".join(c for c in plano if not unicodedata.combining(c)).strip().lower()


def _estado_linear(valor):
    """El id del estado de Linear (team LEAD) que corresponde al valor de
    Twenty, o ""."""
    datos = calendario._pedir(CONSULTA_ESTADOS_LEAD)
    equipos = (datos.get("teams") or {}).get("nodes") or []
    estados = ((equipos[0] if equipos else {}).get("states") or {}).get("nodes") or []
    for e in estados:
        if _NOMBRES_LINEAR.get(_sin_acentos(e.get("name"))) == valor:
            return e.get("id") or ""
    return ""


def mover_estado(lead_id, estado):
    """Mueve el lead de columna como lo haría el drag del admin. Con
    CRM_ADMIN_CLAVE va por /api/crm/lead-estado (la misma ruta del panel);
    sin clave, el respaldo mueve el issue en Linear directo y espeja el
    estado en Twenty. Vuelve True si quedó."""
    if estado not in DESTINOS_DRAG:
        return False
    if not crm_twenty.twenty_configurado():
        fila = next((f for f in _MUESTRA if f["id"] == lead_id), None)
        if fila is None:
            return False
        fila["estado"] = estado
        refrescar()
        _refrescar_retail()
        return True
    if _admin_clave():
        try:
            ok = _ruta_admin("/api/crm/lead-estado",
                             {"leadId": lead_id, "estado": estado})
        except httpx.HTTPError:
            ok = False
        if ok:
            refrescar()
            _refrescar_retail()
        return ok
    fila = _fila_por_id(lead_id)
    if fila is None:
        return False
    # Linear primero (la fuente); un lead sin issue solo espeja Twenty.
    issue = _issue_de(fila)
    if issue:
        try:
            destino = _estado_linear(estado)
            if not destino:
                return False
            datos = calendario._pedir(MUTACION_ESTADO,
                                      {"id": issue, "state": destino})
            if not (datos.get("issueUpdate") or {}).get("success"):
                return False
        except Exception:
            return False
    try:
        crm_twenty._twenty_patch(f"leads/{quote(lead_id)}", {"estado": estado})
    except Exception:
        return False
    refrescar()
    _refrescar_retail()
    return True


MUTACION_NOTA = """
mutation Comentar($issueId: String!, $body: String!) {
  commentCreate(input: { issueId: $issueId, body: $body }) { success }
}
"""


def escribir_nota(lead_id, texto):
    """Una nota sobre el lead, como la del panel: comentario en el issue de
    Linear (firmado). Con CRM_ADMIN_CLAVE va por /api/crm/lead-nota; sin
    clave, el respaldo comenta el issue directo con la conexión a Linear
    que ya existe. Vuelve True si quedó."""
    texto = (texto or "").strip()[:2000]
    if not texto:
        return False
    if not crm_twenty.twenty_configurado():
        return True  # en muestra no hay dónde anotar; la pantalla sigue
    if _admin_clave():
        try:
            return _ruta_admin("/api/crm/lead-nota",
                               {"leadId": lead_id, "texto": texto})
        except httpx.HTTPError:
            return False
    fila = _fila_por_id(lead_id)
    issue = _issue_de(fila) if fila else ""
    if not issue:
        return False
    try:
        datos = calendario._pedir(MUTACION_NOTA,
                                  {"issueId": issue,
                                   "body": f"{texto}\n\n_— desde inventario_"})
        return bool((datos.get("commentCreate") or {}).get("success"))
    except Exception:
        return False
