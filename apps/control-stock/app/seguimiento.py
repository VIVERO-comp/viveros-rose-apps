"""El Seguimiento con fecha (2/10/2026, spec C).

El botón Seguimiento de la ficha deja de ser una bandera a secas: al
tocarlo pide fecha (mañana / 3 días / 1 semana / elegir) y, además de la
etiqueta señal «Seguimiento» de siempre, crea la actividad
«Seguimiento — <cliente>» en el calendario, asignada al `Resp:` del lead
por el mecanismo del calendario (la marca `resp=`, nunca `assignee`).

**La actividad NO mueve el estado del lead** — no es Entrega, Instalación
ni Mantenimiento. Se distingue por un CAMPO (`seguimiento_lead.
actividad_id`, comparado contra el id de la actividad que se marca
Hecha), nunca por el texto del título: el mismo patrón que el
mantenimiento recurrente (`mantenimiento.es_recurrente`). Al marcarla
«Hecha», la etiqueta señal se quita sola y la fila se borra — el gancho
vive en `agenda.al_marcar_hecha`, ANTES del camino que entrega.

**El tipo de actividad lo crea Korto en Linear** (grupo «Tipo de
actividad» del equipo VIV); su nombre vive en la clave de config
`SEGUIMIENTO_TIPO` («Seguimiento» si nadie la cambió — podría quedar
«Seguimiento cliente»). El código solo BUSCA: si el tipo no existe
todavía, NO se crea la actividad — la señal se pone igual (el
comportamiento de hoy) y el aviso lo dice en pantalla y en el log, sin
romper nada.

La tabla `seguimiento_lead` (misma base datos/control-stock.db) ancla la
actividad al lead y le da la fecha a la tarjeta de Control sin releer el
calendario en cada pintado — apoyo de pantalla, como las demás: la
verdad del estado sigue en Linear.
"""

import re
from datetime import datetime

from . import calendario, linear_leads
from .datos import ZONA_PANAMA, _db, leer_config

# La clave de config con el NOMBRE del tipo en Linear, y su valor si
# nadie la cambió. Editable sin desplegar, como RESPONDER_A_MANO_HORAS.
CLAVE_TIPO = "SEGUIMIENTO_TIPO"
TIPO_POR_DEFECTO = "Seguimiento"

# La etiqueta señal del lead (equipo LEAD) — es OTRA cosa que el tipo de
# actividad del calendario (equipo VIV), aunque se llamen igual: conviven.
SENAL = "Seguimiento"


def iniciar_tablas():
    with _db() as con:
        # Una fila por lead con seguimiento programado: qué actividad es
        # (para el gancho de «Hecha») y para cuándo (para la tarjeta).
        # `actividad_id` puede quedar NULL si la cita no se pudo crear —
        # la fila no se escribe en ese caso, pero el esquema no lo
        # prohíbe para no reventar con una base vieja.
        con.execute("""
            CREATE TABLE IF NOT EXISTS seguimiento_lead (
                lead_ref TEXT PRIMARY KEY,
                actividad_id TEXT,
                fecha TEXT NOT NULL,
                creado_en TEXT NOT NULL
            )
        """)


def registro_aviso(texto):
    """Un aviso al log, propio del módulo (mismo criterio que
    mantenimiento.py)."""
    import logging
    logging.getLogger("control_stock").warning(texto)


def _hoy():
    return datetime.now(ZONA_PANAMA).date()


def tipo_configurado():
    """El nombre del tipo de actividad en Linear, desde config."""
    return (leer_config(CLAVE_TIPO, "") or "").strip() or TIPO_POR_DEFECTO


def tipo_en_linear():
    """(id_etiqueta, aviso) — ¿existe el tipo en el grupo «Tipo de
    actividad» del equipo del calendario?

    En modo muestra (sin Linear) el tipo "existe": el calendario de
    muestra crea en memoria y es como se prueba el flujo completo. Con
    Linear real se busca por NOMBRE crudo en `catalogo()["tipos_nombres"]`
    — así un tipo que Korto llame «Seguimiento cliente» también calza. Si
    no está (o Linear no contestó), el id sale vacío y `aviso` trae el
    texto para la pantalla: la actividad NO se crea — el código nunca
    crea etiquetas ni tipos.
    """
    nombre = tipo_configurado()
    if not calendario.configurado():
        return "muestra", ""
    try:
        encontrado = (calendario.catalogo().get("tipos_nombres") or {}) \
            .get(nombre.lower())
    except calendario.ErrorCalendario as fallo:
        return "", (f"No se pudo mirar el catálogo del calendario: {fallo}")
    if encontrado:
        return encontrado, ""
    return "", (f"el tipo de actividad «{nombre}» todavía no existe en "
                f"Linear (grupo Tipo de actividad del calendario): la "
                f"cita no se creó. Lo crea Abraham; el botón queda listo.")


def _fila(ref):
    iniciar_tablas()
    with _db() as con:
        return con.execute(
            "SELECT * FROM seguimiento_lead WHERE lead_ref=?", (ref,)).fetchone()


def _guardar(ref, actividad_id, fecha):
    iniciar_tablas()
    with _db() as con:
        con.execute(
            "INSERT INTO seguimiento_lead (lead_ref, actividad_id, fecha, creado_en) "
            "VALUES (?,?,?,?) "
            "ON CONFLICT(lead_ref) DO UPDATE SET "
            "actividad_id=excluded.actividad_id, fecha=excluded.fecha, "
            "creado_en=excluded.creado_en",
            (ref, actividad_id, fecha, datetime.now(ZONA_PANAMA).isoformat()))


def _borrar(ref):
    iniciar_tablas()
    with _db() as con:
        con.execute("DELETE FROM seguimiento_lead WHERE lead_ref=?", (ref,))


def fechas():
    """{lead_ref: fecha} de todos los seguimientos programados — UNA
    consulta por pintada del tablero, no una por tarjeta."""
    iniciar_tablas()
    with _db() as con:
        return {f["lead_ref"]: f["fecha"] for f in con.execute(
            "SELECT lead_ref, fecha FROM seguimiento_lead")}


def _cancelar_cita_anterior(fila):
    """Reprogramar pisa la cita vieja: primero se cancela la anterior
    (best-effort: un Linear caído no frena la nueva — la tabla manda y
    el gancho de «Hecha» de la vieja ya no matchea)."""
    if not fila or not fila["actividad_id"]:
        return
    try:
        calendario.cambiar_estado(fila["actividad_id"], "cancel")
    except Exception as fallo:
        registro_aviso(
            f"Seguimiento: la cita anterior de {fila['lead_ref']} no se "
            f"pudo cancelar: {fallo}")


def _poner_senal(lead):
    """La etiqueta señal de siempre, con su candado de siempre: si no
    existe en Linear no se pone, queda en el log y nada revienta."""
    if SENAL not in linear_leads.senales_disponibles():
        registro_aviso(
            f"La señal «{SENAL}» no existe en Linear: {lead['ref']} queda "
            f"sin ella.")
        return
    try:
        linear_leads.poner_etiqueta_suelta(lead["id"], SENAL, True)
    except linear_leads.ErrorLeads as fallo:
        registro_aviso(
            f"La señal «{SENAL}» no se pudo poner en {lead['ref']}: {fallo}")


def programar(ref, fecha, autor=""):
    """El botón con fecha: señal + actividad + ancla. ("aviso", "error").

    El orden: primero la actividad (si Linear falla ahí, nada se movió y
    el empleado lo reintenta — mismo criterio que `agenda.agendar`),
    después la señal, de último la fila local.
    """
    lead = linear_leads.uno(ref)
    if lead is None:
        return "", linear_leads.mensaje_lead_ausente(ref)
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", fecha or ""):
        return "", "Falta la fecha del seguimiento."
    if fecha < _hoy().isoformat():
        return "", "Esa fecha ya pasó: el seguimiento va de hoy en adelante."

    etiqueta_id, aviso_tipo = tipo_en_linear()
    if not etiqueta_id:
        # El candado de la casa: sin el tipo no hay cita, pero la señal de
        # siempre se pone igual — el lead no se queda sin su bandera por
        # algo que falta en Linear.
        _poner_senal(lead)
        linear_leads.refrescar()
        registro_aviso(f"Seguimiento de {ref} sin cita: {aviso_tipo}")
        return (f"{lead['nombre']}: Seguimiento puesto, pero ojo — "
                + aviso_tipo), ""

    anterior = _fila(ref)
    _cancelar_cita_anterior(anterior)
    try:
        creada = calendario.crear(
            tipo="seguimiento", cliente=lead["nombre"], fecha=fecha,
            hora=calendario.HORA_POR_DEFECTO,
            dur=calendario.DURACION_POR_DEFECTO,
            lugar="", resp_id="", prioridad=3,
            nota="Seguimiento programado desde la ficha de Control.",
            lead=lead["ref"], resp_lead=lead.get("resp") or "",
            etiqueta_id="" if etiqueta_id == "muestra" else etiqueta_id)
    except calendario.ErrorCalendario as fallo:
        return "", f"La cita del seguimiento no se pudo crear: {fallo}"

    _poner_senal(lead)
    _guardar(ref, creada["id"], fecha)
    linear_leads.refrescar()
    quien = f" · {lead['resp']}" if lead.get("resp") else ""
    return (f"Seguimiento de {lead['nombre']} el "
            f"{calendario.dmy(fecha)}{quien}. La tarjeta sube ese día."), ""


def es_de_seguimiento(actividad):
    """¿Esta actividad es EL seguimiento programado de su lead?

    Por el campo `actividad_id`, nunca por el título — igual que
    `mantenimiento.es_recurrente`: una actividad de tipo Seguimiento
    agendada a mano, suelta, no matchea y sigue su camino normal (que
    para ese tipo no mueve nada, porque no entrega).
    """
    ref = (actividad or {}).get("lead") or ""
    if not ref:
        return False
    fila = _fila(ref)
    return bool(fila and fila["actividad_id"]
                and fila["actividad_id"] == actividad.get("id"))


def al_marcar_hecha(actividad, autor=""):
    """«Hecha» en el calendario: la señal se quita sola y la fila se
    borra. El estado del lead NO se toca — un seguimiento no entrega.
    Devuelve el aviso para la pantalla."""
    ref = (actividad or {}).get("lead") or ""
    _borrar(ref)
    lead = linear_leads.uno(ref)
    if lead is None:
        return ""
    try:
        linear_leads.poner_etiqueta_suelta(lead["id"], SENAL, False)
    except linear_leads.ErrorLeads as fallo:
        return (f"Seguimiento de {lead['nombre']} hecho, pero la señal no "
                f"se pudo quitar del lead: {fallo}")
    linear_leads.refrescar()
    return (f"Seguimiento de {lead['nombre']} hecho: la señal se quitó "
            f"sola. El lead se queda donde está.")


def cancelar(ref):
    """El empleado apagó la señal a mano desde la ficha: la cita pendiente
    se cancela y la fila se borra — una cita huérfana le pediría a alguien
    un seguimiento que ya nadie marcó. Devuelve el texto extra del aviso,
    o ""."""
    fila = _fila(ref)
    if fila is None:
        return ""
    _cancelar_cita_anterior(fila)
    _borrar(ref)
    return "La cita del seguimiento se canceló."
