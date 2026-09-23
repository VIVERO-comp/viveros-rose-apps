"""La pestaña Control: el kanban de los chats de WhatsApp.

Versión C elegida por el dueño en el artefacto "Tablero Control"
(23/09/2026): cuatro columnas, la tarjeta enseña el último mensaje y trae
el botón de responder por WhatsApp, y las reglas del "semáforo":

- **En curso** (primera): el vivero contestó de último — la conversación
  va andando. Negociando o en la entrega, la tarjeta se queda aquí: nada
  la archiva sola.
- **Esperando respuesta**: el cliente escribió de último y ESPERA la
  respuesta del vivero — punto rojo (corrección de Abraham, 23/09/2026:
  al principio quedó al revés).
- **Terminado**: cae sola cuando el lead sale **Ganado** en el CRM del
  admin (el pipeline se mueve en Linear; aquí se lee ese estado). Va
  ANTES de Inactivo (pedido del mismo día).
- **Inactivo**: SOLO a mano, con motivo (los mismos motivoNoAvance del
  CRM: No contestó · Dejó de responder · Solo preguntaba · Dijo que no).

La mano siempre le gana al automático: una tarjeta arrastrada se queda
donde la pusieron hasta que llegue un mensaje NUEVO del chat — entonces
las reglas vuelven a decidir (un inactivo revive, un "esperando" con
mensaje del cliente vuelve a En curso con su punto rojo).

Los mensajes salen del Twenty real (los mismos `mensajesWhatsapp` del
panel /admin) y los Ganados de Linear, con el caché y el refresco en fondo
de siempre. Sin claves corre con datos de muestra.
"""

import re
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

from . import calendario, crm_flujo, crm_twenty, retail
from .datos import ZONA_PANAMA, _db

DIAS_VENTANA = 7      # cuántos días de mensajes se miran
TTL_CHATS = 120       # mismo TTL que los leads de Retail

# Corrección de Abraham (23/09/2026): "Esperando respuesta" es el chat que
# ESPERA la respuesta del vivero (el cliente escribió de último); "En
# curso" es la conversación andando (el vivero contestó de último).
COLUMNAS = [
    {"clave": "en_curso", "titulo": "En curso",
     "pie": "El vivero contestó de último; la conversación va andando."},
    {"clave": "esperando", "titulo": "Esperando respuesta",
     "pie": "El cliente escribió de último: alguien le debe respuesta."},
    {"clave": "terminado", "titulo": "Terminado",
     "pie": "Ganado en el CRM del admin: cae aquí sola."},
    {"clave": "inactivo", "titulo": "Inactivo",
     "pie": "Dijo gracias, no contestó o solo preguntaba. Si escribe, revive."},
]
CLAVES_COLUMNA = {c["clave"] for c in COLUMNAS}

# Los mismos motivos del CRM (lead.motivoNoAvance): así el día que esto se
# escriba de vuelta a Twenty (fase 2 del plan), el vocabulario ya calza.
MOTIVOS = {
    "no_contesto": "No contestó",
    "dejo_de_responder": "Dejó de responder",
    "solo_preguntaba": "Solo preguntaba",
    "dijo_que_no": "Dijo que no / precio",
}

_cache = {"en": 0, "dato": None}


def iniciar_tablas():
    with _db() as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS control_tablero (
                chat TEXT PRIMARY KEY,
                columna TEXT NOT NULL,
                motivo TEXT,
                ultima TEXT NOT NULL,
                actualizado TEXT NOT NULL
            )
        """)


# ---------------------------------------------------------------------------
# Lectura de chats (Twenty o muestra) y de los Ganados (Linear o muestra)
# ---------------------------------------------------------------------------

_MUESTRA = [
    {"chat": "m1", "nombre": "Tamara",         "cel": "6552-0966", "dir": "ENTRANTE",
     "persona": "p1",
     "texto": "¿Tienen calatheas grandes?", "fecha": "2026-09-23T14:40:00+00:00"},
    {"chat": "m2", "nombre": "Kev",            "cel": "6209-7754", "dir": "SALIENTE",
     "persona": "p2",
     "texto": "Le paso el precio en un rato 🌿", "fecha": "2026-09-23T13:05:00+00:00"},
    {"chat": "m3", "nombre": "Diana Caballero", "cel": "6114-9077", "dir": "ENTRANTE",
     "persona": "",  # número sin lead en el CRM: vive solo en este tablero
     "texto": "Buenas, sigo esperando la cotización", "fecha": "2026-09-22T21:10:00+00:00"},
    {"chat": "m4", "nombre": "Soledad",        "cel": "6455-1832", "dir": "SALIENTE",
     "persona": "p4",  # su lead ya salió Ganado en el CRM
     "texto": "¡Que las disfrute! Cualquier cosa me escribe 🌿",
     "fecha": "2026-09-21T16:20:00+00:00"},
]


def _legible(telefono):
    """El teléfono crudo del mensaje -> '6203-7333' (o tal cual si no es
    un celular panameño de 8 dígitos)."""
    digitos = re.sub(r"\D", "", telefono or "")
    if digitos.startswith("507"):
        digitos = digitos[3:]
    if len(digitos) == 8:
        return f"{digitos[:4]}-{digitos[4:]}"
    return digitos


def _crudos():
    """[{chat, nombre, cel, dir, texto, fecha}] del período, sin el estado
    del tablero todavía."""
    if not crm_twenty.twenty_configurado():
        return [dict(c) for c in _MUESTRA]
    if _cache["dato"] is not None:
        if time.time() - _cache["en"] >= TTL_CHATS:
            calendario._en_fondo("control", _buscar)
        return [dict(c) for c in _cache["dato"]]
    try:
        return [dict(c) for c in _buscar()]
    except Exception:
        return []


def _buscar():
    # UTC con "Z", el mismo formato que manda el panel /admin: a Twenty el
    # ISO con offset (-05:00) no le gusta en los filtros.
    desde = datetime.now(timezone.utc) - timedelta(days=DIAS_VENTANA)
    desde_iso = desde.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    mensajes = []
    cursor = None
    for _ in range(10):  # tope duro: 600 mensajes bastan y sobran
        ruta = ('mensajesWhatsapp?filter=fecha[gte]:"' + quote(desde_iso)
                + '"&order_by=fecha[AscNullsFirst]&limit=60')
        if cursor:
            ruta += "&starting_after=" + quote(cursor)
        j = crm_twenty._twenty(ruta)
        mensajes.extend((j.get("data") or {}).get("mensajesWhatsapp") or [])
        pagina = j.get("pageInfo") or (j.get("data") or {}).get("pageInfo") or {}
        cursor = pagina.get("endCursor") if pagina.get("hasNextPage") else None
        if not cursor:
            break

    por_chat = {}
    for m in mensajes:
        chat_id = m.get("chatId")
        if not chat_id:
            continue
        c = por_chat.setdefault(chat_id, {"chat": chat_id, "nombre": "", "cel": "",
                                          "persona": "", "dir": "", "texto": "",
                                          "fecha": ""})
        if m.get("chatNombre") and not c["nombre"]:
            c["nombre"] = m["chatNombre"]
        if m.get("telefono") and not c["cel"]:
            c["cel"] = _legible(m["telefono"])
        if m.get("personaId") and not c["persona"]:
            c["persona"] = m["personaId"]
        fecha = str(m.get("fecha") or m.get("createdAt") or "")
        if fecha >= c["fecha"]:
            c.update({"fecha": fecha, "texto": m.get("texto") or "",
                      "dir": m.get("direccion") or "ENTRANTE"})
    filas = list(por_chat.values())
    _cache.update({"en": time.time(), "dato": filas})
    return filas


def refrescar():
    _cache.update({"en": 0, "dato": None})


# ---------------------------------------------------------------------------
# El tablero: Ganado manda, luego la mano (hasta el próximo mensaje),
# luego el semáforo (quién habló de último)
# ---------------------------------------------------------------------------

def _marcas():
    iniciar_tablas()
    with _db() as con:
        filas = con.execute(
            "SELECT chat, columna, motivo, ultima FROM control_tablero").fetchall()
    return {f[0]: {"columna": f[1], "motivo": f[2], "ultima": f[3]} for f in filas}


def _leads_por_celular():
    """{últimos 8 dígitos: lead de Retail} para el salto 'Ver en Retail'."""
    por_cel = {}
    for lead in retail._crudos():
        digitos = re.sub(r"\D", "", lead.get("cel") or "")[-8:]
        if len(digitos) == 8:
            por_cel.setdefault(digitos, lead)
    return por_cel


def _hace_bonito(iso_texto):
    try:
        cuando = datetime.fromisoformat(str(iso_texto).replace("Z", "+00:00"))
        dias = (datetime.now(ZONA_PANAMA).date()
                - cuando.astimezone(ZONA_PANAMA).date()).days
    except (ValueError, TypeError):
        return ""
    if dias <= 0:
        return "hoy"
    return f"hace {dias} día" + ("s" if dias > 1 else "")


def tablero():
    """([columnas con sus chats], {chat_id: chat}) todo resuelto en Python.

    La prioridad de cada tarjeta, en sincronía con el CRM del admin:
    1. Su lead salió GANADO → Terminado (nadie la mueve a mano de ahí).
    2. La mano del empleado, mientras no llegue un mensaje más nuevo —
       EXCEPTO un "inactivo" local de un chat con lead: ese vive en el
       motivo del lead en Twenty (la mano lo escribió allá), así que si
       Twenty ya no lo tiene (el admin lo quitó), aquí tampoco.
    3. El motivo del lead en Twenty → Inactivo (puesto aquí o en el admin,
       es el mismo dato).
    4. El semáforo: quién habló de último.
    """
    marcas = _marcas()
    leads = _leads_por_celular()
    crm = crm_flujo.por_persona()
    chats = []
    for c in _crudos():
        digitos = re.sub(r"\D", "", c.get("cel") or "")[-8:]
        lead_crm = crm.get(c.get("persona") or "")
        motivo_crm = (lead_crm or {}).get("motivoNoAvance") or ""
        marca = marcas.get(c["chat"])
        mano_fresca = bool(marca) and c["fecha"] <= marca["ultima"]
        if lead_crm and lead_crm.get("estado") == "GANADO":
            c["columna"], c["motivo"] = "terminado", ""
        elif mano_fresca and not (marca["columna"] == "inactivo" and lead_crm):
            c["columna"] = marca["columna"]
            c["motivo"] = MOTIVOS.get(marca["motivo"] or "", "")
        elif motivo_crm:
            c["columna"] = "inactivo"
            c["motivo"] = crm_flujo.MOTIVOS.get(motivo_crm, motivo_crm)
        else:
            # El semáforo: quién habló de último. El cliente escribió →
            # Esperando respuesta (le deben una); el vivero contestó → En
            # curso. Un inactivo con mensaje nuevo del cliente cae aquí y
            # por eso revive solo.
            c["columna"] = "en_curso" if c["dir"] == "SALIENTE" else "esperando"
            c["motivo"] = ""
        c["debe"] = c["columna"] == "esperando" and c["dir"] != "SALIENTE"
        c["nombre"] = c["nombre"] or c["cel"] or "Sin identificar"
        c["cuando"] = crm_twenty._cuando_bonito(c["fecha"])
        c["hace"] = _hace_bonito(c["fecha"])
        c["lead"] = leads.get(digitos)  # None si no calza con un lead de Retail
        c["lead_crm_id"] = (lead_crm or {}).get("id") or ""
        c["ganado"] = c["columna"] == "terminado"
        chats.append(c)
    chats.sort(key=lambda c: c["fecha"], reverse=True)
    columnas = [dict(col, chats=[c for c in chats if c["columna"] == col["clave"]])
                for col in COLUMNAS]
    return columnas, {c["chat"]: c for c in chats}


def mover(chat_id, columna, motivo=""):
    """Guarda la mano del empleado y la SINCRONIZA con el CRM del admin:
    mandar a Inactivo un chat con lead registra el motivo en el lead (el
    mismo porqué que pone el panel), y sacarlo de Inactivo lo quita. La
    mano local manda hasta que el chat reciba un mensaje más nuevo que
    `ultima`; entonces el semáforo vuelve a decidir. Vuelve "" si todo
    bien, o el mensaje de error para la pantalla."""
    if columna not in CLAVES_COLUMNA or not chat_id:
        return ""
    _columnas, por_chat = tablero()
    chat = por_chat.get(chat_id) or {}
    ultima = chat.get("fecha") or ""

    # El espejo hacia el CRM (Twenty/Linear/Odoo, vía crm_flujo): primero
    # se escribe allá; si el CRM no responde, la mano NO se guarda — la
    # tarjeta rebotaría al refrescar y contaría una mentira.
    lead_id = chat.get("lead_crm_id") or ""
    if lead_id:
        if columna == "inactivo":
            if not crm_flujo.registrar_motivo(lead_id, (motivo or "").upper()):
                return "No se pudo registrar el motivo en el CRM; inténtalo de nuevo."
        elif chat.get("columna") == "inactivo":
            if not crm_flujo.registrar_motivo(lead_id, ""):
                return "No se pudo quitar el motivo en el CRM; inténtalo de nuevo."

    iniciar_tablas()
    ahora = datetime.now(ZONA_PANAMA).isoformat()
    with _db() as con:
        con.execute("""
            INSERT INTO control_tablero (chat, columna, motivo, ultima, actualizado)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(chat) DO UPDATE SET columna = excluded.columna,
                motivo = excluded.motivo, ultima = excluded.ultima,
                actualizado = excluded.actualizado
        """, (chat_id, columna, motivo if motivo in MOTIVOS else None, ultima, ahora))
    return ""
