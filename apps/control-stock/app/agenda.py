"""Fase 4: el calendario cierra el embudo.

Los tres últimos estados del embudo los mueve esta pantalla (24/09/2026):

    Por agendar  --(poner fecha)-->  Agendado
    Agendado     --(«Hecha»)------>  Entregado
    Entregado    --(saldo 0)------>  Ganado

El bloque «Por agendar» del calendario lista los leads que ya pagaron
(estado 4 del embudo), con su etiqueta de pago y **el saldo que trae
Odoo**. Un toque en «Agendar» pide fecha · tipo · responsable, crea la
actividad del calendario amarrada al lead y el lead pasa a Agendado.

En la actividad, el saldo va **arriba del botón**, nunca escondido: quien
entrega lo ve antes de marcar «Hecha». Al marcarla, el lead pasa a
Entregado; si el saldo quedó en cero, sigue solo a Ganado. Si entrega con
saldo, se queda en Entregado con la etiqueta «Cobrar saldo» y aparece en
la vista Cobrar de Linear; cuando entre el pago que salda, pasa a Ganado.

Reglas y decisiones que este módulo cumple:

- **Un sistema, un trabajo.** El estado vive en Linear (`linear_leads`), el
  dinero en Odoo (`sale.order.saldo_pendiente`, el campo de la Fase 3b) y
  la fecha en el calendario. Aquí no se guarda NADA: no hay tabla.
- **El saldo no se inventa.** Si Odoo no responde, la pantalla lo dice en
  vez de mostrar $0 y mentir — un $0 falso hace que alguien entregue sin
  cobrar.
- **Cualquier empleado con acceso al calendario puede marcar «Hecha»**, no
  solo el responsable (decisión del dueño).
- **El responsable al agendar se sugiere del lead y es editable**
  (decisión del 24/09/2026), y va por el nombre del empleado en la marca
  de la actividad, nunca por `assignee`: los empleados no tienen asiento
  de Linear.
- **Un lead sin responsable que se marca «Hecha» hereda el del empleado
  que la marcó** (decisión del 24/09/2026).
- **Una Recogida no mueve el estado.** Retirar las plantas de alquiler
  después del evento es solo una actividad: el lead ya se entregó.
- **Reprogramar mueve la fecha sin tocar el estado.**
"""

import re
import time
import unicodedata

from . import calendario, linear_leads, ventas

TTL_SALDOS = 60

# Los 5 tipos que se pueden agendar desde un lead (decisión del dueño,
# 24/09/2026: entrega · montaje · mantenimiento · visita · retiro).
#
# `clave` es un tipo que YA existe en el grupo "Tipo de actividad" de
# Linear, porque las etiquetas nunca se crean solas (regla que no se
# rompe): «montaje» es la etiqueta «Instalación» y «retiro» es
# «Recogida», que además ya vive en el filtro Eventos del calendario.
#
# `entrega` dice si marcar la actividad como Hecha mueve el lead a
# Entregado. La Recogida no: es el retiro de las plantas de alquiler
# DESPUÉS del evento, cuando el lead ya se entregó.
TIPOS = [
    {"clave": "entrega", "entrega": True},
    {"clave": "instalacion", "entrega": True},
    {"clave": "mantenimiento", "entrega": True},
    {"clave": "visita", "entrega": True},
    {"clave": "recogida", "entrega": False},
]
for _t in TIPOS:
    _t["nombre"] = calendario.POR_CLAVE[_t["clave"]]["nombre"]
    _t["color"] = calendario.POR_CLAVE[_t["clave"]]["color"]

POR_CLAVE = {t["clave"]: t for t in TIPOS}


def cierra_la_entrega(tipo):
    """¿Marcar Hecha una actividad de este tipo mueve el lead a Entregado?

    Un tipo que no es de los cinco agendables (una reunión, una compra)
    nunca mueve el embudo: no es trabajo del cliente.
    """
    return bool((POR_CLAVE.get(tipo) or {}).get("entrega"))


# ---------------------------------------------------------------------------
# El saldo, desde Odoo (el campo saldo_pendiente de la Fase 3b)
# ---------------------------------------------------------------------------

CAMPOS_ORDEN = ["name", "client_order_ref", "amount_total", "total_pagado",
                "saldo_pendiente", "etapa_cobro", "fecha_agendada"]

_saldos_cache = {"en": 0, "dato": None, "error": ""}


def _pp_de(lead):
    return (lead.get("pp") or "").strip().upper()


def _leer_saldos_de_odoo():
    """{PP-XXXXX: ficha de cobro} leyendo las cotizaciones vivas de Odoo.

    Una orden se casa con su lead por el `client_order_ref` (las de
    servicio) o por el `lead_ref` de su oportunidad (las de retail), que es
    exactamente como las casa el addon al registrar un pago.
    """
    filas = ventas._ejecutar("sale.order", "search_read", [
        [("state", "!=", "cancel"),
         ("reemplazada_por_id", "=", False),
         "|",
         ("client_order_ref", "=like", "PP-%"),
         ("opportunity_id.lead_ref", "=like", "PP-%")],
        CAMPOS_ORDEN + ["opportunity_id"],
    ], {"limit": 400, "order": "id desc"})

    # Para las de retail el ref vive en la oportunidad: se leen de una sola
    # vez en vez de una consulta por orden.
    ids_oportunidad = sorted({f["opportunity_id"][0] for f in filas
                              if f.get("opportunity_id")})
    refs_oportunidad = {}
    if ids_oportunidad:
        for lead_odoo in ventas._ejecutar(
                "crm.lead", "read", [ids_oportunidad, ["lead_ref"]]):
            refs_oportunidad[lead_odoo["id"]] = (
                (lead_odoo.get("lead_ref") or "").strip().upper())

    saldos = {}
    for fila in filas:
        ref = (fila.get("client_order_ref") or "").strip().upper()
        if not ref.startswith("PP-") and fila.get("opportunity_id"):
            ref = refs_oportunidad.get(fila["opportunity_id"][0], "")
        if not ref.startswith("PP-") or ref in saldos:
            continue  # `order: id desc`: la primera es la más nueva
        saldos[ref] = {
            "orden_id": fila["id"],
            "orden": fila.get("name") or "",
            "total": float(fila.get("amount_total") or 0.0),
            "pagado": float(fila.get("total_pagado") or 0.0),
            "saldo": float(fila.get("saldo_pendiente") or 0.0),
            "etapa": fila.get("etapa_cobro") or "",
            "agendada": fila.get("fecha_agendada") or "",
        }
    return saldos


def saldos(refrescar=False):
    """({PP-XXXXX: ficha}, error).

    `error` es el texto para la pantalla cuando Odoo no responde. En ese
    caso las fichas vienen vacías A PROPÓSITO: es mejor que la pantalla
    diga "no se pudo leer el saldo" que enseñar un $0 que no es cierto.
    """
    if not ventas.configurado():
        return dict(_SALDOS_MUESTRA), ""
    guardado = _saldos_cache["dato"]
    if guardado is not None and not refrescar:
        if time.time() - _saldos_cache["en"] >= TTL_SALDOS:
            calendario._en_fondo("agenda-saldos", lambda: saldos(refrescar=True))
        return dict(guardado), _saldos_cache["error"]
    try:
        dato = _leer_saldos_de_odoo()
    except Exception as fallo:  # XML-RPC, red, Odoo caído: da igual cuál
        _saldos_cache.update({
            "en": time.time(), "dato": {},
            "error": f"Odoo no contestó: el saldo no se pudo leer ({fallo})."})
        return {}, _saldos_cache["error"]
    _saldos_cache.update({"en": time.time(), "dato": dato, "error": ""})
    return dict(dato), ""


def refrescar():
    _saldos_cache.update({"en": 0, "dato": None, "error": ""})


def plata(monto):
    """$1 525.00 — el formato de la app, con el espacio de los miles."""
    texto = f"{float(monto or 0):,.2f}".replace(",", " ")
    return f"${texto}"


def _con_saldo(lead, fichas, error):
    """El lead con su plata pegada, lista para la plantilla.

    `saldo_conocido` es la diferencia que importa: False significa "no se
    sabe", y la pantalla tiene que decirlo en vez de pintar $0.00.
    """
    ficha = fichas.get(_pp_de(lead))
    lead = dict(lead)
    if ficha is None:
        lead.update({
            "saldo": None, "saldo_texto": "", "saldo_conocido": False,
            "total": None, "pagado": None, "orden": "", "orden_id": None,
            "agendada": "",
            "saldo_aviso": (error or "Sin cotización en Odoo para este lead.")})
        return lead
    lead.update({
        "saldo": ficha["saldo"], "saldo_conocido": True,
        "saldo_texto": plata(ficha["saldo"]),
        "total": ficha["total"], "total_texto": plata(ficha["total"]),
        "pagado": ficha["pagado"], "pagado_texto": plata(ficha["pagado"]),
        "orden": ficha["orden"], "orden_id": ficha["orden_id"],
        "etapa": ficha["etapa"], "agendada": ficha["agendada"],
        "saldo_aviso": ""})
    return lead


def por_agendar():
    """{"leads": [...], "error": str} — el bloque del calendario.

    Los leads en «Por agendar» (ya entró un pago real), el más viejo
    arriba, cada uno con su etiqueta de pago y su saldo.
    """
    fichas, error = saldos()
    leads = [_con_saldo(l, fichas, error)
             for l in linear_leads.en_estado("POR_AGENDAR")]
    leads.sort(key=lambda l: -l["dias"])
    return {"leads": leads, "error": error}


def lead_con_saldo(ref):
    """Un lead por su LEAD-NN, con su plata. None si no está."""
    lead = linear_leads.uno(ref)
    if lead is None:
        return None
    fichas, error = saldos()
    return _con_saldo(lead, fichas, error)


# ---------------------------------------------------------------------------
# Agendar: la fecha crea la actividad y el lead pasa a Agendado
# ---------------------------------------------------------------------------

def _sin_acentos(texto):
    plano = unicodedata.normalize("NFD", texto or "")
    return "".join(c for c in plano if not unicodedata.combining(c)).strip().lower()


def responsable_de_empleada(empleada):
    """El nombre `Resp:` que le corresponde a quien está en la sesión, o "".

    Se casa por el primer nombre, sin acentos ni mayúsculas, contra las
    etiquetas del grupo Responsable de Linear: así "Rubén Pérez" y
    "ruben@viverorose.com" caen los dos en `Resp: Ruben`, y sumar a alguien
    al equipo sigue siendo crear su etiqueta allá.
    """
    candidatos = []
    for campo in ("nombre", "email", "id"):
        valor = (empleada or {}).get(campo) or ""
        candidatos.append(_sin_acentos(valor.split("@")[0]))
        candidatos.extend(_sin_acentos(p) for p in valor.replace(".", " ").split())
    for nombre in linear_leads.responsables():
        if _sin_acentos(nombre) in candidatos:
            return nombre
    return ""


def agendar(ref_lead, tipo, fecha, hora=None, resp="", dur=None, lugar="",
            nota="", autor=""):
    """Crea la actividad del lead y lo pasa a «Agendado».

    El orden importa: primero la actividad (si Linear falla ahí, nada se
    movió y el empleado lo vuelve a intentar), después el estado del lead y
    de último la fecha en Odoo, que es informativa.

    Devuelve el texto del aviso para la pantalla.
    """
    if tipo not in POR_CLAVE:
        raise calendario.ErrorCalendario("Ese tipo de actividad no se agenda desde un lead.")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", fecha or ""):
        raise calendario.ErrorCalendario("Falta la fecha.")
    lead = linear_leads.uno(ref_lead)
    if lead is None:
        raise calendario.ErrorCalendario("Ese lead ya no está en Linear.")

    resp = (resp or "").strip() or lead.get("resp") or ""
    creada = calendario.crear(
        tipo=tipo, cliente=lead["nombre"], fecha=fecha,
        hora=hora or calendario.HORA_POR_DEFECTO,
        dur=dur or calendario.DURACION_POR_DEFECTO,
        lugar=lugar, resp_id="", prioridad=3, nota=nota,
        lead=lead["ref"], resp_lead=resp)

    # El responsable del lead se pone si no lo tenía, o si el empleado
    # eligió otro al agendar: el lead y su actividad no pueden discrepar.
    if resp and resp != lead.get("resp"):
        try:
            linear_leads.poner_responsable(lead["id"], resp)
        except linear_leads.ErrorLeads:
            pass  # la etiqueta es un extra: la actividad ya quedó

    linear_leads.mover_estado(lead["id"], "AGENDADO")
    _escribir_fecha_en_odoo(lead, fecha)
    linear_leads.refrescar()
    refrescar()

    quien = f" · {resp}" if resp else ""
    return (f"{POR_CLAVE[tipo]['nombre']} de {lead['nombre']} el "
            f"{calendario.dmy(fecha)}{quien}. El lead pasó a Agendado.")


def _escribir_fecha_en_odoo(lead, fecha):
    """La fecha en `sale.order.fecha_agendada` (el campo de la Fase 3b).

    Best-effort a propósito: es informativa para quien mire la cotización
    en Odoo. Si falla, la actividad y el embudo ya quedaron bien.
    """
    if not ventas.configurado():
        return
    fichas, _error = saldos()
    ficha = fichas.get(_pp_de(lead))
    if not ficha or not ficha.get("orden_id"):
        return
    try:
        ventas._ejecutar("sale.order", "write",
                         [[ficha["orden_id"]], {"fecha_agendada": fecha}])
    except Exception:
        pass


def reprogramar(id_actividad, fecha, hora=None):
    """Mueve la fecha SIN tocar el estado del lead (regla del plan)."""
    calendario.mover(id_actividad, fecha, hora)
    actividad = next((a for a in calendario.listar("1970-01-01", "2100-01-01")
                      if a["id"] == id_actividad), None)
    if actividad and actividad.get("lead"):
        lead = linear_leads.uno(actividad["lead"])
        if lead:
            _escribir_fecha_en_odoo(lead, fecha)
    return f"Movida al {calendario.dmy(fecha)}. El lead se queda donde está."


# ---------------------------------------------------------------------------
# «Hecha» → Entregado, y Ganado si el saldo quedó en cero
# ---------------------------------------------------------------------------

def al_marcar_hecha(actividad, autor=""):
    """Lo que el embudo hace cuando una actividad se marca Hecha.

    Devuelve el texto que la pantalla le suma al aviso, o "" si esta
    actividad no mueve nada (una Recogida, o una actividad sin lead).
    """
    ref = (actividad or {}).get("lead") or ""
    if not ref or not cierra_la_entrega(actividad.get("tipo")):
        return ""
    lead = linear_leads.uno(ref)
    if lead is None:
        return ""

    # Decisión del dueño (24/09/2026): un lead sin responsable hereda el
    # del empleado que marcó la actividad — alguien la hizo, y ese alguien
    # queda registrado.
    if not lead.get("resp"):
        heredado = actividad.get("resp_lead") or autor
        if heredado:
            try:
                linear_leads.poner_responsable(lead["id"], heredado)
            except linear_leads.ErrorLeads:
                pass

    movido = linear_leads.mover_estado(lead["id"], "ENTREGADO")
    aviso = f"{lead['nombre']} pasó a Entregado." if movido else ""
    aviso += " " + _cerrar_o_cobrar(lead, autor)
    linear_leads.refrescar()
    return aviso.strip()


def _cerrar_o_cobrar(lead, autor=""):
    """Ganado si el saldo está en cero; si no, «Cobrar saldo».

    Ganado = Entregado + saldo 0 (la regla del embudo). Con saldo, el lead
    se queda en Entregado con su etiqueta y sale en la vista Cobrar de
    Linear: nadie da por cerrado lo que todavía no se cobró.
    """
    fichas, error = saldos(refrescar=True)
    ficha = fichas.get(_pp_de(lead))
    if ficha is None:
        # Sin saldo conocido NO se cierra: cerrar un lead que quizá debe
        # plata es el error caro. Se queda en Entregado.
        return (f"El saldo no se pudo leer ({error})." if error
                else "Sin cotización en Odoo: queda en Entregado.")
    if ficha["saldo"] > 0.005:
        try:
            linear_leads.poner_pago(lead["id"], "Cobrar saldo")
        except linear_leads.ErrorLeads:
            pass
        return (f"Queda {plata(ficha['saldo'])} por cobrar: se marcó "
                f"«Cobrar saldo» y sale en la vista Cobrar.")
    try:
        linear_leads.poner_pago(lead["id"], "Pagado 100%")
    except linear_leads.ErrorLeads:
        pass
    if linear_leads.mover_estado(lead["id"], "GANADO"):
        return "Sin saldo: pasó a Ganado."
    return "Sin saldo pendiente."


def cerrar_los_que_ya_pagaron():
    """Los Entregado que ya no deben nada pasan a Ganado.

    El pago que salda una entrega entra en Odoo, y el addon solo empuja
    hacia «Por agendar» — que a un Entregado no lo mueve, porque la
    escalera no degrada. Alguien tiene que cerrar el círculo, y es esto:
    una pasada barata que corre en fondo al abrir el calendario.

    Devuelve los nombres de los leads que cerró.
    """
    if not linear_leads.escritura_activa() and linear_leads.configurado():
        return []
    entregados = linear_leads.en_estado("ENTREGADO")
    if not entregados:
        return []
    fichas, error = saldos()
    if error:
        return []  # sin saldo confiable no se cierra nada
    cerrados = []
    for lead in entregados:
        ficha = fichas.get(_pp_de(lead))
        if ficha is None or ficha["saldo"] > 0.005:
            continue
        try:
            linear_leads.poner_pago(lead["id"], "Pagado 100%")
            if linear_leads.mover_estado(lead["id"], "GANADO"):
                cerrados.append(lead["nombre"])
        except linear_leads.ErrorLeads:
            continue
    if cerrados:
        linear_leads.refrescar()
    return cerrados


def cerrar_en_fondo():
    """La pasada de arriba, sin que la pantalla la espere."""
    calendario._en_fondo("agenda-cerrar", cerrar_los_que_ya_pagaron)


# ---------------------------------------------------------------------------
# Modo muestra: la plata de los leads de ejemplo de linear_leads
# ---------------------------------------------------------------------------

_SALDOS_MUESTRA = {
    "PP-70211": {"orden_id": 79, "orden": "S00079", "total": 1525.0,
                 "pagado": 762.5, "saldo": 762.5, "etapa": "abono",
                 "agendada": ""},
    "PP-70208": {"orden_id": 80, "orden": "S00080", "total": 340.0,
                 "pagado": 340.0, "saldo": 0.0, "etapa": "pagado",
                 "agendada": ""},
    "PP-70207": {"orden_id": 81, "orden": "S00081", "total": 2400.0,
                 "pagado": 1200.0, "saldo": 1200.0, "etapa": "abono",
                 "agendada": ""},
    # Hotel Bristol (LEAD-88) entregó con saldo: es el que enseña la
    # etiqueta "Cobrar saldo" y el que NO puede pasar a Ganado.
    "PP-70205": {"orden_id": 82, "orden": "S00082", "total": 600.0,
                 "pagado": 300.0, "saldo": 300.0, "etapa": "abono",
                 "agendada": ""},
    "PP-70195": {"orden_id": 83, "orden": "S00083", "total": 180.0,
                 "pagado": 180.0, "saldo": 0.0, "etapa": "pagado",
                 "agendada": ""},
}
