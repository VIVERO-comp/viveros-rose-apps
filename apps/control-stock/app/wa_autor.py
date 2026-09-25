"""Quién respondió: qué dispositivo escribió cada mensaje saliente del 6099.

Fase A (25/09/2026). El WhatsApp del negocio (6099-1459) lo contestan varias
personas desde varios equipos, y hasta hoy todas las respuestas se veían
iguales. WhatsApp sí distingue el dispositivo que envió — es el `:N` del JID
del remitente —, pero **OpenWA lo tira a la basura**: su adaptador de baileys
arma el remitente con `id.split(':')[0]` y solo llena `author` en grupos, así
que en un chat 1-a-1 el dato ya no existe cuando el webhook llega a Vercel.

WAHA, que está vinculado al mismo número, sí lo trae:

    _data.Info.Sender = "<lid>:3@lid"     -> dispositivo 3
    _data.Info.ID     = "A5C42A8BD0…"     -> el MISMO waMessageId que
                                             OpenWA ya guarda en Twenty

De ahí el diseño: WAHA avisa por webhook, este módulo anota el dispositivo, y
la Fase B casa por `waMessageId` para escribir el autor en el mensaje que
Twenty ya tiene. Este módulo NO envía nada a WhatsApp: WAHA sigue siendo de
solo lectura, solo que ahora avisa hacia afuera.

**Privacidad (la regla del dueño, 25/09/2026).** Aquí no entra ni una palabra
de ninguna conversación:

- `wa_dispositivo` es un contador por dispositivo: cuántos mensajes y cuándo
  fue el último. No sabe de qué chat ni de quién.
- `wa_pendiente` guarda un id opaco de WhatsApp y el dispositivo, nada más:
  sin texto, sin número y sin chat. Se borra al aplicarse, y a las 24 horas
  si nunca casó con un mensaje de Twenty.

Y el candado de fondo: el autor solo se escribe **encima de un mensaje que
Twenty ya tenía**. Si el `waMessageId` no está allá, es un chat que el sistema
no sigue y aquí no se crea nada. Este módulo nunca ensancha lo que el CRM
guarda; solo le pone nombre a lo que ya estaba.

Los nombres nunca se inventan, que es la otra regla: un dispositivo que nadie
mapeó sale como «Equipo · dispositivo N», no como una persona.
"""

import hashlib
import hmac
import os
import re
from datetime import datetime, timedelta

from .datos import ZONA_PANAMA, _db, ahora_iso

# El teléfono principal es el dispositivo 0 de WhatsApp: su JID viene sin
# sufijo. No es "desconocido", es el celular del negocio, y así se llama.
TELEFONO = "0"
NOMBRE_TELEFONO = "Teléfono"

# Lo que mande nuestro propio sistema por la API de WAHA llega con
# `source: "api"`. No es ningún empleado y no debe ocupar una fila de la
# tabla de Ajustes.
NOMBRE_SISTEMA = "Sistema"

# Cuánto vive un pendiente que nunca casó con un mensaje de Twenty. Pasado
# ese plazo se borra: si en un día OpenWA no lo guardó, es un chat que el CRM
# no sigue y no hay nada que anotarle.
HORAS_PENDIENTE = 24

# `<digitos>:<N>@<dominio>`, donde `:<N>` es opcional. El dominio puede ser
# @lid (lo que manda WAHA hoy), @s.whatsapp.net o @c.us: el sufijo se lee
# igual en los tres.
_SENDER = re.compile(r"^(?P<base>[\d\-]{5,25})(?::(?P<dispositivo>\d{1,3}))?@")


def iniciar_tablas():
    with _db() as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS wa_dispositivo (
            dispositivo TEXT PRIMARY KEY,
            nombre TEXT NOT NULL DEFAULT '',
            mensajes INTEGER NOT NULL DEFAULT 0,
            visto_en TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS wa_pendiente (
            wa_message_id TEXT PRIMARY KEY,
            dispositivo TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT '',
            cuando TEXT NOT NULL
        );
        """)


# ---------------------------------------------------------------------------
# Leer el dispositivo del JID
# ---------------------------------------------------------------------------

def dispositivo_de(sender):
    """'…585:3@lid' -> '3'; '…585@lid' -> '0'; cualquier otra cosa -> ''.

    Sin sufijo NO es un error: WhatsApp numera el teléfono principal como
    dispositivo 0 y por eso lo manda pelado.
    """
    hallazgo = _SENDER.match(str(sender or "").strip())
    if not hallazgo:
        return ""
    return hallazgo.group("dispositivo") or TELEFONO


def nombre_de(dispositivo, source="", mapa=None):
    """El nombre que se ve en el chat, con la escalera que fijó el dueño.

    Sistema (lo que manda nuestro código) > el nombre que alguien puso en
    Ajustes > «Teléfono» para el celular del negocio > «Equipo · dispositivo
    N». El último escalón existe para no inventar nunca un nombre: un
    dispositivo sin mapear se ve, se nota y se puede asignar, pero no se
    disfraza de persona.
    """
    if (source or "").strip().lower() == "api":
        return NOMBRE_SISTEMA
    dispositivo = str(dispositivo or "").strip()
    if not dispositivo:
        return ""
    puesto = (mapa if mapa is not None else mapa_de_nombres()).get(dispositivo, "")
    if puesto:
        return puesto
    if dispositivo == TELEFONO:
        return NOMBRE_TELEFONO
    return f"Equipo · dispositivo {dispositivo}"


# ---------------------------------------------------------------------------
# Anotar lo que avisa WAHA
# ---------------------------------------------------------------------------

def anotar(wa_message_id, dispositivo, source="", cuando=None):
    """Guarda el dispositivo de un saliente. Devuelve True si quedó anotado.

    Dos cosas distintas y las dos mínimas: el contador del dispositivo (para
    que Ajustes pueda mostrarlo y nombrarlo) y el pendiente (para que la Fase
    B sepa a qué mensaje ponerle el autor). Ni una ni otra guarda texto.

    Idempotente: WAHA reintenta sus webhooks, y un reintento no puede inflar
    el contador ni duplicar el pendiente.
    """
    wa_message_id = str(wa_message_id or "").strip()
    dispositivo = str(dispositivo or "").strip()
    if not wa_message_id or not dispositivo:
        return False
    cuando = cuando or ahora_iso()
    iniciar_tablas()
    with _db() as con:
        nuevo = con.execute(
            "INSERT OR IGNORE INTO wa_pendiente "
            "(wa_message_id, dispositivo, source, cuando) VALUES (?, ?, ?, ?)",
            (wa_message_id, dispositivo, (source or "").strip(), cuando)).rowcount
        if not nuevo:
            return False  # ya lo habíamos visto: ni contador ni pendiente
        con.execute(
            "INSERT INTO wa_dispositivo (dispositivo, nombre, mensajes, visto_en) "
            "VALUES (?, '', 1, ?) "
            "ON CONFLICT(dispositivo) DO UPDATE SET "
            "  mensajes = mensajes + 1, visto_en = excluded.visto_en",
            (dispositivo, cuando))
    return True


def limpiar_pendientes(horas=HORAS_PENDIENTE, ahora=None):
    """Borra los pendientes que nunca casaron. Devuelve cuántos se fueron.

    Es la promesa de privacidad hecha código: un id que en 24 horas no
    encontró su mensaje en Twenty es de un chat que el CRM no sigue, y no se
    queda guardado."""
    iniciar_tablas()
    ahora = ahora or datetime.now(ZONA_PANAMA)
    corte = (ahora - timedelta(hours=horas)).isoformat()
    with _db() as con:
        return con.execute(
            "DELETE FROM wa_pendiente WHERE cuando < ?", (corte,)).rowcount


# ---------------------------------------------------------------------------
# La tabla de Ajustes
# ---------------------------------------------------------------------------

def mapa_de_nombres():
    """{dispositivo: nombre} de los que tienen nombre puesto."""
    iniciar_tablas()
    with _db() as con:
        filas = con.execute(
            "SELECT dispositivo, nombre FROM wa_dispositivo WHERE nombre <> ''"
        ).fetchall()
    return {f["dispositivo"]: f["nombre"] for f in filas}


def vistos():
    """[{dispositivo, nombre, se_ve, mensajes, visto_en, es_telefono}]

    Todo lo que ha escrito por el número, para la tabla de Ajustes. El
    teléfono primero y después por actividad: el que más escribe es el que
    más urge nombrar.
    """
    iniciar_tablas()
    mapa = mapa_de_nombres()
    with _db() as con:
        filas = con.execute(
            "SELECT dispositivo, nombre, mensajes, visto_en FROM wa_dispositivo"
        ).fetchall()
    lista = [{
        "dispositivo": f["dispositivo"],
        "nombre": f["nombre"],
        "se_ve": nombre_de(f["dispositivo"], mapa=mapa),
        "mensajes": f["mensajes"],
        "visto_en": f["visto_en"],
        "es_telefono": f["dispositivo"] == TELEFONO,
        "sin_nombre": not f["nombre"] and f["dispositivo"] != TELEFONO,
    } for f in filas]
    lista.sort(key=lambda d: (not d["es_telefono"], -d["mensajes"]))
    return lista


def nombrar(dispositivo, nombre):
    """Le pone (o le quita, con '') el nombre a un dispositivo.

    Se puede reasignar siempre, y eso no es un detalle: cuando alguien vuelve
    a vincular su computadora, WhatsApp le da un número nuevo, y el viejo
    queda de otra persona o de nadie. Arreglarlo tiene que ser escribir un
    nombre en Ajustes, no un despliegue.
    """
    dispositivo = str(dispositivo or "").strip()
    if not dispositivo:
        return False
    iniciar_tablas()
    with _db() as con:
        con.execute(
            "INSERT INTO wa_dispositivo (dispositivo, nombre, mensajes, visto_en) "
            "VALUES (?, ?, 0, ?) "
            "ON CONFLICT(dispositivo) DO UPDATE SET nombre = excluded.nombre",
            (dispositivo, (nombre or "").strip()[:40], ahora_iso()))
    return True


# ---------------------------------------------------------------------------
# El webhook de WAHA
# ---------------------------------------------------------------------------

def secreto():
    return (os.environ.get("WAHA_WEBHOOK_SECRET") or "").strip()


def configurado():
    return bool(secreto())


def firma_valida(cuerpo_crudo, firma):
    """La firma de WAHA: HMAC-SHA512 del cuerpo CRUDO, en hex.

    El cuerpo crudo y no el JSON reserializado: un solo espacio de
    diferencia cambia el hash. Comparación en tiempo constante.
    """
    if not configurado():
        return False
    firma = (firma or "").strip()
    if not firma:
        return False
    esperada = hmac.new(secreto().encode(), cuerpo_crudo, hashlib.sha512).hexdigest()
    return hmac.compare_digest(esperada, firma.lower())


def leer_evento(evento):
    """El payload de WAHA -> {wa_message_id, dispositivo, source} o None.

    Devuelve None para todo lo que no es una respuesta nuestra en un chat de
    una persona: los entrantes, los grupos, los estados y cualquier evento
    que no sea `message.any`. El filtro vive aquí y no en el endpoint para
    que se pueda probar sin levantar la app.
    """
    if not isinstance(evento, dict):
        return None
    if evento.get("event") != "message.any":
        return None
    m = evento.get("payload") or {}
    if not isinstance(m, dict) or m.get("fromMe") is not True:
        return None

    datos = m.get("_data") or {}
    info = datos.get("Info") or {} if isinstance(datos, dict) else {}
    if not isinstance(info, dict):
        info = {}
    # Grupos y estados no son conversaciones con un cliente. WhatsApp los
    # marca en el propio evento; se revisa aquí aunque el filtro del webhook
    # ya los descarte, porque la firma solo garantiza el origen, no el
    # contenido.
    if str(info.get("IsGroup")).lower() == "true" or m.get("isGroup") is True:
        return None
    if str(m.get("chatId") or info.get("Chat") or "").endswith(("@g.us", "@broadcast")):
        return None

    wa_message_id = str(info.get("ID") or "").strip()
    if not wa_message_id:
        # WAHA compone su id como `true_<chat>_<ID>`: si falta el crudo, el
        # último tramo sirve igual y es el mismo que guarda Twenty.
        partes = str(m.get("id") or "").split("_")
        wa_message_id = partes[-1].strip() if len(partes) >= 3 else ""
    dispositivo = dispositivo_de(info.get("Sender"))
    if not wa_message_id or not dispositivo:
        return None
    return {
        "wa_message_id": wa_message_id,
        "dispositivo": dispositivo,
        "source": str(m.get("source") or "").strip(),
    }
