"""El mantenimiento mensual (28/09/2026, pedido de Abraham).

Un lead con interés **Mantenimiento** que pasa a **Ganado** arranca una
serie de citas del calendario: una cada mes, el mismo día del mes en que
ganó ("el día ancla"), asignada a su `Resp:`. Se crean DE A UNA — al
marcar «Hecha» la de este mes nace la del mes siguiente — así el
calendario nunca se llena de meses futuros que nadie pidió.

**Estas citas NUNCA mueven el estado del lead: sigue en Ganado.** Aunque
el tipo Mantenimiento SÍ entrega en el camino normal de agendar (la regla
de la Fase 4, "solo entregan Entrega, Instalación y Mantenimiento"), la
recurrente queda explícitamente FUERA de ese cierre — se distingue por un
CAMPO (`mantenimiento_lead.actividad_pendiente_id`, comparado contra el id
de la actividad que se marca Hecha), nunca por una convención de texto en
el título: un Mantenimiento agendado a mano, suelto, sigue entregando
normal.

El enganche de creación vive en UN SOLO lugar: `agenda._mover_a_ganado`,
el único punto por el que pasa cualquier camino hacia Ganado (con saldo 0
al marcar Hecha, o cuando el pago que salda entra después). Este módulo no
sabe nada de CÓMO se llega a Ganado, solo qué hacer cuando se llega.

**SIN RETROACTIVOS** (pedido explícito de Abraham, 28/09/2026): esto
arranca para los leads que pasen a Ganado desde que este módulo se
desplegó. Los que ya estaban Ganado antes no ganan citas solas — nadie
recorre el tablero hacia atrás a buscarlos, y no hay ninguna migración que
los complete.

El ancla del día NUNCA se deriva del último evento: si se entregó un 31,
la cita de un mes corto cae en su último día, pero al mes siguiente vuelve
al 31. Por eso se guarda el ancla ORIGINAL en la tabla, no la fecha de la
cita anterior.

Nada de esto le manda un mensaje al cliente: solo pone una actividad en el
calendario del equipo y dos comentarios internos en el issue del lead (uno
al parar). Fail-soft como el resto de la app: si el calendario no
contesta, el error queda en el log y el lead sigue ganando igual; si
Linear no contesta al comentar «Parar», la serie ya quedó apagada.
"""

from datetime import date, datetime, timedelta

from . import calendario, linear_leads
from .datos import ZONA_PANAMA, _db

# El único interés que dispara la serie — el mismo texto que ya usa el
# grupo Interés de Linear (`linear_leads.GRUPO_INTERES`), traducido a los
# 5 tipos del negocio. No se copia de otra constante porque acá alcanza
# con comparar el texto tal cual sale en `lead["interes"]`.
INTERES_MANTENIMIENTO = "Mantenimiento"

# El texto del comentario al parar, tal cual lo pidió Abraham.
TEXTO_PARAR = "Mantenimiento mensual detenido."


def iniciar_tablas():
    with _db() as con:
        # Una fila por lead con mantenimiento alguna vez arrancado. El
        # ancla es el DÍA DEL MES original (1-31); `actividad_pendiente_id`
        # es la cita futura vigente (o NULL si no se pudo crear, o si ya
        # se paró) — es el campo explícito que distingue una recurrente de
        # cualquier otra actividad de tipo Mantenimiento.
        con.execute("""
            CREATE TABLE IF NOT EXISTS mantenimiento_lead (
                ref TEXT PRIMARY KEY,
                ancla INTEGER NOT NULL,
                activo INTEGER NOT NULL DEFAULT 1,
                actividad_pendiente_id TEXT,
                creado_en TEXT NOT NULL
            )
        """)


def registro_aviso(texto):
    """Un aviso al log. Aparte para que las pruebas puedan mirarlo — y
    propio de este módulo, no prestado de otro (la misma razón que ya
    documentó control.py: un NameError prestado solo se ve el día que algo
    falla de verdad)."""
    import logging
    logging.getLogger("control_stock").warning(texto)


def _hoy():
    return datetime.now(ZONA_PANAMA).date()


def _ultimo_dia_del_mes(anio, mes):
    siguiente = date(anio + 1, 1, 1) if mes == 12 else date(anio, mes + 1, 1)
    return (siguiente - timedelta(days=1)).day


def _siguiente_fecha(referencia, ancla):
    """El día `ancla` del mes SIGUIENTE al de `referencia`, recortado al
    último día si ese mes es corto.

    El ancla nunca se deriva de dónde cayó el evento anterior: si se
    entregó un 31, la cita de febrero cae el 28 (o 29) porque no hay otra
    opción, pero la de marzo vuelve al 31 — por eso `ancla` siempre viene
    de la tabla (el original), nunca del día de `referencia`.
    """
    anio, mes = referencia.year, referencia.month + 1
    if mes > 12:
        anio, mes = anio + 1, 1
    return date(anio, mes, min(ancla, _ultimo_dia_del_mes(anio, mes)))


def _crear_cita(lead, fecha):
    """La actividad del calendario: tipo Mantenimiento, asignada al
    `Resp:` del lead por NOMBRE en la marca (nunca por assignee — mismo
    criterio que `agenda.agendar()`, los empleados no tienen asiento de
    Linear). Sin Resp:, la marca no lleva `resp=` y la actividad se ve sin
    asignar, tal cual pidió Abraham ("que se vea")."""
    return calendario.crear(
        tipo="mantenimiento", cliente=lead["nombre"], fecha=fecha.isoformat(),
        hora=calendario.HORA_POR_DEFECTO, dur=calendario.DURACION_POR_DEFECTO,
        lugar="", resp_id="", prioridad=3,
        nota="Mantenimiento mensual (serie automática).",
        lead=lead["ref"], resp_lead=lead.get("resp") or "")


# ---------------------------------------------------------------------------
# Al ganar: arranca la serie
# ---------------------------------------------------------------------------

def al_ganar(lead, autor=""):
    """Si el lead tiene interés Mantenimiento, crea la primera cita — un
    mes después de HOY (el día que gana, que es el ancla) — y arranca su
    fila en la tabla. Con cualquier otro interés no hace nada.

    Idempotente: si el lead ya tiene una fila (no debería pasar — Ganado
    no vuelve a ganar — pero una corrida doble de `cerrar_los_que_ya_
    pagaron()` no puede duplicar la serie), no crea una segunda.
    """
    if (lead.get("interes") or "") != INTERES_MANTENIMIENTO:
        return
    iniciar_tablas()
    with _db() as con:
        ya = con.execute(
            "SELECT 1 FROM mantenimiento_lead WHERE ref=?", (lead["ref"],)).fetchone()
    if ya:
        return

    hoy = _hoy()
    ancla = hoy.day
    fecha_cita = _siguiente_fecha(hoy, ancla)
    try:
        creada = _crear_cita(lead, fecha_cita)
    except Exception as fallo:
        registro_aviso(
            f"Mantenimiento: no se pudo crear la primera cita de {lead['ref']}: {fallo}")
        creada = None

    with _db() as con:
        con.execute(
            "INSERT INTO mantenimiento_lead"
            " (ref, ancla, activo, actividad_pendiente_id, creado_en)"
            " VALUES (?,?,?,?,?)",
            (lead["ref"], ancla, 1, creada["id"] if creada else None,
             datetime.now(ZONA_PANAMA).isoformat()))


# ---------------------------------------------------------------------------
# Al marcar Hecha una recurrente: nace la siguiente, el lead no se toca
# ---------------------------------------------------------------------------

def es_recurrente(actividad):
    """¿Esta actividad es LA cita recurrente vigente de su lead?

    Se compara el id contra `actividad_pendiente_id` — el campo explícito
    que decide, nunca una convención de texto en el título: un
    Mantenimiento agendado a mano para el mismo lead, suelto, no matchea y
    sigue el camino normal de `agenda.al_marcar_hecha`.
    """
    ref = (actividad or {}).get("lead") or ""
    if not ref:
        return False
    iniciar_tablas()
    with _db() as con:
        fila = con.execute(
            "SELECT actividad_pendiente_id FROM mantenimiento_lead WHERE ref=?",
            (ref,)).fetchone()
    return bool(fila and fila["actividad_pendiente_id"]
               and fila["actividad_pendiente_id"] == actividad.get("id"))


def al_marcar_hecha(actividad, autor=""):
    """La cita de este mes se marcó Hecha: nace la del mes siguiente.

    NO toca el estado del lead — sigue en Ganado, a propósito: esto es
    justo lo que separa a esta actividad del camino normal de entrega.
    Devuelve el aviso para la pantalla.
    """
    ref = (actividad or {}).get("lead") or ""
    iniciar_tablas()
    with _db() as con:
        fila = con.execute(
            "SELECT * FROM mantenimiento_lead WHERE ref=?", (ref,)).fetchone()
    if fila is None or not fila["activo"]:
        return ""  # se paró mientras tanto: no se crea una cita más
    lead = linear_leads.uno(ref)
    if lead is None:
        return ""

    try:
        referencia = date.fromisoformat(actividad.get("fecha") or "")
    except ValueError:
        referencia = _hoy()  # sin fecha (no debería pasar): mejor hoy que reventar
    fecha_siguiente = _siguiente_fecha(referencia, fila["ancla"])

    try:
        creada = _crear_cita(lead, fecha_siguiente)
    except Exception as fallo:
        registro_aviso(
            f"Mantenimiento: no se pudo crear la próxima cita de {ref}: {fallo}")
        creada = None

    with _db() as con:
        con.execute(
            "UPDATE mantenimiento_lead SET actividad_pendiente_id=? WHERE ref=?",
            (creada["id"] if creada else None, ref))

    if creada:
        return (f"Mantenimiento de {lead['nombre']}: próxima cita el "
                f"{calendario.dmy(fecha_siguiente.isoformat())}.")
    return f"Mantenimiento de {lead['nombre']}: no se pudo agendar la próxima cita."


# ---------------------------------------------------------------------------
# Parar: el cliente ya no quiere más
# ---------------------------------------------------------------------------

def activo(ref):
    """¿Este lead tiene mantenimiento activo? Decide si el botón «Parar
    mantenimiento» aparece en su ficha."""
    iniciar_tablas()
    with _db() as con:
        fila = con.execute(
            "SELECT activo FROM mantenimiento_lead WHERE ref=?", (ref,)).fetchone()
    return bool(fila and fila["activo"])


def parar(ref, autor=""):
    """Apaga la serie: `activo=0`, cancela la cita futura pendiente (si
    existía) y deja UN comentario firmado en el issue. Devuelve
    ("aviso", "error").

    Fail-soft con criterio: si cancelar la cita en el calendario falla, la
    serie se apaga IGUAL (nadie quiere que un Linear caído deje una
    recurrente encendida por accidente) y el aviso lo dice; si el
    comentario falla, todo lo demás ya quedó hecho.
    """
    iniciar_tablas()
    with _db() as con:
        fila = con.execute(
            "SELECT * FROM mantenimiento_lead WHERE ref=?", (ref,)).fetchone()
    if fila is None or not fila["activo"]:
        return "", "Este lead no tiene mantenimiento activo."
    lead = linear_leads.uno(ref)
    if lead is None:
        return "", linear_leads.mensaje_lead_ausente(ref)

    aviso_extra = ""
    if fila["actividad_pendiente_id"]:
        try:
            calendario.cambiar_estado(fila["actividad_pendiente_id"], "cancel")
        except calendario.ErrorCalendario as fallo:
            aviso_extra = f" La cita pendiente no se pudo cancelar: {fallo}"

    with _db() as con:
        con.execute(
            "UPDATE mantenimiento_lead SET activo=0, actividad_pendiente_id=NULL"
            " WHERE ref=?", (ref,))

    try:
        linear_leads.comentar(lead["id"], TEXTO_PARAR, autor=autor)
    except linear_leads.ErrorLeads as fallo:
        return ("Mantenimiento detenido." + aviso_extra,
                f"No se pudo dejar la nota en el issue: {fallo}")
    return "Mantenimiento detenido." + aviso_extra, ""
