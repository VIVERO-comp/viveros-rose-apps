"""La orden de compra en Odoo y la entrada de stock al recibir.

Fase 2 de la pestaña Compras, y la pieza que le da sentido al resto: hasta
ahora el tablero anotaba qué se le compra a quién, pero Odoo no se enteraba
de nada. Acá las líneas de la compra se vuelven una `purchase.order` de
verdad, y lo que llega al vivero sube el stock por el camino de Odoo.

**El reparto no cambia**: Linear sigue siendo el único tablero (el estado de
la compra vive en la columna de su issue) y Odoo sigue siendo solo dinero y
stock. Este módulo es el ÚNICO que escribe en `purchase.order` y en los
`stock.picking` de una compra; `compras.py` sigue siendo la única puerta al
proyecto COMPRAS de Linear.

Las tres cosas que decide este módulo, y por qué:

1. **Al pasar a «Pedido a proveedor» nace la orden en Odoo.** Esa columna
   significa «ya se le pidió», y en Odoo eso no es una solicitud de
   presupuesto en borrador: es una orden confirmada. Por eso `asegurar_orden`
   crea Y confirma — y confirmar es lo que hace que Odoo cree solo la
   entrada de stock que después se recibe.

2. **Si Odoo no contesta, la compra se mueve igual.** Linear manda sobre el
   estado. Lo que NO puede pasar es un issue que diga «pedido» con un Odoo
   que no se enteró **en silencio**: la tarjeta queda diciendo «falta la
   orden en Odoo» (`compras.desde_pedido` + sin `orden_compra_id`) y el
   panel trae el botón de reintentar. La desincronización se ve.

3. **Recibir valida la entrada de Odoo con LO QUE LLEGÓ BUENO.** El stock no
   se sube por un camino propio: se valida el `stock.picking` que Odoo ya
   creó, que es el que deja rastro (movimientos, valoración, el «por
   recibir» de la orden). Lo dañado no entra al stock y se anota acá
   (`compra_danado`), porque Odoo no tiene dónde ponerlo sin entrar en
   mermas, que es otra cosa. Lo que faltó queda pendiente en la orden, que
   es como Odoo ya lo maneja: se pidieron 40, llegaron 35 → quedan 5 por
   recibir, y la recepción se puede repetir la semana que viene.

**Nada de impuestos.** Ninguna línea manda un campo de impuesto, igual que
en Vender: los impuestos son los que tenga cada producto en Odoo y el
código no los decide (medido el 01/10/2026: ningún producto del catálogo
tiene impuesto de compra y el valor por defecto de la compañía está vacío,
así que hoy la orden sale en $0.00 de impuesto sola). Escribir `taxes_id`
—aunque fuera para vaciarlo— sería tocar lo fiscal, y eso no se toca.

**La idempotencia, que es lo que hace seguro el reintento.** La orden lleva
en `origin` la referencia de la compra (`Compra VIV-204 · Control
Viverorose`) y antes de crear nada se busca si ya existe una con ese origen:
si la tanda anterior creó la orden y se cortó antes de guardar el número,
el reintento la ENCUENTRA en vez de crear una segunda. Es el mismo truco
que usa el order-api con los pedidos en línea. Verificado antes de usarlo:
`origin` de `purchase.order` no lo lee nadie más en los tres repos (el que
sí tiene dueño es el de `sale.order`, que el order-api usa justo para esto).

La puerta a Odoo es **siempre** `ventas._ejecutar(...)`, llamada así y no
importada, para que las pruebas la puedan reemplazar. Fail-soft con
criterio: todo devuelve `{"ok": False, "error": …}` cuando Odoo no contesta,
y **una lista vacía nunca significa «Odoo falló»**.
"""

from datetime import datetime

from . import compras, linear_leads, ventas
from .datos import ZONA_PANAMA, _db

# La columna en la que nace la orden de compra. Es la clave del tablero de
# `compras.ESTADOS`, no un nombre de Linear: el nombre allá es «Pedido a
# proveedor» y vive en un solo lugar.
CLAVE_PEDIDO = "PEDIDO"

# La columna que abre la recepción.
CLAVE_RECIBIDO = "RECIBIDO"

# Los estados de una `purchase.order` en los que la orden ya está pedida de
# verdad (y por lo tanto Odoo ya creó su entrada de stock).
ESTADOS_PEDIDA = ("purchase", "done")

# El estado de la doble validación de Odoo: la orden se confirmó pero espera
# aprobación de un gerente, así que TODAVÍA no tiene entrada de stock. No se
# fuerza nada desde acá — se dice y se deja que alguien la apruebe.
ESTADO_POR_APROBAR = "to approve"

# OJO: `picking_ids` viene del módulo `purchase_stock` (el que hace nacer la
# entrada de stock al confirmar). Está en el Odoo real y en el de pruebas —
# medido el 30/09/2026 al confirmar una orden a mano y ver nacer su
# `stock.picking`— pero si en una base no estuviera, el `read` revienta y
# eso sale como `{"ok": False}` con su motivo en la pantalla, nunca un 500.
CAMPOS_ORDEN = ["name", "state", "order_line", "picking_ids", "amount_total",
                "amount_tax", "partner_id", "origin"]

CAMPOS_LINEA_ORDEN = ["product_id", "name", "product_qty", "qty_received",
                      "price_unit"]

CAMPOS_PICKING = ["name", "state"]

CAMPOS_MOVIMIENTO = ["product_id", "product_uom_qty", "quantity", "state"]

# El contexto con el que se valida la entrada. `skip_backorder` es el que
# hace que Odoo NO abra su asistente de «¿creamos el pendiente?» y lo cree
# solo: por XML-RPC un asistente es un diálogo que nadie puede contestar, y
# el pendiente es justo lo que queremos. Los otros dos son por si esta base
# todavía trae los asistentes viejos de «transferencia inmediata» y del SMS.
CONTEXTO_VALIDAR = {"skip_backorder": True, "skip_immediate": True,
                    "skip_sms": True}


# ---------------------------------------------------------------------------
# Lo dañado: la única cosa de la recepción que Odoo no sabe guardar
#
# Odoo tiene dónde poner «llegó» y «falta», pero «llegó roto» solo cabe como
# una merma (un ajuste de inventario con su cuenta contable), y eso es otra
# decisión y de otro dueño. Acá se anota en la app: no sube el stock, no
# toca plata, y queda el número para el reclamo al proveedor —que es la
# tanda siguiente— y para la ficha del proveedor.
# ---------------------------------------------------------------------------

def iniciar_tablas():
    with _db() as con:
        # Una fila por vez que se anotó algo dañado, y no una por producto:
        # la recepción se puede repetir (llega una parte hoy y el resto la
        # semana que viene), y machacar la fila anterior borraría que el
        # primer camión también traía tres macetas rotas.
        con.execute("""
            CREATE TABLE IF NOT EXISTS compra_danado (
                n INTEGER PRIMARY KEY AUTOINCREMENT,
                ref TEXT NOT NULL,
                sku TEXT NOT NULL DEFAULT '',
                nombre TEXT NOT NULL DEFAULT '',
                cantidad REAL NOT NULL DEFAULT 0,
                quien TEXT NOT NULL DEFAULT '',
                anotada TEXT NOT NULL DEFAULT ''
            )
        """)
        con.execute("CREATE INDEX IF NOT EXISTS compra_danado_ref "
                    "ON compra_danado (ref)")


def _anotar_danado(ref, renglones, quien=""):
    """Guarda lo que llegó roto de esta recepción. `renglones` es
    `[(sku, nombre, cantidad)]` y solo entran las cantidades > 0."""
    renglones = [(s, n, c) for s, n, c in renglones if c > 0]
    if not renglones:
        return
    iniciar_tablas()
    ahora = datetime.now(ZONA_PANAMA).isoformat()
    with _db() as con:
        con.executemany(
            "INSERT INTO compra_danado (ref, sku, nombre, cantidad, quien, "
            "anotada) VALUES (?, ?, ?, ?, ?, ?)",
            [(str(ref), sku or "", nombre or sku or "", float(cantidad),
              str(quien or ""), ahora) for sku, nombre, cantidad in renglones])


def danado_de(ref):
    """Lo dañado anotado en esta compra, de lo más viejo a lo más nuevo.

    Lista vacía es «no llegó nada roto», que es la respuesta normal: acá no
    hay nada que Odoo pueda tumbar, es una tabla local.
    """
    iniciar_tablas()
    with _db() as con:
        filas = con.execute(
            "SELECT * FROM compra_danado WHERE ref = ? ORDER BY n",
            (str(ref or ""),)).fetchall()
    return [{"n": f["n"], "sku": f["sku"], "nombre": f["nombre"],
             "cantidad": f["cantidad"],
             "cantidad_texto": compras._cantidad_bonita(f["cantidad"]),
             "quien": f["quien"], "anotada": f["anotada"]} for f in filas]


def total_danado(ref):
    """Cuántas unidades dañadas lleva anotada esta compra en total."""
    return sum(d["cantidad"] for d in danado_de(ref))


# ---------------------------------------------------------------------------
# Lo que le falta a una compra para poder pedirse
# ---------------------------------------------------------------------------

class ErrorOrden(Exception):
    """Falla al armar la orden en Odoo, con el texto que se le muestra a
    quien está usando la pantalla. La atrapa `asegurar_orden` y sale como
    `{"ok": False}` — nunca sube hasta la pantalla como un 500."""


def _sin_orden(error, orden=None):
    """El «no quedó» con la misma forma siempre: quien llama no tiene que
    adivinar qué llaves vienen según por dónde falló."""
    return {"ok": False, "error": error, "orden": orden, "ya_estaba": False}


def falta_para_pedir(ref, compra=None):
    """Qué le falta a esta compra para que su orden pueda nacer en Odoo, en
    palabras claras, o "" si no le falta nada.

    Se consulta **antes** de mover la tarjeta a «Pedido a proveedor»: una
    compra sin proveedor de Odoo o sin productos no puede tener orden, y
    eso se dice antes y no después de que el tablero ya diga «pedido».

    No habla de Odoo caído a propósito: eso NO es un dato que falte, es un
    mal rato, y en ese caso la compra se mueve igual (ver `al_pedir`).
    """
    compra = compra if compra is not None else compras.uno(ref)
    if compra is None:
        return compras.mensaje_compra_ausente(ref)
    if compra.get("orden_compra_id"):
        return ""      # ya tiene su orden: nada que crear
    lineas = compras.lineas_de(compra["ref"])
    if not lineas:
        return (f"{compra['ref']} no tiene ningún producto anotado, así que "
                f"no se le puede hacer la orden de compra en Odoo. Agregá los "
                f"productos y después movela a «Pedido».")
    if not compra.get("proveedor_id"):
        escrito = (compra.get("proveedor") or "").strip()
        if escrito:
            return (f"«{escrito}» no es todavía un contacto de Odoo, así que "
                    f"la orden de compra no se puede hacer a su nombre. Hay "
                    f"que crearlo como proveedor en Odoo primero.")
        return (f"{compra['ref']} no tiene proveedor, y una orden de compra "
                f"de Odoo se le hace a alguien. Anotá el proveedor y después "
                f"movela a «Pedido».")
    return ""


# ---------------------------------------------------------------------------
# Crear (y confirmar) la orden de compra
# ---------------------------------------------------------------------------

def _origen(ref):
    """Lo que la orden lleva en `origin`: de dónde salió, y la llave con la
    que se la vuelve a encontrar si el reintento llega después de que Odoo
    ya la creó."""
    return f"Compra {ref} · Control Viverorose"


def _producto_de(linea):
    """El id de `product.product` de esa línea, o None.

    La línea puede haberse guardado sin id (Odoo no contestó al agregarla,
    o el producto acababa de nacer): el SKU es lo durable, así que se le
    pregunta a Odoo por él. Una línea de compra de Odoo **exige**
    `product_id`, así que sin esto no hay orden.
    """
    if linea.get("producto_id"):
        return int(linea["producto_id"])
    producto = compras.producto_por_sku(linea.get("sku") or "")
    return producto["id"] if producto else None


def _lineas_para_odoo(lineas):
    """([(0, 0, valores)], error) — las líneas de la orden, o el motivo por
    el que no se pueden armar.

    Si un producto no se encuentra en Odoo, se devuelve el error y NO una
    orden a medias: una orden con tres de los cinco productos es peor que
    ninguna, porque nadie se enteraría de los dos que faltan.

    **Ningún campo de impuesto viaja acá**, ni para ponerlo ni para
    vaciarlo: los decide Odoo con lo que tenga el producto.
    """
    ordenes = []
    for linea in lineas:
        producto_id = _producto_de(linea)
        if not producto_id:
            return [], (f"No se encontró en Odoo el producto "
                        f"«{linea.get('nombre') or linea.get('sku')}»"
                        f"{(' (' + linea['sku'] + ')') if linea.get('sku') else ''}"
                        f", así que la orden de compra no se puede hacer "
                        f"completa.")
        valores = {
            "product_id": producto_id,
            # `name` es obligatorio en una línea de compra. Va el nombre que
            # la compra guardó: es el que el empleado vio al agregarlo.
            "name": (linea.get("nombre") or linea.get("sku") or "")[:250],
            "product_qty": float(linea["cantidad"]),
        }
        # El costo es OPCIONAL y None es «no se sabe»: en ese caso no se
        # manda `price_unit` y Odoo pone lo que sepa del producto. Inventar
        # un 0 dejaría una orden que dice que el proveedor regala.
        if linea.get("costo") is not None:
            valores["price_unit"] = float(linea["costo"])
        ordenes.append((0, 0, valores))
    return ordenes, ""


def _orden_existente(ref):
    """La orden de compra que ya tiene esta compra por su `origin`, o None.

    Es la red que hace seguro el reintento: si el intento anterior creó la
    orden en Odoo y se cortó antes de guardar su número, acá se la
    encuentra. Las canceladas no cuentan — esa orden ya no es la de esta
    compra, y hay que poder volver a pedirla.
    """
    filas = ventas._ejecutar(
        "purchase.order", "search_read", [[["origin", "=", _origen(ref)],
                                           ["state", "!=", "cancel"]]],
        {"fields": ["name", "state"], "limit": 1, "order": "id desc"})
    return filas[0] if filas else None


def asegurar_orden(ref, autor=""):
    """La orden de compra de esta compra, creada y confirmada en Odoo.

    `{"ok", "error", "orden": {"id", "nombre", "estado"}, "ya_estaba"}`.

    Hace las dos mitades y es **idempotente en las dos**, que es lo que
    hace seguro el botón de reintentar:

    - si la compra todavía no tiene orden, la busca por su `origin` antes de
      crear nada y la crea solo si de verdad no existe;
    - si la orden existe pero quedó en borrador (se cortó entre crear y
      confirmar), la confirma.

    Confirmar no es un extra: es lo que hace que Odoo cree solo la entrada
    de stock que después se recibe. Y no toca ni un centavo — no factura ni
    paga nada, igual que `action_confirm` de una venta.
    """
    compra = compras.uno(ref)
    if compra is None:
        return _sin_orden(compras.mensaje_compra_ausente(ref))
    ref = compra["ref"]
    falta = falta_para_pedir(ref, compra=compra)
    if falta:
        return _sin_orden(falta)
    if not ventas.configurado():
        return _sin_orden("Odoo no está conectado en este servidor, así que "
                          "la orden de compra no se puede crear.")

    guardada = compra.get("orden_compra_id")
    ya_estaba = bool(guardada)
    try:
        if guardada:
            leida = ventas._ejecutar("purchase.order", "read",
                                     [[int(guardada)]],
                                     {"fields": ["name", "state"]})
            if not leida:
                # El número que teníamos no existe en Odoo: la borraron, o
                # es de otra época. Se vuelve a empezar en vez de quedarse
                # mirando un id muerto.
                guardada, ya_estaba = None, False
            else:
                orden = {"id": int(guardada), "nombre": leida[0].get("name") or "",
                         "estado": leida[0].get("state") or ""}
        if not guardada:
            encontrada = _orden_existente(ref)
            if encontrada is not None:
                ya_estaba = True
                orden = {"id": encontrada["id"],
                         "nombre": encontrada.get("name") or "",
                         "estado": encontrada.get("state") or ""}
            else:
                orden = _crear_en_odoo(ref, compra)
        # El número se guarda ANTES de confirmar: si confirmar falla, la
        # orden ya existe en Odoo y el reintento tiene que encontrarla por
        # su id en vez de crear otra.
        compras.guardar_orden(ref, orden["id"], orden["nombre"])
        if orden["estado"] not in ESTADOS_PEDIDA:
            orden = _confirmar(orden)
    except Exception as error:
        return _sin_orden(compras._error(error))

    if orden["estado"] == ESTADO_POR_APROBAR:
        return {"ok": False, "ya_estaba": ya_estaba, "orden": orden,
                "error": (f"La orden {orden['nombre']} quedó esperando "
                          f"aprobación en Odoo, así que todavía no tiene su "
                          f"entrada de stock. Hay que aprobarla allá.")}
    if orden["estado"] not in ESTADOS_PEDIDA:
        return {"ok": False, "ya_estaba": ya_estaba, "orden": orden,
                "error": (f"La orden {orden['nombre']} se creó en Odoo pero "
                          f"no quedó confirmada (está en «{orden['estado']}»). "
                          f"Hay que confirmarla allá.")}
    if not ya_estaba:
        _comentar(compra, f"Orden de compra **{orden['nombre']}** creada en "
                          f"Odoo con los productos de esta compra.", autor)
    return {"ok": True, "error": "", "orden": orden, "ya_estaba": ya_estaba}


def _crear_en_odoo(ref, compra):
    """La `purchase.order` nueva. Devuelve {id, nombre, estado}."""
    # Los dos datos se vuelven a exigir ACÁ, aunque `falta_para_pedir` ya
    # los haya mirado: ese chequeo se saltea cuando la compra tiene un
    # número de orden guardado, y si ese número resultó muerto en Odoo el
    # camino llega hasta acá. Sin esto, una compra sin productos crearía una
    # orden VACÍA en Odoo, que es basura que alguien tendría que ir a
    # borrar.
    lineas = compras.lineas_de(ref)
    if not lineas or not compra.get("proveedor_id"):
        raise ErrorOrden(falta_para_pedir(ref, compra=dict(
            compra, orden_compra_id=None)))
    ordenes, error = _lineas_para_odoo(lineas)
    if error:
        raise ErrorOrden(error)
    valores = {
        "partner_id": int(compra["proveedor_id"]),
        # De dónde salió, y la llave del reintento (ver `_orden_existente`).
        "origin": _origen(ref),
        "order_line": ordenes,
    }
    nueva = ventas._ejecutar("purchase.order", "create", [valores])
    if isinstance(nueva, list):
        nueva = nueva[0] if nueva else 0
    if not nueva:
        raise ErrorOrden("Odoo no dijo el número de la orden de compra.")
    leida = ventas._ejecutar("purchase.order", "read", [[int(nueva)]],
                             {"fields": ["name", "state"]})
    fila = leida[0] if leida else {}
    return {"id": int(nueva), "nombre": fila.get("name") or "",
            "estado": fila.get("state") or "draft"}


def _confirmar(orden):
    """Confirma la orden y RELEE su estado, sin creerle a la respuesta.

    `button_confirm` puede devolver una acción de ventana que la capa
    XML-RPC no sabe serializar (el mismo caso que `_ejecutar_sin_respuesta`
    de ventas resuelve): el método SÍ corre. Por eso lo que vale es el
    estado leído de vuelta, no lo que la llamada contestó.
    """
    ventas._ejecutar_sin_respuesta("purchase.order", "button_confirm",
                                   [[orden["id"]]])
    leida = ventas._ejecutar("purchase.order", "read", [[orden["id"]]],
                             {"fields": ["name", "state"]})
    if not leida:
        return dict(orden)
    return {"id": orden["id"], "nombre": leida[0].get("name") or orden["nombre"],
            "estado": leida[0].get("state") or ""}


def al_pedir(ref, autor=""):
    """La orden de compra, pedida justo después de mover la tarjeta a
    «Pedido a proveedor». Devuelve ("aviso", "error").

    **El error de acá NO deshace el movimiento**: Linear es el tablero y
    manda sobre el estado. Lo que deja es el aviso en la pantalla y la
    marca en la tarjeta («falta la orden en Odoo»), para que la
    desincronización se vea en vez de esconderse.
    """
    resultado = asegurar_orden(ref, autor=autor)
    if not resultado["ok"]:
        return "", (f"La compra se movió, pero la orden de compra no quedó "
                    f"en Odoo: {resultado['error']} Se puede reintentar "
                    f"desde la tarjeta.")
    orden = resultado["orden"]
    if resultado["ya_estaba"]:
        return f"La orden {orden['nombre']} ya estaba en Odoo.", ""
    return f"Orden de compra {orden['nombre']} creada en Odoo.", ""


def _comentar(compra, texto, autor=""):
    """El comentario firmado en el issue. Es un EXTRA: si Linear no lo
    guarda, lo que pasó en Odoo ya pasó y decir lo contrario sería
    mentir."""
    try:
        linear_leads.comentar(compra["id"], texto, autor=autor)
    except Exception as fallo:
        compras.registro_aviso(f"La compra {compra['ref']}: el comentario no "
                               f"se pudo guardar en Linear: {fallo}")


# ---------------------------------------------------------------------------
# Recibir: la entrada de stock de Odoo, validada con lo que llegó bueno
# ---------------------------------------------------------------------------

def _num(valor, defecto=0.0):
    """Un número que se pueda recibir: cero o más. Lo ilegible cae en
    `defecto` en vez de reventar la pantalla."""
    crudo = str(valor if valor is not None else "").strip()
    if not crudo:
        return defecto
    numero = ventas._num_positivo(crudo, defecto=None, permitir_cero=True)
    if numero is None or numero < 0:
        return defecto
    return min(float(numero), 100000.0)


def _sin_recepcion(error):
    return {"ok": False, "error": error, "orden": None, "renglones": [],
            "picking": None, "cerrada": False, "danado": []}


def recepcion(ref):
    """Las líneas de la entrada de stock que está abierta, para la pantalla
    de recibir.

    `{"ok", "error", "orden", "renglones", "picking", "cerrada", "danado"}`.

    Cada renglón es un MOVIMIENTO de la entrada abierta y no una línea de la
    orden, a propósito: el movimiento es lo que se escribe al validar, y
    leer y escribir por el mismo id es lo que evita la clase de bug que ya
    costó caro (escribir con una llave y leer con otra, sin que la
    contradicción se note nunca). Los totales de la orden —cuánto se pidió
    en total y cuánto se recibió ya— se le pegan al renglón por su producto,
    solo para mostrar.

    `cerrada` True es «Odoo ya no tiene nada por recibir de esta orden», que
    es «no hay», no «no sé».
    """
    compra = compras.uno(ref)
    if compra is None:
        return _sin_recepcion(compras.mensaje_compra_ausente(ref))
    if not compra.get("orden_compra_id"):
        return _sin_recepcion(
            f"{compra['ref']} todavía no tiene orden de compra en Odoo, así "
            f"que no hay entrada que recibir. Se crea al pasar la compra a "
            f"«Pedido».")
    if not ventas.configurado():
        return _sin_recepcion("Odoo no está conectado en este servidor, así "
                              "que la entrada no se puede recibir.")
    try:
        datos = _leer_recepcion(compra)
    except Exception as error:
        return _sin_recepcion(compras._error(error))
    return {**datos, "ok": True, "error": "",
            "danado": danado_de(compra["ref"])}


def _leer_recepcion(compra):
    """Lo que Odoo dice de la orden y de su entrada abierta."""
    orden_id = int(compra["orden_compra_id"])
    leida = ventas._ejecutar("purchase.order", "read", [[orden_id]],
                             {"fields": CAMPOS_ORDEN})
    if not leida:
        raise ErrorOrden(
            f"La orden de compra que {compra['ref']} tiene guardada ya no "
            f"está en Odoo.")
    orden_fila = leida[0]
    orden = {"id": orden_id, "nombre": orden_fila.get("name") or "",
             "estado": orden_fila.get("state") or "",
             "total": orden_fila.get("amount_total"),
             "impuesto": orden_fila.get("amount_tax")}

    # Los totales de la orden, por producto: cuánto se pidió y cuánto Odoo
    # dice que ya se recibió. Es información de pantalla.
    por_producto = {}
    ids_linea = list(orden_fila.get("order_line") or [])
    if ids_linea:
        for fila in ventas._ejecutar("purchase.order.line", "read",
                                     [ids_linea],
                                     {"fields": CAMPOS_LINEA_ORDEN}):
            producto_id = _id_de(fila.get("product_id"))
            acumulado = por_producto.setdefault(
                producto_id, {"pedido": 0.0, "recibido": 0.0})
            acumulado["pedido"] += float(fila.get("product_qty") or 0.0)
            acumulado["recibido"] += float(fila.get("qty_received") or 0.0)

    picking, movimientos = _entrada_abierta(orden_fila.get("picking_ids") or [])
    renglones = []
    for movimiento in movimientos:
        producto_id = _id_de(movimiento.get("product_id"))
        totales = por_producto.get(producto_id) or {"pedido": None,
                                                    "recibido": None}
        esperado = float(movimiento.get("product_uom_qty") or 0.0)
        renglones.append({
            "movimiento": movimiento["id"],
            "producto_id": producto_id,
            "nombre": _nombre_de(movimiento.get("product_id")),
            "esperado": esperado,
            "esperado_texto": compras._cantidad_bonita(esperado),
            "pedido": totales["pedido"],
            "pedido_texto": (None if totales["pedido"] is None
                             else compras._cantidad_bonita(totales["pedido"])),
            "recibido": totales["recibido"],
            "recibido_texto": (None if totales["recibido"] is None
                               else compras._cantidad_bonita(totales["recibido"])),
        })
    return {"orden": orden, "renglones": renglones,
            "picking": (picking or {}).get("id"),
            "cerrada": picking is None}


def _entrada_abierta(ids_picking):
    """(picking, movimientos) de la entrada que todavía está abierta, o
    (None, []) si Odoo ya no tiene nada por recibir.

    Se queda con la PRIMERA abierta: una orden recibida por partes va
    dejando un pendiente por vez, y recibir de a una entrada es justo lo
    que se quiere — la próxima se recibe en el próximo viaje.
    """
    ids_picking = [int(i) for i in ids_picking if i]
    if not ids_picking:
        return None, []
    pickings = ventas._ejecutar(
        "stock.picking", "read", [ids_picking], {"fields": CAMPOS_PICKING})
    abiertos = [p for p in pickings
                if (p.get("state") or "") not in ("done", "cancel")]
    if not abiertos:
        return None, []
    picking = sorted(abiertos, key=lambda p: p["id"])[0]
    movimientos = ventas._ejecutar(
        "stock.move", "search_read", [[["picking_id", "=", picking["id"]]]],
        {"fields": CAMPOS_MOVIMIENTO, "order": "id"})
    vivos = [m for m in movimientos
             if (m.get("state") or "") not in ("done", "cancel")]
    return picking, vivos


def _id_de(campo):
    """El id de un campo many2one leído por XML-RPC, que llega como
    `[id, nombre]` (o False cuando está vacío)."""
    if isinstance(campo, (list, tuple)) and campo:
        return int(campo[0])
    return None


def _nombre_de(campo):
    if isinstance(campo, (list, tuple)) and len(campo) > 1:
        return str(campo[1] or "")
    return ""


def recibir(ref, llegadas=None, danadas=None, autor=""):
    """Valida la entrada de Odoo con lo que llegó bueno y anota lo dañado.

    `llegadas` y `danadas` llegan como `{id_de_movimiento: cantidad}` —el
    número del renglón va en el nombre del campo, porque un formulario sin
    JavaScript no puede mandar una lista de objetos.

    `{"ok", "error", "aviso", "pendiente", "entraron", "danadas"}`.

    Las tres reglas, y las tres son del dueño:

    - **lo que entra al stock es lo que llegó BUENO** (llegó − dañadas): el
      `quantity` del movimiento se escribe con eso y no con lo que se pidió;
    - **lo dañado no entra**, y queda anotado en la app;
    - **lo que faltó queda pendiente** en la orden de compra, que es como
      Odoo ya lo maneja. No se cierra nada a la fuerza, así que la
      recepción se puede repetir cuando llegue el resto.
    """
    estado = recepcion(ref)
    if not estado["ok"]:
        return {"ok": False, "error": estado["error"], "aviso": "",
                "pendiente": None, "entraron": 0.0, "danadas": 0.0}
    if estado["cerrada"]:
        return {"ok": False, "aviso": "", "pendiente": None,
                "entraron": 0.0, "danadas": 0.0,
                "error": (f"La orden {estado['orden']['nombre']} ya no tiene "
                          f"nada por recibir en Odoo.")}

    cuentas, error = _cuentas_de(estado["renglones"], llegadas, danadas)
    if error:
        return {"ok": False, "error": error, "aviso": "", "pendiente": None,
                "entraron": 0.0, "danadas": 0.0}

    compra = compras.uno(ref)
    try:
        for cuenta in cuentas:
            # `picked` dice si alguien ya tocó este movimiento: en cero va
            # en False, para que Odoo no lo tome por procesado y lo deje
            # entero en el pendiente.
            ventas._ejecutar(
                "stock.move", "write",
                [[cuenta["movimiento"]], {"quantity": cuenta["bueno"],
                                          "picked": cuenta["bueno"] > 0}])
        _validar_entrada(estado["picking"])
        pendiente = _pendiente_de(compra)
    except Exception as fallo:
        return {"ok": False, "aviso": "", "pendiente": None,
                "entraron": 0.0, "danadas": 0.0,
                "error": (f"No se pudo registrar la entrada en Odoo: "
                          f"{compras._error(fallo)}")}

    # Lo dañado se anota DESPUÉS de que Odoo aceptó: si la entrada no se
    # pudo validar, nada llegó y anotar roturas de una recepción que no
    # pasó dejaría un número que nadie puede explicar.
    #
    # El SKU sale de las líneas de la compra, cruzadas por producto: el
    # movimiento de Odoo trae el id y el nombre del producto pero no la
    # referencia, y el SKU es lo durable para el reclamo de después. Si no
    # calza queda vacío, que no pierde nada — lo que importa de lo dañado
    # es el nombre y la cantidad.
    sku_por_producto = {l["producto_id"]: l["sku"]
                        for l in compras.lineas_de(compra["ref"])
                        if l.get("producto_id")}
    _anotar_danado(
        compra["ref"],
        [(sku_por_producto.get(cuenta["producto_id"], ""), cuenta["nombre"],
          cuenta["danadas"]) for cuenta in cuentas], quien=autor)

    entraron = round(sum(c["bueno"] for c in cuentas), 2)
    rotas = round(sum(c["danadas"] for c in cuentas), 2)
    aviso = _texto_recepcion(estado["orden"]["nombre"], entraron, rotas,
                             pendiente)
    _comentar(compra, _texto_comentario(entraron, rotas, pendiente), autor)
    return {"ok": True, "error": "", "aviso": aviso, "pendiente": pendiente,
            "entraron": entraron, "danadas": rotas}


def _cuentas_de(renglones, llegadas, danadas):
    """(cuentas, error) — cuánto llegó y cuánto dañado por movimiento, ya
    validado.

    Dos candados, y los dos son errores de dedo que de otro modo quedarían
    guardados en Odoo:

    - **dañadas no puede pasar de lo que llegó**: «llegaron 10, 12 rotas» no
      quiere decir nada y restar daría negativo;
    - **algo tiene que haber llegado**: validar una entrada con todo en cero
      la cerraría sin que entrara nada, y el pendiente quedaría igual.
    """
    llegadas = llegadas or {}
    danadas = danadas or {}
    cuentas = []
    for renglon in renglones:
        clave = str(renglon["movimiento"])
        llego = _num(llegadas.get(clave, llegadas.get(renglon["movimiento"])))
        roto = _num(danadas.get(clave, danadas.get(renglon["movimiento"])))
        if roto > llego:
            return [], (f"De «{renglon['nombre']}» anotaste "
                        f"{compras._cantidad_bonita(roto)} dañadas pero solo "
                        f"{compras._cantidad_bonita(llego)} que llegaron. "
                        f"Las dañadas son parte de lo que llegó.")
        cuentas.append({"movimiento": renglon["movimiento"],
                        "nombre": renglon["nombre"],
                        "producto_id": renglon["producto_id"],
                        "llego": llego, "danadas": roto,
                        "bueno": round(llego - roto, 2)})
    if not cuentas:
        return [], "Esta entrada no tiene renglones que recibir."
    if sum(c["llego"] for c in cuentas) <= 0:
        return [], ("No anotaste nada que llegó. Si el camión no vino, dejá "
                    "la compra en «En camino» y recibila cuando llegue.")
    return cuentas, ""


def _validar_entrada(picking_id):
    """Valida el `stock.picking`, que es lo que hace subir el stock.

    `button_validate` puede devolver una acción de ventana que XML-RPC no
    sabe serializar; el método SÍ corre (es el caso que
    `ventas._ejecutar_sin_respuesta` documenta). Con `skip_backorder` en el
    contexto, Odoo crea solo la entrada pendiente con lo que no llegó en vez
    de abrir su asistente — y ese pendiente es justo lo que se quiere.

    **Y después se RELEE el estado, sin creerle a la respuesta.** Es el
    candado que importa de verdad acá: si algún día ese nombre de contexto
    cambiara, Odoo devolvería su asistente, la respuesta se tragaría como
    «acción de ventana» y la entrada se quedaría sin validar **en
    silencio** — el stock no subiría y la pantalla diría que sí. Leer el
    estado de vuelta convierte ese silencio en un error que se ve.
    """
    picking_id = int(picking_id)
    ventas._ejecutar_sin_respuesta("stock.picking", "button_validate",
                                   [[picking_id]],
                                   {"context": dict(CONTEXTO_VALIDAR)})
    leido = ventas._ejecutar("stock.picking", "read", [[picking_id]],
                             {"fields": ["name", "state"]})
    estado = (leido[0] if leido else {}).get("state") or ""
    if estado != "done":
        raise ErrorOrden(
            f"Odoo no dio por validada la entrada "
            f"{(leido[0] if leido else {}).get('name') or picking_id}: quedó "
            f"en «{estado or 'sin estado'}», así que el stock NO subió.")


def _pendiente_de(compra):
    """Cuántas unidades le quedan por recibir a la orden, leídas de vuelta
    de Odoo, o None si no se pudo saber.

    **None es «no se sabe» y nunca 0**: decir «no queda nada» porque la
    lectura falló mandaría a nadie a esperar el resto del camión.
    """
    try:
        leida = ventas._ejecutar("purchase.order", "read",
                                 [[int(compra["orden_compra_id"])]],
                                 {"fields": ["order_line"]})
        ids_linea = list((leida[0] if leida else {}).get("order_line") or [])
        if not ids_linea:
            return None
        falta = 0.0
        for fila in ventas._ejecutar("purchase.order.line", "read",
                                     [ids_linea],
                                     {"fields": ["product_qty", "qty_received"]}):
            falta += max(0.0, float(fila.get("product_qty") or 0.0)
                         - float(fila.get("qty_received") or 0.0))
        return round(falta, 2)
    except Exception as error:
        compras.registro_aviso(f"No se pudo leer lo que queda por recibir de "
                               f"la orden de {compra['ref']}: "
                               f"{compras._error(error)}")
        return None


def _texto_recepcion(orden, entraron, rotas, pendiente):
    """El aviso de la pantalla, armado en Python como todo lo que se pinta.

    Dice las tres cosas por separado y **nunca da por cerrado lo que no
    sabe**: con `pendiente` en None el renglón del pendiente no sale.
    """
    partes = [f"{orden}: entraron {compras._cantidad_bonita(entraron)} al "
              f"stock"]
    if rotas:
        partes.append(f"{compras._cantidad_bonita(rotas)} dañadas no entraron")
    if pendiente is None:
        partes.append("no se pudo leer cuánto queda por recibir")
    elif pendiente > 0:
        partes.append(f"quedan {compras._cantidad_bonita(pendiente)} por "
                      f"recibir")
    else:
        partes.append("no queda nada por recibir")
    return " · ".join(partes) + "."


def _texto_comentario(entraron, rotas, pendiente):
    partes = [f"Entrada registrada: **{compras._cantidad_bonita(entraron)}** "
              f"al stock"]
    if rotas:
        partes.append(f"**{compras._cantidad_bonita(rotas)}** dañadas (no "
                      f"entraron al stock)")
    if pendiente is not None and pendiente > 0:
        partes.append(f"quedan **{compras._cantidad_bonita(pendiente)}** por "
                      f"recibir")
    return ". ".join(partes) + "."
