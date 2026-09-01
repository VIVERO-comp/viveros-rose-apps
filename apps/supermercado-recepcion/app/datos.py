"""Capa de datos de la app de recepción.

Las órdenes, las sucursales y el catálogo vienen del stock-proxy (que los
lee de Odoo). Sucursales y catálogo conservan datos de prueba como último
fallback; las órdenes no: sin conexión, la pestaña Entregas queda vacía.
Lo que la empleada confirma (recepciones, devoluciones, intercambios,
historial) vive en SQLite local para sobrevivir reinicios.

Cuando llegue la integración real, las funciones odoo_* de abajo pasarán a
llamar al order-api (router supermercado) y SQLite quedará como caché/cola
local; Odoo será la fuente de verdad.
"""

import json
import os
import sqlite3
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from .calculos import calcular_orden

ZONA_PANAMA = ZoneInfo("America/Panama")

_CATALOGO_BASE = [
    ("VR-001", "Hierba Buena VR", 1.75), ("VR-002", "Romero VR", 1.75),
    ("VR-003", "Menta VR", 1.75), ("VR-004", "Ruda VR", 1.75),
    ("VR-005", "Chavelitas VR", 1.5), ("VR-006", "Mini Jade VR", 2.0),
    ("VR-007", "Jade VR", 3.2), ("VR-008", "Fitonia Roja VR", 2.25),
    ("VR-009", "Cactus Variados Pequeño VR", 1.95),
    ("VR-010", "Suculentas Variadas Pequeñas VR", 1.95),
    ("VR-011", "Phothus VR", 1.8), ("VR-012", "Millonaria Samiocula VR", 6.0),
    ("VR-013", "Coronita de Cristo VR", 2.25), ("VR-014", "Marigold VR", 1.98),
    ("VR-015", "Novio Chino VR", 2.25), ("VR-016", "Cielito Azul VR", 1.95),
    ("VR-017", "Zamioculca VR", 5.5), ("VR-018", "Sansevieria VR", 4.25),
    ("VR-019", "Calathea VR", 3.75), ("VR-020", "Fitonia Blanca VR", 2.25),
    ("VR-021", "Peperomia VR", 2.5), ("VR-022", "Echeveria VR", 1.95),
]
CATALOGO = [{"sku": s, "nombre": n, "precio": p} for s, n, p in _CATALOGO_BASE]
POR_SKU = {p["sku"]: p for p in CATALOGO}


# Las órdenes ya no tienen datos simulados: vienen del stock-proxy (pedidos
# de venta confirmados a sucursales de supermercado en Odoo). Sin conexión,
# la lista queda vacía. La referencia que ve la empleada es el NOMBRE DEL
# PEDIDO (S00774); si el pedido trae "referencia de cliente" en Odoo, se
# muestra debajo como "Ref. súper".

# Datos de prueba (fallback): las reales vienen del stock-proxy, que las lee
# de Odoo (hijos del partner Super Extra, ver docs/odoo-integration.md).
SUCURSALES = [
    ("Super Xtra", "Villalobos"), ("Riba Smith", "Bella Vista"),
    ("Super 99", "Costa del Este"), ("Supermercados Rey", "Vía España"),
]


# ---------------------------------------------------------------------------
# Sucursales y catálogo en vivo, vía stock-proxy. Degradación en cadena:
# respuesta fresca → caché en memoria (TTL) → último valor bueno aunque haya
# vencido → datos de prueba si nunca hubo conexión. La app nunca se cae por
# el proxy, y sin STOCK_PROXY_URL/STOCK_API_KEY funciona igual que antes.
# ---------------------------------------------------------------------------

TTL_SUCURSALES = 300
TTL_CATALOGO = 60
TTL_ENTREGAS = 30

_cache_proxy = {}  # recurso -> {"valor": json, "en": epoch}


def reiniciar_cache_proxy():
    """Solo para pruebas."""
    _cache_proxy.clear()


def _pedir_al_proxy(recurso):
    import httpx

    url = os.environ["STOCK_PROXY_URL"].rstrip("/")
    clave = os.environ["STOCK_API_KEY"]
    respuesta = httpx.get(f"{url}/{recurso}", headers={"X-API-Key": clave}, timeout=4)
    respuesta.raise_for_status()
    return respuesta.json()


def _obtener_del_proxy(recurso, ttl):
    if not os.environ.get("STOCK_PROXY_URL") or not os.environ.get("STOCK_API_KEY"):
        return None
    guardado = _cache_proxy.get(recurso)
    if guardado and time.time() - guardado["en"] < ttl:
        return guardado["valor"]
    try:
        valor = _pedir_al_proxy(recurso)
    except Exception:
        return guardado["valor"] if guardado else None
    _cache_proxy[recurso] = {"valor": valor, "en": time.time()}
    return valor


def obtener_clientes_supermercado():
    """[{clave, nombre, sucursales: [{ref, nombre}]}] — del proxy o de prueba.

    `clave` identifica al cliente en las URLs: su ref de Odoo cuando existe,
    su nombre en los datos de prueba."""
    remoto = _obtener_del_proxy("sucursales", TTL_SUCURSALES)
    if remoto and remoto.get("clients"):
        return [
            {
                "clave": c["ref"] or c["name"],
                "nombre": c["name"],
                "sucursales": [{"ref": s["ref"], "nombre": s["name"]} for s in c["branches"]],
            }
            for c in remoto["clients"]
        ]
    agrupado = {}
    for cliente, sucursal in SUCURSALES:
        agrupado.setdefault(cliente, []).append(sucursal)
    return [
        {"clave": cliente, "nombre": cliente,
         "sucursales": [{"ref": None, "nombre": s} for s in lista]}
        for cliente, lista in agrupado.items()
    ]


def obtener_catalogo():
    """[{sku, nombre, precio, disponible}] — del proxy o los 22 de prueba.

    `disponible` es None cuando se trabaja con datos de prueba (no se inventa
    un stock que no existe)."""
    remoto = _obtener_del_proxy("catalogo", TTL_CATALOGO)
    if remoto and remoto.get("items"):
        return [
            {"sku": i["sku"], "nombre": i["name"], "precio": i["price_cents"] / 100,
             "disponible": i["available"]}
            for i in remoto["items"]
        ]
    return [{**p, "disponible": None} for p in CATALOGO]


def buscar_producto(sku):
    """El producto del catálogo vigente (o de prueba) para ese SKU."""
    encontrado = next((p for p in obtener_catalogo() if p["sku"] == sku), None)
    return encontrado or POR_SKU.get(sku)


def obtener_ordenes():
    """Pedidos confirmados a supermercados, desde el stock-proxy.

    No hay órdenes de prueba: sin conexión (o sin configurar el proxy) la
    lista es vacía y la pestaña Entregas muestra su estado vacío."""
    remoto = _obtener_del_proxy("entregas", TTL_ENTREGAS)
    if not remoto:
        return []
    ordenes = []
    for pedido in remoto.get("orders", []):
        lineas = [
            {"sku": l["sku"], "nombre": l["name"],
             "precio": l["unit_price_cents"] / 100, "enviado": l["qty"]}
            for l in pedido["lines"] if l["qty"] > 0
        ]
        if not lineas:
            continue
        ordenes.append({
            "pedido": pedido["name"],
            "refSuper": pedido.get("customer_ref"),
            "odooId": pedido["id"],
            "cliente": pedido["client"]["name"],
            "sucursal": pedido["branch"]["name"],
            "sucursalRef": pedido["branch"].get("ref"),
            "fecha": pedido["date"],
            "lineas": lineas,
        })
    return ordenes


def ahora():
    """Hora local de Panamá en formato corto, como el prototipo (ej. 3:42 p.m.)."""
    return datetime.now(ZONA_PANAMA).strftime("%-I:%M %p").lower().replace("am", "a.m.").replace("pm", "p.m.")


# ---------------------------------------------------------------------------
# Sincronización con Odoo, vía order-api (router supermercado). La única
# operación que viaja es la RECEPCIÓN (opción B: la salida se valida con lo
# aceptado, sin backorder ni devolución en Odoo); el regreso y los
# intercambios son registro local a propósito. La confirmación se guarda
# SIEMPRE en SQLite antes de intentar nada: si el order-api u Odoo no
# responden, queda en la cola y el reintentador la manda después (el
# servidor es idempotente, reintentar nunca duplica).
# ---------------------------------------------------------------------------

def _orden_api():
    url = os.environ.get("ORDER_API_URL")
    clave = os.environ.get("ORDER_API_KEY")
    return (url.rstrip("/"), clave) if url and clave else None


def sincronizacion_configurada():
    return _orden_api() is not None


def _encolar_para_odoo(operacion, pedido, payload):
    with _db() as con:
        con.execute(
            "INSERT INTO pendientes_odoo (operacion, pedido, payload, estado, creada_en)"
            " VALUES (?,?,?,'pendiente',?)",
            (operacion, pedido, json.dumps(payload), datetime.now(ZONA_PANAMA).isoformat()),
        )
    # Mejor esfuerzo al instante; si falla, el reintentador la toma después.
    try:
        sincronizar_pendientes()
    except Exception:
        pass


def pedidos_sin_sincronizar():
    """Pedidos cuya recepción aún no llegó a Odoo (para avisarlo en la UI)."""
    if not sincronizacion_configurada():
        return set()
    with _db() as con:
        filas = con.execute(
            "SELECT pedido FROM pendientes_odoo WHERE estado != 'hecha'"
        ).fetchall()
    return {f["pedido"] for f in filas}


def sincronizar_pendientes():
    """Manda la cola al order-api. 200 → hecha; error de conexión o 5xx →
    sigue pendiente (se reintenta); 4xx → error permanente (no se martilla
    con algo que nunca va a entrar solo)."""
    configuracion = _orden_api()
    if configuracion is None:
        return 0
    import httpx

    url, clave = configuracion
    with _db() as con:
        filas = con.execute(
            "SELECT n, payload FROM pendientes_odoo WHERE estado='pendiente' ORDER BY n"
        ).fetchall()
    sincronizadas = 0
    for fila in filas:
        try:
            respuesta = httpx.post(
                f"{url}/api/supermercado/recepciones",
                json=json.loads(fila["payload"]),
                headers={"X-API-Key": clave}, timeout=8,
            )
        except Exception as error:
            # Servidor inalcanzable: no martillar el resto, se reintenta luego.
            with _db() as con:
                con.execute(
                    "UPDATE pendientes_odoo SET intentos=intentos+1, ultimo_error=? WHERE n=?",
                    (str(error)[:300], fila["n"]),
                )
            break
        with _db() as con:
            if respuesta.status_code == 200:
                con.execute(
                    "UPDATE pendientes_odoo SET estado='hecha', sincronizada_en=?,"
                    " ultimo_error=NULL WHERE n=?",
                    (datetime.now(ZONA_PANAMA).isoformat(), fila["n"]),
                )
                sincronizadas += 1
            else:
                estado = "error" if 400 <= respuesta.status_code < 500 else "pendiente"
                con.execute(
                    "UPDATE pendientes_odoo SET estado=?, intentos=intentos+1,"
                    " ultimo_error=? WHERE n=?",
                    (estado, f"HTTP {respuesta.status_code}: {respuesta.text[:250]}", fila["n"]),
                )
    return sincronizadas


def odoo_crear_intercambio(payload):
    # {cliente, sucursal, lineas:[{sku, danadas}], empleadoId, fechaHora}
    return {"ok": True, "payload": payload}


def odoo_completar_intercambio(payload):
    # {intercambioId, empleadoId, fechaHora}
    return {"ok": True, "payload": payload}


# ---------------------------------------------------------------------------
# Estado local en SQLite: lo confirmado sobrevive reinicios del proceso.
# ---------------------------------------------------------------------------

def _ruta_db():
    # Se lee en cada conexión (no al importar) para que los tests puedan
    # apuntar cada caso a una base temporal limpia.
    return os.environ.get(
        "RECEPCION_DB", os.path.join(os.path.dirname(__file__), "..", "recepcion.db")
    )


def _db():
    con = sqlite3.connect(_ruta_db())
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    return con


def iniciar_db():
    with _db() as con:
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS recepciones (
                pedido TEXT PRIMARY KEY,         -- nombre del pedido (S00774)
                aceptado TEXT NOT NULL,          -- JSON {sku: cantidad}
                confirmada_en TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS devoluciones (
                pedido TEXT PRIMARY KEY,
                datos TEXT NOT NULL,             -- JSON {odooId, cliente, sucursal, lineas}
                regresada INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS intercambios (
                id TEXT PRIMARY KEY,
                datos TEXT NOT NULL,             -- JSON {cliente, sucursal, lineas, hora}
                estado TEXT NOT NULL             -- pendiente | completado
            );
            CREATE TABLE IF NOT EXISTS historial (
                n INTEGER PRIMARY KEY AUTOINCREMENT,
                tipo TEXT NOT NULL,              -- entrega | intercambio
                datos TEXT NOT NULL,             -- JSON del resumen mostrado
                creado_en TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS empleadas (
                usuario TEXT PRIMARY KEY,
                nombre TEXT NOT NULL,
                hash TEXT NOT NULL,              -- pbkdf2_sha256$iter$sal$hash
                activa INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS sesiones (
                token TEXT PRIMARY KEY,
                usuario TEXT NOT NULL,
                creada_en TEXT NOT NULL,
                expira_en TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS pendientes_odoo (
                n INTEGER PRIMARY KEY AUTOINCREMENT,
                operacion TEXT NOT NULL,         -- recepcion
                pedido TEXT NOT NULL,
                payload TEXT NOT NULL,           -- JSON que espera el order-api
                estado TEXT NOT NULL,            -- pendiente | hecha | error
                intentos INTEGER NOT NULL DEFAULT 0,
                ultimo_error TEXT,
                creada_en TEXT NOT NULL,
                sincronizada_en TEXT
            );
            """
        )
        _migrar_esquema(con)


def _migrar_esquema(con):
    """El esquema v1 llamaba "factura" a la referencia; ahora es "pedido"."""
    migrado = False
    for tabla in ("recepciones", "devoluciones"):
        columnas = [fila[1] for fila in con.execute(f"PRAGMA table_info({tabla})")]
        if "factura" in columnas:
            con.execute(f"ALTER TABLE {tabla} RENAME COLUMN factura TO pedido")
            migrado = True
    if migrado:
        con.execute("""UPDATE historial SET datos = replace(datos, '"factura"', '"pedido"')""")


def ordenes_pendientes(busqueda=""):
    """Órdenes aún no confirmadas, por fecha descendente (nunca por número)."""
    with _db() as con:
        confirmadas = {f["pedido"] for f in con.execute("SELECT pedido FROM recepciones")}
    pendientes = [o for o in obtener_ordenes() if o["pedido"] not in confirmadas]
    q = _normalizar(busqueda.strip())
    if q:
        pendientes = [
            o for o in pendientes
            if q in _normalizar(o["pedido"]) or q in _normalizar(o["refSuper"] or "")
            or q in _normalizar(o["cliente"]) or q in _normalizar(o["sucursal"])
        ]
    return sorted(pendientes, key=lambda o: o["fecha"], reverse=True)


def obtener_orden(pedido):
    return next((o for o in obtener_ordenes() if o["pedido"] == pedido), None)


def orden_confirmada(pedido):
    with _db() as con:
        return con.execute("SELECT 1 FROM recepciones WHERE pedido=?", (pedido,)).fetchone() is not None


def confirmar_recepcion(pedido, aceptado, empleada):
    """Sella la recepción: guarda estado, devolución (si hay) e historial."""
    orden = obtener_orden(pedido)
    if orden is None:
        return None
    resultado = calcular_orden(orden["lineas"], aceptado)
    _encolar_para_odoo("recepcion", pedido, {
        "odooId": orden["odooId"], "pedido": pedido,
        "lineas": [
            {"sku": l["sku"], "aceptado": aceptado.get(l["sku"], l["enviado"]),
             "devuelto": l["enviado"] - aceptado.get(l["sku"], l["enviado"])}
            for l in orden["lineas"]
        ],
        "empleadoId": empleada["id"], "fechaHora": datetime.now(ZONA_PANAMA).isoformat(),
    })
    with _db() as con:
        con.execute(
            "INSERT OR REPLACE INTO recepciones (pedido, aceptado, confirmada_en) VALUES (?,?,?)",
            (pedido, json.dumps(aceptado), datetime.now(ZONA_PANAMA).isoformat()),
        )
        if resultado["dev"] > 0:
            con.execute(
                "INSERT OR REPLACE INTO devoluciones (pedido, datos, regresada) VALUES (?,?,0)",
                (pedido, json.dumps({
                    "odooId": orden["odooId"], "cliente": orden["cliente"],
                    "sucursal": orden["sucursal"],
                    "lineas": [
                        {"sku": d["sku"], "nombre": d["nombre"], "cantidad": d["devuelto"],
                         "precio": d["precio"]}
                        for d in resultado["dif"]
                    ],
                })),
            )
        con.execute(
            "INSERT INTO historial (tipo, datos, creado_en) VALUES ('entrega', ?, ?)",
            (json.dumps({
                "hora": ahora(), "pedido": pedido, "cliente": orden["cliente"],
                "sucursal": orden["sucursal"], "acep": resultado["acep"],
                "dev": resultado["dev"], "t_acep": resultado["t_acep"],
                "regreso": resultado["dev"] == 0,
            }), datetime.now(ZONA_PANAMA).isoformat()),
        )
    return {"orden": orden, **resultado}


def devoluciones_pendientes():
    with _db() as con:
        filas = con.execute("SELECT pedido, datos FROM devoluciones WHERE regresada=0 ORDER BY rowid").fetchall()
    return [{"pedido": f["pedido"], **json.loads(f["datos"])} for f in filas]


def confirmar_regreso(pedido, empleada):
    # Registro local a propósito (opción B): en Odoo la orden ya quedó con lo
    # aceptado al validar la recepción; el regreso físico no toca Odoo.
    devolucion = next((d for d in devoluciones_pendientes() if d["pedido"] == pedido), None)
    if devolucion is None:
        return
    with _db() as con:
        con.execute("UPDATE devoluciones SET regresada=1 WHERE pedido=?", (pedido,))
        # El renglón del historial de esa entrega pasa a "regreso completado".
        for fila in con.execute("SELECT n, datos FROM historial WHERE tipo='entrega'").fetchall():
            datos = json.loads(fila["datos"])
            if datos.get("pedido") == pedido:
                datos["regreso"] = True
                con.execute("UPDATE historial SET datos=? WHERE n=?", (json.dumps(datos), fila["n"]))


def intercambios_todos():
    with _db() as con:
        filas = con.execute("SELECT id, datos, estado FROM intercambios ORDER BY rowid").fetchall()
    return [{"id": f["id"], "estado": f["estado"], **json.loads(f["datos"])} for f in filas]


def crear_intercambio(cliente, sucursal, lineas, empleada):
    nuevo = {
        "id": f"I-{int(time.time() * 1000)}", "cliente": cliente, "sucursal": sucursal,
        "lineas": lineas, "hora": ahora(),
    }
    odoo_crear_intercambio({
        "cliente": cliente, "sucursal": sucursal,
        "lineas": [{"sku": l["sku"], "danadas": l["danadas"]} for l in lineas],
        "empleadoId": empleada["id"], "fechaHora": datetime.now(ZONA_PANAMA).isoformat(),
    })
    with _db() as con:
        con.execute(
            "INSERT INTO intercambios (id, datos, estado) VALUES (?,?, 'pendiente')",
            (nuevo["id"], json.dumps({k: v for k, v in nuevo.items() if k not in ("id",)})),
        )
    return nuevo


def completar_intercambio(intercambio_id, empleada):
    intercambio = next((i for i in intercambios_todos() if i["id"] == intercambio_id), None)
    if intercambio is None or intercambio["estado"] != "pendiente":
        return
    odoo_completar_intercambio({
        "intercambioId": intercambio_id, "empleadoId": empleada["id"],
        "fechaHora": datetime.now(ZONA_PANAMA).isoformat(),
    })
    with _db() as con:
        con.execute("UPDATE intercambios SET estado='completado' WHERE id=?", (intercambio_id,))
        con.execute(
            "INSERT INTO historial (tipo, datos, creado_en) VALUES ('intercambio', ?, ?)",
            (json.dumps({
                "hora": ahora(), "cliente": intercambio["cliente"],
                "sucursal": intercambio["sucursal"],
                "total": sum(l["danadas"] for l in intercambio["lineas"]),
                "valor": round(sum(l["danadas"] * l["precio"] for l in intercambio["lineas"]), 2),
            }), datetime.now(ZONA_PANAMA).isoformat()),
        )


def historial_movimientos():
    """Historial del más reciente al más viejo, como el prototipo."""
    with _db() as con:
        filas = con.execute("SELECT tipo, datos FROM historial ORDER BY n DESC").fetchall()
    return [{"tipo": f["tipo"], **json.loads(f["datos"])} for f in filas]


def _normalizar(texto):
    import unicodedata
    return "".join(
        c for c in unicodedata.normalize("NFD", texto.lower()) if unicodedata.category(c) != "Mn"
    )
