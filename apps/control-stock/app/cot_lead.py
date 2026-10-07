"""La ficha del lead <-> Odoo: qué cotizaciones y ventas tiene un lead, y
cuál de ellas es "la real" para el embudo.

Solo datos. Ninguna ruta, ninguna plantilla, ninguna escritura en Linear ni
en Twenty — eso lo hace la otra tanda, cableando este módulo a la pantalla
de Control. Nunca confirma, nunca cobra, nunca factura.

La puerta a Odoo es SIEMPRE `ventas._ejecutar` (XML-RPC), la misma que usan
`cotizaciones.py` y `ventas.py`: no se abre ninguna otra conexión. Se llama
como `ventas._ejecutar(...)` (nunca `from .ventas import _ejecutar`) para
que las pruebas puedan reemplazarla con `monkeypatch.setattr(ventas,
"_ejecutar", falso)`, igual que en `tests/test_cotizaciones.py` y
`tests/test_ventas.py`.

El acuerdo de la conexión lead <-> orden (corregido el 28/09/2026, con el
OK de Abraham — la primera versión de este módulo usaba `client_order_ref`
para la conexión, y eso pisaba el número público de los pedidos en línea,
`VR-XXXXXX`, que el order-api y el addon usan en más de una docena de
lugares para no duplicar un pedido y para saber que es un pedido en línea):

- **Conectada** = `lead_ref` de la orden vale el `PP-XXXXX` del lead.
- **La real** = de las conectadas, la única con `lead_real` en True.
  Marcar una como la real se lo quita a las demás del mismo lead: la
  invariante (nunca dos reales) se garantiza acá.
- Desconectar limpia `lead_ref` Y `lead_real` de esa orden.
- `client_order_ref` es del order-api (el `VR-XXXXXX` de un pedido en
  línea): aquí es de **solo lectura**, y solo para descartar las
  referencias internas de la app (vista previa, muestra de PDF). Nunca se
  escribe ni se limpia. Una orden con `client_order_ref = "VR-549312"`
  (un pedido en línea, ya pagado) puede conectarse a un lead igual que
  cualquier otra: es justo el caso que motivó el cambio, porque en el
  Odoo real las únicas órdenes con plata encima son las `VR-`.
- `linear_issue_url` **no es la conexión**: es el link que el kanban de
  cobro de Odoo muestra, regenerable. `conectar()` lo llena SOLO si
  estaba vacío (nunca pisa uno que ya tenía); `desconectar()` no lo toca.

**`lead_ref` y `lead_real` TODAVÍA NO EXISTEN en Odoo** al escribir esto:
los crea una fase aparte del addon `vivero_rose_pedidos`. Este módulo está
escrito y probado contra ellos con el doble de Odoo de las pruebas — nadie
debe cablear esto a la pantalla real antes de que esa fase esté desplegada.

Nota sobre `_reemplazar_anteriores_del_lead()` del addon: esa regla corre
SOLO en `create()` (cancela las cotizaciones abiertas anteriores del mismo
lead al nacer una nueva). `conectar()` aquí escribe `lead_ref` con un
`write()` sobre una orden que YA EXISTE, así que esa regla no se dispara —
por diseño: es justo lo que permite el caso de Tamara (S00079 y S00081,
dos trabajos distintos, las dos vigentes y conectadas al mismo lead sin que
ninguna cancele a la otra). No se toca `reemplazada_por_id` en ningún lado
de este módulo.

Fail-soft con Odoo, siempre: toda función que lee Odoo devuelve
`{"ok": False, "error": "..."}` si Odoo no contesta, nunca una lista vacía
que se confunda con "este lead no tiene nada". La diferencia entre "no hay"
y "no sé" tiene que llegar a quien muestre.
"""

import re
from datetime import datetime, timedelta, timezone

from .datos import ZONA_PANAMA
from . import cotizaciones, linear_leads, ventas

# Las 3 etiquetas de pago son las de Linear, leídas de su catálogo — no se
# copian a mano para que un cambio allá no desincronice esto (pedido
# explícito del encargo: "leelo, no lo copies de memoria").
ETIQUETA_ABONO, ETIQUETA_PAGADO, _ETIQUETA_COBRAR_SALDO = linear_leads.LABELS_PAGO

DIAS_CANDIDATAS = 60

# Referencias que no son cotizaciones de un cliente real: la vista previa
# de Vender (una por empleada, "VISTA PREVIA <usuario>") y la propuesta de
# muestra del PDF. Nunca deben aparecer como candidatas para conectar.
_REFS_INTERNAS_PREFIJO = ("VISTA PREVIA",)
_REFS_INTERNAS_EXACTAS = {"MUESTRA-PDF"}

ESTADOS_ORDEN = {
    "draft": "Borrador",
    "sent": "Enviada",
    "sale": "Confirmada",
    "done": "Bloqueada",
    "cancel": "Cancelada",
}

# Los campos de sale.order que arma cada orden legible. `total_pagado` y
# `saldo_pendiente` son los que ya calcula el addon (cobro.py) desde pagos
# reales — nunca se recalculan aquí. `client_order_ref` viaja SOLO para
# descartar referencias internas (nunca se escribe ni se usa como conexión:
# es el VR-XXXXXX del order-api). `lead_ref`/`lead_real` son la conexión.
CAMPOS_ORDEN = [
    "name", "date_order", "amount_total", "state", "etapa_cobro",
    "total_pagado", "saldo_pendiente", "linear_issue_url",
    "client_order_ref", "reemplazada_por_id", "lead_ref", "lead_real",
    # La casilla del 50/50 (28/09/2026): sin ella la tarjeta no pide
    # el abono del 50%.
    "pago_50_50",
]


def _error(error):
    """El mensaje humano de una falla de Odoo, con el mismo criterio que
    `ventas._mensaje_de_error` (la última línea del traceback de un Fault,
    o el texto de cualquier otra excepción, recortado)."""
    return ventas._mensaje_de_error(error)


def _pp_de(lead):
    """El PP-XXXXX del lead, en mayúsculas y validado; "" si no lo trae."""
    pp = str((lead or {}).get("pp") or "").strip().upper()
    return pp if pp.startswith("PP-") else ""


def _es_ref_interna(ref):
    ref = (ref or "").strip().upper()
    return ref in _REFS_INTERNAS_EXACTAS or any(
        ref.startswith(p) for p in _REFS_INTERNAS_PREFIJO)


def _hace_60_dias_utc():
    """El corte de "últimos 60 días", en UTC como texto para Odoo (mismo
    criterio que `resumen.py:_ventana_utc`: Odoo guarda sus datetime en UTC
    sin zona, así que la conversión pasa siempre por una zona explícita, no
    por sumar/restar horas a mano)."""
    corte = datetime.now(ZONA_PANAMA) - timedelta(days=DIAS_CANDIDATAS)
    return corte.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _orden_legible(fila):
    """Una fila cruda de sale.order -> lo que la tarjeta necesita."""
    return {
        "orden_id": fila["id"],
        "nombre": fila.get("name") or "",
        "fecha": fila.get("date_order") or "",
        "total": fila.get("amount_total") or 0.0,
        "estado": ESTADOS_ORDEN.get(fila.get("state"), fila.get("state") or ""),
        "estado_clave": fila.get("state") or "",
        "pagado": fila.get("total_pagado") or 0.0,
        "saldo": fila.get("saldo_pendiente") or 0.0,
        "etapa_cobro": fila.get("etapa_cobro") or "",
        # "la real": la única conectada con lead_real en True.
        "es_real": bool(fila.get("lead_real")),
        # Ausente (Odoo viejo) cuenta como True: es el default del addon.
        "pide_abono": bool(fila.get("pago_50_50", True)),
        # El link al kanban de cobro de Odoo — regenerable, no es la
        # conexión (ver el docstring del módulo).
        "issue_url": fila.get("linear_issue_url") or "",
        "reemplazada": bool(fila.get("reemplazada_por_id")),
    }


# ---------------------------------------------------------------------------
# 1. Las órdenes conectadas a un lead
# ---------------------------------------------------------------------------

def ordenes_del_lead(lead):
    """Las órdenes con `lead_ref` = el PP del lead, más nuevas primero.
    `{"ok": True, "ordenes": [...]}` o `{"ok": False, "error": "..."}` si
    Odoo no contestó — nunca una lista vacía por una falla."""
    pp = _pp_de(lead)
    if not pp:
        # Sin PP no hay nada que buscar: esto sí es "no hay", no "no sé".
        return {"ok": True, "error": None, "ordenes": []}
    try:
        filas = ventas._ejecutar(
            "sale.order", "search_read",
            [[["lead_ref", "=ilike", pp]]],
            {"fields": CAMPOS_ORDEN, "order": "date_order desc"})
    except Exception as error:
        return {"ok": False, "error": _error(error), "ordenes": []}
    return {"ok": True, "error": None,
            "ordenes": [_orden_legible(f) for f in filas]}


def _orden_real(lead):
    """La orden real del lead, o None si no hay (o si Odoo falló: el
    llamador debe revisar "ok" antes de asumir "no hay real")."""
    resultado = ordenes_del_lead(lead)
    if not resultado["ok"]:
        return resultado, None
    reales = [o for o in resultado["ordenes"] if o["es_real"]]
    return resultado, (reales[0] if reales else None)


# ---------------------------------------------------------------------------
# 2. Candidatas para conectar
# ---------------------------------------------------------------------------

def candidatas_del_lead(lead, texto_libre=""):
    """Las cotizaciones/ventas candidatas a conectar con este lead:

    - Automático: por teléfono del lead (con o sin guion) o por nombre del
      contacto, dentro de los últimos 60 días.
    - Manual: `texto_libre` (para un número "S000xx", por si la orden está
      a otro nombre) — sin el límite de 60 días, porque el sentido de
      buscar por número es encontrar justo la que el filtro automático no
      hubiera traído.

    Las dos bolsas se unen (una orden que calce por los dos caminos sale
    una sola vez). Quedan fuera siempre las YA conectadas (`lead_ref`
    puesto — a este lead o a otro: si es a este, ya están en
    `ordenes_del_lead`, no son "candidatas") y las referencias internas de
    la app (vista previa, muestra de PDF) — esas se descartan por
    `client_order_ref`, que es de solo lectura aquí.

    Un pedido en línea (`client_order_ref = "VR-XXXXXX"`) SÍ puede salir
    como candidata: ese campo no es la conexión, así que no lo excluye.

    El filtrado es en Python, no por dominio de Odoo: el negocio tiene un
    puñado de órdenes (11 hoy), así que traer todo y filtrar aquí es más
    simple de mantener y de probar que armar un dominio con relaciones
    (`partner_id.phone`) sobre XML-RPC.
    """
    celular = re.sub(r"\D", "", str((lead or {}).get("celular") or ""))
    nombre = str((lead or {}).get("nombre") or "").strip().lower()
    texto_libre = (texto_libre or "").strip().lower()
    pp_propio = _pp_de(lead)
    variantes_digitos = {re.sub(r"\D", "", v)
                         for v in cotizaciones._variantes_telefono(celular)} if celular else set()

    try:
        filas = ventas._ejecutar(
            "sale.order", "search_read", [[]],
            {"fields": CAMPOS_ORDEN + ["partner_id"]})
    except Exception as error:
        return {"ok": False, "error": _error(error), "candidatas": []}

    ids_partner = [f["partner_id"][0] for f in filas if f.get("partner_id")]
    try:
        partners = {p["id"]: p for p in ventas._ejecutar(
            "res.partner", "read", [ids_partner], {"fields": ["name", "phone"]})} \
            if ids_partner else {}
    except Exception as error:
        return {"ok": False, "error": _error(error), "candidatas": []}

    corte = _hace_60_dias_utc()
    # Los teléfonos de los DEMÁS leads vivos, para marcar la candidata
    # ambigua (2/10/2026, OK de Abraham): un teléfono compartido entre dos
    # clientes puede hacer que la cotización de B salga como candidata en
    # la ficha de A — la marca avisa, no bloquea (conectar sigue siendo
    # decisión de la empleada) y no toca el amarre automático del espejo.
    telefonos_otros = _telefonos_de_otros_leads_vivos(lead)
    candidatas = []
    for fila in filas:
        ref_interna = (fila.get("client_order_ref") or "").strip().upper()
        if _es_ref_interna(ref_interna):
            continue
        if (fila.get("lead_ref") or "").strip():
            # Conectada — a este lead o a otro: ya no es "candidata".
            continue
        partner = partners.get((fila.get("partner_id") or [None])[0]) or {}
        telefono_orden = re.sub(r"\D", "", str(partner.get("phone") or ""))
        coincide_telefono = bool(variantes_digitos) and (
            telefono_orden and telefono_orden[-8:] in variantes_digitos)
        coincide_nombre = bool(nombre) and nombre in (partner.get("name") or "").lower()
        coincide_libre = bool(texto_libre) and texto_libre in (fila.get("name") or "").lower()

        entra = coincide_libre
        if not entra and (coincide_telefono or coincide_nombre):
            fecha = fila.get("date_order") or ""
            entra = fecha >= corte

        if not entra:
            continue
        legible = _orden_legible(fila)
        legible["cliente"] = partner.get("name") or ""
        # La marca de ambigüedad, decidida ACÁ (la plantilla solo pinta):
        # el teléfono de esta orden también es de otro lead vivo.
        legible["ambigua"] = bool(
            telefono_orden and telefono_orden[-8:] in telefonos_otros)
        legible["aviso_ambigua"] = (
            "Este teléfono es de más de un cliente: revisa que la "
            "cotización sea de ESTE lead antes de conectarla."
            if legible["ambigua"] else "")
        candidatas.append(legible)

    candidatas.sort(key=lambda o: o["fecha"], reverse=True)
    return {"ok": True, "error": None, "candidatas": candidatas}


def _telefonos_de_otros_leads_vivos(lead):
    """Los últimos 8 dígitos del celular de cada OTRO lead vivo del
    tablero (los cerrados —Ganado, Perdido— no cuentan: a un lead cerrado
    no se le hace una venta nueva). Best-effort a propósito: la marca de
    ambigüedad es un extra, y si Linear no contesta las candidatas salen
    igual, solo que sin marca."""
    from . import linear_leads  # diferido: este módulo es la capa de Odoo
    try:
        leads = linear_leads.listar()
    except Exception:
        return set()
    ref_propio = str((lead or {}).get("ref") or "")
    telefonos = set()
    for otro in leads:
        if otro.get("ref") == ref_propio:
            continue
        if otro.get("estado") in linear_leads.CERRADOS:
            continue
        digitos = re.sub(r"\D", "", str(otro.get("celular") or ""))
        if len(digitos) >= 7:
            telefonos.add(digitos[-8:])
    return telefonos


# ---------------------------------------------------------------------------
# 3. Conectar / desconectar / marcar cuál es la real
# ---------------------------------------------------------------------------

def conectar(orden_id, lead):
    """Conecta la orden al lead: `lead_ref` = su PP-XXXXX. Rechaza conectar
    una orden que ya está conectada a OTRO lead — una orden no pertenece a
    dos leads. `linear_issue_url` (el link del kanban de cobro) se llena
    SOLO si estaba vacío: es un dato regenerable, no la conexión, y no se
    pisa uno que ya tenía (por ejemplo, una corrección hecha a mano en
    Odoo)."""
    pp = _pp_de(lead)
    if not pp:
        raise ValueError("Ese lead no tiene su código PP- de cliente.")
    orden_id = int(orden_id)
    actual = ventas._ejecutar(
        "sale.order", "read", [[orden_id]],
        {"fields": ["lead_ref", "linear_issue_url"]})
    if not actual:
        raise ValueError("Esa orden ya no está en Odoo.")
    ref_actual = (actual[0].get("lead_ref") or "").strip().upper()
    if ref_actual and ref_actual != pp:
        raise ValueError(
            f"Esa orden ya está conectada a otro lead ({ref_actual}).")
    valores = {"lead_ref": pp}
    if not actual[0].get("linear_issue_url"):
        url = (lead or {}).get("url") or ""
        if url:
            valores["linear_issue_url"] = url
    ventas._ejecutar("sale.order", "write", [[orden_id], valores])


def desconectar(orden_id):
    """Quita la conexión: limpia `lead_ref` Y `lead_real` de esa orden (si
    era la real, deja de serlo). NO toca `linear_issue_url` — es el link
    regenerable del kanban de cobro de Odoo, no la conexión con el lead;
    borrarlo no protege nada y rompe el link para quien lo esté mostrando."""
    ventas._ejecutar("sale.order", "write", [[int(orden_id)], {
        "lead_ref": False, "lead_real": False}])


def marcar_real(orden_id, lead):
    """Marca esta orden como LA real del lead: pone `lead_real` en True, y
    se lo QUITA a cualquier otra orden conectada al mismo lead — nunca
    puede haber dos reales. Exige que la orden ya esté conectada (usar
    `conectar` primero)."""
    pp = _pp_de(lead)
    if not pp:
        raise ValueError("Ese lead no tiene su código PP- de cliente.")
    orden_id = int(orden_id)
    actual = ventas._ejecutar("sale.order", "read", [[orden_id]],
                              {"fields": ["lead_ref"]})
    if not actual:
        raise ValueError("Esa orden ya no está en Odoo.")
    ref_actual = (actual[0].get("lead_ref") or "").strip().upper()
    if ref_actual != pp:
        raise ValueError(
            "Esa orden no está conectada a este lead: conéctala primero.")
    hermanas = ventas._ejecutar(
        "sale.order", "search",
        [[["lead_ref", "=ilike", pp], ["id", "!=", orden_id]]])
    if hermanas:
        ventas._ejecutar("sale.order", "write",
                         [hermanas, {"lead_real": False}])
    ventas._ejecutar("sale.order", "write", [[orden_id], {"lead_real": True}])


def quitar_real(orden_id):
    """Deja al lead sin real, sin desconectar la orden: le quita solo
    `lead_real`. (`desconectar` hace esto y además la desconecta.)"""
    ventas._ejecutar("sale.order", "write",
                     [[int(orden_id)], {"lead_real": False}])


# ---------------------------------------------------------------------------
# 4. La plata de la real
# ---------------------------------------------------------------------------

def _plata_de_orden(real):
    """La plata de UNA orden ya legible (`_orden_legible`), en la forma que
    muestra la ficha de Control.

    Aparte a propósito: la regla del abono del 50% no puede vivir escrita
    dos veces, así que este cálculo es el único lugar donde se decide.
    """
    total = real["total"]
    return {
        "ok": True, "error": None, "hay_real": True,
        "orden_id": real["orden_id"], "orden": real["nombre"],
        "total": total,
        # Sin la casilla del 50/50 la tarjeta no pide abono: la fila
        # dice «pago completo al confirmar» (dueño, 28/09/2026).
        "pide_abono": real["pide_abono"],
        "abono_50": round(total / 2, 2) if real["pide_abono"] else None,
        "pagado": real["pagado"], "saldo": real["saldo"],
        "etapa_cobro": real["etapa_cobro"],
    }


def plata_de_la_real(lead):
    """El total, el abono del 50% (lo que el negocio pide de adelanto), lo
    que de verdad pagó y el saldo de la orden real — tal cual los tiene
    Odoo, nunca calculados aparte ni guardados en ningún lado.

    `{"ok": True, "hay_real": False}` si el lead no tiene real (no es un
    error); `{"ok": False, "error": "..."}` si no se pudo leer Odoo."""
    resultado, real = _orden_real(lead)
    if not resultado["ok"]:
        return {"ok": False, "error": resultado["error"], "hay_real": False}
    if real is None:
        return {"ok": True, "error": None, "hay_real": False}
    return _plata_de_orden(real)


def plata_de_varios(leads):
    """La plata de LA REAL de MUCHOS leads, en UNA sola consulta a Odoo.

    Es `plata_de_la_real` para un tablero entero: el monto de la tarjeta
    (BLOQUE 43, lienzo de Roles) lo necesita para cada lead de las nueve
    columnas, y pedirlo lead por lead serían ~25 viajes XML-RPC en cada
    pintada de la pantalla.

    Devuelve `{"ok", "error", "por_lead": {ref: plata}}`, con la MISMA
    forma que arma `_plata_de_orden` — la regla del abono del 50% sigue
    viviendo en un solo lugar. Un lead sin orden real sencillamente no
    aparece en el diccionario: eso es «no tiene», y la tarjeta no pinta
    monto. Con Odoo caído, `ok` es False y `por_lead` queda VACÍO: un
    monto que no se pudo leer no se pinta nunca, y jamás se inventa en
    cero (sería plata falsa en la cara de quien reparte el trabajo).

    Un lead sin PP-XXXXX no se pregunta: no hay por dónde buscarlo.
    """
    por_pp = {}
    for lead in leads or []:
        pp = _pp_de(lead)
        ref = (lead or {}).get("ref") or ""
        if pp and ref:
            por_pp.setdefault(pp, []).append(ref)
    if not por_pp:
        return {"ok": True, "error": None, "por_lead": {}}
    try:
        filas = ventas._ejecutar(
            "sale.order", "search_read",
            [[["lead_real", "=", True], ["lead_ref", "in", sorted(por_pp)]]],
            {"fields": CAMPOS_ORDEN, "order": "date_order desc"})
    except Exception as error:
        return {"ok": False, "error": _error(error), "por_lead": {}}
    por_lead = {}
    for fila in filas:
        # `lead_ref` se escribe siempre en mayúsculas (`conectar`), pero el
        # casamiento se hace igual sin distinguirlas: una corrección a mano
        # en Odoo no tiene por qué respetar esa costumbre nuestra.
        pp = (fila.get("lead_ref") or "").strip().upper()
        plata = _plata_de_orden(_orden_legible(fila))
        for ref in por_pp.get(pp, []):
            # La invariante «una sola real por lead» la garantiza
            # `marcar_real`; si por lo que sea hubiera dos, manda la más
            # nueva (el `order` de la consulta) y no se suman.
            por_lead.setdefault(ref, plata)
    return {"ok": True, "error": None, "por_lead": por_lead}


# ---------------------------------------------------------------------------
# 5. Qué implica la conexión para el embudo (dato, no acción)
# ---------------------------------------------------------------------------

def _estado_por_etapa(etapa_cobro):
    if etapa_cobro == "pagado":
        return {"estado": "POR_AGENDAR", "etiqueta_pago": ETIQUETA_PAGADO}
    if etapa_cobro == "abono":
        return {"estado": "POR_AGENDAR", "etiqueta_pago": ETIQUETA_ABONO}
    if etapa_cobro == "cotizado":
        return {"estado": "COTIZADO", "etiqueta_pago": None}
    # "cerrada" (cancelada/reemplazada) o cualquier otra cosa: sin
    # sugerencia — no hay una real de la que se pueda desprender un estado.
    return {"estado": None, "etiqueta_pago": None}


def estado_sugerido(lead):
    """El estado que le correspondería al lead según su orden real, como
    DATO — quien mueve el issue de Linear es la otra tanda, esto solo lo
    informa. Sin pago -> COTIZADO; con abono -> POR_AGENDAR + "Abono 50%";
    pagada del todo -> POR_AGENDAR + "Pagado 100%". Los nombres de las
    etiquetas de pago son exactamente los de `linear_leads.LABELS_PAGO`.

    `{"ok": True, "estado": None, "etiqueta_pago": None}` si no hay real
    todavía (no es un error); `{"ok": False, "error": "..."}` si Odoo
    falló."""
    resultado, real = _orden_real(lead)
    if not resultado["ok"]:
        return {"ok": False, "error": resultado["error"],
                "estado": None, "etiqueta_pago": None}
    if real is None:
        return {"ok": True, "error": None, "estado": None, "etiqueta_pago": None}
    return {"ok": True, "error": None, **_estado_por_etapa(real["etapa_cobro"])}


# ---------------------------------------------------------------------------
# 6. El PDF de una orden
# ---------------------------------------------------------------------------

def pdf_de_orden(orden_id):
    """El PDF nativo de Odoo de esa orden (el mismo reporte que usa
    Vender). `RuntimeError` con un mensaje claro si Odoo no lo pudo dar —
    nunca deja pasar un stacktrace crudo a la pantalla."""
    try:
        return ventas.descargar_pdf("sale.report_saleorder", int(orden_id))
    except Exception as error:
        raise RuntimeError(
            f"No se pudo generar el PDF de la orden: {_error(error)}") from error
