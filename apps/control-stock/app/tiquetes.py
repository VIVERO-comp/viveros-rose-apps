"""La pestaña Pedidos: el trabajo ya pagado, en tiquetes.

Pedido de Abraham (30/09/2026), literal: «por agendar es cuando me pagan.
Entonces, todo lo que está en por agendar, me tiene que salir en pedidos, y
si se agenda, que salga en la fecha ahí. […] si yo lo pongo la fecha en
calendario, también que se ponga y que la pueda editar en el tiquete. Pero
quiero que sean como que tiquetes.» Es para los repartidores, para Mary y el
equipo, «así se actualiza solo».

**Este módulo no guarda NADA.** No hay tabla, no hay columna nueva, no hay
segunda verdad. Un tiquete es la composición de tres cosas que ya existen:

- el **estado, el responsable (`Resp:`), el interés y las señales** del
  issue del equipo LEAD (`linear_leads`);
- la **fecha** de la actividad del calendario amarrada a ese lead
  (`calendario`, por la marca `lead=LEAD-NN` de su descripción);
- la **plata** de la orden real de Odoo (`cot_lead`), con el abono del 50%
  solo si la orden lo pide (`pago_50_50`).

Eso es lo que quiso decir con «se actualiza solo»: el tablero REFLEJA el
CRM, no es una lista que alguien mantenga al día.

Qué sale y qué no
-----------------
Solo dos estados del embudo, y nada más:

    Por agendar   ya entró un pago real y falta la fecha
    Agendado      ya tiene su actividad en el calendario

Nuevo, Hablando y Cotizado no salen (todavía no hay plata), y Entregado,
Ganado, Perdido y Recordatorio tampoco (el trabajo ya no está por hacer).
El tablero es de **trabajo pagado por hacer**.

Los grupos, en este orden:

    ⚠ Falta agendar   los que no tienen actividad viva — franja ámbar
    ⚠ Atrasado        su fecha ya pasó y nadie marcó «Hecha»
    Hoy · jueves 2
    Mañana · viernes 3
    Vie 3 oct …

Los atrasados van ARRIBA de Hoy a propósito (eso no estaba en la maqueta):
una entrega que se pasó de fecha no puede esconderse al fondo de la lista.

La fecha es UNA sola
--------------------
Poner o cambiar la fecha desde el tiquete va por el MISMO camino que el
calendario (`agenda.agendar` la primera vez, `agenda.reprogramar` después),
así que la ida y la vuelta salen gratis: crear la actividad ya manda el lead
a **Agendado** (regla 5 del embudo) y una fecha puesta desde el calendario
aparece en el tiquete porque los dos leen la misma actividad. Aquí no se
escribe una segunda fuente de verdad para la fecha.

Solo el día, sin hora: hoy el sistema agenda todo a las 9:00 y eso no cambia
en esta tanda. Si la actividad YA trae otra hora (puesta desde el
calendario), el tiquete la muestra («Hoy · 2 pm») y al cambiar el día esa
hora se conserva — `calendario.mover` con `hora=None` no toca la marca.

Quién puede editar: el MISMO candado de Control (`control.puede_tocar`).
Todos ven el tablero completo; cada quien mueve solo lo suyo y el dueño
mueve todo. Un tiquete ajeno se ve, con su ✎ apagado.
"""

import time
from datetime import date, datetime, timedelta

from . import agenda, calendario, control, cot_lead, linear_leads, ventas

# Los dos estados del embudo que son un tiquete. En este orden no importa
# (el agrupado es por fecha), pero la lista sí: es la definición de "trabajo
# pagado por hacer" y el único lugar donde se decide qué entra al tablero.
ESTADOS = ("POR_AGENDAR", "AGENDADO")

# Las claves de los dos grupos que no son un día del calendario.
GRUPO_FALTA = "falta"
GRUPO_ATRASADO = "atrasado"

# Hasta dónde se mira el calendario hacia adelante. Las ATRASADAS no
# dependen de esto: `calendario.listar` las trae siempre, por su propio
# alias en la consulta. Un año alcanza de sobra para trabajo pagado y
# mantiene la llave de la caché estable todo el día.
DIAS_ADELANTE = 365

# El tipo de actividad que se le sugiere a un lead según su interés. Los
# tres destinos son justamente los que CIERRAN la entrega
# (`agenda.cierra_la_entrega`), que es lo correcto: un trabajo pagado que se
# agenda es trabajo que se va a entregar. El selector del formulario viene
# pre-elegido con esto y se puede cambiar — adivinar en silencio que un
# paisajismo es una «Entrega» dejaría el calendario diciendo otra cosa.
TIPO_POR_INTERES = {
    "Mantenimiento": "mantenimiento",
    "Paisajismo": "instalacion",
}
TIPO_POR_DEFECTO = "entrega"


def tipo_sugerido(lead):
    """El tipo de actividad que se le propone a este lead (ver
    `TIPO_POR_INTERES`)."""
    return TIPO_POR_INTERES.get((lead or {}).get("interes") or "",
                                TIPO_POR_DEFECTO)


# ---------------------------------------------------------------------------
# La actividad que manda en el tiquete
# ---------------------------------------------------------------------------

def _viva(actividad):
    """Una actividad cuenta si tiene fecha y nadie la marcó Hecha ni la
    canceló. Es la misma regla de `agenda.marcar_entregas_pendientes` y de
    `calendario.contar_vivas`: una cancelada no es trabajo que hacer."""
    return bool(actividad.get("fecha")) and actividad.get("estado") not in (
        "hecha", "cancel")


def _peso(actividad):
    """Cuál de las actividades de un lead manda en su tiquete.

    Primero las que ENTREGAN (Entrega, Instalación, Mantenimiento): esas son
    el trabajo. Una Recogida o una Visita del mismo lead son actividades de
    apoyo y no pueden robarle la fecha al tiquete — el alquiler que se
    entrega el viernes y se recoge el domingo tiene que leerse como «viernes».
    Entre iguales, la más temprana.
    """
    return (0 if agenda.cierra_la_entrega(actividad.get("tipo")) else 1,
            actividad.get("fecha") or "", actividad.get("hora") or "")


def actividad_por_lead(actividades):
    """{LEAD-NN: la actividad que manda} de las que vienen amarradas a un
    lead. Las no amarradas (una compra, una reunión) no son de nadie."""
    por_lead = {}
    for actividad in actividades or []:
        ref = (actividad.get("lead") or "").strip()
        if not ref or not _viva(actividad):
            continue
        anterior = por_lead.get(ref)
        if anterior is None or _peso(actividad) < _peso(anterior):
            por_lead[ref] = actividad
    return por_lead


def _actividades(refrescar=False):
    """Las actividades del calendario que pueden ser un tiquete: de hoy
    hacia adelante, más las atrasadas (esas las trae `listar` solas).

    Devuelve (lista, error): si Linear no contesta, la lista queda vacía y
    el error viaja a la pantalla — ningún tiquete se inventa una fecha.
    """
    hoy = calendario.hoy()
    try:
        return calendario.listar(
            hoy.isoformat(),
            (hoy + timedelta(days=DIAS_ADELANTE)).isoformat(),
            refrescar=refrescar), ""
    except calendario.ErrorCalendario as fallo:
        return [], str(fallo)


# ---------------------------------------------------------------------------
# La plata, de Odoo y cacheada (el tablero no espera a Odoo)
# ---------------------------------------------------------------------------

TTL_PLATA = 60

_plata_cache = {"en": 0, "dato": None, "error": ""}


def reiniciar_cache():
    """La caché de la plata, vacía. La llaman las pruebas y `refrescar()`."""
    _plata_cache.update({"en": 0, "dato": None, "error": ""})


def plata_de(refrescar=False):
    """({PP-XXXXX: plata}, error) — mismo patrón de velocidad que
    `agenda.saldos()`: lo guardado sale YA y, si venció el TTL, Odoo se
    consulta por detrás. Con la caché fría sí se espera una consulta, que es
    UNA para todos los tiquetes (ver `cot_lead.plata_de_las_reales`).

    El mapa es de TODAS las órdenes reales, no de los leads de esta
    pantalla: así el ✎ de un tiquete y el tablero completo comparten la
    misma caché sin que el primero la deje recortada para el segundo.

    Sin Odoo configurado no hay plata y tampoco hay error: el tiquete dice
    «sin cotización conectada», que en modo muestra es la verdad.

    Con error, las fichas vienen vacías A PROPÓSITO: el tiquete tiene que
    poder decir «no se pudo leer Odoo» en vez de pintar un total que no es.
    """
    if not ventas.configurado():
        return {}, ""
    guardado = _plata_cache["dato"]
    if guardado is not None and not refrescar:
        if time.time() - _plata_cache["en"] >= TTL_PLATA:
            calendario._en_fondo("tiquetes-plata",
                                 lambda: plata_de(refrescar=True))
        return dict(guardado), _plata_cache["error"]
    resultado = cot_lead.plata_de_las_reales()
    if not resultado["ok"]:
        _plata_cache.update({
            "en": time.time(), "dato": {},
            "error": f"Odoo no contestó: {resultado['error']}"})
        return {}, _plata_cache["error"]
    _plata_cache.update({"en": time.time(), "dato": resultado["plata"],
                         "error": ""})
    return dict(resultado["plata"]), ""


def refrescar():
    """Todo lo que el tablero lee, marcado para releer."""
    linear_leads.refrescar()
    calendario.invalidar_cache()
    reiniciar_cache()


# ---------------------------------------------------------------------------
# Cómo se lee una fecha
# ---------------------------------------------------------------------------

def _mes_corto(dia):
    return calendario.MESES[dia.month - 1][:3]


def fecha_corta(iso, dia_hoy=None):
    """La fecha del pie del tiquete: «Hoy», «Mañana», «Ayer» o «Vie 3 oct»."""
    if not iso:
        return ""
    hoy = date.fromisoformat(dia_hoy or calendario.hoy().isoformat())
    dia = date.fromisoformat(iso)
    diferencia = (dia - hoy).days
    if diferencia == 0:
        return "Hoy"
    if diferencia == 1:
        return "Mañana"
    if diferencia == -1:
        return "Ayer"
    return f"{calendario.DOW[dia.weekday()]} {dia.day} {_mes_corto(dia)}"


def titulo_de_dia(iso, dia_hoy=None):
    """El rótulo del grupo: «Hoy · jueves 2», «Mañana · viernes 3», «Vie 3 oct»."""
    hoy = date.fromisoformat(dia_hoy or calendario.hoy().isoformat())
    dia = date.fromisoformat(iso)
    diferencia = (dia - hoy).days
    largo = calendario.DOW_LARGO[dia.weekday()].lower()
    if diferencia == 0:
        return f"Hoy · {largo} {dia.day}"
    if diferencia == 1:
        return f"Mañana · {largo} {dia.day}"
    return f"{calendario.DOW[dia.weekday()]} {dia.day} {_mes_corto(dia)}"


def _trabajos(cuantos):
    return f"{cuantos} trabajo" + ("s" if cuantos != 1 else "")


# ---------------------------------------------------------------------------
# El tiquete
# ---------------------------------------------------------------------------

def _iniciales(nombre):
    """La letra del avatar del responsable, o «?» si no tiene dueño."""
    letras = [c for c in (nombre or "") if c.isalpha()]
    return letras[0].upper() if letras else "?"


def _que_se_hace(actividad, lead):
    """El renglón «qué se hace» del tiquete.

    Agendado: el tipo de actividad y, si la actividad trae nota, la nota
    recortada («Entrega · entrar por el sótano»). Sin agendar: lo que falta,
    dicho en una línea — un tiquete se lee suelto (para eso es un tiquete) y
    tiene que decir qué le pasa aunque nadie vea el rótulo del grupo.
    El interés del lead no va aquí: vive en su chip.
    """
    if actividad is None:
        return "Falta ponerle fecha"
    nombre = calendario.nombre_de_tipo(actividad.get("tipo"))
    nota = " ".join((actividad.get("nota") or "").split())
    if nota:
        if len(nota) > 60:
            nota = nota[:59].rstrip() + "…"
        return f"{nombre} · {nota}"
    return nombre


def _tiquete(lead, actividad, plata, plata_error, dia_hoy, puede_editar):
    """Un lead + su actividad + su plata, listo para la plantilla.

    La plantilla no decide nada: recibe los textos, el color de la franja y
    si el ✎ está vivo.
    """
    falta = actividad is None
    fecha = (actividad or {}).get("fecha") or ""
    hora = (actividad or {}).get("hora") or ""
    # La hora solo se nombra si NO es la de siempre: el sistema agenda todo
    # a las 9:00, así que repetirla en cada tiquete no dice nada. Una hora
    # distinta la puso alguien a mano en el calendario, y esa sí importa.
    hora_propia = bool(hora and hora != calendario.HORA_POR_DEFECTO)
    texto_fecha = "Sin fecha" if falta else fecha_corta(fecha, dia_hoy)
    if hora_propia:
        texto_fecha += " · " + calendario.hora_bonita(hora)

    if plata is not None:
        aviso_plata = ""
    elif plata_error:
        aviso_plata = plata_error
    else:
        aviso_plata = "Sin cotización conectada"

    return {
        "ref": lead["ref"],
        "nombre": lead["nombre"],
        "pp": lead.get("pp") or "",
        "celular": lead.get("celular") or "",
        "wa": lead.get("wa") or "",
        "estado": lead["estado"],
        "estado_nombre": lead.get("estado_nombre") or "",
        "interes": lead.get("interes") or "",
        "pago": lead.get("pago") or "",
        "te_toca": bool(lead.get("te_toca")),
        "resp": lead.get("resp") or "",
        "resp_titulo": lead.get("resp") or "Sin dueño",
        "inicial": _iniciales(lead.get("resp")),
        # Lo de la actividad.
        "falta_fecha": falta,
        "actividad_id": (actividad or {}).get("id") or "",
        "actividad_ref": (actividad or {}).get("ref") or "",
        "tipo": (actividad or {}).get("tipo") or "",
        "tipo_nombre": (calendario.nombre_de_tipo(actividad.get("tipo"))
                        if actividad else ""),
        "zona": (actividad or {}).get("lugar") or "",
        "que": _que_se_hace(actividad, lead),
        "fecha": fecha,
        "hora": hora,
        "fecha_texto": texto_fecha,
        # Ámbar cuando falta la fecha; si está agendado, el color del tipo
        # de actividad — el MISMO que usa el calendario, para que un trabajo
        # se vea igual en las dos pantallas.
        "franja": ("" if falta else calendario.color_de(actividad.get("tipo"))),
        "plata": plata,
        "plata_aviso": aviso_plata,
        "puede_editar": puede_editar,
        # Hace cuánto NACIÓ el lead (`createdAt` del issue), que es lo que
        # Linear guarda. NO es «hace cuánto pagó»: el instante del pago no
        # está en ningún lado que esta pantalla pueda leer, así que no se
        # nombra así en ninguna parte. Ordena «Falta agendar» —el lead más
        # viejo arriba— y se pinta en el tiquete sin fecha.
        "dias": lead.get("dias") or 0,
        "hace": lead.get("hace") or "",
    }


def _grupo(clave, titulo, nota, tiquetes, urge=False, hoy=False):
    return {"clave": clave, "titulo": titulo, "nota": nota,
            "tiquetes": tiquetes, "urge": urge, "hoy": hoy}


def agrupar(tiquetes, dia_hoy=None):
    """Los tiquetes en sus grupos, en el orden en que se leen.

    Falta agendar arriba siempre (aunque esté vacío NO se pinta: un grupo
    vacío con su rótulo haría ruido todos los días), después los atrasados,
    después Hoy, Mañana y cada día que tenga algo. Un día sin tiquetes no
    existe: el tablero es la lista del trabajo, no un calendario.
    """
    dia_hoy = dia_hoy or calendario.hoy().isoformat()
    faltan = [t for t in tiquetes if t["falta_fecha"]]
    atrasados = [t for t in tiquetes
                 if not t["falta_fecha"] and t["fecha"] < dia_hoy]
    por_dia = {}
    for tiquete in tiquetes:
        if tiquete["falta_fecha"] or tiquete["fecha"] < dia_hoy:
            continue
        por_dia.setdefault(tiquete["fecha"], []).append(tiquete)

    grupos = []
    if faltan:
        # El lead más viejo arriba (`dias`, desde que nació el issue). Es el
        # reloj que hay: Linear no guarda cuándo entró el pago.
        faltan.sort(key=lambda t: (-(t.get("dias") or 0), t["ref"]))
        grupos.append(_grupo(
            GRUPO_FALTA, "⚠ Falta agendar",
            f"{len(faltan)} · ya pagaron", faltan, urge=True))
    if atrasados:
        atrasados.sort(key=lambda t: (t["fecha"], t["hora"], t["ref"]))
        grupos.append(_grupo(
            GRUPO_ATRASADO, "⚠ Atrasado",
            f"{len(atrasados)} · se pasó la fecha", atrasados, urge=True))
    for dia in sorted(por_dia):
        del_dia = sorted(por_dia[dia], key=lambda t: (t["hora"], t["ref"]))
        grupos.append(_grupo(
            dia, titulo_de_dia(dia, dia_hoy), _trabajos(len(del_dia)),
            del_dia, hoy=(dia == dia_hoy)))
    return grupos


def tablero(alcance, leads=None, actividades=None, refrescar=False,
            puede_escribir=True):
    """Todo lo que la pantalla necesita.

    `{"grupos", "cuantos", "error_calendario", "error_plata", "actualizado"}`.
    Los dos errores viajan APARTE: que Odoo no conteste no puede esconder
    las fechas, y que Linear no conteste no puede esconder la plata. Cada
    bloque dice lo suyo y nada se inventa.
    """
    if leads is None:
        leads = linear_leads.listar(refrescar=refrescar)
    error_calendario = ""
    if actividades is None:
        actividades, error_calendario = _actividades(refrescar=refrescar)

    del_embudo = [l for l in leads if l["estado"] in ESTADOS]
    # Sin tiquetes no se le pregunta nada a Odoo: una pantalla vacía no
    # tiene por qué pagar una consulta.
    plata_por_pp, error_plata = (
        plata_de(refrescar=refrescar) if del_embudo else ({}, ""))
    por_lead = actividad_por_lead(actividades)

    dia_hoy = calendario.hoy().isoformat()
    tiquetes = [
        _tiquete(lead, por_lead.get(lead["ref"]),
                 plata_por_pp.get((lead.get("pp") or "").strip().upper()),
                 error_plata, dia_hoy,
                 puede_escribir and control.puede_tocar(lead, alcance))
        for lead in del_embudo]

    return {
        "grupos": agrupar(tiquetes, dia_hoy),
        "cuantos": len(tiquetes),
        "error_calendario": error_calendario,
        "error_plata": error_plata,
        "actualizado": calendario.hora_bonita(
            datetime.now(calendario.ZONA_PANAMA).strftime("%H:%M")),
    }


def para_editar(ref, alcance, puede_escribir=True):
    """El tiquete que el ✎ abre, con lo que el formulario necesita: los
    responsables de Linear, los tipos agendables y el tipo sugerido.

    None si el lead no existe, no es un tiquete, o esta sesión no lo puede
    tocar — el candado no es el botón que no se pinta.
    """
    lead = linear_leads.uno(ref)
    if lead is None or lead["estado"] not in ESTADOS:
        return None
    if not (puede_escribir and control.puede_tocar(lead, alcance)):
        return None
    actividades, _error = _actividades()
    actividad = actividad_por_lead(actividades).get(lead["ref"])
    plata_por_pp, error_plata = plata_de()
    dia_hoy = calendario.hoy().isoformat()
    tiquete = _tiquete(
        lead, actividad, plata_por_pp.get((lead.get("pp") or "").upper()),
        error_plata, dia_hoy, True)
    return {
        "tiquete": tiquete,
        "responsables": linear_leads.responsables(),
        "tipos": agenda.TIPOS,
        "tipo_sugerido": actividad["tipo"] if actividad else tipo_sugerido(lead),
        # Con fecha puesta el campo arranca en la que tiene; sin fecha, en
        # hoy: la mayoría del trabajo pagado se agenda para pronto.
        "fecha_sugerida": tiquete["fecha"] or dia_hoy,
        "resp_sugerido": (lead.get("resp")
                          or (actividad or {}).get("resp_lead") or ""),
        "editable": True,
    }


# ---------------------------------------------------------------------------
# Poner o cambiar la fecha: SIEMPRE por el camino del calendario
# ---------------------------------------------------------------------------

def poner_fecha(ref, fecha, tipo="", resp="", autor=""):
    """La fecha del tiquete. Devuelve ("aviso", "error").

    Sin actividad todavía -> `agenda.agendar`: nace la actividad amarrada al
    lead y el lead pasa a **Agendado** por la regla 5 del embudo. Con
    actividad -> `agenda.reprogramar`, que mueve el día SIN tocar el estado
    y, con `hora=None`, deja la hora tal como estaba.

    No hay un camino propio: si esto escribiera la fecha por su cuenta,
    habría dos verdades y la del calendario sería la equivocada la mitad de
    las veces.
    """
    lead = linear_leads.uno(ref)
    if lead is None:
        return "", linear_leads.mensaje_lead_ausente(ref)
    if lead["estado"] not in ESTADOS:
        return "", (f"{lead['nombre']} está en "
                    f"{lead.get('estado_nombre') or 'otro estado'}: no es un "
                    f"pedido por hacer.")
    resp = (resp or "").strip()
    if resp and resp not in linear_leads.responsables():
        # Las etiquetas no se crean solas (regla 3): acá se dice en vez de
        # inventar una.
        return "", f"No existe la etiqueta «Resp: {resp}» en Linear."

    actividades, error = _actividades()
    if error:
        return "", f"No se pudo leer el calendario: {error}"
    actividad = actividad_por_lead(actividades).get(lead["ref"])

    try:
        if actividad is None:
            aviso = agenda.agendar(
                ref_lead=lead["ref"], tipo=(tipo or tipo_sugerido(lead)),
                fecha=fecha, resp=resp, autor=autor)
        else:
            aviso = agenda.reprogramar(actividad["id"], fecha)
            aviso = _ajustar_responsable(lead, actividad, resp, aviso)
    except (calendario.ErrorCalendario, linear_leads.ErrorLeads) as fallo:
        return "", str(fallo)
    refrescar()
    return aviso, ""


def _ajustar_responsable(lead, actividad, resp, aviso):
    """El responsable al reprogramar: la etiqueta `Resp:` del lead Y la
    marca de la actividad, para que las dos digan lo mismo.

    Es un extra: la fecha ya quedó movida, así que un fallo de Linear acá
    no deshace nada y solo se nombra en el aviso. El `assignee` del issue
    no se toca nunca (regla 2).
    """
    if not resp or resp == (lead.get("resp") or ""):
        return aviso
    try:
        linear_leads.poner_responsable(lead["id"], resp)
        calendario.cambiar_detalle(actividad["id"], resp_lead=resp)
    except (linear_leads.ErrorLeads, calendario.ErrorCalendario):
        return aviso + f" El responsable no se pudo cambiar a {resp}."
    return aviso + f" Ahora es de {resp}."
