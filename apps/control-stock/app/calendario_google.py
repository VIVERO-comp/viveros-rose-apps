"""Empuje del calendario a Google Calendar (fase 2 del plan, 22/09/2026).

El feed ICS (fase 1) le sirve perfecto a Apple, pero Google refresca los
ICS cada 12–24 horas. Este es el camino rápido para Google: cada empleada
conecta su cuenta desde Ajustes (un OAuth aparte del login, con el scope de
eventos y guardando el refresh token) y sus actividades del CALENDARIO ROSE
se escriben directo en su Google Calendar por la API REST — aparecen en el
teléfono en segundos, y un iPhone con la cuenta de Google agregada también
las ve.

Reglas (las mismas del plan):

- **One-way: Linear manda.** Mover el evento en Google no cambia Linear; la
  siguiente pasada de conciliación lo devuelve a como dice Linear.
- **Idempotente.** Cada evento lleva
  `extendedProperties.private.linear = <id del issue>`: reintentar
  actualiza, nunca duplica. Y todos llevan `origen = calendario-rose`, que
  es como la conciliación encuentra "los nuestros" para borrar huérfanos.
- **La conciliación no depende de ningún aviso** (la regla de los pagos):
  después de cada escritura de la app se dispara una pasada en segundo
  plano, y un hilo la repite cada 15 minutos — eso recoge también lo que se
  editó directo en Linear, sin webhook.
- Una actividad **cancelada o reasignada se borra** del Google Calendar de
  quien la tenía: ahí no hace falta historial, que vive en Linear.

Usa las mismas credenciales OAuth del login (GOOGLE_CLIENT_ID/SECRET); el
redirect `/calendario/google/callback` tiene que estar dado de alta en esa
pantalla de Google Cloud. Sin credenciales el módulo queda apagado y la
tarjeta no aparece en Ajustes.
"""

import os
import threading
import time
from datetime import datetime, timedelta
from urllib.parse import urlencode

import httpx

from . import calendario
from .acceso_google import FalloGoogle, URL_AUTORIZACION, URL_TOKENS, _reclamos, configurado
from .datos import ZONA_PANAMA, _db

SCOPE = "openid email https://www.googleapis.com/auth/calendar.events"
URL_EVENTOS = "https://www.googleapis.com/calendar/v3/calendars/primary/events"
URL_REVOCAR = "https://oauth2.googleapis.com/revoke"

# Access tokens vigentes por usuario: {usuario: (token, expira_epoch)}.
_tokens = {}
_hilo = {"encendido": False}


def iniciar_tablas():
    with _db() as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS calendario_google (
                usuario TEXT PRIMARY KEY,
                email TEXT NOT NULL,
                refresh_token TEXT NOT NULL,
                conectado_en TEXT NOT NULL
            )
        """)


# ---------------------------------------------------------------------------
# Conexión (OAuth con scope de calendario)
# ---------------------------------------------------------------------------

def url_conectar(redirect_uri, estado):
    """La pantalla de Google pidiendo permiso de calendario.

    `access_type=offline` + `prompt=consent` obligan a que llegue el
    refresh token, que es lo que deja empujar sin la empleada presente.
    """
    return URL_AUTORIZACION + "?" + urlencode({
        "client_id": os.environ["GOOGLE_CLIENT_ID"],
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": SCOPE,
        "state": estado,
        "access_type": "offline",
        "prompt": "consent",
    })


def canjear(codigo, redirect_uri):
    """{email, refresh_token} de la cuenta que dio permiso, o FalloGoogle."""
    try:
        respuesta = httpx.post(URL_TOKENS, data={
            "code": codigo,
            "client_id": os.environ["GOOGLE_CLIENT_ID"],
            "client_secret": os.environ["GOOGLE_CLIENT_SECRET"],
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        }, timeout=10)
    except Exception:
        raise FalloGoogle("Google no respondió")
    if respuesta.status_code != 200:
        raise FalloGoogle(f"Google respondió {respuesta.status_code}")
    datos = respuesta.json()
    if not datos.get("refresh_token"):
        raise FalloGoogle("Google no entregó el refresh token")
    reclamos = _reclamos(datos.get("id_token", ""))
    if not reclamos.get("email"):
        raise FalloGoogle("La respuesta no trae el email")
    return {"email": reclamos["email"].strip().lower(),
            "refresh_token": datos["refresh_token"]}


def conectar(usuario, email, refresh_token):
    with _db() as con:
        con.execute(
            """INSERT INTO calendario_google (usuario, email, refresh_token, conectado_en)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(usuario) DO UPDATE SET email = excluded.email,
                 refresh_token = excluded.refresh_token,
                 conectado_en = excluded.conectado_en""",
            (usuario, email, refresh_token, datetime.now(ZONA_PANAMA).isoformat()))
    _tokens.pop(usuario, None)


def desconectar(usuario):
    """Borra la conexión y le pide a Google revocar el permiso (si Google no
    contesta, la fila igual se va: sin refresh token no se empuja más)."""
    fila = conexion_de(usuario)
    with _db() as con:
        con.execute("DELETE FROM calendario_google WHERE usuario = ?", (usuario,))
    _tokens.pop(usuario, None)
    if fila:
        try:
            httpx.post(URL_REVOCAR, data={"token": fila["refresh_token"]}, timeout=10)
        except Exception:
            pass


def conexion_de(usuario):
    with _db() as con:
        fila = con.execute(
            "SELECT usuario, email, refresh_token, conectado_en FROM calendario_google WHERE usuario = ?",
            (usuario,)).fetchone()
    return dict(fila) if fila else None


def conexiones():
    with _db() as con:
        return [dict(f) for f in con.execute(
            "SELECT usuario, email, refresh_token FROM calendario_google").fetchall()]


def _access_token(usuario, refresh_token):
    token, expira = _tokens.get(usuario, ("", 0))
    if token and expira > time.time() + 60:
        return token
    respuesta = httpx.post(URL_TOKENS, data={
        "client_id": os.environ["GOOGLE_CLIENT_ID"],
        "client_secret": os.environ["GOOGLE_CLIENT_SECRET"],
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    }, timeout=10)
    if respuesta.status_code != 200:
        raise FalloGoogle(f"Google respondió {respuesta.status_code} al refrescar")
    datos = respuesta.json()
    _tokens[usuario] = (datos["access_token"], time.time() + datos.get("expires_in", 3600))
    return _tokens[usuario][0]


# ---------------------------------------------------------------------------
# El empuje en sí
# ---------------------------------------------------------------------------

def _google(metodo, url, token, **kwargs):
    return httpx.request(metodo, url, headers={"Authorization": "Bearer " + token},
                         timeout=15, **kwargs)


def cuerpo_de(actividad):
    """El evento de Google que corresponde a una actividad de Linear."""
    inicio = datetime.fromisoformat(
        actividad["fecha"] + "T" + (actividad["hora"] or calendario.HORA_POR_DEFECTO))
    fin = inicio + timedelta(minutes=actividad["dur"] or calendario.DURACION_POR_DEFECTO)
    titulo = calendario.nombre_de_tipo(actividad["tipo"]) + " — " + actividad["cliente"]
    if actividad["estado"] == "hecha":
        titulo = "✓ " + titulo
    detalle = [actividad["ref"], "Responsable: " + actividad["resp"]]
    if actividad["nota"]:
        detalle.append(actividad["nota"])
    if actividad["url"]:
        detalle.append(actividad["url"])
    return {
        "summary": titulo,
        "location": actividad["lugar"] or "",
        "description": "\n".join(x for x in detalle if x),
        "start": {"dateTime": inicio.isoformat(), "timeZone": "America/Panama"},
        "end": {"dateTime": fin.isoformat(), "timeZone": "America/Panama"},
        "extendedProperties": {"private": {
            "linear": actividad["id"], "origen": "calendario-rose"}},
    }


def _evento_existente(token, id_linear):
    respuesta = _google("GET", URL_EVENTOS, token, params={
        "privateExtendedProperty": "linear=" + id_linear,
        "showDeleted": "false", "maxResults": "2"})
    if respuesta.status_code != 200:
        raise FalloGoogle(f"Google respondió {respuesta.status_code} al buscar")
    articulos = respuesta.json().get("items", [])
    return articulos[0]["id"] if articulos else None


def empujar(usuario, refresh_token, actividad):
    """Deja el Google Calendar de `usuario` como dice Linear para esta
    actividad: crea, actualiza o borra. Idempotente por el id de Linear."""
    token = _access_token(usuario, refresh_token)
    existente = _evento_existente(token, actividad["id"])
    if actividad["estado"] == "cancel" or not actividad["fecha"]:
        if existente:
            _google("DELETE", URL_EVENTOS + "/" + existente, token)
        return "borrado" if existente else "nada"
    cuerpo = cuerpo_de(actividad)
    if existente:
        _google("PUT", URL_EVENTOS + "/" + existente, token, json=cuerpo)
        return "actualizado"
    _google("POST", URL_EVENTOS, token, json=cuerpo)
    return "creado"


def _mios(actividades, email):
    """Las actividades cuyo responsable de Linear tiene este email."""
    ids = {p["id"] for p in calendario.responsables() if p.get("email") == email}
    return [a for a in actividades if a["resp_id"] in ids]


def sincronizar_usuario(fila, actividades):
    """Concilia el Google Calendar de una empleada contra Linear: empuja lo
    suyo y borra los eventos nuestros que ya no le corresponden."""
    suyas = _mios(actividades, fila["email"])
    token = _access_token(fila["usuario"], fila["refresh_token"])
    for actividad in suyas:
        empujar(fila["usuario"], fila["refresh_token"], actividad)
    # Huérfanos: eventos nuestros en su calendario cuyo issue ya no es suyo
    # (reasignado), ya no existe o quedó cancelado.
    vigentes = {a["id"] for a in suyas if a["estado"] != "cancel" and a["fecha"]}
    pagina = _google("GET", URL_EVENTOS, token, params={
        "privateExtendedProperty": "origen=calendario-rose", "maxResults": "250"})
    if pagina.status_code != 200:
        return
    for evento in pagina.json().get("items", []):
        id_linear = (evento.get("extendedProperties", {})
                     .get("private", {}).get("linear", ""))
        if id_linear and id_linear not in vigentes:
            _google("DELETE", URL_EVENTOS + "/" + evento["id"], token)


def sincronizar_todo():
    """Una pasada completa: cada cuenta conectada queda como dice Linear.
    Los fallos de una cuenta no frenan a las demás."""
    filas = conexiones()
    if not filas or not configurado():
        return
    desde = (datetime.now(ZONA_PANAMA).date() - timedelta(days=30)).isoformat()
    hasta = (datetime.now(ZONA_PANAMA).date() + timedelta(days=120)).isoformat()
    try:
        actividades = calendario.listar(desde, hasta)
    except calendario.ErrorCalendario:
        return
    for fila in filas:
        try:
            sincronizar_usuario(fila, actividades)
        except Exception:
            continue


def sincronizar_en_fondo():
    """La misma pasada, sin hacer esperar a nadie (tras una escritura)."""
    if not configurado():
        return
    threading.Thread(target=sincronizar_todo, daemon=True).start()


def arrancar_hilo():
    """El conciliador de cada 15 minutos. No depende de ningún aviso: lo que
    se editó directo en Linear llega a Google como mucho 15 minutos después."""
    if _hilo["encendido"] or not configurado():
        return
    _hilo["encendido"] = True

    def rueda():
        while True:
            time.sleep(15 * 60)
            try:
                sincronizar_todo()
            except Exception:
                pass

    threading.Thread(target=rueda, daemon=True).start()
