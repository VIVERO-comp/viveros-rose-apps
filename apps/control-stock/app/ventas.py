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
import re
import secrets
import sqlite3
import time
import xmlrpc.client
from datetime import datetime

from .datos import ZONA_PANAMA, _db
from . import crm_leads

# Estados del registro local, en orden. Cada uno es un paso YA logrado en
# Odoo; el siguiente paso solo corre si el anterior quedó sellado.
ESTADOS = ("cotizacion", "confirmada", "entregada", "facturada", "pagado")

ETIQUETAS_ESTADO = {
    "cotizacion": "Cotización",
    "confirmada": "Confirmada · factura pendiente",
    "entregada": "Entregada · factura pendiente",
    "facturada": "Facturada · pago pendiente",
    "pagado": "Pagado",
    "cancelada": "Cancelada",
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
# Cargos opcionales de una venta o cotización (dueño, 23/09/2026): envío a
# domicilio (si nosotros le llevamos la planta) e instalación. Cada uno es
# un producto de servicio en Odoo resuelto por su código —se crea la
# primera vez que hace falta, como el SV-PERSONALIZADO de cotizaciones— y
# entra como una línea más de la orden: así sale solo en la cotización, la
# propuesta y la factura PDF sin tocar las plantillas de los reportes.
# Sin impuestos, como todo lo del negocio (las plantas van exentas de ITBMS).
# ---------------------------------------------------------------------------

CARGOS = (
    {"clave": "envio", "codigo": "SV-ENVIO", "nombre": "Envío a domicilio"},
    # Producto PROPIO del cargo (23/09/2026). Antes esto reusaba el
    # SV-INSTALACION de las cotizaciones de servicio, que en Odoo se llama
    # "Instalación, transporte y mantenimiento inicial": ese nombre es el
    # correcto para alquiler, mantenimiento y paisajismo —ahí sí se vende
    # ese trabajo— pero la FACTURA imprime el nombre del producto, así que
    # a un cliente que solo pagó que le pusieran la planta la factura le
    # prometía transporte y mantenimiento inicial. Con producto propio la
    # cotización y la factura dicen las dos "Instalación", y las facturas
    # de servicio no cambian en nada.
    {"clave": "instalacion", "codigo": "SV-CARGO-INSTALACION", "nombre": "Instalación"},
)
CODIGOS_CARGO = {c["codigo"]: c["clave"] for c in CARGOS}
# El código viejo sigue reconociéndose al RELEER una cotización hecha antes
# del cambio: su cargo tiene que volver a su casilla del formulario y no
# aparecer como un servicio suelto.
CODIGOS_CARGO["SV-INSTALACION"] = "instalacion"
_cache_cargos = {}


def _id_producto_cargo(codigo, nombre):
    if codigo in _cache_cargos:
        return _cache_cargos[codigo]
    ids = _ejecutar("product.product", "search",
                    [[["default_code", "=", codigo]]],
                    {"limit": 1, "context": {"active_test": False}})
    producto_id = ids[0] if ids else _ejecutar("product.product", "create", [{
        "name": nombre,
        "default_code": codigo,
        "type": "service",
        "list_price": 0.0,
        "taxes_id": [[6, 0, []]],
        "invoice_policy": "order",
    }])
    if isinstance(producto_id, list):
        producto_id = producto_id[0]
    _cache_cargos[codigo] = producto_id
    return producto_id


def lineas_de_cargos(cargos):
    """Las líneas de orden de los cargos con monto (> 0); {} o montos en
    cero no agregan nada — son opcionales."""
    lineas = []
    for cargo in CARGOS:
        try:
            monto = float((cargos or {}).get(cargo["clave"]) or 0)
        except (TypeError, ValueError):
            monto = 0.0
        if monto > 0:
            lineas.append({
                "product_id": _id_producto_cargo(cargo["codigo"], cargo["nombre"]),
                "product_uom_qty": 1,
                "price_unit": round(monto, 2),
                # El nombre pelado ("Envío a domicilio", "Instalación") y
                # no la descripción de venta del producto, que en Odoo
                # promete "preparación de suelo, siembra…": el rótulo no
                # debe prometer lo que el cargo no es (rótulos honestos).
                "name": cargo["nombre"],
            })
    return lineas


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


def foto_por_sku(sku):
    """(bytes, tipo_mime) de la foto de Odoo para la pantalla de Stock, que
    solo la pide para los SKUs sin foto en Cloudinary: la imagen de la ficha
    si la tiene y, si no, el primer adjunto de imagen del producto (las fotos
    de referencia que el dueño deja en Archivos). Misma caché en disco y TTL
    que foto_producto, con clave por SKU."""
    sku = str(sku or "")
    if not re.fullmatch(r"[A-Za-z0-9._-]+", sku):
        return None
    ruta = os.path.join(_dir_fotos(), f"sku-{sku}.bin")
    if os.path.exists(ruta) and time.time() - os.path.getmtime(ruta) < TTL_FOTOS:
        with open(ruta, "rb") as archivo:
            return _como_foto(archivo.read())
    try:
        contenido = _foto_de_odoo(sku)
    except Exception:
        # Sin Odoo se sirve lo que haya en disco aunque esté vencido.
        if os.path.exists(ruta):
            with open(ruta, "rb") as archivo:
                return _como_foto(archivo.read())
        return None
    with open(ruta, "wb") as archivo:
        archivo.write(contenido)
    return _como_foto(contenido)


def _foto_de_odoo(sku):
    filas = _ejecutar("product.template", "search_read",
                      [[["default_code", "=", sku]]],
                      {"fields": ["image_128"], "limit": 1,
                       "context": {"active_test": False}})
    if not filas:
        return b""
    if filas[0].get("image_128"):
        return base64.b64decode(filas[0]["image_128"])
    adjuntos = _ejecutar("ir.attachment", "search_read",
                         [[["res_model", "=", "product.template"],
                           ["res_id", "=", filas[0]["id"]],
                           ["mimetype", "like", "image%"]]],
                         {"fields": ["datas"], "limit": 1, "order": "id"})
    if adjuntos and adjuntos[0].get("datas"):
        return base64.b64decode(adjuntos[0]["datas"])
    return b""


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
                precio REAL,                -- precio a mano; NULL = el de Odoo
                PRIMARY KEY (usuario, producto_id)
            );
            CREATE TABLE IF NOT EXISTS venta_borrador (
                usuario TEXT PRIMARY KEY,   -- cliente del formulario en curso
                nombre TEXT NOT NULL DEFAULT '',
                celular TEXT NOT NULL DEFAULT '',
                servicios TEXT,             -- JSON de los renglones de servicio
                renglones TEXT,             -- JSON de los renglones libres (personalizada)
                extra TEXT                  -- JSON de los datos opcionales del cliente
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
        # Migración suave: el carrito pudo nacer sin el precio a mano
        # (venderle más caro a un cliente, 23/09/2026).
        columnas_carrito = [fila[1] for fila in con.execute(
            "PRAGMA table_info(venta_carrito)")]
        if "precio" not in columnas_carrito:
            con.execute("ALTER TABLE venta_carrito ADD COLUMN precio REAL")
        # Migración suave: el borrador pudo nacer sin los renglones de
        # servicio (cotizaciones de servicio, 17/09/2026).
        columnas_borrador = [fila[1] for fila in con.execute(
            "PRAGMA table_info(venta_borrador)")]
        if "servicios" not in columnas_borrador:
            con.execute("ALTER TABLE venta_borrador ADD COLUMN servicios TEXT")
        if "renglones" not in columnas_borrador:
            con.execute("ALTER TABLE venta_borrador ADD COLUMN renglones TEXT")
        if "extra" not in columnas_borrador:
            con.execute("ALTER TABLE venta_borrador ADD COLUMN extra TEXT")
        # Migración suave: la tabla pudo nacer sin la columna celular.
        columnas = [fila[1] for fila in con.execute("PRAGMA table_info(ventas_locales)")]
        if "celular" not in columnas:
            con.execute("ALTER TABLE ventas_locales ADD COLUMN celular TEXT")
        # Migración suave: token del enlace público de la factura.
        if "token" not in columnas:
            con.execute("ALTER TABLE ventas_locales ADD COLUMN token TEXT")
        # Migración suave: qué compró el cliente, para la tarjeta del historial.
        if "resumen" not in columnas:
            con.execute("ALTER TABLE ventas_locales ADD COLUMN resumen TEXT")
        # Migración suave: el espejo en el CRM (22/09/2026) — referencia
        # PP-XXXXX del lead, su issue de Linear (LEAD-NN, la llave del
        # kanban Retail), el link, y la oportunidad del Flujo de Odoo.
        if "lead_ref" not in columnas:
            con.execute("ALTER TABLE ventas_locales ADD COLUMN lead_ref TEXT")
        if "lead_issue" not in columnas:
            con.execute("ALTER TABLE ventas_locales ADD COLUMN lead_issue TEXT")
        if "lead_url" not in columnas:
            con.execute("ALTER TABLE ventas_locales ADD COLUMN lead_url TEXT")
        if "oportunidad_id" not in columnas:
            con.execute("ALTER TABLE ventas_locales ADD COLUMN oportunidad_id INTEGER")
        # El lead "pendiente" de cada empleada: al tocar "Cotizar en Vender"
        # en la ficha de Retail queda anotado aquí, y la próxima
        # venta/cotización que esa empleada cree nace vinculada a él.
        con.execute("""
            CREATE TABLE IF NOT EXISTS venta_lead_pendiente (
                usuario TEXT PRIMARY KEY,
                ref TEXT NOT NULL,
                nombre TEXT NOT NULL DEFAULT ''
            )
        """)


def guardar_borrador(usuario, nombre, celular, servicios=None, datos=None,
                     renglones=None):
    """El formulario en curso (nombre, celular, los datos opcionales del
    cliente y los renglones que ya escribió): sobrevive a los reloads de
    agregar/quitar plantas (venta.js lo manda mientras se escribe). Los
    argumentos en None dejan lo guardado como estaba."""
    crudo = None if servicios is None else json.dumps(servicios, ensure_ascii=False)
    libres = None if renglones is None else json.dumps(renglones, ensure_ascii=False)
    extra = None if datos is None else json.dumps(
        {campo: (datos.get(campo) or "") for campo in CAMPOS_EXTRA},
        ensure_ascii=False)
    with _db() as con:
        con.execute(
            "INSERT INTO venta_borrador (usuario, nombre, celular, servicios,"
            " renglones, extra) VALUES (?,?,?,?,?,?)"
            " ON CONFLICT (usuario) DO UPDATE SET nombre=?, celular=?,"
            " servicios=COALESCE(?, servicios), renglones=COALESCE(?, renglones),"
            " extra=COALESCE(?, extra)",
            (usuario, nombre, celular, crudo, libres, extra,
             nombre, celular, crudo, libres, extra))


def _json_o_defecto(crudo, defecto):
    if not crudo:
        return defecto
    try:
        valor = json.loads(crudo)
    except ValueError:
        return defecto
    return valor if isinstance(valor, type(defecto)) else defecto


def borrador_de(usuario):
    with _db() as con:
        fila = con.execute(
            "SELECT nombre, celular, servicios, renglones, extra FROM venta_borrador"
            " WHERE usuario=?", (usuario,)).fetchone()
    vacio = {campo: "" for campo in CAMPOS_EXTRA}
    if not fila:
        return {"nombre": "", "celular": "", "servicios": [], "renglones": [], **vacio}
    extra = _json_o_defecto(fila["extra"], {})
    return {"nombre": fila["nombre"], "celular": fila["celular"],
            "servicios": _json_o_defecto(fila["servicios"], []),
            "renglones": _json_o_defecto(fila["renglones"], []),
            **vacio, **{campo: (extra.get(campo) or "") for campo in CAMPOS_EXTRA}}


def _limpiar_borrador(usuario):
    with _db() as con:
        con.execute("DELETE FROM venta_borrador WHERE usuario=?", (usuario,))


# ---------------------------------------------------------------------------
# El amarre con la pestaña Retail. "Cotizar en Vender" desde la ficha de un
# lead deja el lead pendiente; la próxima venta o cotización de esa empleada
# nace con lead_ref y la tarjeta del tablero se mueve sola a "Cotizado".
# ---------------------------------------------------------------------------

def poner_lead_pendiente(usuario, ref, nombre=""):
    with _db() as con:
        con.execute("""
            INSERT INTO venta_lead_pendiente (usuario, ref, nombre) VALUES (?,?,?)
            ON CONFLICT(usuario) DO UPDATE SET ref = excluded.ref,
                nombre = excluded.nombre
        """, (usuario, ref, (nombre or "").strip()))


def lead_pendiente(usuario):
    with _db() as con:
        fila = con.execute(
            "SELECT ref, nombre FROM venta_lead_pendiente WHERE usuario=?",
            (usuario,)).fetchone()
    return dict(fila) if fila else None


def quitar_lead_pendiente(usuario):
    with _db() as con:
        con.execute("DELETE FROM venta_lead_pendiente WHERE usuario=?", (usuario,))


def tomar_lead_pendiente(usuario):
    """El lead pendiente, quitándolo: se consume una sola vez."""
    lead = lead_pendiente(usuario)
    if lead:
        quitar_lead_pendiente(usuario)
    return lead


def vincular_lead(n, issue):
    """Amarra (o con issue None desamarra) una venta local a un issue del
    kanban Retail (LEAD-NN). Es la corrección manual desde la ficha; el
    espejo del CRM llena lead_issue solo al crear la venta."""
    with _db() as con:
        con.execute("UPDATE ventas_locales SET lead_issue=? WHERE n=?",
                    (issue or None, n))


def vinculadas_por_lead():
    """{LEAD-NN: [ventas locales vinculadas, la más nueva primero]}."""
    with _db() as con:
        filas = con.execute(
            "SELECT * FROM ventas_locales WHERE lead_issue IS NOT NULL"
            " ORDER BY n DESC").fetchall()
    resultado = {}
    for fila in filas:
        resultado.setdefault(fila["lead_issue"], []).append(dict(fila))
    return resultado


def sin_lead(limite=6):
    """Las ventas locales recientes que aún no pertenecen a ningún lead
    (candidatas a vincular desde la ficha de Retail)."""
    with _db() as con:
        filas = con.execute(
            "SELECT * FROM ventas_locales WHERE lead_issue IS NULL"
            " ORDER BY n DESC LIMIT ?", (limite,)).fetchall()
    return [dict(f) for f in filas]


def carrito_de(usuario):
    """[{producto_id, cantidad, sku, nombre, precio, precio_odoo,
    precio_editado, importe}] con precios frescos de Odoo, más el total. Un
    producto que ya no existe en Odoo se descarta del carrito en silencio.

    El precio de cada línea puede estar escrito a mano (pedido del dueño,
    23/09/2026: "por si acaso le vendo más caro"): cuando lo está, manda
    ese y el de Odoo queda a la vista para poder volver a él."""
    with _db() as con:
        filas = con.execute(
            "SELECT producto_id, cantidad, precio FROM venta_carrito"
            " WHERE usuario=? ORDER BY rowid", (usuario,)).fetchall()
    if not filas:
        return [], 0.0
    datos_odoo = productos_por_id([f["producto_id"] for f in filas])
    lineas = []
    for fila in filas:
        producto = datos_odoo.get(fila["producto_id"])
        if producto is None:
            quitar_del_carrito(usuario, fila["producto_id"])
            continue
        precio_odoo = producto["precio"]
        a_mano = fila["precio"]
        precio = round(float(a_mano), 2) if a_mano is not None else precio_odoo
        lineas.append({
            "producto_id": fila["producto_id"], "cantidad": fila["cantidad"],
            **producto, "precio": precio, "precio_odoo": precio_odoo,
            "precio_editado": a_mano is not None,
            "importe": round(fila["cantidad"] * precio, 2),
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


def cambiar_precio(usuario, producto_id, precio):
    """Fija el precio unitario de una línea del carrito. Un precio vacío
    devuelve la línea al precio de Odoo (el botón "precio de Odoo"). Un
    precio ilegible avisa en vez de adivinar."""
    crudo = str(precio if precio is not None else "").strip().replace(",", ".")
    crudo = crudo.lstrip("$").strip()
    if not crudo:
        valor = None
    else:
        try:
            valor = round(float(crudo), 2)
        except ValueError:
            raise ValueError("Ese precio no es un número.")
        if valor < 0:
            raise ValueError("El precio no puede ser negativo.")
        valor = min(valor, 999999.0)
    with _db() as con:
        con.execute(
            "UPDATE venta_carrito SET precio=? WHERE usuario=? AND producto_id=?",
            (valor, usuario, int(producto_id)))


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


def token_de(n):
    """El token del enlace público de la factura; se crea la primera vez."""
    venta = obtener_venta(n)
    if venta is None:
        return None
    if venta.get("token"):
        return venta["token"]
    token = secrets.token_urlsafe(12)
    _actualizar_venta(n, token=token)
    return token


def venta_por_token(token):
    if not token:
        return None
    with _db() as con:
        fila = con.execute("SELECT * FROM ventas_locales WHERE token=?",
                           (token,)).fetchone()
    return dict(fila) if fila else None


# ---------------------------------------------------------------------------
# Flujo contra Odoo
# ---------------------------------------------------------------------------

# Los datos opcionales del bloque Cliente, iguales en Nueva Venta y en las
# cotizaciones de servicio (pedido del dueño 17/09/2026: que se puedan
# tomar RUC, cédula, correo y dirección, todos opcionales). "Empresa" salió
# de la lista ese mismo día: no se imprime en la propuesta y su lugar en el
# bloque lo ocupa ahora el proyecto de la cotización.
CAMPOS_CLIENTE = ("ruc", "cedula", "correo", "direccion")
# Lo que el borrador guarda además del nombre y el celular: los datos
# opcionales del cliente Y los cargos opcionales (envío, instalación), que
# también tienen que sobrevivir a los reloads de agregar/quitar plantas.
CAMPOS_EXTRA = CAMPOS_CLIENTE + ("envio", "instalacion")


def valores_de_cliente(datos):
    """Los datos opcionales del formulario -> campos de res.partner. El RUC
    y la cédula van los dos al Tax ID (vat) porque en Panamá es el mismo
    dato para la empresa y para la persona: si hay RUC manda el RUC, y la
    cédula queda además en la referencia (ref) para poder buscarla."""
    datos = datos or {}
    limpio = {campo: (datos.get(campo) or "").strip() for campo in CAMPOS_CLIENTE}
    valores = {}
    if limpio["ruc"] or limpio["cedula"]:
        valores["vat"] = limpio["ruc"] or limpio["cedula"]
    if limpio["cedula"]:
        valores["ref"] = limpio["cedula"]
    if limpio["correo"]:
        valores["email"] = limpio["correo"]
    if limpio["direccion"]:
        valores["street"] = limpio["direccion"]
    return valores


def completar_cliente(partner_id, valores):
    """Rellena en Odoo SOLO los campos que estén vacíos: lo que Odoo ya
    tiene manda (es la fuente de verdad) y nunca se sobreescribe con lo que
    se digitó en la app."""
    if not valores:
        return
    actual = _ejecutar("res.partner", "read", [[partner_id]],
                       {"fields": list(valores)})[0]
    faltantes = {campo: valor for campo, valor in valores.items()
                 if not actual.get(campo)}
    if faltantes:
        _ejecutar("res.partner", "write", [[partner_id], faltantes])


def _cliente_id(nombre, celular="", datos=None):
    """El partner para la orden: el genérico "Cliente Local" si no dieron
    nombre; si lo dieron, se busca por nombre exacto y se crea si no existe
    (con el celular y los datos opcionales que hayan llenado)."""
    nombre = (nombre or "").strip()
    valores_extra = valores_de_cliente(datos)
    if not nombre:
        return _id_config("VENTA_CLIENTE_LOCAL")
    ids = _ejecutar("res.partner", "search", [[["name", "=ilike", nombre]]], {"limit": 1})
    if ids:
        completar_cliente(ids[0], valores_extra)
        return ids[0]
    valores = {"name": nombre, "customer_rank": 1, "company_type": "person",
               **valores_extra}
    if (celular or "").strip():
        # "phone" y no "mobile": este Odoo no tiene el campo mobile en
        # res.partner (crear con mobile reventaba la venta con cliente nuevo).
        valores["phone"] = celular.strip()
    return _ejecutar("res.partner", "create", [valores])


def _linea_de_planta(linea):
    """El renglón de Odoo de una planta del carrito. Solo lleva price_unit
    si el precio se escribió a mano (23/09/2026): sin él, Odoo aplica su
    lista de precios, que es lo normal."""
    renglon = {"product_id": linea["producto_id"],
               "product_uom_qty": linea["cantidad"]}
    if linea.get("precio_editado"):
        renglon["price_unit"] = linea["precio"]
    return renglon


def crear_cotizacion(empleada, nombre_cliente, celular="", datos=None,
                     cargos=None):
    """Crea el sale.order borrador (etiqueta LOCAL, diario de ventas normal)
    y el registro local. Devuelve el registro. El carrito y el borrador se
    limpian solo si Odoo aceptó la orden. `cargos` son los opcionales de
    envío/instalación ({clave: monto}); entran como líneas de la orden."""
    usuario = empleada["id"]
    lineas, _total_visto = carrito_de(usuario)
    if not lineas:
        raise ValueError("Agrega al menos una planta a la venta.")
    extras = lineas_de_cargos(cargos)
    partner = _cliente_id(nombre_cliente, celular, datos)
    orden_id = _ejecutar("sale.order", "create", [{
        "partner_id": partner,
        "tag_ids": [[6, 0, [_id_config("VENTA_TAG_LOCAL")]]],
        # El precio lo pone Odoo (lista de precios vigente), salvo que la
        # empleada lo haya escrito a mano en el carrito — ahí manda el
        # suyo. Los cargos siempre lo traen (es el monto digitado).
        "order_line": [[0, 0, _linea_de_planta(l)] for l in lineas]
                      + [[0, 0, x] for x in extras],
    }])
    if isinstance(orden_id, list):
        orden_id = orden_id[0]
    leido = _ejecutar("sale.order", "read", [[orden_id]],
                      {"fields": ["name", "amount_total"]})[0]
    resumen = ", ".join(f"{l['cantidad']}× {l['nombre']}" for l in lineas)
    if extras:
        con_monto = [c["nombre"] for c in CARGOS
                     if float((cargos or {}).get(c["clave"]) or 0) > 0]
        resumen += ", " + ", ".join(n.lower() for n in con_monto)
    espejo, oportunidad_id = _espejar_en_crm(
        empleada, nombre_cliente, celular, partner, orden_id,
        leido["name"], leido["amount_total"])
    with _db() as con:
        cursor = con.execute(
            "INSERT INTO ventas_locales (creado_en, empleada, cliente, celular,"
            " orden_id, orden, total, estado, resumen, lead_ref, lead_issue,"
            " lead_url, oportunidad_id)"
            " VALUES (?,?,?,?,?,?,?, 'cotizacion', ?,?,?,?,?)",
            (_ahora(), empleada["nombre"], (nombre_cliente or "").strip() or "Cliente Local",
             (celular or "").strip() or None,
             orden_id, leido["name"], leido["amount_total"], resumen,
             (espejo or {}).get("codigoRef"), (espejo or {}).get("identifier"),
             (espejo or {}).get("url"), oportunidad_id))
        n = cursor.lastrowid
    # El lead pendiente de "Cotizar en Vender" (ficha de Retail) se consume
    # SIEMPRE aquí (que no se le pegue a la próxima venta ajena); si el
    # espejo no resolvió un issue, ese lead es el amarre.
    lead = tomar_lead_pendiente(usuario)
    if lead and not (espejo or {}).get("identifier"):
        vincular_lead(n, lead["ref"])
    vaciar_carrito(usuario)
    _limpiar_borrador(usuario)
    return obtener_venta(n)


# ---------------------------------------------------------------------------
# Vista previa del PDF (dueño, 23/09/2026: "antes de generar cotización haz
# vista previa del pdf con botón de salida").
#
# El PDF lo arma Odoo desde una orden, así que la vista previa necesita una:
# se usa UNA sola por empleada, marcada "VISTA PREVIA <usuario>", que se
# reescribe en cada vistazo. No se borra al salir porque el usuario de la
# app no tiene permiso para borrar pedidos en Odoo (lo mismo que se
# descubrió con la cotización de muestra), y reusarla evita que se acumulen.
#
# Lo importante: NO es la cotización. No crea el registro local, no limpia
# el carrito, no abre oportunidad en el CRM y no toca la pestaña Retail —
# todo eso pasa recién cuando la empleada toca "Generar cotización".
# ---------------------------------------------------------------------------

REF_VISTA_PREVIA = "VISTA PREVIA"


def _orden_vista_previa(usuario, partner, lineas):
    ref = f"{REF_VISTA_PREVIA} {usuario}"
    nuevas = [[0, 0, linea] for linea in lineas]
    ids = _ejecutar("sale.order", "search",
                    [[["client_order_ref", "=", ref], ["state", "=", "draft"]]],
                    {"limit": 1})
    if ids:
        # El 5 borra las líneas del vistazo anterior antes de poner las de
        # ahora: la orden es siempre la misma, el contenido no.
        _ejecutar("sale.order", "write", [[ids[0]], {
            "partner_id": partner, "order_line": [[5, 0, 0]] + nuevas}])
        return ids[0]
    orden = _ejecutar("sale.order", "create", [{
        "partner_id": partner, "order_line": nuevas, "client_order_ref": ref,
        "tag_ids": [[6, 0, [_id_config("VENTA_TAG_LOCAL")]]],
    }])
    return orden[0] if isinstance(orden, list) else orden


def pdf_vista_previa(empleada, nombre_cliente, celular="", datos=None, cargos=None):
    """El PDF de la cotización tal como saldría, sin crear la venta.

    Mismas líneas que crear_cotizacion —las plantas del carrito con el
    precio que ponga Odoo, más los cargos con monto— para que lo que se ve
    sea lo que después se genera."""
    usuario = empleada["id"]
    lineas, _total = carrito_de(usuario)
    if not lineas:
        raise ValueError("Agrega al menos una planta para ver la cotización.")
    partner = _cliente_id(nombre_cliente, celular, datos)
    orden = _orden_vista_previa(usuario, partner, [
        {"product_id": l["producto_id"], "product_uom_qty": l["cantidad"]}
        for l in lineas] + lineas_de_cargos(cargos))
    return descargar_pdf("sale.report_saleorder", orden)


def _espejar_en_crm(empleada, nombre_cliente, celular, partner, orden_id,
                    orden, total):
    """El espejo de la venta en el CRM (decisión del 22/09/2026): el lead
    en Linear/Twenty vía el puente de Vercel, la oportunidad en el Flujo de
    Odoo (etiqueta RETAIL VENTA, etapa Cotizado) amarrada a la orden, y la
    tarjeta del kanban Retail en su columna. Todo best-effort: si algo
    falla, la venta ya está creada y sale igual."""
    from . import cotizaciones, retail  # diferidos: cotizaciones importa este módulo
    pendiente = tomar_lead_pendiente(empleada["id"])
    espejo = crm_leads.espejar_venta(nombre_cliente, celular, "venta",
                                     orden, total, empleada["nombre"],
                                     issue=(pendiente or {}).get("ref", ""))
    if pendiente and not (espejo or {}).get("identifier"):
        # El puente no respondió, pero la venta venía de la ficha de un
        # lead concreto del kanban: queda vinculada igual.
        espejo = {**(espejo or {}), "identifier": pendiente["ref"]}
    oportunidad_id = None
    try:
        # Sin espejo Y sin datos del cliente no hay a quién dar seguimiento:
        # una tarjeta "Cliente Local" en el Flujo sería puro ruido.
        if espejo or (nombre_cliente or "").strip() or (celular or "").strip():
            oportunidad_id = cotizaciones._oportunidad_espejada(
                partner, (nombre_cliente or "").strip() or "Cliente Local",
                "RETAIL VENTA", espejo)
            _ejecutar("sale.order", "write",
                      [[orden_id], {"opportunity_id": oportunidad_id}])
            _ejecutar("crm.lead", "write",
                      [[oportunidad_id], {"expected_revenue": total}])
    except Exception as error:
        print(f"ventas: oportunidad de {orden} falló: {error!r}", flush=True)
    identificador = (espejo or {}).get("identifier")
    if identificador:
        try:
            retail.mover(identificador, "facturar")
            retail.refrescar()
        except Exception as error:
            print(f"ventas: kanban Retail de {orden} falló: {error!r}", flush=True)
    return espejo, oportunidad_id


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
    venta = obtener_venta(n)
    if venta["estado"] == "pagado":
        _avanzar_crm_pagada(venta)
    return venta


def _avanzar_crm_pagada(venta):
    """Con la venta cobrada, el espejo avanza: la oportunidad del Flujo
    pasa a Facturado y la tarjeta del kanban Retail a "Facturado · por
    entregar". El sync entre los dos tableros vive SOLO en Cotizado y
    Facturado (regla de Abraham, 22/09/2026): Abono lo mueve él a mano,
    Pagado no se pone solo y "Entregado" existe solo en el kanban.
    Best-effort: el cobro ya quedó sellado y nada de esto lo tumba."""
    from . import cotizaciones, retail  # diferidos
    if venta.get("oportunidad_id"):
        try:
            _ejecutar("crm.lead", "write", [[venta["oportunidad_id"]], {
                "stage_id": cotizaciones._id_ref(
                    "vivero_rose_pedidos.etapa_flujo_facturado"),
            }])
        except Exception as error:
            print(f"ventas: Flujo de {venta.get('orden')} falló: {error!r}",
                  flush=True)
    if venta.get("lead_ref"):
        # El avance tambien viaja al CRM: label Facturado en el issue y
        # etapaVenta en Twenty (los chips de Chats, 23/09/2026).
        crm_leads.marcar_odoo(venta["lead_ref"], etapa="FACTURADO")
    if venta.get("lead_issue"):
        try:
            retail.mover(venta["lead_issue"], "entregar")
            retail.refrescar()
        except Exception as error:
            print(f"ventas: kanban Retail de {venta.get('orden')} falló: "
                  f"{error!r}", flush=True)


def cancelar(n):
    """Cancela una cotización: la orden en Odoo pasa a cancelada y el
    registro local queda en estado 'cancelada'. Solo aplica a cotizaciones
    (una venta ya cobrada no se cancela desde aquí)."""
    venta = obtener_venta(n)
    if venta is None:
        return None
    if venta["estado"] != "cotizacion":
        raise ValueError("Solo se puede cancelar una cotización.")
    _ejecutar("sale.order", "action_cancel", [[venta["orden_id"]]])
    _actualizar_venta(n, estado="cancelada", ultimo_error=None)
    return obtener_venta(n)


def lineas_de_factura(venta):
    """Las líneas de producto de la factura en Odoo, para la plantilla
    pública: nombre, cantidad, precio unitario e importe."""
    filas = _ejecutar("account.move.line", "search_read",
                      [[["move_id", "=", venta["factura_id"]],
                        ["display_type", "=", "product"]]],
                      {"fields": ["name", "quantity", "price_unit",
                                  "price_subtotal"]})
    return [{"nombre": f["name"], "cantidad": int(f["quantity"]),
             "precio": f["price_unit"], "importe": f["price_subtotal"]}
            for f in filas]


def lineas_de_cotizacion(venta):
    """Las líneas de la orden en Odoo, para la cotización pública."""
    filas = _ejecutar("sale.order.line", "search_read",
                      [[["order_id", "=", venta["orden_id"]],
                        ["display_type", "=", False]]],
                      {"fields": ["name", "product_uom_qty", "price_unit",
                                  "price_subtotal"]})
    return [{"nombre": f["name"], "cantidad": int(f["product_uom_qty"]),
             "precio": f["price_unit"], "importe": f["price_subtotal"]}
            for f in filas]


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


_cache_plantillas = {}


def _plantilla_de_reporte(referencia):
    """El report_name (la plantilla QWeb) de un reporte a partir de la
    referencia XML de su acción: /report/pdf/... usa ese nombre, NO el
    xml_id de la acción. En los reportes estándar de Odoo los dos coinciden
    (sale.report_saleorder), pero en los del addon no: la acción se llama
    reporte_propuesta_venta y su plantilla plantilla_propuesta_venta, y por
    eso la propuesta de servicio devolvía 404."""
    if referencia in _cache_plantillas:
        return _cache_plantillas[referencia]
    plantilla = referencia
    try:
        modulo, nombre = referencia.split(".", 1)
        filas = _ejecutar("ir.model.data", "search_read",
                          [[["module", "=", modulo], ["name", "=", nombre],
                            ["model", "=", "ir.actions.report"]]],
                          {"fields": ["res_id"], "limit": 1})
        if filas:
            accion = _ejecutar("ir.actions.report", "read", [[filas[0]["res_id"]]],
                               {"fields": ["report_name"]})
            plantilla = accion[0]["report_name"] or referencia
    except Exception:
        pass  # sin Odoo a mano, se prueba con la referencia tal cual
    _cache_plantillas[referencia] = plantilla
    return plantilla


def descargar_pdf(reporte, registro_id):
    """El PDF nativo de Odoo (la propuesta del addon, la cotización o la
    factura estándar). Reautentica una vez si la sesión web venció."""
    import urllib.error

    plantilla = _plantilla_de_reporte(reporte)
    url = os.environ["ODOO_URL"].rstrip("/") + f"/report/pdf/{plantilla}/{int(registro_id)}"
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
