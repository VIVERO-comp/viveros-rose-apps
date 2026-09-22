"""Suscripción del calendario en el teléfono (feed ICS).

Fase 1 del plan de sincronización (22/09/2026). Cada empleada tiene un
enlace secreto `/calendario.ics?t=<token>` que su calendario puede
suscribir:

- **Apple (iPhone/Mac)**: cuenta "Calendario suscrito"; refresca cada pocos
  minutos si se le pide.
- **Google Calendar**: "Otros calendarios → Desde URL". Funciona, pero
  Google refresca a su ritmo (12–24 horas y sin botón de actualizar); el
  camino rápido con Google es la fase 2 (empuje por API).

El feed es SOLO de lectura: Linear sigue siendo la fuente de verdad y nada
de lo que el teléfono haga con el evento vuelve por aquí. Cada actividad
sale como VEVENT con el UID fijo al id del issue de Linear —editarla
actualiza el evento en vez de duplicarlo— y una cancelada viaja como
STATUS:CANCELLED, porque nada se borra. La zona es America/Panama (sin
horario de verano).

El token vive en SQLite (`calendario_suscripciones`), uno por empleada, y
se regenera desde Ajustes: el enlace viejo muere en el acto. Con el token
el feed trae lo de esa empleada (su usuario de Linear, amarrado por el
email verificado de Google); una admin, o una empleada sin usuario de
Linear enlazado, recibe el calendario del equipo completo.
"""

import secrets
from datetime import datetime, timedelta

from icalendar import Calendar, Event, vDuration

from . import calendario
from .datos import ZONA_PANAMA, _db

# Cuánto calendario viaja en el archivo: lo reciente y lo que viene. Un
# feed no es un archivo histórico; el historial completo vive en Linear.
DIAS_ATRAS = 30
DIAS_ADELANTE = 120

# Cada cuánto conviene que el cliente vuelva a pedir el archivo. Apple lo
# respeta; Google lo ignora y va a su ritmo.
REFRESCO = timedelta(minutes=15)


def iniciar_tablas():
    with _db() as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS calendario_suscripciones (
                usuario TEXT PRIMARY KEY,
                token TEXT UNIQUE NOT NULL,
                creado_en TEXT NOT NULL
            )
        """)


def token_de(usuario):
    """El token del feed de esta empleada; nace la primera vez que se pide."""
    with _db() as con:
        fila = con.execute(
            "SELECT token FROM calendario_suscripciones WHERE usuario = ?",
            (usuario,)).fetchone()
        if fila:
            return fila["token"]
        token = secrets.token_urlsafe(24)
        con.execute(
            "INSERT INTO calendario_suscripciones (usuario, token, creado_en) VALUES (?, ?, ?)",
            (usuario, token, datetime.now(ZONA_PANAMA).isoformat()))
        return token


def regenerar(usuario):
    """Token nuevo: el enlace anterior deja de servir en el acto."""
    with _db() as con:
        token = secrets.token_urlsafe(24)
        con.execute(
            """INSERT INTO calendario_suscripciones (usuario, token, creado_en)
               VALUES (?, ?, ?)
               ON CONFLICT(usuario) DO UPDATE SET
                 token = excluded.token, creado_en = excluded.creado_en""",
            (usuario, token, datetime.now(ZONA_PANAMA).isoformat()))
        return token


def empleada_del_token(token):
    """La empleada dueña del token, o None. Solo cuentas activas: desactivar
    a alguien le corta también la suscripción del teléfono."""
    if not token:
        return None
    with _db() as con:
        fila = con.execute(
            """SELECT e.usuario, e.nombre, e.email, e.email_verificado
               FROM calendario_suscripciones s
               JOIN empleadas e ON e.usuario = s.usuario AND e.activa = 1
               WHERE s.token = ?""",
            (token,)).fetchone()
    if fila is None:
        return None
    return {"id": fila["usuario"], "nombre": fila["nombre"],
            "email": fila["email"], "email_verificado": fila["email_verificado"]}


def rango():
    """(desde, hasta) en ISO para pedirle a calendario.listar()."""
    hoy = datetime.now(ZONA_PANAMA).date()
    return ((hoy - timedelta(days=DIAS_ATRAS)).isoformat(),
            (hoy + timedelta(days=DIAS_ADELANTE)).isoformat())


def feed(actividades, nombre="Calendario Rose"):
    """El archivo ICS, como bytes listos para responder."""
    cal = Calendar()
    cal.add("prodid", "-//Vivero Rose//Control de Stock//ES")
    cal.add("version", "2.0")
    cal.add("x-wr-calname", nombre)
    cal.add("x-wr-timezone", "America/Panama")
    # Las dos grafías del intervalo de refresco: la del estándar y la vieja.
    cal.add("refresh-interval", vDuration(REFRESCO), parameters={"VALUE": "DURATION"})
    cal.add("x-published-ttl", vDuration(REFRESCO))

    ahora = datetime.now(ZONA_PANAMA)
    for a in actividades:
        if not a["fecha"]:
            continue
        try:
            inicio = datetime.fromisoformat(
                a["fecha"] + "T" + (a["hora"] or calendario.HORA_POR_DEFECTO)
            ).replace(tzinfo=ZONA_PANAMA)
        except ValueError:
            continue
        evento = Event()
        evento.add("uid", a["id"] + "@calendario.plantaspanama.com")
        titulo = calendario.nombre_de_tipo(a["tipo"]) + " — " + a["cliente"]
        if a["estado"] == "hecha":
            titulo = "✓ " + titulo
        evento.add("summary", titulo)
        evento.add("dtstart", inicio)
        evento.add("dtend", inicio + timedelta(minutes=a["dur"] or calendario.DURACION_POR_DEFECTO))
        evento.add("dtstamp", ahora)
        if a["lugar"]:
            evento.add("location", a["lugar"])
        detalle = [a["ref"], "Responsable: " + a["resp"]]
        if a["nota"]:
            detalle.append(a["nota"])
        evento.add("description", "\n".join(x for x in detalle if x))
        if a["url"]:
            evento.add("url", a["url"])
        if a["estado"] == "cancel":
            evento.add("status", "CANCELLED")
        cal.add_component(evento)

    # Los VTIMEZONE que los DTSTART;TZID necesitan (Apple los exige).
    cal.add_missing_timezones()
    return cal.to_ical()
