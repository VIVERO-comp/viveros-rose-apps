"""Reconciliación M1 (proyecto Orquesta): cada venta de Odoo contra TODAS
las fuentes, clasificada en una sola clase A–H.

Qué es: un informe de solo lectura que pone en una fila cada `sale.order`
de Odoo y le pregunta a cada fuente qué dice de ella — Odoo (la orden, sus
facturas, sus salidas de inventario, la etapa manual del Flujo del CRM),
el SQLite local de Control (`ventas_locales`, `cotizaciones_servicio`),
Linear (el estado del lead del embudo y su etiqueta de pago, SOLO lectura)
y un CSV opcional de pagos que el dueño informa a mano. Con todo eso, cada
venta cae en UNA clase:

    A Cotizada · B Vencida · C Debe · D Pagada · E Pagada y entregada ·
    F Pago fuera de Odoo · G Entrega sin confirmar · H Revisar

Reglas que este módulo NO negocia:

- **JAMÁS escribe en Odoo.** La única puerta es `ventas._ejecutar` (la
  misma de toda la app) pasando por `_leer()`, que solo deja pasar
  `search_read`, `read`, `search_count` y `fields_get`. Tampoco escribe
  en Linear ni en Twenty: de Linear solo se lee la lista de leads.
- **El estado de dinero SIEMPRE sale de Odoo.** `pagado` es la suma de los
  pagos reales sobre las facturas de la orden (`amount_total −
  amount_residual` de cada factura asentada), nunca `amount_to_invoice`
  (la política de facturación es mixta: 100 productos facturan por
  entregado, así que ese campo no dice cuánto se debe). Una fila del CSV
  externo NUNCA se convierte en pago ni en «pagado» en ninguna parte:
  solo aparece como texto entre las fuentes y como señal para la clase F.
- **Nada se inventa** (la regla de `resumen.py`): si una fuente no
  contesta o no está configurada, su bloque queda en blanco y el informe
  LO DICE en `huecos` — nunca un cero que parezca una noticia.
- **Perdido es de Linear, no de aquí**: el barrido de 14 días desde el
  último WhatsApp es OTRA regla que vive en el frontend. M1 solo LEE el
  estado del lead para mostrarlo; «Vencida» (clase B) es un atributo de la
  COTIZACIÓN (su `validity_date`, o `date_order` + 14 días si no tiene),
  y no se reactiva sola aunque el lead vuelva a Hablando.
- Las etapas del Flujo de Odoo se resuelven por su xml_id vía
  `ir.model.data` (`vivero_rose_pedidos.etapa_flujo_*`), NUNCA con ids a
  mano: hoy son 6/7/8/9 pero eso no se fija en código.
- Las facturas del diario «Ventas Super Extra» quedan FUERA del dinero de
  todas las clases: se cuentan aparte (n, total) y el informe las lista
  como «fuera de alcance». El diario se resuelve por NOMBRE (como las
  categorías: los ids cambian entre bases).
- La fuente «Entrega» se reporta SIEMPRE en dos renglones separados: lo
  que dice Odoo (la salida) y lo que dice el Calendario/Linear (el estado
  del lead). No se mezclan, porque discrepan justo cuando importa (G).
- Twenty no se consulta: lo que M1 necesita del embudo (estado del lead,
  etiqueta de pago) vive en Linear, que ya es el único tablero. Si Linear
  no está configurado —en pruebas los tokens arrancan con `CLAVE` y eso
  cuenta como ausencia— la fuente entera reporta «sin datos».
- La sección «Creado hoy fuera de Orquesta» (`creado_hoy`) lista todo
  pedido, factura de cliente y pago nacido HOY en hora de Panamá, con
  quién lo creó, y marca SOSPECHAS de duplicado (mismo cliente, monto
  parecido). Sospecha = texto que se muestra; jamás una conclusión ni
  una acción.

La pantalla consume `informe_datos()` (el contrato exacto está en su
docstring). La línea de comandos es
`python -m app.reconciliacion informe [--salida DIR]`.
"""

import csv
import os
import re
import sqlite3
from datetime import date, datetime, timedelta, timezone

from . import calculos, linear_leads, ventas
from .datos import ZONA_PANAMA, _db, _ruta_db

# ---------------------------------------------------------------------------
# Vocabulario
# ---------------------------------------------------------------------------

# Las 8 clases, en su orden natural de embudo. El nombre es el que ve el
# dueño; la clave es la letra con la que viaja por el contrato.
CLASES = {
    "A": "Cotizada",
    "B": "Vencida",
    "C": "Debe",
    "D": "Pagada",
    "E": "Pagada y entregada",
    "F": "Pago fuera de Odoo",
    "G": "Entrega sin confirmar",
    "H": "Revisar",
}

# El desempate cuando una venta cae en varias: gana la más grave. H y F
# son anomalías de dinero (mandan sobre todo), G es una anomalía de
# entrega, y el resto es el orden normal del embudo al revés.
PRIORIDAD = ("H", "F", "G", "C", "D", "E", "B", "A")

# Vencimiento de una cotización SIN `validity_date` (las del carrito de
# Vender no la tienen): la fecha de la cotización + 14 días. Es un
# atributo de la cotización — no confundir con el barrido de Perdido del
# frontend, que cuenta 14 días desde el último WhatsApp y es de Linear.
DIAS_VENCIMIENTO = 14

# Las canceladas de los últimos N días entran al informe solo como
# información (clase "CANCELADA", fuera de los contadores A–H).
DIAS_CANCELADAS = 30

# El diario de VENTA que queda fuera de alcance (29 facturas impagas de la
# época de Super Extra, $3,109.85 al 1/10/2026): su dinero no entra a
# ninguna clase. Por NOMBRE, nunca por id.
DIARIO_FUERA_DE_ALCANCE = "Ventas Super Extra"

# Las etapas del Flujo del CRM de Odoo que puso el addon, por su xml_id.
MODULO_ADDON = "vivero_rose_pedidos"
ETAPAS_FLUJO = ("cotizado", "facturado", "abono", "pagado")

# La tanda de ventas VIEJAS que la sesión de «ventas externas» registró en
# el Odoo de producción el 1/10/2026: nueve órdenes (S00109–S00117) más la
# factura y el pago de S00078 (Emiraf). Sus facturas y pagos llevan la
# fecha fiscal REAL (25/08–28/09); solo el `date_order` de las nueve quedó
# con la fecha de ese día. Declaradas históricas por Korto tras
# verificarlo en el Odoo real. LISTA CERRADA A PROPÓSITO, nada de
# heurística por fecha ni por creador: S00107 (Sofia) y S00090 (Ilayda)
# son ventas reales de ese mismo día y NO entran. Si otro día aparece
# otra tanda, la declara Korto y se suma aquí a mano.
ORDENES_HISTORICAS = {"S00078", "S00109", "S00110", "S00111", "S00112",
                      "S00113", "S00114", "S00115", "S00116", "S00117"}

# Los textos de la marca histórica (el contrato y las pruebas los citan).
# La clase NO cambia nunca por ser histórica: el dinero está bien; solo
# el motivo deja de sonar a pendiente de hoy.
SUFIJO_HISTORICA = "Histórica registrada el 1/10"
TEXTO_D_HISTORICA = ("Pagada completa (venta vieja registrada el 1/10); "
                     "la entrega se regulariza en M2")

# Las DOS etiquetas automáticas de Linear que solo nacen de un pago real
# en Odoo (las escribe el addon al entrar la plata). Si el lead las lleva
# y Odoo dice $0, eso es una anomalía (clase H): la etiqueta no pudo nacer
# sola. «Cobrar saldo» —la tercera del grupo— no entra en esta regla.
# Distinto de la etapa MANUAL del Flujo (Abono/Pagado), que la mueve
# Abraham a mano y con $0 en Odoo significa clase F, no H.
ETIQUETAS_PAGO_AUTOMATICAS = tuple(
    e for e in linear_leads.LABELS_PAGO if e != "Cobrar saldo")

# Estados de ventas_locales (SQLite) que dicen «esto se cobró o se vendió»
# sin que Odoo tenga el pago: señal para la clase F.
ESTADOS_LOCALES_PAGADA = ("pagado", "vendida")

# Textos fijos del contrato (la pantalla y las pruebas los citan).
TEXTO_F = "Pago informado, no registrado en Odoo · verificar"
TEXTO_H_ETIQUETA = ("Revisar: Linear marca pago y Odoo no lo tiene "
                    "(¿pago anulado?)")
TEXTO_PAGO_EXTERNO = ("Pago externo informado — pendiente de "
                      "regularización en Odoo")
TEXTO_SIN_VALIDEZ = "sin fecha de validez; se usó fecha de la cotización"
TEXTO_VOLVIO = "el cliente volvió a escribir"
TEXTO_LINEAR_SIN_CERRAR = "Linear sin cerrar"

# Tolerancia de centavos al comparar plata (redondeos de Odoo).
_CENTAVO = 0.009

# Los campos que se piden de cada orden / factura / salida. `tag_ids`
# viaja para que la pantalla pueda distinguir la etiqueta LOCAL si lo
# necesita; aquí no decide nada.
CAMPOS_VENTA = [
    "name", "state", "date_order", "validity_date", "partner_id",
    "amount_total", "invoice_ids", "picking_ids", "opportunity_id",
    "client_order_ref", "tag_ids",
]
CAMPOS_FACTURA = ["move_type", "state", "amount_total", "amount_residual",
                  "payment_state", "invoice_date", "journal_id"]
CAMPOS_SALIDA = ["state", "date_done", "scheduled_date"]

# Referencias internas de la app, que no son ventas de un cliente real: la
# orden fija de la vista previa de Vender (una por empleada, apuntando al
# partner comodín) y la propuesta de muestra del PDF. Mismo criterio que
# `cot_lead._es_ref_interna`: quedan FUERA del informe y de «Creado hoy»
# — una orden de utilería contada como «Cotización vigente» (o como
# sospecha de duplicado) sería ruido, no reconciliación. Se descartan por
# `client_order_ref`, que aquí es de solo lectura.
_REFS_INTERNAS_PREFIJO = ("VISTA PREVIA",)
_REFS_INTERNAS_EXACTAS = {"MUESTRA-PDF"}


def _es_ref_interna(ref):
    ref = (ref or "").strip().upper()
    return ref in _REFS_INTERNAS_EXACTAS or any(
        ref.startswith(p) for p in _REFS_INTERNAS_PREFIJO)


# ---------------------------------------------------------------------------
# La puerta a Odoo: solo lectura, verificada en cada llamada
# ---------------------------------------------------------------------------

# Los únicos métodos que este módulo tiene permitido pedirle a Odoo. La
# lista es cerrada a propósito: una prueba revisa que el código fuente no
# nombre ningún método de escritura, y esta verificación en vivo es el
# segundo candado por si alguien suma una llamada nueva sin pasar por
# `_leer`.
METODOS_LECTURA = ("search_read", "read", "search_count", "fields_get")


def _leer(modelo, metodo, args, kw=None):
    """La ÚNICA forma en que este módulo habla con Odoo. Reusa la conexión
    de `ventas._ejecutar` (no se abre otra puerta) y rechaza cualquier
    método que no sea de lectura ANTES de que viaje."""
    if metodo not in METODOS_LECTURA:
        raise RuntimeError(
            f"reconciliacion.py es de SOLO lectura: {modelo}.{metodo} "
            "no está en la lista de métodos permitidos.")
    return ventas._ejecutar(modelo, metodo, args, kw)


def _error(fallo):
    return ventas._mensaje_de_error(fallo)


# ---------------------------------------------------------------------------
# Utilidades de fecha y teléfono
# ---------------------------------------------------------------------------

def _hoy():
    return datetime.now(ZONA_PANAMA).date()


def _fecha_panama(texto):
    """Un datetime de Odoo (UTC sin zona, "AAAA-MM-DD HH:MM:SS") como
    fecha de Panamá. None si no se puede leer."""
    try:
        cruda = datetime.strptime(str(texto)[:19], "%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        try:
            cruda = datetime.strptime(str(texto)[:10], "%Y-%m-%d")
        except (ValueError, TypeError):
            return None
    return cruda.replace(tzinfo=timezone.utc).astimezone(ZONA_PANAMA).date()


def _ultimos8(texto):
    """Los últimos 8 dígitos de un teléfono — la forma local de Panamá,
    el mismo criterio con el que `cot_lead.py` casa órdenes con leads."""
    digitos = re.sub(r"\D", "", str(texto or ""))
    return digitos[-8:] if len(digitos) >= 8 else digitos


# ---------------------------------------------------------------------------
# Las fuentes que no son Odoo (cada una reemplazable en las pruebas)
# ---------------------------------------------------------------------------

def _linear_con_datos():
    """¿Hay un Linear real que leer? Un token que arranca con `CLAVE` es
    el marcador neutralizado del entorno de pruebas y cuenta como
    ausencia — sin esto, `linear_leads.listar()` sin credenciales devuelve
    la MUESTRA, que jamás puede entrar a una reconciliación."""
    clave = (os.environ.get("LINEAR_API_KEY") or "").strip()
    return bool(clave) and not clave.upper().startswith("CLAVE")


def _leads_de_linear():
    """(leads, hueco): la lista real de leads del embudo, o (None, texto)
    si Linear no está configurado o no contestó. Nunca la muestra."""
    if not _linear_con_datos():
        return None, ("Linear: sin datos (esta instancia no tiene "
                      "credenciales reales).")
    try:
        return linear_leads.listar(refrescar=True), None
    except Exception as fallo:
        return None, f"Linear no contestó: {str(fallo)[:160]}"


def _ventas_locales():
    """({orden_id: estado}, hueco) de la tabla `ventas_locales` del SQLite
    de Control. El estado es el del pipeline local de Vender."""
    try:
        with _db() as con:
            filas = con.execute(
                "SELECT orden_id, estado FROM ventas_locales"
                " WHERE orden_id IS NOT NULL").fetchall()
    except sqlite3.OperationalError as fallo:
        return {}, f"El SQLite local (ventas_locales) no se pudo leer: {fallo}"
    return {int(f["orden_id"]): (f["estado"] or "") for f in filas}, None


def _cotizaciones_locales():
    """({orden_id: tipo}, hueco) de `cotizaciones_servicio` (SQLite):
    solo para citarla entre las fuentes, no decide ninguna clase."""
    try:
        with _db() as con:
            filas = con.execute(
                "SELECT orden_id, tipo FROM cotizaciones_servicio").fetchall()
    except sqlite3.OperationalError as fallo:
        return {}, ("El SQLite local (cotizaciones_servicio) no se pudo "
                    f"leer: {fallo}")
    return {int(f["orden_id"]): (f["tipo"] or "") for f in filas}, None


def _ruta_pagos_externos():
    """El CSV opcional de pagos informados por el dueño vive junto a la
    base viva (`datos/pagos_externos.csv` en producción: la carpeta de la
    base ES `datos/`). Se resuelve desde la ruta de la base y no con una
    ruta relativa al proceso, para que funcione igual dentro del
    contenedor y en la Mac."""
    return os.path.join(os.path.dirname(os.path.abspath(_ruta_db())),
                        "pagos_externos.csv")


def _pagos_externos():
    """(filas, hueco) del CSV externo. Que NO exista no es un hueco: el
    archivo es opcional. Que exista y no se pueda leer, sí. Cada fila es
    un dict con telefono, cliente, monto, metodo, fecha, entregado. Una
    fila de aquí JAMÁS se convierte en pago: es texto y señal de F."""
    ruta = _ruta_pagos_externos()
    if not os.path.exists(ruta):
        return [], None
    try:
        with open(ruta, newline="", encoding="utf-8") as archivo:
            filas = [dict(f) for f in csv.DictReader(archivo)]
    except Exception as fallo:
        return [], (f"El CSV de pagos externos ({ruta}) existe pero no se "
                    f"pudo leer: {str(fallo)[:160]}")
    return filas, None


# ---------------------------------------------------------------------------
# Lectura de Odoo (todo junto, en pocas consultas)
# ---------------------------------------------------------------------------

def _corte_canceladas():
    corte = (datetime.now(ZONA_PANAMA).astimezone(timezone.utc)
             - timedelta(days=DIAS_CANCELADAS))
    return corte.strftime("%Y-%m-%d %H:%M:%S")


def _etapas_del_flujo():
    """({stage_id: clave}, hueco): las etapas del Flujo resueltas por su
    xml_id vía ir.model.data. Si el addon no está en esa base (pruebas
    viejas), el Flujo entero queda como hueco y ninguna venta gana F por
    etapa — nunca se adivinan ids."""
    nombres = [f"etapa_flujo_{clave}" for clave in ETAPAS_FLUJO]
    try:
        filas = _leer("ir.model.data", "search_read",
                      [[["module", "=", MODULO_ADDON],
                        ["name", "in", nombres]]],
                      {"fields": ["name", "res_id"]})
    except Exception as fallo:
        return {}, ("Las etapas del Flujo de Odoo no se pudieron resolver "
                    f"por ir.model.data: {_error(fallo)}")
    etapas = {int(f["res_id"]): f["name"].removeprefix("etapa_flujo_")
              for f in filas}
    if not etapas:
        return {}, ("Las etapas del Flujo de Odoo no existen en esta base "
                    f"(no hay xml_ids {MODULO_ADDON}.etapa_flujo_*).")
    return etapas, None


def _fuera_de_alcance():
    """(dict, hueco): las facturas del diario «Ventas Super Extra»,
    contadas aparte. Su dinero no entra a ninguna clase."""
    vacio = {"n": 0, "total": 0.0, "detalle": [],
             "nombre": DIARIO_FUERA_DE_ALCANCE}
    try:
        diarios = _leer("account.journal", "search_read",
                        [[["name", "=", DIARIO_FUERA_DE_ALCANCE]]],
                        {"fields": ["id", "name"]})
        if not diarios:
            return vacio, None
        facturas = _leer("account.move", "search_read",
                         [[["journal_id", "=", diarios[0]["id"]],
                           ["move_type", "=", "out_invoice"],
                           ["state", "=", "posted"]]],
                         {"fields": ["name", "amount_total",
                                     "amount_residual", "payment_state"]})
    except Exception as fallo:
        return vacio, (f"El diario «{DIARIO_FUERA_DE_ALCANCE}» no se pudo "
                       f"leer: {_error(fallo)}")
    detalle = [
        (f"{f.get('name') or ''} · {calculos.dinero(f.get('amount_total') or 0)}"
         + (" · impaga" if f.get("payment_state") in ("not_paid", "partial")
            else ""))
        for f in facturas]
    return {"n": len(facturas),
            "total": round(sum(float(f.get("amount_total") or 0)
                               for f in facturas), 2),
            "detalle": detalle,
            "nombre": DIARIO_FUERA_DE_ALCANCE}, None


def _ventana_hoy_utc():
    """(desde, hasta) del día de HOY en Panamá, como texto UTC para un
    dominio de Odoo — el mismo cuidado de zona que `_fecha_panama` pero al
    revés (y el mismo criterio que `resumen._ventana_utc`): la medianoche
    se toma en Panamá y se CONVIERTE, nunca se suman horas a mano."""
    dia = datetime.now(ZONA_PANAMA).date()
    inicio = datetime(dia.year, dia.month, dia.day, tzinfo=ZONA_PANAMA)
    fin = inicio + timedelta(days=1)
    fmt = "%Y-%m-%d %H:%M:%S"
    return (inicio.astimezone(timezone.utc).strftime(fmt),
            fin.astimezone(timezone.utc).strftime(fmt))


def _creado_hoy_vacio():
    return {"ordenes": [], "facturas": [], "pagos": [], "total_ordenes": 0.0}


def _quien_creo(fila):
    """El nombre del usuario de Odoo que creó el registro (`create_uid`
    viene como [id, nombre]). «—» si Odoo no lo trae: no se inventa."""
    uid = fila.get("create_uid")
    if isinstance(uid, (list, tuple)) and len(uid) > 1:
        return uid[1] or "—"
    return "—"


def _cliente_de(fila):
    partner = fila.get("partner_id")
    if isinstance(partner, (list, tuple)) and len(partner) > 1:
        return partner[0], (partner[1] or "")
    return None, ""


def _sospecha_duplicado(orden_hoy, universo):
    """La SOSPECHA de duplicado de un pedido creado hoy: otro pedido NO
    cancelado del MISMO cliente (mismos últimos 8 dígitos del teléfono)
    con monto parecido (|diferencia| ≤ máx(1% del monto, $1)). Es una
    sospecha que se MUESTRA, nunca una conclusión ni una acción: aquí no
    se decide nada y mucho menos se toca Odoo. Si hay varias parecidas se
    nombra la primera por número de orden."""
    partner_id, _ = _cliente_de(orden_hoy)
    partner = universo["partners"].get(partner_id) or {}
    telefono = _ultimos8(partner.get("phone") or "")
    if not telefono:
        return ""
    monto = float(orden_hoy.get("amount_total") or 0)
    tolerancia = max(abs(monto) * 0.01, 1.0)
    parecidas = []
    for otra in universo["ordenes"]:
        if otra.get("id") == orden_hoy.get("id"):
            continue
        if (otra.get("state") or "") == "cancel":
            continue
        otro_partner = universo["partners"].get(
            (otra.get("partner_id") or [0])[0]) or {}
        if _ultimos8(otro_partner.get("phone") or "") != telefono:
            continue
        if abs(float(otra.get("amount_total") or 0) - monto) <= tolerancia:
            parecidas.append(otra.get("name") or "")
    if not parecidas:
        return ""
    return (f"posible duplicado de {sorted(parecidas)[0]} "
            "(mismo cliente, monto parecido)")


def _creado_hoy(universo):
    """(dict, hueco): todo lo que nació HOY (hora de Panamá) en Odoo —
    pedidos, facturas de cliente (out_invoice y out_refund, en cualquier
    estado) y pagos — y QUIÉN lo creó. Existe para ver de un vistazo lo
    que otra mano (u otra sesión) metió al Odoo de producción antes de
    correr la reconciliación contra él. Solo lectura, como todo el módulo:
    se mira y se reporta, no se concluye ni se corrige."""
    desde, hasta = _ventana_hoy_utc()
    de_hoy = [["create_date", ">=", desde], ["create_date", "<", hasta]]
    try:
        ordenes_hoy = _leer(
            "sale.order", "search_read", [list(de_hoy)],
            {"fields": ["name", "partner_id", "amount_total", "state",
                        "create_uid", "client_order_ref"],
             "order": "id asc"})
        # Las internas (vista previa, muestra) tampoco son «creado hoy»:
        # estrenar la orden fija dispararía un renglón —y hasta una
        # sospecha de duplicado— por pura utilería de la app.
        ordenes_hoy = [o for o in ordenes_hoy
                       if not _es_ref_interna(o.get("client_order_ref"))]
        facturas_hoy = _leer(
            "account.move", "search_read",
            [list(de_hoy) + [["move_type", "in",
                              ["out_invoice", "out_refund"]]]],
            {"fields": ["name", "partner_id", "amount_total", "state",
                        "create_uid"], "order": "id asc"})
        pagos_hoy = _leer(
            "account.payment", "search_read", [list(de_hoy)],
            {"fields": ["name", "partner_id", "amount", "state",
                        "create_uid"], "order": "id asc"})
    except Exception as fallo:
        return _creado_hoy_vacio(), ("La sección «Creado hoy» no se pudo "
                                     f"leer: {_error(fallo)}")

    def _renglon(fila, monto, sospecha="", historica=False):
        return {"nombre": fila.get("name") or "",
                "cliente": _cliente_de(fila)[1],
                "monto": round(float(monto or 0), 2),
                "estado": fila.get("state") or "",
                "creado_por": _quien_creo(fila),
                # La sospecha solo aplica a los pedidos; en facturas y
                # pagos va "" (el contrato lo fija así). Igual la marca
                # histórica: solo un pedido puede llevarla en True.
                "sospecha": sospecha,
                "historica": historica}

    ordenes = [_renglon(o, o.get("amount_total"),
                        _sospecha_duplicado(o, universo),
                        (o.get("name") or "") in ORDENES_HISTORICAS)
               for o in ordenes_hoy]
    facturas = [_renglon(f, f.get("amount_total")) for f in facturas_hoy]
    pagos = [_renglon(p, p.get("amount")) for p in pagos_hoy]
    return {"ordenes": ordenes, "facturas": facturas, "pagos": pagos,
            "total_ordenes": round(sum(o["monto"] for o in ordenes), 2)}, None


def _leer_universo():
    """Todo lo que hace falta de Odoo, en un dict: órdenes (vivas y
    canceladas recientes), sus clientes, facturas, salidas y la etapa del
    Flujo de cada oportunidad. Lanza si Odoo no contesta — el llamador lo
    convierte en hueco, nunca en ceros."""
    vivas = _leer("sale.order", "search_read",
                  [[["state", "!=", "cancel"]]],
                  {"fields": CAMPOS_VENTA, "order": "date_order desc"})
    canceladas = _leer("sale.order", "search_read",
                       [[["state", "=", "cancel"],
                         ["date_order", ">=", _corte_canceladas()]]],
                       {"fields": CAMPOS_VENTA, "order": "date_order desc"})
    ordenes = [o for o in list(vivas) + list(canceladas)
               if not _es_ref_interna(o.get("client_order_ref"))]

    ids_partner = sorted({(o.get("partner_id") or [0])[0]
                          for o in ordenes if o.get("partner_id")})
    partners = {p["id"]: p for p in _leer(
        "res.partner", "read", [ids_partner],
        {"fields": ["name", "phone"]})} if ids_partner else {}

    ids_factura = sorted({fid for o in ordenes
                          for fid in (o.get("invoice_ids") or [])})
    facturas = {f["id"]: f for f in _leer(
        "account.move", "read", [ids_factura],
        {"fields": CAMPOS_FACTURA})} if ids_factura else {}

    ids_salida = sorted({sid for o in ordenes
                         for sid in (o.get("picking_ids") or [])})
    salidas = {s["id"]: s for s in _leer(
        "stock.picking", "read", [ids_salida],
        {"fields": CAMPOS_SALIDA})} if ids_salida else {}

    ids_oportunidad = sorted({(o.get("opportunity_id") or [0])[0]
                              for o in ordenes if o.get("opportunity_id")})
    oportunidades = {l["id"]: l for l in _leer(
        "crm.lead", "read", [ids_oportunidad],
        {"fields": ["stage_id"]})} if ids_oportunidad else {}

    return {"ordenes": ordenes, "partners": partners, "facturas": facturas,
            "salidas": salidas, "oportunidades": oportunidades}


# ---------------------------------------------------------------------------
# Señales de una orden (lo que cada fuente dice de ella)
# ---------------------------------------------------------------------------

def _es_factura_fuera(factura):
    diario = factura.get("journal_id") or []
    nombre = diario[1] if isinstance(diario, (list, tuple)) and len(diario) > 1 else ""
    return (nombre or "").strip() == DIARIO_FUERA_DE_ALCANCE


def _pagado_de(orden, facturas):
    """La plata REAL que entró por las facturas de esta orden: suma de
    `amount_total − amount_residual` de cada factura de cliente asentada
    (las notas de crédito restan), saltando las del diario fuera de
    alcance. NUNCA `amount_to_invoice`: con la política mixta de
    facturación ese campo no dice cuánto se debe."""
    pagado = 0.0
    renglones = []
    for fid in orden.get("invoice_ids") or []:
        factura = facturas.get(fid)
        if not factura:
            continue
        nombre = factura.get("name") or f"factura {fid}"
        if _es_factura_fuera(factura):
            renglones.append(
                f"Factura {nombre}: diario «{DIARIO_FUERA_DE_ALCANCE}» — "
                "fuera de alcance, su dinero no cuenta")
            continue
        if (factura.get("state") or "") != "posted":
            renglones.append(f"Factura {nombre}: sin asentar, no cuenta")
            continue
        entro = (float(factura.get("amount_total") or 0)
                 - float(factura.get("amount_residual") or 0))
        if (factura.get("move_type") or "") == "out_refund":
            entro = -entro
            renglones.append(
                f"Nota de crédito {nombre}: resta {calculos.dinero(abs(entro))}")
        else:
            renglones.append(
                f"Factura {nombre}: {factura.get('payment_state') or '—'}, "
                f"entró {calculos.dinero(entro)}")
        pagado += entro
    return round(pagado, 2), renglones


def _entrega_odoo(orden, salidas):
    """(entregado, renglón): lo que dice la SALIDA de Odoo. Entregado =
    hay al menos una salida no cancelada y todas las no canceladas están
    validadas (done). Las canceladas no bloquean ni entregan."""
    vivas = [salidas[sid] for sid in orden.get("picking_ids") or []
             if sid in salidas and (salidas[sid].get("state") or "") != "cancel"]
    if not vivas:
        return False, "Salida de Odoo: sin salida registrada"
    if all((s.get("state") or "") == "done" for s in vivas):
        return True, "Salida de Odoo: validada (done)"
    return False, "Salida de Odoo: sin validar"


def _vencimiento(orden, hoy):
    """(vencida, nota): si la cotización está vencida, y de dónde salió la
    fecha. Con `validity_date` manda esa fecha; sin ella (las del carrito
    de Vender no la tienen) manda `date_order` + 14 días y la nota lo
    dice. Vencida es atributo de la COTIZACIÓN: el Perdido del lead es
    otra regla (del frontend) que aquí ni se calcula ni se cambia."""
    validez = orden.get("validity_date")
    if validez:
        # `validity_date` es una fecha PLANA de Odoo (sin hora ni zona):
        # se compara tal cual, sin pasarla por UTC — convertirla correría
        # el vencimiento un día para atrás en la tarde de Panamá.
        try:
            fecha = date.fromisoformat(str(validez)[:10])
        except (ValueError, TypeError):
            fecha = None
        if fecha is not None:
            return fecha < hoy, ""
    fecha_orden = _fecha_panama(orden.get("date_order"))
    if fecha_orden is None:
        return False, ""
    vencida = (hoy - fecha_orden).days >= DIAS_VENCIMIENTO
    return vencida, (TEXTO_SIN_VALIDEZ if vencida else "")


# ---------------------------------------------------------------------------
# La clasificación
# ---------------------------------------------------------------------------

def _clasificar(s):
    """De las señales de una venta, (clase, motivo). `s` trae: state,
    total, pagado, debe, vencida, nota_vencida, entregado_odoo,
    entregado_calendario, etapa_flujo, local_pagada, pago_externo,
    etiqueta_pago, lead_estado, lead_sin_plata."""
    estado = s["state"]
    cotizacion = estado in ("draft", "sent")
    confirmada = estado in ("sale", "done")
    sin_pago = s["pagado"] <= _CENTAVO
    cubre = s["total"] > _CENTAVO and s["pagado"] >= s["total"] - _CENTAVO

    clases = {}

    # H: las fuentes de verdad se contradicen, o no se puede saber.
    if (s["etiqueta_pago"] in ETIQUETAS_PAGO_AUTOMATICAS
            and s["lead_sin_plata"]):
        clases["H"] = TEXTO_H_ETIQUETA
    elif cotizacion and not sin_pago:
        clases["H"] = ("Revisar: cotización sin confirmar con pago encima "
                       f"({calculos.dinero(s['pagado'])})")
    elif s["pagado"] > s["total"] + _CENTAVO:
        clases["H"] = (f"Revisar: pagado ({calculos.dinero(s['pagado'])}) "
                       f"mayor que el total "
                       f"({calculos.dinero(s['total'])})")

    # F: alguien dice que se pagó, pero Odoo no tiene la plata. F NUNCA
    # significa pagado: es un aviso de regularizar.
    if sin_pago and (s["etapa_flujo"] in ("abono", "pagado")
                     or s["local_pagada"] or s["pago_externo"]):
        clases["F"] = TEXTO_F

    # G: el Calendario/Linear dice entregado y la salida de Odoo no está
    # validada. Solo con dato real del calendario (True, no None).
    if s["entregado_calendario"] is True and not s["entregado_odoo"]:
        clases["G"] = ("Entrega marcada en el Calendario/Linear; la salida "
                       "de Odoo no está validada")

    if confirmada:
        # C: confirmada y con saldo. Entregada con saldo SIGUE siendo C.
        # La espec define C con pagado > 0; una confirmada con $0 pagado y
        # sin ninguna otra señal (el caso Emiraf: S00078, $3,420 por
        # facturar) también cae aquí a propósito — la clase se llama
        # «Debe» y eso es exactamente lo que pasa; mandarla a H la
        # pintaría de rojo siendo negocio normal. Si hay señal de pago
        # fuera (F) o anomalía (H), esas ganan por el desempate.
        if s["debe"] > _CENTAVO:
            if s["entregado_odoo"]:
                clases["C"] = f"Entregado, debe {calculos.dinero(s['debe'])}"
            elif s["pagado"] > _CENTAVO:
                clases["C"] = f"Con abono, debe {calculos.dinero(s['debe'])}"
            else:
                clases["C"] = ("Confirmada sin pago; debe "
                               f"{calculos.dinero(s['debe'])}")
        if cubre and not s["entregado_odoo"]:
            clases["D"] = "Pagada completa en Odoo; salida sin validar"
        if cubre and s["entregado_odoo"]:
            motivo = "Pagada y entregada"
            if (s["lead_estado"] is not None
                    and s["lead_estado"] not in ("ENTREGADO", "GANADO")):
                motivo += f" · {TEXTO_LINEAR_SIN_CERRAR}"
            clases["E"] = motivo

    if cotizacion:
        if s["vencida"] and sin_pago:
            motivo = "Cotización vencida"
            if s["nota_vencida"]:
                motivo += f" ({s['nota_vencida']})"
            if s["lead_estado"] == "HABLANDO":
                # Vencida no se reactiva sola: sigue en B, con la nota.
                motivo += f" · {TEXTO_VOLVIO}"
            clases["B"] = motivo
        if not s["vencida"]:
            clases["A"] = "Cotización vigente"

    for clase in PRIORIDAD:
        if clase in clases:
            return clase, clases[clase]
    # Nada calzó (p. ej. confirmada en $0 sin señales): a revisar, nunca
    # se adivina una clase buena.
    return "H", "Revisar: ninguna regla la describe"


# ---------------------------------------------------------------------------
# El informe completo
# ---------------------------------------------------------------------------

def _informe_vacio(huecos):
    return {"contadores": {**{c: 0 for c in CLASES}, "rojo": 0},
            "ventas": [],
            "fuera_de_alcance": {"n": 0, "total": 0.0, "detalle": [],
                                 "nombre": DIARIO_FUERA_DE_ALCANCE},
            "creado_hoy": _creado_hoy_vacio(),
            "huecos": huecos}


def informe_datos():
    """El informe para la pantalla. Contrato EXACTO (otra trabajadora lo
    consume):

    - `contadores`: {"A"…"H": n, "rojo": F+G+H}.
    - `ventas`: lista de dicts con orden_id, nombre, cliente, telefono,
      fecha (`date_order` de Odoo tal cual, "AAAA-MM-DD HH:MM:SS", o "" si
      la orden no la trae — NUNCA la de hoy: una fecha inventada es peor
      que ninguna),
      total, pagado, debe, clase, motivo, marca_prueba,
      confirmada (True si la orden está en `sale`/`done`; False mientras
      sea cotización en `draft`/`sent`. NO se puede deducir de la clase:
      F, G y H caen de los dos lados. En las CANCELADAS va False),
      entregado_odoo,
      entregado_calendario (True/False/None = sin datos), historica
      (True solo para la tanda cerrada `ORDENES_HISTORICAS`: ventas
      viejas registradas tarde el 1/10, con la clase intacta y el motivo
      marcado), fuentes_odoo
      (lista de str) y fuentes_otras (lista de str). Las canceladas de los
      últimos 30 días van con clase "CANCELADA", solo informativas, fuera
      de los contadores.
    - `fuera_de_alcance`: {"n", "total", "detalle", "nombre"} del diario
      «Ventas Super Extra».
    - `creado_hoy`: lo que nació HOY (hora de Panamá) en Odoo, venga de
      donde venga — {"ordenes": [...], "facturas": [...], "pagos": [...],
      "total_ordenes": float}; cada lista trae dicts {nombre, cliente,
      monto, estado, creado_por, sospecha, historica}. `sospecha` solo
      aplica a los pedidos (posible duplicado por mismo cliente y monto
      parecido); en facturas y pagos va "". `historica` marca los pedidos
      de `ORDENES_HISTORICAS` (en facturas y pagos va False). Es
      información para mirar, nunca una conclusión.
    - `huecos`: lista de str con las fuentes que no contestaron. Un hueco
      no es un cero: lo que no se sabe se dice.
    """
    huecos = []
    if not ventas.configurado():
        huecos.append("Odoo no está configurado en esta instancia: no hay "
                      "ventas que reconciliar.")
        return _informe_vacio(huecos)
    try:
        universo = _leer_universo()
    except Exception as fallo:
        huecos.append(f"Odoo no contestó: {_error(fallo)}")
        return _informe_vacio(huecos)

    etapas, hueco = _etapas_del_flujo()
    if hueco:
        huecos.append(hueco)
    fuera, hueco = _fuera_de_alcance()
    if hueco:
        huecos.append(hueco)
    creado_hoy, hueco = _creado_hoy(universo)
    if hueco:
        huecos.append(hueco)

    leads, hueco = _leads_de_linear()
    if hueco:
        huecos.append(hueco)
    locales, hueco = _ventas_locales()
    if hueco:
        huecos.append(hueco)
    cot_locales, hueco = _cotizaciones_locales()
    if hueco:
        huecos.append(hueco)
    externos, hueco = _pagos_externos()
    if hueco:
        huecos.append(hueco)

    leads_por_tel = {}
    for lead in leads or []:
        tel = _ultimos8(lead.get("celular") or "")
        if tel:
            leads_por_tel.setdefault(tel, lead)
    externos_por_tel = {}
    for fila in externos:
        tel = _ultimos8(fila.get("telefono") or "")
        if tel:
            externos_por_tel.setdefault(tel, fila)

    hoy = _hoy()

    # Primera pasada: señales por orden (hace falta el pagado agregado por
    # lead para la regla H de la etiqueta automática).
    preparadas = []
    pagado_por_lead = {}
    for orden in universo["ordenes"]:
        partner = universo["partners"].get(
            (orden.get("partner_id") or [0])[0]) or {}
        telefono = _ultimos8(partner.get("phone") or "")
        pagado, renglones_facturas = _pagado_de(orden, universo["facturas"])
        lead = leads_por_tel.get(telefono) if telefono else None
        preparadas.append({"orden": orden, "partner": partner,
                           "telefono": telefono, "pagado": pagado,
                           "renglones_facturas": renglones_facturas,
                           "lead": lead})
        if lead is not None and orden.get("state") != "cancel":
            clave = lead.get("ref") or lead.get("id") or telefono
            pagado_por_lead[clave] = pagado_por_lead.get(clave, 0.0) + pagado

    contadores = {c: 0 for c in CLASES}
    filas = []
    for p in preparadas:
        orden, lead = p["orden"], p["lead"]
        total = round(float(orden.get("amount_total") or 0), 2)
        pagado = p["pagado"]
        debe = round(total - pagado, 2)
        cliente = p["partner"].get("name") or ""
        cancelada = orden.get("state") == "cancel"

        entregado_odoo, renglon_salida = _entrega_odoo(
            orden, universo["salidas"])
        vencida, nota_vencida = _vencimiento(orden, hoy)

        oportunidad = universo["oportunidades"].get(
            (orden.get("opportunity_id") or [0])[0]) or {}
        etapa_id = (oportunidad.get("stage_id") or [0])[0]
        etapa_flujo = etapas.get(etapa_id, "")

        local_estado = locales.get(orden.get("id"))
        local_pagada = (local_estado or "") in ESTADOS_LOCALES_PAGADA
        cot_local = cot_locales.get(orden.get("id"))
        pago_externo = externos_por_tel.get(p["telefono"]) if p["telefono"] else None

        if lead is not None:
            lead_estado = lead.get("estado") or ""
            etiqueta_pago = lead.get("pago") or ""
            entregado_calendario = lead_estado in ("ENTREGADO", "GANADO")
            clave_lead = lead.get("ref") or lead.get("id") or p["telefono"]
            lead_sin_plata = pagado_por_lead.get(clave_lead, 0.0) <= _CENTAVO
        else:
            lead_estado = None
            etiqueta_pago = ""
            entregado_calendario = None
            lead_sin_plata = False

        # Las dos caras de la entrega, SIEMPRE en renglones separados.
        fuentes_odoo = [
            f"Odoo: orden {orden.get('name') or ''} en estado "
            f"{orden.get('state') or ''}, total {calculos.dinero(total)}",
            *p["renglones_facturas"],
            renglon_salida,
        ]
        if etapa_flujo:
            fuentes_odoo.append(f"Flujo de Odoo (manual): etapa {etapa_flujo}")

        fuentes_otras = []
        if leads is None:
            fuentes_otras.append("Calendario/Linear: sin datos")
        elif lead is None:
            fuentes_otras.append(
                "Calendario/Linear: sin lead que coincida por teléfono")
        else:
            fuentes_otras.append(
                f"Calendario/Linear: lead {lead.get('ref') or ''} en "
                f"{lead_estado or '—'}"
                + (" (entrega Hecha)" if entregado_calendario else ""))
            if etiqueta_pago:
                fuentes_otras.append(
                    f"Linear: etiqueta de pago «{etiqueta_pago}»")
        if local_estado is not None:
            fuentes_otras.append(
                f"Ventas locales (SQLite): estado {local_estado or '—'}")
        if cot_local is not None:
            fuentes_otras.append(
                f"Cotización de servicio (SQLite): tipo {cot_local or '—'}")
        if pago_externo is not None:
            fuentes_otras.append(TEXTO_PAGO_EXTERNO)

        historica = (orden.get("name") or "") in ORDENES_HISTORICAS
        if cancelada:
            clase = "CANCELADA"
            motivo = (f"Cancelada en Odoo (últimos {DIAS_CANCELADAS} días) "
                      "— solo informativa")
        else:
            clase, motivo = _clasificar({
                "state": orden.get("state") or "",
                "total": total, "pagado": pagado, "debe": debe,
                "vencida": vencida, "nota_vencida": nota_vencida,
                "entregado_odoo": entregado_odoo,
                "entregado_calendario": entregado_calendario,
                "etapa_flujo": etapa_flujo,
                "local_pagada": local_pagada,
                "pago_externo": pago_externo is not None,
                "etiqueta_pago": etiqueta_pago,
                "lead_estado": lead_estado,
                "lead_sin_plata": lead_sin_plata,
            })
            contadores[clase] += 1

        # Una histórica conserva su CLASE tal cual (el dinero está bien);
        # solo el motivo dice que es la tanda vieja. En D el motivo entero
        # cambia para que no suene a entrega pendiente de hoy.
        if historica:
            if clase == "D":
                motivo = TEXTO_D_HISTORICA
            else:
                motivo += f" · {SUFIJO_HISTORICA}"

        filas.append({
            "orden_id": orden.get("id"),
            "nombre": orden.get("name") or "",
            "cliente": cliente,
            "telefono": p["telefono"],
            # ¿Es una VENTA CONFIRMADA o todavía una cotización? La clase
            # A–H no sirve para preguntarlo: F, G y H caen de los dos lados
            # (medido el 7/10 en el Odoo de pruebas: de 5 filas en clase F,
            # 1 era `sale` y 4 `draft`). Por eso el estado de la orden viaja
            # como un sí/no de negocio — Finanzas lo necesita para sumar
            # SOLO ventas confirmadas (BLOQUE 59.2), y acá es el único lugar
            # donde el `state` de Odoo está en la mano.
            "confirmada": (orden.get("state") or "") in ("sale", "done"),
            # La fecha de la venta, cruda de Odoo. Ya se leía (`date_order`
            # está en CAMPOS_VENTA, es lo que ordena el universo) pero se
            # quedaba adentro: la cola de pagos no tenía cómo decir DE
            # CUÁNDO es cada fila (7/10/2026).
            "fecha": orden.get("date_order") or "",
            "total": total,
            "pagado": pagado,
            "debe": debe,
            "clase": clase,
            "motivo": motivo,
            # La S00084 («Prueba Flujo OST») no se excluye: se clasifica
            # normal y la pantalla la rotula «PRUEBA / REVISIÓN ADMIN».
            "marca_prueba": "prueba" in cliente.lower(),
            "entregado_odoo": entregado_odoo,
            "entregado_calendario": entregado_calendario,
            "historica": historica,
            "fuentes_odoo": fuentes_odoo,
            "fuentes_otras": fuentes_otras,
        })

    contadores["rojo"] = contadores["F"] + contadores["G"] + contadores["H"]
    return {"contadores": contadores, "ventas": filas,
            "fuera_de_alcance": fuera, "creado_hoy": creado_hoy,
            "huecos": huecos}


# ---------------------------------------------------------------------------
# Línea de comandos: python -m app.reconciliacion informe [--salida DIR]
# ---------------------------------------------------------------------------

def _escribir_csv(datos, carpeta):
    """El CSV del informe: una fila por venta, con lo que dijo cada
    fuente. Devuelve la ruta escrita."""
    nombre = f"reconciliacion_{datetime.now(ZONA_PANAMA):%Y%m%d}.csv"
    ruta = os.path.join(carpeta, nombre)
    with open(ruta, "w", newline="", encoding="utf-8") as archivo:
        pluma = csv.writer(archivo)
        pluma.writerow(["clase", "orden", "cliente", "total", "pagado",
                        "debe", "motivo", "fuentes_odoo", "fuentes_otras",
                        "marca_prueba"])
        for v in datos["ventas"]:
            pluma.writerow([
                v["clase"], v["nombre"], v["cliente"],
                # El CSV va SIN `$` y SIN coma de miles a propósito: es
                # para una hoja de cálculo, y una coma ahí la leería como
                # texto (o partiría la celda). El formato de la casa
                # (`calculos.dinero`) es para lo que lee una persona.
                f"{v['total']:.2f}", f"{v['pagado']:.2f}",
                f"{v['debe']:.2f}", v["motivo"],
                " | ".join(v["fuentes_odoo"]),
                " | ".join(v["fuentes_otras"]),
                "sí" if v["marca_prueba"] else "",
            ])
    return ruta


def _cli(argv=None):
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m app.reconciliacion",
        description="Reconciliación M1: cada venta de Odoo contra todas "
                    "las fuentes, en solo lectura.")
    sub = parser.add_subparsers(dest="mando")
    informe = sub.add_parser("informe", help="imprime los totales por "
                             "clase y escribe el CSV del día")
    informe.add_argument("--salida", default=".",
                         help="carpeta donde dejar el CSV (por defecto, "
                              "la carpeta actual)")
    args = parser.parse_args(argv)
    if args.mando != "informe":
        parser.print_help()
        return 2

    datos = informe_datos()
    print(f"Reconciliación M1 — {datetime.now(ZONA_PANAMA):%d/%m/%Y}")
    for clase, nombre in CLASES.items():
        print(f"  {clase} {nombre}: {datos['contadores'][clase]}")
    print(f"  Rojo (F+G+H): {datos['contadores']['rojo']}")
    hoy_creado = datos["creado_hoy"]
    print("  Creado hoy fuera de Orquesta: "
          f"{len(hoy_creado['ordenes'])} pedidos · "
          f"{len(hoy_creado['facturas'])} facturas · "
          f"{len(hoy_creado['pagos'])} pagos")
    for familia, etiqueta in (("ordenes", "Pedido"), ("facturas", "Factura"),
                              ("pagos", "Pago")):
        for r in hoy_creado[familia]:
            renglon = (f"    {etiqueta} {r['nombre']} · "
                       f"{r['cliente'] or '—'} · {calculos.dinero(r['monto'])} · "
                       f"{r['estado'] or '—'} · creado por {r['creado_por']}")
            if r["sospecha"]:
                renglon += f" — {r['sospecha']}"
            if r["historica"]:
                renglon += " — histórica registrada el 1/10"
            print(renglon)
    if hoy_creado["ordenes"]:
        print("    Total de pedidos creados hoy: "
              f"{calculos.dinero(hoy_creado['total_ordenes'])}")
    canceladas = sum(1 for v in datos["ventas"] if v["clase"] == "CANCELADA")
    if canceladas:
        print(f"  Canceladas (informativas, últimos {DIAS_CANCELADAS} "
              f"días): {canceladas}")
    fuera = datos["fuera_de_alcance"]
    print(f"  Fuera de alcance («{DIARIO_FUERA_DE_ALCANCE}»): "
          f"{fuera['n']} facturas · {calculos.dinero(fuera['total'])}")
    for hueco in datos["huecos"]:
        print(f"  HUECO: {hueco}")
    if not datos["ventas"] and datos["huecos"]:
        print("  Sin ventas que listar: el informe quedó con huecos "
              "(arriba dice cuáles). Nada se inventa.")
        return 1
    ruta = _escribir_csv(datos, args.salida)
    print(f"  CSV: {ruta}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
