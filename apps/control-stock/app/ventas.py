"""Pestaña "Crear Venta": ventas locales directas contra Odoo por XML-RPC.

Flujo COMPLETAMENTE aparte del de Super Extra: aquí la empleada arma un
carrito y la app crea la orden en el diario de ventas normal (el default de
"Facturas de cliente"), con la etiqueta LOCAL. El diario "Ventas Super
Extra" y todo el circuito stock-proxy/order-api no se tocan.

Es la única excepción a la regla "la app nunca toca Odoo directo" (el
resto lee por stock-proxy y ajusta por order-api): decisión explícita del
dueño para esta pestaña. Sin ODOO_URL/ODOO_DB/ODOO_USER/ODOO_PASSWORD
la pestaña muestra un aviso y no ofrece acciones.

El cobro va por pasos y cada paso sellado se guarda en SQLite ANTES de
intentar el siguiente: si Odoo falla a la mitad, el historial refleja el
estado real (ej. "confirmada, factura pendiente") y el botón Reintentar
continúa desde ahí sin duplicar nada (la factura se reutiliza si ya existe).

Los productos facturan por cantidad ENTREGADA (invoice_policy delivery),
así que el cobro valida primero la salida de inventario — correcto además
para una venta local: el cliente se lleva las plantas en el momento.
"""

import base64
import json
import os
import sqlite3
import time
import xmlrpc.client
from datetime import datetime

from .datos import ZONA_PANAMA, _db

# Estados del registro local, en orden. Cada uno es un paso YA logrado en
# Odoo; el siguiente paso solo corre si el anterior quedó sellado.
ESTADOS = ("cotizacion", "confirmada", "entregada", "facturada", "pagado")

ETIQUETAS_ESTADO = {
    "cotizacion": "Cotización",
    "confirmada": "Confirmada · factura pendiente",
    "entregada": "Entregada · factura pendiente",
    "facturada": "Facturada · pago pendiente",
    "pagado": "Pagado",
}

TTL_FOTOS = 24 * 3600


# ---------------------------------------------------------------------------
# Conexión XML-RPC (una sola puerta: _ejecutar, que las pruebas reemplazan)
# ---------------------------------------------------------------------------

_conexion = {"uid": None, "modelos": None}


def configurado():
    return all(os.environ.get(v) for v in ("ODOO_URL", "ODOO_DB", "ODOO_USER", "ODOO_PASSWORD"))


def reiniciar_conexion():
    """Solo para pruebas."""
    _conexion["uid"] = None
    _conexion["modelos"] = None


def _autenticar():
    url = os.environ["ODOO_URL"].rstrip("/")
    comun = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common", allow_none=True)
    uid = comun.authenticate(
        os.environ["ODOO_DB"], os.environ["ODOO_USER"], os.environ["ODOO_PASSWORD"], {})
    if not uid:
        raise RuntimeError("Odoo rechazó las credenciales")
    _conexion["uid"] = uid
    _conexion["modelos"] = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object", allow_none=True)


def _ejecutar(modelo, metodo, args, kw=None):
    if _conexion["uid"] is None:
        _autenticar()
    return _conexion["modelos"].execute_kw(
        os.environ["ODOO_DB"], _conexion["uid"], os.environ["ODOO_PASSWORD"],
        modelo, metodo, args, kw or {})


def _ejecutar_sin_respuesta(modelo, metodo, args, kw=None):
    """Para métodos de Odoo que devuelven una acción de ventana con None
    adentro, que la capa XML-RPC no puede serializar: el método SÍ corre y
    confirma en la base, solo la respuesta revienta. Quien llama debe
    verificar el efecto real leyendo de vuelta."""
    try:
        _ejecutar(modelo, metodo, args, kw)
    except xmlrpc.client.Fault as falla:
        if "cannot marshal None" not in str(falla.faultString):
            raise


def _id_config(nombre):
    """Los ids fijos creados con el OK del dueño (diarios, etiqueta, cliente)."""
    return int(os.environ[nombre])


def diario_de(metodo):
    return _id_config("VENTA_DIARIO_YAPPY" if metodo == "yappy" else "VENTA_DIARIO_EFECTIVO")


# ---------------------------------------------------------------------------
# Catálogo: búsqueda en vivo y fotos cacheadas en disco
# ---------------------------------------------------------------------------

def buscar_productos(texto):
    """Productos PL- por nombre o SKU, con precio de Odoo (máx. 20)."""
    texto = (texto or "").strip()
    if not texto:
        return []
    dominio = [
        ["default_code", "like", "PL-"], ["sale_ok", "=", True],
        "|", ["name", "ilike", texto], ["default_code", "ilike", texto],
    ]
    filas = _ejecutar("product.product", "search_read", [dominio],
                      {"fields": ["default_code", "name", "list_price"],
                       "limit": 20, "order": "name"})
    return [{"id": f["id"], "sku": f["default_code"], "nombre": f["name"],
             "precio": f["list_price"]} for f in filas]


def productos_por_id(ids):
    """{id: {sku, nombre, precio}} leído fresco de Odoo (los totales del
    carrito y de la orden salen SIEMPRE de aquí, nunca de lo que vio antes
    el navegador)."""
    if not ids:
        return {}
    filas = _ejecutar("product.product", "read", [list(ids)],
                      {"fields": ["default_code", "name", "list_price"]})
    return {f["id"]: {"sku": f["default_code"], "nombre": f["name"],
                      "precio": f["list_price"]} for f in filas}


def _dir_fotos():
    ruta = os.environ.get(
        "VENTA_FOTOS_DIR",
        os.path.join(os.path.dirname(__file__), "..", "fotos_productos"))
    os.makedirs(ruta, exist_ok=True)
    return ruta


def foto_producto(producto_id):
    """(bytes, tipo_mime) de image_128, cacheada en disco con TTL de 24h.
    None si el producto no tiene foto (también se cachea la ausencia para
    no preguntar a Odoo en cada render)."""
    ruta = os.path.join(_dir_fotos(), f"{int(producto_id)}.bin")
    if os.path.exists(ruta) and time.time() - os.path.getmtime(ruta) < TTL_FOTOS:
        with open(ruta, "rb") as archivo:
            contenido = archivo.read()
        return _como_foto(contenido)
    try:
        filas = _ejecutar("product.product", "read", [[int(producto_id)]],
                          {"fields": ["image_128"]})
    except Exception:
        # Sin Odoo se sirve lo que haya en disco aunque esté vencido.
        if os.path.exists(ruta):
            with open(ruta, "rb") as archivo:
                return _como_foto(archivo.read())
        return None
    crudo = filas and filas[0].get("image_128")
    contenido = base64.b64decode(crudo) if crudo else b""
    with open(ruta, "wb") as archivo:
        archivo.write(contenido)
    return _como_foto(contenido)


def _como_foto(contenido):
    if not contenido:
        return None
    tipo = "image/jpeg" if contenido[:2] == b"\xff\xd8" else "image/png"
    return contenido, tipo


# ---------------------------------------------------------------------------
# Carrito por empleada, en SQLite (server-side: nada de precios del cliente)
# ---------------------------------------------------------------------------

def iniciar_tablas():
    with _db() as con:
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS venta_carrito (
                usuario TEXT NOT NULL,
                producto_id INTEGER NOT NULL,
                cantidad INTEGER NOT NULL,
                PRIMARY KEY (usuario, producto_id)
            );
            CREATE TABLE IF NOT EXISTS venta_borrador (
                usuario TEXT PRIMARY KEY,   -- cliente del formulario en curso
                nombre TEXT NOT NULL DEFAULT '',
                celular TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS ventas_locales (
                n INTEGER PRIMARY KEY AUTOINCREMENT,
                creado_en TEXT NOT NULL,
                empleada TEXT NOT NULL,
                cliente TEXT NOT NULL,
                celular TEXT,
                orden_id INTEGER,
                orden TEXT,
                factura_id INTEGER,
                factura TEXT,
                total REAL,
                metodo TEXT,                -- yappy | efectivo | NULL
                estado TEXT NOT NULL,       -- ver ESTADOS
                ultimo_error TEXT
            );
            """
        )
        # Migración suave: la tabla pudo nacer sin la columna celular.
        columnas = [fila[1] for fila in con.execute("PRAGMA table_info(ventas_locales)")]
        if "celular" not in columnas:
            con.execute("ALTER TABLE ventas_locales ADD COLUMN celular TEXT")


def guardar_borrador(usuario, nombre, celular):
    """El nombre/celular del formulario en curso: sobrevive a los reloads de
    agregar/quitar plantas (venta.js lo manda mientras se escribe)."""
    with _db() as con:
        con.execute(
            "INSERT INTO venta_borrador (usuario, nombre, celular) VALUES (?,?,?)"
            " ON CONFLICT (usuario) DO UPDATE SET nombre=?, celular=?",
            (usuario, nombre, celular, nombre, celular))


def borrador_de(usuario):
    with _db() as con:
        fila = con.execute("SELECT nombre, celular FROM venta_borrador WHERE usuario=?",
                           (usuario,)).fetchone()
    return {"nombre": fila["nombre"], "celular": fila["celular"]} if fila \
        else {"nombre": "", "celular": ""}


def _limpiar_borrador(usuario):
    with _db() as con:
        con.execute("DELETE FROM venta_borrador WHERE usuario=?", (usuario,))


def carrito_de(usuario):
    """[{producto_id, cantidad, sku, nombre, precio, importe}] con precios
    frescos de Odoo, más el total. Un producto que ya no existe en Odoo se
    descarta del carrito en silencio."""
    with _db() as con:
        filas = con.execute(
            "SELECT producto_id, cantidad FROM venta_carrito WHERE usuario=? ORDER BY rowid",
            (usuario,)).fetchall()
    if not filas:
        return [], 0.0
    datos_odoo = productos_por_id([f["producto_id"] for f in filas])
    lineas = []
    for fila in filas:
        producto = datos_odoo.get(fila["producto_id"])
        if producto is None:
            quitar_del_carrito(usuario, fila["producto_id"])
            continue
        lineas.append({
            "producto_id": fila["producto_id"], "cantidad": fila["cantidad"],
            **producto, "importe": round(fila["cantidad"] * producto["precio"], 2),
        })
    return lineas, round(sum(l["importe"] for l in lineas), 2)


def agregar_al_carrito(usuario, producto_id, cantidad):
    cantidad = max(1, min(int(cantidad), 999))
    with _db() as con:
        con.execute(
            "INSERT INTO venta_carrito (usuario, producto_id, cantidad) VALUES (?,?,?)"
            " ON CONFLICT (usuario, producto_id) DO UPDATE SET cantidad = cantidad + ?",
            (usuario, int(producto_id), cantidad, cantidad))


def cambiar_cantidad(usuario, producto_id, cantidad):
    cantidad = int(cantidad)
    if cantidad <= 0:
        quitar_del_carrito(usuario, producto_id)
        return
    with _db() as con:
        con.execute(
            "UPDATE venta_carrito SET cantidad=? WHERE usuario=? AND producto_id=?",
            (min(cantidad, 999), usuario, int(producto_id)))


def quitar_del_carrito(usuario, producto_id):
    with _db() as con:
        con.execute("DELETE FROM venta_carrito WHERE usuario=? AND producto_id=?",
                    (usuario, int(producto_id)))


def vaciar_carrito(usuario):
    with _db() as con:
        con.execute("DELETE FROM venta_carrito WHERE usuario=?", (usuario,))


# ---------------------------------------------------------------------------
# Registro local de ventas
# ---------------------------------------------------------------------------

def _ahora():
    return datetime.now(ZONA_PANAMA).isoformat()


def ventas_todas():
    with _db() as con:
        filas = con.execute("SELECT * FROM ventas_locales ORDER BY n DESC").fetchall()
    return [dict(f) for f in filas]


def obtener_venta(n):
    with _db() as con:
        fila = con.execute("SELECT * FROM ventas_locales WHERE n=?", (n,)).fetchone()
    return dict(fila) if fila else None


def _actualizar_venta(n, **campos):
    columnas = ", ".join(f"{c}=?" for c in campos)
    with _db() as con:
        con.execute(f"UPDATE ventas_locales SET {columnas} WHERE n=?",
                    (*campos.values(), n))


# ---------------------------------------------------------------------------
# Flujo contra Odoo
# ---------------------------------------------------------------------------

def _cliente_id(nombre, celular=""):
    """El partner para la orden: el genérico "Cliente Local" si no dieron
    nombre; si lo dieron, se busca por nombre exacto y se crea si no existe
    (con el celular como móvil del contacto)."""
    nombre = (nombre or "").strip()
    if not nombre:
        return _id_config("VENTA_CLIENTE_LOCAL")
    ids = _ejecutar("res.partner", "search", [[["name", "=ilike", nombre]]], {"limit": 1})
    if ids:
        return ids[0]
    valores = {"name": nombre, "customer_rank": 1, "company_type": "person"}
    if (celular or "").strip():
        valores["mobile"] = celular.strip()
    return _ejecutar("res.partner", "create", [valores])


def crear_cotizacion(empleada, nombre_cliente, celular=""):
    """Crea el sale.order borrador (etiqueta LOCAL, diario de ventas normal)
    y el registro local. Devuelve el registro. El carrito y el borrador se
    limpian solo si Odoo aceptó la orden."""
    usuario = empleada["id"]
    lineas, _total_visto = carrito_de(usuario)
    if not lineas:
        raise ValueError("Agrega al menos una planta a la venta.")
    partner = _cliente_id(nombre_cliente, celular)
    orden_id = _ejecutar("sale.order", "create", [{
        "partner_id": partner,
        "tag_ids": [[6, 0, [_id_config("VENTA_TAG_LOCAL")]]],
        # Sin price_unit: el precio lo pone Odoo (lista de precios vigente).
        "order_line": [[0, 0, {"product_id": l["producto_id"],
                               "product_uom_qty": l["cantidad"]}]
                       for l in lineas],
    }])
    if isinstance(orden_id, list):
        orden_id = orden_id[0]
    leido = _ejecutar("sale.order", "read", [[orden_id]],
                      {"fields": ["name", "amount_total"]})[0]
    with _db() as con:
        cursor = con.execute(
            "INSERT INTO ventas_locales (creado_en, empleada, cliente, celular,"
            " orden_id, orden, total, estado) VALUES (?,?,?,?,?,?,?, 'cotizacion')",
            (_ahora(), empleada["nombre"], (nombre_cliente or "").strip() or "Cliente Local",
             (celular or "").strip() or None,
             orden_id, leido["name"], leido["amount_total"]))
        n = cursor.lastrowid
    vaciar_carrito(usuario)
    _limpiar_borrador(usuario)
    return obtener_venta(n)


def _confirmar_orden(venta):
    _ejecutar("sale.order", "action_confirm", [[venta["orden_id"]]])
    total = _ejecutar("sale.order", "read", [[venta["orden_id"]]],
                      {"fields": ["amount_total"]})[0]["amount_total"]
    _actualizar_venta(venta["n"], estado="confirmada", total=total)


def _entregar_orden(venta):
    """Valida las salidas de la orden con todo entregado: la venta local se
    lleva en el momento, y sin entrega Odoo no deja facturar (los productos
    facturan por cantidad entregada)."""
    pickings = _ejecutar("stock.picking", "search_read",
                         [[["sale_id", "=", venta["orden_id"]],
                           ["state", "not in", ["done", "cancel"]]]],
                         {"fields": ["state"]})
    for picking in pickings:
        movimientos = _ejecutar("stock.move", "search_read",
                                [[["picking_id", "=", picking["id"]]]],
                                {"fields": ["product_uom_qty"]})
        for movimiento in movimientos:
            _ejecutar("stock.move", "write",
                      [[movimiento["id"]],
                       {"quantity": movimiento["product_uom_qty"], "picked": True}])
        _ejecutar("stock.picking", "button_validate", [[picking["id"]]])
    _actualizar_venta(venta["n"], estado="entregada")


def _facturar_orden(venta):
    """Crea (o reutiliza) la factura de la orden y la publica. Reutilizar es
    lo que hace al reintento seguro: si el intento anterior creó la factura
    pero no llegó a publicarla, no se crea otra."""
    orden = _ejecutar("sale.order", "read", [[venta["orden_id"]]],
                      {"fields": ["invoice_ids"]})[0]
    factura_id = None
    for candidata in _ejecutar("account.move", "read", [orden["invoice_ids"]],
                               {"fields": ["state"]}) if orden["invoice_ids"] else []:
        if candidata["state"] != "cancel":
            factura_id = candidata["id"]
            estado_factura = candidata["state"]
            break
    if factura_id is None:
        contexto = {"active_model": "sale.order", "active_ids": [venta["orden_id"]],
                    "active_id": venta["orden_id"]}
        asistente = _ejecutar("sale.advance.payment.inv", "create",
                              [{"advance_payment_method": "delivered"}],
                              {"context": contexto})
        if isinstance(asistente, list):
            asistente = asistente[0]
        _ejecutar_sin_respuesta("sale.advance.payment.inv", "create_invoices",
                                [[asistente]], {"context": contexto})
        orden = _ejecutar("sale.order", "read", [[venta["orden_id"]]],
                          {"fields": ["invoice_ids"]})[0]
        if not orden["invoice_ids"]:
            raise RuntimeError("Odoo no creó la factura de la orden")
        factura_id = orden["invoice_ids"][-1]
        estado_factura = "draft"
    if estado_factura == "draft":
        _ejecutar("account.move", "action_post", [[factura_id]])
    leida = _ejecutar("account.move", "read", [[factura_id]],
                      {"fields": ["name", "amount_total"]})[0]
    _actualizar_venta(venta["n"], estado="facturada", factura_id=factura_id,
                      factura=leida["name"], total=leida["amount_total"])


def _pagar_factura(venta, metodo):
    """Registra el pago manual por el total en el diario del método. Si la
    factura ya quedó pagada (reintento tras un corte), no paga dos veces."""
    factura = _ejecutar("account.move", "read", [[venta["factura_id"]]],
                        {"fields": ["payment_state"]})[0]
    if factura["payment_state"] not in ("paid", "in_payment"):
        contexto = {"active_model": "account.move", "active_ids": [venta["factura_id"]]}
        asistente = _ejecutar("account.payment.register", "create",
                              [{"journal_id": diario_de(metodo)}], {"context": contexto})
        if isinstance(asistente, list):
            asistente = asistente[0]
        _ejecutar_sin_respuesta("account.payment.register", "action_create_payments",
                                [[asistente]], {"context": contexto})
        factura = _ejecutar("account.move", "read", [[venta["factura_id"]]],
                            {"fields": ["payment_state"]})[0]
        if factura["payment_state"] not in ("paid", "in_payment"):
            raise RuntimeError("Odoo no registró el pago de la factura")
    _actualizar_venta(venta["n"], estado="pagado", metodo=metodo)


_PASOS_COBRO = (
    ("cotizacion", _confirmar_orden),
    ("confirmada", _entregar_orden),
    ("entregada", _facturar_orden),
)


def cobrar(n, metodo):
    """Corre los pasos que falten hasta dejar la venta pagada. Devuelve el
    registro final; si un paso falla, el registro queda en el último estado
    sellado con el error guardado, y volver a llamar retoma desde ahí."""
    if metodo not in ("yappy", "efectivo"):
        raise ValueError("Método de pago desconocido.")
    venta = obtener_venta(n)
    if venta is None:
        return None
    _actualizar_venta(n, metodo=metodo, ultimo_error=None)
    try:
        for estado, paso in _PASOS_COBRO:
            venta = obtener_venta(n)
            if venta["estado"] == estado:
                paso(venta)
        venta = obtener_venta(n)
        if venta["estado"] == "facturada":
            _pagar_factura(venta, metodo)
    except Exception as error:
        _actualizar_venta(n, ultimo_error=_mensaje_de_error(error))
    return obtener_venta(n)


def _mensaje_de_error(error):
    if isinstance(error, xmlrpc.client.Fault):
        # El faultString de Odoo trae el traceback completo; la última línea
        # no vacía es el mensaje humano.
        lineas = [l.strip() for l in str(error.faultString).splitlines() if l.strip()]
        return (lineas[-1] if lineas else "Error de Odoo")[:300]
    return str(error)[:300]


# ---------------------------------------------------------------------------
# PDFs estándar de Odoo (por sesión web: el XML-RPC no renderiza reportes).
# Solo librería estándar: un opener con su jarra de cookies hace de sesión.
# ---------------------------------------------------------------------------

_sesion_web = {"abridor": None}


def _autenticar_web():
    import http.cookiejar
    import urllib.request

    abridor = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    peticion = urllib.request.Request(
        os.environ["ODOO_URL"].rstrip("/") + "/web/session/authenticate",
        data=json.dumps({"jsonrpc": "2.0", "params": {
            "db": os.environ["ODOO_DB"], "login": os.environ["ODOO_USER"],
            "password": os.environ["ODOO_PASSWORD"]}}).encode(),
        headers={"Content-Type": "application/json"})
    with abridor.open(peticion, timeout=15) as respuesta:
        cuerpo = json.load(respuesta)
    if cuerpo.get("error"):
        raise RuntimeError("Odoo rechazó las credenciales del reporte")
    _sesion_web["abridor"] = abridor


def descargar_pdf(reporte, registro_id):
    """El PDF nativo de Odoo (sale.report_saleorder / account.report_invoice).
    Reautentica una vez si la sesión web venció."""
    import urllib.error

    url = os.environ["ODOO_URL"].rstrip("/") + f"/report/pdf/{reporte}/{int(registro_id)}"
    for _intento in (1, 2):
        if _sesion_web["abridor"] is None:
            _autenticar_web()
        try:
            with _sesion_web["abridor"].open(url, timeout=60) as respuesta:
                if respuesta.headers.get("Content-Type", "").startswith("application/pdf"):
                    return respuesta.read()
        except urllib.error.HTTPError:
            pass
        _sesion_web["abridor"] = None  # sesión vencida: reintentar una vez
    raise RuntimeError("Odoo no devolvió el PDF")
