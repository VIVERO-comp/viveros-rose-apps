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
- **Solo tres tipos entregan**: Entrega, Instalación y Mantenimiento. Una
  Visita (ir a ver el sitio) y una Recogida (retirar las plantas de
  alquiler después del evento) son solo actividades y no tocan el estado.
- **Reprogramar mueve la fecha sin tocar el estado.**
"""

import hmac
import os
import re
import time
import unicodedata

from . import calculos, calendario, linear_leads, mantenimiento, ventas

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
# Entregado. Regla del dueño (24/09/2026): solo entregan las tres que
# dejan el trabajo hecho —Entrega, Instalación y Mantenimiento—. Las otras
# dos NO tocan el estado:
#
#   Visita   se va a ver el sitio; el trabajo no se hizo todavía.
#   Recogida es el retiro de las plantas de alquiler DESPUÉS del evento,
#            cuando el lead ya se entregó.
TIPOS = [
    {"clave": "entrega", "entrega": True},
    {"clave": "instalacion", "entrega": True},
    {"clave": "mantenimiento", "entrega": True},
    {"clave": "visita", "entrega": False},
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
    """$1,525.00 — EL formato de dinero de la app (`calculos.dinero`).

    Hasta el 7/10/2026 esta función ponía un ESPACIO donde va la coma, y
    era la tercera cara del mismo dinero: el tablero decía «$1,150.00», la
    ficha «$1522.50» y la agenda «$1 525.00». Un monto no puede tener tres
    caras en la misma app. El `or 0` se queda: acá un saldo ausente ya
    viene filtrado antes y sí significa cero.
    """
    return calculos.dinero(float(monto or 0))


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


def clientes_para_sugerir(actividades):
    """Los nombres para el `<datalist>` del campo Cliente de «Actividad
    nueva» (29/09/2026): los clientes de las actividades que la pantalla
    ya trae, deduplicados sin distinguir mayúsculas y en orden
    alfabético.

    Los leads del embudo NO van aquí a propósito: los trae el buscador
    único de al lado (`buscar_personas`), que además AMARRA la actividad.
    Esto cubre la CUARTA fuente, la que el buscador no mira: el cliente
    que solo existe como texto en una actividad vieja del calendario — no
    es partner de Odoo, no es cliente de Vender y no es lead de nadie.

    La lista viaja RENDERIZADA: cero consultas al vuelo mientras se
    escribe, y el campo sigue siendo texto libre (un cliente nuevo se
    escribe igual).
    """
    nombres = {}
    for actividad in actividades or []:
        nombre = (actividad.get("cliente") or "").strip()
        if nombre:
            nombres.setdefault(nombre.lower(), nombre)
    return sorted(nombres.values(), key=str.lower)


def leads_para_buscar():
    """([{ref, nombre, celular, estado, estado_nombre, resp}], aviso).

    Los leads VIVOS del embudo, que son los que pueden recibir trabajo
    nuevo: un cerrado queda fuera. **El aviso es la mitad importante**: si
    Linear no contesta, quien busque tiene que leer que falta una fuente
    entera, porque una lista vacía se lee como «esa persona no existe».
    """
    try:
        leads = linear_leads.listar()
    except linear_leads.ErrorLeads as fallo:
        return [], f"{AVISO_LEADS_HUECO} ({fallo})"
    return [{"ref": l["ref"], "nombre": l["nombre"],
             "celular": l.get("celular") or "",
             "estado": l.get("estado") or "",
             "estado_nombre": l.get("estado_nombre") or "",
             "resp": l.get("resp") or ""}
            for l in leads if not l.get("cerrado")], ""


# `leads_para_conectar()` se fue con el selector que la pedía (BLOQUE 59):
# su única llamada era el `<select name="lead">` que el buscador único
# reemplazó, y la lista de leads vivos la sirve ahora `leads_para_buscar()`
# —la misma, pero con el aviso de cuando Linear no contesta—.


# ---------------------------------------------------------------------------
# EL BUSCADOR ÚNICO de «Actividad nueva» (BLOQUE 59, item 4 de Jay)
#
# Antes el formulario traía DOS campos para la misma pregunta: un selector
# con los leads vivos y, al lado, un «Cliente» de texto libre. Quien
# agendaba tenía que saber de antemano si la persona era un lead del CRM o
# un cliente que solo existe en Odoo, y si se equivocaba de campo la
# actividad nacía suelta sin que nada avisara.
#
# Ahora hay UNA pregunta —«quién»— y una sola caja donde se escribe un
# nombre o un teléfono. Las dos fuentes se buscan juntas:
#
#   los leads VIVOS del embudo      linear_leads.listar()  (vía leads_para_buscar)
#   la gente de Odoo + Vender + CRM contactos.buscar()     (la unión de A7)
#
# Y se devuelve UNA fila por persona: si esa persona tiene un lead vivo, la
# fila lo trae, y elegirla amarra la actividad igual que antes. Si no, la
# fila es un cliente suelto y la actividad nace sin lead, como siempre.
#
# Lo que el buscador NO hace, a propósito: inventar. Si Linear o Odoo no se
# pueden leer, la fuente ausente se DICE en los avisos (`contactos.buscar`
# y `leads_para_buscar` los traen) — nunca una lista vacía con cara de «no
# hay nadie».
# ---------------------------------------------------------------------------

AVISO_LEADS_HUECO = ("Linear no contestó: los leads del embudo no entran en "
                     "esta búsqueda")

# Cuántas filas se pintan. Más que esto no se lee de un vistazo, y el
# resto se dice con un número en vez de esconderse.
TOPE_BUSCADOR = 12

# Sin nada escrito salen los leads vivos (lo que enseñaba el selector de
# antes): cero costo, porque `linear_leads.listar()` sirve lo guardado.
# Los contactos entran al escribir, y eso también es una decisión de
# COSTO: `contactos.buscar()` le pregunta a Odoo por todos los partner y
# sus órdenes, y abrir el formulario no tiene por qué pagar eso.
PISTA_SIN_BUSCAR = ("Escribí un nombre o un teléfono para buscar también "
                    "entre los clientes de Odoo y de Vender.")

VACIO_BUSCADOR = ("Nadie con ese nombre ni con ese teléfono. Escribí el "
                  "nombre en «Cliente» y la actividad nace suelta.")


def _fila_persona(nombre, telefono, lead, detalle, fuente, clave):
    """UNA fila del buscador, que es UNA persona.

    `lead` es el LEAD-NN vivo que le cuelga, o "". Es el campo que decide
    todo lo de después: con lead la actividad nace amarrada (y con un tipo
    agendable el embudo se mueve); sin lead, suelta.
    """
    return {"clave": clave, "nombre": nombre, "telefono": telefono,
            "lead": lead, "detalle": detalle, "fuente": fuente,
            "es_lead": bool(lead)}


def buscar_personas(q="", tope=TOPE_BUSCADOR):
    """TODO lo que el buscador único pinta, decidido acá (regla 10).

    Devuelve `{"q", "filas", "cuenta", "mas", "avisos", "pista", "vacio",
    "con_contactos"}`. Las filas vienen con `lead` y `nombre` ya
    resueltos: la plantilla solo arma el enlace con esos dos valores, y el
    POST de siempre (`/calendario/actividad`) los recibe en los campos
    `lead` y `cliente` que ya existían — por eso el guardado no se toca.
    """
    from . import contactos  # diferido: contactos importa este módulo

    q = (q or "").strip()
    vivos, aviso_leads = leads_para_buscar()
    por_ref = {l["ref"]: l for l in vivos}
    # El teléfono normalizado de cada lead vivo, para reconocerlo dentro de
    # una fila de contactos que vino por el lado de Odoo.
    ref_por_tel = {}
    for lead in vivos:
        tel = contactos.normalizar_telefono(lead["celular"])
        if tel:
            ref_por_tel.setdefault(tel, lead["ref"])

    avisos = [aviso_leads] if aviso_leads else []
    filas = []
    usados = set()
    # Los que `contactos.buscar` encontró y NO devolvió por su propio tope.
    # Se cuentan aparte porque si no, el «hay N más» de abajo mentiría por
    # lo bajo: diría solo los que este módulo recortó.
    sobrantes = 0

    if q:
        # Con algo escrito: la unión entera (Odoo + Vender + CRM). Se pide
        # con el tope ya puesto para no traer cientos de filas que nadie va
        # a pintar.
        hallado = contactos.buscar(q, tope=tope)
        # SOLO el hueco de Odoo. El de Linear que trae `contactos` es el de
        # SU tercera fuente, y acá los leads vienen de `linear_leads`
        # directo (ver el bucle de abajo): repetir ese aviso diría «faltan
        # los leads» mientras los leads están a la vista.
        if hallado["aviso_odoo"]:
            avisos.append(hallado["aviso_odoo"])
        sobrantes = max(0, hallado["cuenta"] - len(hallado["filas"]))
        for fila in hallado["filas"]:
            ref = next((r for r in fila["leads"] if r in por_ref), "")
            if not ref:
                ref = ref_por_tel.get(
                    fila["tel_norm"]
                    or contactos.normalizar_telefono(fila["telefono"]), "")
            if ref:
                usados.add(ref)
            filas.append(_fila_persona(
                nombre=fila["nombre"],
                telefono=fila["telefono"],
                lead=ref,
                detalle=(_detalle_lead(por_ref[ref]) if ref else ""),
                fuente=fila["fuente_texto"],
                clave=fila["id"]))

    # Los leads vivos que ninguna fila de contactos trajo. Con `q` esto
    # tapa el hueco de verdad: si Linear no se puede leer DESDE contactos
    # (su tercera fuente exige una key de verdad, ver
    # `contactos.linear_conectado`) o si Odoo está caído, el lead seguiría
    # estando y hay que poder elegirlo. Sin `q` son TODOS: lo que enseñaba
    # el selector de antes, igual pero buscable.
    for lead in vivos:
        if lead["ref"] in usados:
            continue
        if q and not contactos.casa_busqueda(
                {"nombre": lead["nombre"], "telefono": lead["celular"],
                 "tel_norm": contactos.normalizar_telefono(lead["celular"])},
                q):
            continue
        filas.append(_fila_persona(
            nombre=lead["nombre"], telefono=lead["celular"],
            lead=lead["ref"], detalle=_detalle_lead(lead), fuente="CRM",
            clave=lead["ref"]))

    # Los leads primero —elegir uno es lo que amarra el trabajo— y cada
    # grupo por nombre: un orden que no depende de qué fuente contestó
    # primero.
    filas.sort(key=lambda f: (0 if f["es_lead"] else 1,
                              (f["nombre"] or "").lower()))
    cuenta = len(filas) + sobrantes
    mas = sobrantes
    if tope and len(filas) > tope:
        mas += len(filas) - tope
        filas = filas[:tope]

    return {"q": q, "filas": filas, "cuenta": cuenta, "mas": mas,
            "avisos": avisos, "con_contactos": bool(q),
            "pista": "" if q else PISTA_SIN_BUSCAR,
            "vacio": VACIO_BUSCADOR}


def _detalle_lead(lead):
    """«Por agendar · Mary» — el estado del embudo y quién lo atiende."""
    partes = [lead.get("estado_nombre") or lead.get("estado") or ""]
    if lead.get("resp"):
        partes.append(lead["resp"])
    return " · ".join(p for p in partes if p)


def elegido_en_el_buscador(ref_lead="", cliente="", tipo=""):
    """La persona YA elegida, para la pastilla de arriba del buscador.

    Se arma con los MISMOS dos campos que el formulario le manda al POST
    (`lead` y `cliente`), leídos de vuelta: lo que la pantalla dice que
    está elegido es exactamente lo que se va a guardar, sin un tercer
    lugar donde la verdad pueda diferir.

    El `tipo` entra solo para decir la consecuencia con la verdad: los
    cinco tipos agendables mueven el lead a «Agendado», y cualquier otro
    (un Alquiler, una reunión) deja la actividad amarrada sin tocar el
    estado. Es la regla que ya vive en el POST, dicha antes de guardar.

    None cuando no hay nadie elegido todavía.
    """
    ref_lead = (ref_lead or "").strip()
    cliente = (cliente or "").strip()
    if not ref_lead and not cliente:
        return None
    if not ref_lead:
        return {"lead": "", "nombre": cliente, "detalle": "", "existe": True,
                "aviso": "Cliente suelto: la actividad no se amarra a "
                         "ningún lead."}
    try:
        lead = linear_leads.uno(ref_lead)
    except linear_leads.ErrorLeads:
        lead = None
    if lead is None:
        # Un ref que ya no está en el tablero (o Linear caído): el POST lo
        # va a rechazar con este mismo criterio, así que se dice ANTES en
        # vez de dejar que el guardado sea la primera noticia.
        return {"lead": ref_lead, "nombre": cliente or ref_lead,
                "detalle": "", "existe": False,
                "aviso": linear_leads.mensaje_lead_ausente(ref_lead)}
    return {"lead": lead["ref"], "nombre": cliente or lead["nombre"],
            "detalle": _detalle_lead(lead), "existe": True,
            "aviso": ("La actividad nace amarrada y el lead pasa a "
                      "«Agendado»." if tipo in POR_CLAVE else
                      "La actividad nace amarrada a este lead, sin mover "
                      "su estado.")}


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
        raise calendario.ErrorCalendario("Falta la fecha.", campo="fecha")
    lead = linear_leads.uno(ref_lead)
    if lead is None:
        # Con el ref adentro, y distinguiendo el ref vacío (29/09/2026):
        # mismo criterio que Control, ver `mensaje_lead_ausente`.
        raise calendario.ErrorCalendario(
            linear_leads.mensaje_lead_ausente(ref_lead))

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
    # Si se agendó para hoy (o una fecha ya pasada), «Entrega pendiente» se
    # pone de una: no espera la pasada de mañana.
    _revisar_entregas_pendientes_sin_reventar()

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
    # Reprogramar puede sacar a un lead de "hoy" (se movió al futuro) o
    # meterlo (se movió a hoy o para atrás): «Entrega pendiente» se
    # recalcula igual que al agendar.
    _revisar_entregas_pendientes_sin_reventar()
    return f"Movida al {calendario.dmy(fecha)}. El lead se queda donde está."


# ---------------------------------------------------------------------------
# «Hecha» → Entregado, y Ganado si el saldo quedó en cero
# ---------------------------------------------------------------------------

def al_marcar_hecha(actividad, autor=""):
    """Lo que el embudo hace cuando una actividad se marca Hecha.

    Devuelve el texto que la pantalla le suma al aviso, o "" si esta
    actividad no mueve nada (una Recogida, o una actividad sin lead).

    Una cita RECURRENTE de mantenimiento (28/09/2026, ver `mantenimiento.
    py`) es la excepción: entrega en el camino normal por tipo, pero esta
    en concreto no toca el estado — sigue en Ganado. Se distingue por un
    campo explícito (`mantenimiento.es_recurrente`), nunca por el título,
    así que un Mantenimiento agendado a mano sigue entregando como
    siempre.
    """
    ref = (actividad or {}).get("lead") or ""
    if not ref or not cierra_la_entrega(actividad.get("tipo")):
        return ""
    if mantenimiento.es_recurrente(actividad):
        return mantenimiento.al_marcar_hecha(actividad, autor=autor)
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
    # El lead ya salió de Agendado: `marcar_entregas_pendientes()` no vuelve
    # a mirarlo (solo recorre los que SIGUEN en Agendado), así que la señal
    # se quita aquí mismo, directo, en vez de esperar una pasada que ya no
    # lo va a encontrar.
    try:
        linear_leads.poner_etiqueta_suelta(lead["id"], ENTREGA_PENDIENTE, False)
    except linear_leads.ErrorLeads:
        pass
    aviso = f"{lead['nombre']} pasó a Entregado." if movido else ""
    aviso += " " + _cerrar_o_cobrar(lead, autor)
    linear_leads.refrescar()
    return aviso.strip()


def _mover_a_ganado(lead, autor=""):
    """El ÚNICO lugar por el que un lead pasa a Ganado — lo llaman
    `_cerrar_o_cobrar` (saldo 0 al marcar Hecha) y `cerrar_los_que_ya_
    pagaron` (el pago que salda entra después). Además de mover el
    estado, dispara el mantenimiento mensual si corresponde (28/09/2026):
    un solo enganche, no dos, para que un camino nuevo hacia Ganado no
    se olvide de repetirlo.
    """
    movido = linear_leads.mover_estado(lead["id"], "GANADO")
    if movido:
        mantenimiento.al_ganar(lead, autor=autor)
    return movido


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
    if _mover_a_ganado(lead, autor):
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
            if _mover_a_ganado(lead):
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
# «Entrega pendiente»: la señal automática (pedido de Abraham, 28/09/2026)
#
# Regla literal del dueño: «entrega pendiente se activa cuando llega el día
# agendado hasta que ponga entregado». La etiqueta suelta YA existe en
# Linear (equipo LEAD) y en WhatsApp -el sincronizador la baja sola, ver
# SENALES_QUE_BAJAN en waha/sincronizador.py-; este módulo NUNCA la crea
# (regla 3): solo la busca, y si no existe todavía se queda sin ella.
#
# PONERLA: un lead en Agendado con una actividad tocando ya (hoy o
# atrasada) y sin marcar Hecha. QUITARLA: la actividad se marca Hecha (el
# lead deja Agendado) o su fecha se movió al futuro. No es botón de
# Control -no vive en LABELS_SENAL/senales_disponibles()- y no manda nada
# al cliente: es puro espejo del calendario hacia Linear/WhatsApp.
# ---------------------------------------------------------------------------

ENTREGA_PENDIENTE = "Entrega pendiente"


def marcar_entregas_pendientes():
    """Recorre TODOS los Agendado y deja «Entrega pendiente» puesta en los
    que tienen una actividad vencida (hoy o atrasada, sin marcar Hecha) y
    quitada en los que no. Idempotente: solo escribe en los leads donde lo
    puesto hoy no coincide con lo que debería quedar.

    La corre (a) esta misma función al final de `agendar()`, `reprogramar()`
    y `al_marcar_hecha()` -así "agendar para hoy" la pone de una, sin
    esperar la pasada de mañana- y (b) el endpoint `/entregas-pendientes/
    revisar` que dispara el cron de las 7 a.m. de Panamá, para los leads
    que nadie tocó hoy (una actividad que amaneció vencida sola).

    Mismo candado que `cerrar_los_que_ya_pagaron()`: con Linear configurado
    pero SIN escritura activa (la instancia de solo lectura), no toca nada
    -ni siquiera lee-, para no gastar una consulta que no va a usar. Sin
    ningún token (modo muestra) sí corre, contra el tablero de ejemplo:
    es como se prueba esto sin un Linear real.
    """
    if linear_leads.configurado() and not linear_leads.escritura_activa():
        return {"puestas": [], "quitadas": [], "errores": []}

    hoy = calendario.hoy().isoformat()
    actividades = calendario.listar(hoy, hoy)  # trae hoy + las atrasadas
    # Solo las que "entregan" (Entrega, Instalación, Mantenimiento): son las
    # únicas que ponen a un lead en Agendado y las únicas que, al marcarse
    # Hecha, lo sacan de ahí. Una Visita o una Recogida atrasada del mismo
    # lead no cuentan -no son la entrega-.
    con_lead_vencido = {
        a["lead"] for a in actividades
        if a.get("lead") and cierra_la_entrega(a.get("tipo"))
        and a["estado"] not in ("hecha", "cancel")}

    puestas, quitadas, errores = [], [], []
    for lead in linear_leads.en_estado("AGENDADO"):
        toca = lead["ref"] in con_lead_vencido
        tiene = ENTREGA_PENDIENTE in lead.get("etiquetas", [])
        if toca == tiene:
            continue
        try:
            linear_leads.poner_etiqueta_suelta(
                lead["id"], ENTREGA_PENDIENTE, toca)
        except linear_leads.ErrorLeads as fallo:
            errores.append((lead["ref"], str(fallo)))
            continue
        (puestas if toca else quitadas).append(lead["ref"])
    if puestas or quitadas:
        linear_leads.refrescar()
    return {"puestas": puestas, "quitadas": quitadas, "errores": errores}


def _revisar_entregas_pendientes_sin_reventar():
    """La misma pasada, pero que un fallo de red no tumbe quien la llama
    (agendar, reprogramar, marcar Hecha ya quedaron bien; esto es un extra).
    """
    try:
        marcar_entregas_pendientes()
    except Exception:
        pass


def _secreto_entregas_pendientes():
    return (os.environ.get("ENTREGAS_PENDIENTES_SECRETO") or "").strip()


def entregas_pendientes_armado():
    """Sin `ENTREGAS_PENDIENTES_SECRETO` el endpoint de la pasada diaria no
    corre — mismo trato que `resumen.armado()` con `RESUMEN_SECRETO`."""
    return bool(_secreto_entregas_pendientes())


def entregas_pendientes_credencial_valida(cabecera):
    """Compara el secreto sin filtrar por el tiempo que tarda (mismo patrón
    que `resumen.credencial_valida()`)."""
    esperado = _secreto_entregas_pendientes()
    if not esperado:
        return False
    dado = (cabecera or "").removeprefix("Bearer ").strip()
    return hmac.compare_digest(dado, esperado)


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
