"""El apoyo de la pantalla /conversaciones (ITEM 11 del plan de Jay, 5/10/2026).

La puerta que faltaba: hoy una conversación de WhatsApp SOLO se ve dentro
de la ficha de un lead casado, y Jay no puede supervisar el tono de lo que
se escribe. Los mensajes están completos en Twenty (los salientes con su
autor, amarrados a su Person); esta pantalla los lista POR CHAT, casados o
no con un lead del tablero.

Reglas de la casa que este módulo cumple a la letra:

- **SOLO LECTURA.** Aquí no se escribe en Twenty, ni en Linear, ni en
  Odoo: ni un POST. Es supervisión, no operación.
- **Solo admin** (el candado vive en la ruta, `_solo_admin` de main.py,
  el mismo de /revisar).
- **Best-effort y honesto**: Twenty caído o sin key (la instancia de
  pruebas la tiene neutralizada) → la pantalla carga igual y dice «Twenty
  no contesta; no se pudieron cargar las conversaciones», nunca una lista
  vacía que mienta.
- Todo se decide en Python y llega listo a la plantilla (regla 10): el
  agrupado por chat, el orden, el recorte del último mensaje, el enlace
  al lead si casa.
"""

import logging

from . import control, crm_twenty, linear_leads

log = logging.getLogger("control_stock")

# Cuántos mensajes recientes se agrupan por defecto, y hasta dónde deja
# crecer el «Ver más». El tope existe para no pasearse por todo el
# historial de Twenty en una sola pintada.
LIMITE_BASE = 300
LIMITE_TOPE = 1500
# El hilo de UN chat abierto: más que la ficha (60), porque acá se viene
# justamente a leer la conversación entera.
MENSAJES_DE_CHAT = 400

AVISO_CAIDO = ("Twenty no contesta; no se pudieron cargar las "
               "conversaciones.")
AVISO_SIN_KEY = ("Esta instancia no tiene acceso a Twenty; no se "
                 "pudieron cargar las conversaciones.")


def _digitos_locales(texto):
    """Los 8 dígitos panameños de un teléfono, para comparar grafías:
    '6552-0966', '50765520966' y '65520966' son el mismo número."""
    digitos = "".join(c for c in str(texto or "") if c.isdigit())
    if digitos.startswith("507") and len(digitos) == 11:
        digitos = digitos[3:]
    return digitos


def _telefono_bonito(crudo):
    """'50765520966' -> '+507 6552-0966'; lo que no sea Panamá, tal cual."""
    digitos = "".join(c for c in str(crudo or "") if c.isdigit())
    local = _digitos_locales(digitos)
    if len(local) == 8:
        return f"+507 {local[:4]}-{local[4:]}"
    return digitos


def _recorte(texto, largo=110):
    """El último mensaje en un renglón: sin saltos y sin pasarse."""
    plano = " ".join(str(texto or "").split())
    if len(plano) <= largo:
        return plano
    return plano[:largo - 1].rstrip() + "…"


def listar(limite=LIMITE_BASE):
    """{'ok', 'error', 'conversaciones', 'leidos', 'hay_mas'} — la lista
    agrupada por chat, de la más reciente a la más vieja."""
    base = {"ok": False, "error": "", "conversaciones": [],
            "leidos": 0, "hay_mas": False}
    if not crm_twenty.twenty_configurado():
        base["error"] = AVISO_SIN_KEY
        return base
    try:
        crudos, hay_mas = crm_twenty.mensajes_recientes(limite)
    except Exception as fallo:
        log.warning("Las conversaciones no se pudieron leer de Twenty: %r",
                    fallo)
        base["error"] = AVISO_CAIDO
        return base
    base.update(ok=True, conversaciones=_agrupar(crudos),
                leidos=len(crudos), hay_mas=hay_mas)
    return base


def _agrupar(crudos):
    """Los mensajes (del más nuevo al más viejo) -> una tarjeta por chat.

    La llave es el `chatId`; un mensaje sin chatId cae a su teléfono y de
    último a su Person, para no botar nada. Como los mensajes ya vienen en
    orden, el PRIMER mensaje de cada chat es su último mensaje, y el orden
    de aparición de los chats ya es «más reciente primero».
    """
    grupos, orden = {}, []
    for m in crudos:
        clave = (m.get("chatId") or m.get("telefono")
                 or m.get("personaId") or "")
        if not clave:
            continue
        grupo = grupos.get(clave)
        if grupo is None:
            ultimo = crm_twenty.mensaje_legible(m)
            grupo = grupos[clave] = {
                "chat_id": m.get("chatId") or "",
                "nombre": "",
                "telefono": "",
                "persona_id": "",
                "cant": 0,
                "ultimo": {
                    "cuando": ultimo["cuando"],
                    "salida": ultimo["salida"],
                    "autor": ultimo["autor"],
                    "texto": _recorte(ultimo["texto"]),
                },
            }
            orden.append(grupo)
        grupo["cant"] += 1
        # El primer valor no vacío que aparezca gana: el nombre del chat,
        # el teléfono y la Person pueden faltar en algún mensaje suelto.
        if not grupo["nombre"] and (m.get("chatNombre") or "").strip():
            grupo["nombre"] = str(m["chatNombre"]).strip()
        if not grupo["telefono"] and m.get("telefono"):
            grupo["telefono"] = _telefono_bonito(m["telefono"])
        if not grupo["persona_id"] and m.get("personaId"):
            grupo["persona_id"] = m["personaId"]
    for grupo in orden:
        # El título nunca se inventa: nombre del chat, o el teléfono, o
        # la verdad pelada de que no hay ni uno ni otro.
        grupo["titulo"] = (grupo["nombre"] or grupo["telefono"]
                           or "Chat sin nombre")
    return orden


def abrir(chat_id):
    """El hilo completo de UN chat, con su enlace al lead si casa.

    Siempre devuelve el dict (la pantalla pinta el panel igual): si Twenty
    no contesta, `error` lo dice y el hilo queda vacío.
    """
    chat_id = (chat_id or "").strip()
    if not chat_id:
        return None
    abierta = {"chat_id": chat_id, "titulo": "", "telefono": "",
               "cant": 0, "hilo": [], "error": "", "lead": None,
               "twenty_url": ""}
    if not crm_twenty.twenty_configurado():
        abierta["error"] = AVISO_SIN_KEY
        return abierta
    try:
        crudos = crm_twenty.mensajes_de_chat(chat_id, MENSAJES_DE_CHAT)
    except Exception as fallo:
        log.warning("La conversación %s no se pudo leer de Twenty: %r",
                    chat_id, fallo)
        abierta["error"] = ("Twenty no contesta; no se pudo cargar esta "
                            "conversación.")
        return abierta

    nombre = next((str(m.get("chatNombre") or "").strip()
                   for m in crudos if (m.get("chatNombre") or "").strip()), "")
    telefono = next((m.get("telefono") for m in crudos if m.get("telefono")), "")
    persona_id = next((m.get("personaId") for m in crudos if m.get("personaId")), "")

    mensajes = [crm_twenty.mensaje_legible(m) for m in crudos]
    abierta.update(
        titulo=nombre or _telefono_bonito(telefono) or "Chat sin nombre",
        telefono=_telefono_bonito(telefono),
        cant=len(crudos),
        # El MISMO armado del hilo de la ficha (control.hilo): cliente a
        # la izquierda, equipo a la derecha con su autor. Sin sucesos:
        # esos son del issue de un lead, y este chat puede no tener uno.
        hilo=control.hilo(mensajes, (), nombre),
        lead=_lead_del_chat(persona_id, telefono),
        twenty_url=(f"{crm_twenty.twenty_publico()}/object/person/{persona_id}"
                    if persona_id else ""),
    )
    return abierta


def _lead_del_chat(persona_id, telefono):
    """El lead del tablero que casa con este chat, o None. Best-effort de
    verdad: cualquier tropiezo (Twenty, Linear) devuelve None y la
    conversación se muestra igual — esa es la gracia de la pantalla."""
    issue_id = ""
    if persona_id:
        try:
            issue_id = crm_twenty.issue_de_persona(persona_id)
        except Exception:
            issue_id = ""
    try:
        leads = linear_leads.listar()
    except Exception:
        return None
    digitos = _digitos_locales(telefono)
    por_telefono = None
    for lead in leads or []:
        if issue_id and lead.get("id") == issue_id:
            return _lead_chico(lead)
        if (por_telefono is None and digitos
                and _digitos_locales(lead.get("celular")) == digitos):
            por_telefono = lead
    return _lead_chico(por_telefono) if por_telefono else None


def _lead_chico(lead):
    """Lo justo para el enlace del panel: a dónde ir y cómo se llama."""
    return {"ref": lead.get("ref") or "",
            "nombre": lead.get("nombre") or lead.get("ref") or "",
            "estado_nombre": lead.get("estado_nombre") or ""}
