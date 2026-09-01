"""Capa de datos de la app de recepción.

Fase 1: el catálogo, las órdenes y las sucursales son los mismos datos de
prueba del prototipo React (facturas #774, #781 y #770, no consecutivas a
propósito). Lo que la empleada confirma (recepciones, devoluciones,
intercambios, historial) vive en SQLite local para sobrevivir reinicios.

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

EMPLEADO = {"id": "emp-01", "nombre": "Génesis"}

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


def _linea(sku, enviado):
    p = POR_SKU[sku]
    return {"sku": sku, "nombre": p["nombre"], "precio": p["precio"], "enviado": enviado}


# `factura` es la referencia que ve la empleada; los demás IDs son técnicos
# (Odoo) y no se muestran. Los números NO son consecutivos ni se generan aquí.
ORDENES_ODOO = [
    {
        "factura": "774", "odooId": 10774, "pedido": "S00774", "reserva": "RES-2231",
        "transferencia": "WH/OUT/00512", "cliente": "Super Xtra", "sucursal": "Villalobos",
        "fecha": "2026-06-18",
        "lineas": [_linea("VR-001", 2), _linea("VR-002", 3), _linea("VR-003", 2),
                   _linea("VR-004", 3), _linea("VR-005", 6), _linea("VR-006", 4),
                   _linea("VR-007", 4), _linea("VR-008", 4), _linea("VR-009", 3),
                   _linea("VR-010", 2), _linea("VR-011", 2), _linea("VR-012", 4),
                   _linea("VR-013", 2), _linea("VR-014", 3), _linea("VR-015", 4),
                   _linea("VR-016", 2)],
    },
    {
        "factura": "781", "odooId": 10781, "pedido": "S00781", "reserva": "RES-2240",
        "transferencia": "WH/OUT/00519", "cliente": "Riba Smith", "sucursal": "Bella Vista",
        "fecha": "2026-06-18",
        "lineas": [_linea("VR-007", 4), _linea("VR-012", 3), _linea("VR-008", 4),
                   _linea("VR-002", 3), _linea("VR-003", 2), _linea("VR-005", 6),
                   _linea("VR-006", 4), _linea("VR-009", 3), _linea("VR-010", 2),
                   _linea("VR-011", 2), _linea("VR-014", 3), _linea("VR-016", 2)],
    },
    {
        "factura": "770", "odooId": 10770, "pedido": "S00770", "reserva": "RES-2225",
        "transferencia": "WH/OUT/00508", "cliente": "Super 99", "sucursal": "Costa del Este",
        "fecha": "2026-06-17",
        "lineas": [_linea("VR-017", 3), _linea("VR-018", 2), _linea("VR-019", 4),
                   _linea("VR-005", 6), _linea("VR-013", 2), _linea("VR-015", 3)],
    },
]

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


def ahora():
    """Hora local de Panamá en formato corto, como el prototipo (ej. 3:42 p.m.)."""
    return datetime.now(ZONA_PANAMA).strftime("%-I:%M %p").lower().replace("am", "a.m.").replace("pm", "p.m.")


# ---------------------------------------------------------------------------
# Interfaz que mañana implementará el order-api (router supermercado). Los
# payloads son los mismos del prototipo React y de docs/odoo-integration.md.
# ---------------------------------------------------------------------------

def odoo_confirmar_recepcion(payload):
    # {odooId, factura, lineas:[{sku, aceptado, devuelto}], empleadoId, fechaHora}
    return {"ok": True, "payload": payload}


def odoo_confirmar_regreso(payload):
    # {odooId, factura, lineas:[{sku, cantidad}], empleadoId, fechaHora}
    return {"ok": True, "payload": payload}


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
                factura TEXT PRIMARY KEY,
                aceptado TEXT NOT NULL,          -- JSON {sku: cantidad}
                confirmada_en TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS devoluciones (
                factura TEXT PRIMARY KEY,
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
            """
        )


def ordenes_pendientes(busqueda=""):
    """Órdenes aún no confirmadas, por fecha descendente (nunca por factura)."""
    with _db() as con:
        confirmadas = {f["factura"] for f in con.execute("SELECT factura FROM recepciones")}
    pendientes = [o for o in ORDENES_ODOO if o["factura"] not in confirmadas]
    q = _normalizar(busqueda.strip())
    if q:
        pendientes = [
            o for o in pendientes
            if q in o["factura"] or q in _normalizar(o["cliente"]) or q in _normalizar(o["sucursal"])
        ]
    return sorted(pendientes, key=lambda o: o["fecha"], reverse=True)


def obtener_orden(factura):
    return next((o for o in ORDENES_ODOO if o["factura"] == factura), None)


def orden_confirmada(factura):
    with _db() as con:
        return con.execute("SELECT 1 FROM recepciones WHERE factura=?", (factura,)).fetchone() is not None


def confirmar_recepcion(factura, aceptado):
    """Sella la recepción: guarda estado, devolución (si hay) e historial."""
    orden = obtener_orden(factura)
    resultado = calcular_orden(orden["lineas"], aceptado)
    odoo_confirmar_recepcion({
        "odooId": orden["odooId"], "factura": factura,
        "lineas": [
            {"sku": l["sku"], "aceptado": aceptado.get(l["sku"], l["enviado"]),
             "devuelto": l["enviado"] - aceptado.get(l["sku"], l["enviado"])}
            for l in orden["lineas"]
        ],
        "empleadoId": EMPLEADO["id"], "fechaHora": datetime.now(ZONA_PANAMA).isoformat(),
    })
    with _db() as con:
        con.execute(
            "INSERT OR REPLACE INTO recepciones (factura, aceptado, confirmada_en) VALUES (?,?,?)",
            (factura, json.dumps(aceptado), datetime.now(ZONA_PANAMA).isoformat()),
        )
        if resultado["dev"] > 0:
            con.execute(
                "INSERT OR REPLACE INTO devoluciones (factura, datos, regresada) VALUES (?,?,0)",
                (factura, json.dumps({
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
                "hora": ahora(), "factura": factura, "cliente": orden["cliente"],
                "sucursal": orden["sucursal"], "acep": resultado["acep"],
                "dev": resultado["dev"], "t_acep": resultado["t_acep"],
                "regreso": resultado["dev"] == 0,
            }), datetime.now(ZONA_PANAMA).isoformat()),
        )
    return {"orden": orden, **resultado}


def devoluciones_pendientes():
    with _db() as con:
        filas = con.execute("SELECT factura, datos FROM devoluciones WHERE regresada=0 ORDER BY rowid").fetchall()
    return [{"factura": f["factura"], **json.loads(f["datos"])} for f in filas]


def confirmar_regreso(factura):
    devolucion = next((d for d in devoluciones_pendientes() if d["factura"] == factura), None)
    if devolucion is None:
        return
    odoo_confirmar_regreso({
        "odooId": devolucion["odooId"], "factura": factura,
        "lineas": [{"sku": l["sku"], "cantidad": l["cantidad"]} for l in devolucion["lineas"]],
        "empleadoId": EMPLEADO["id"], "fechaHora": datetime.now(ZONA_PANAMA).isoformat(),
    })
    with _db() as con:
        con.execute("UPDATE devoluciones SET regresada=1 WHERE factura=?", (factura,))
        # El renglón del historial de esa entrega pasa a "regreso completado".
        for fila in con.execute("SELECT n, datos FROM historial WHERE tipo='entrega'").fetchall():
            datos = json.loads(fila["datos"])
            if datos.get("factura") == factura:
                datos["regreso"] = True
                con.execute("UPDATE historial SET datos=? WHERE n=?", (json.dumps(datos), fila["n"]))


def intercambios_todos():
    with _db() as con:
        filas = con.execute("SELECT id, datos, estado FROM intercambios ORDER BY rowid").fetchall()
    return [{"id": f["id"], "estado": f["estado"], **json.loads(f["datos"])} for f in filas]


def crear_intercambio(cliente, sucursal, lineas):
    nuevo = {
        "id": f"I-{int(time.time() * 1000)}", "cliente": cliente, "sucursal": sucursal,
        "lineas": lineas, "hora": ahora(),
    }
    odoo_crear_intercambio({
        "cliente": cliente, "sucursal": sucursal,
        "lineas": [{"sku": l["sku"], "danadas": l["danadas"]} for l in lineas],
        "empleadoId": EMPLEADO["id"], "fechaHora": datetime.now(ZONA_PANAMA).isoformat(),
    })
    with _db() as con:
        con.execute(
            "INSERT INTO intercambios (id, datos, estado) VALUES (?,?, 'pendiente')",
            (nuevo["id"], json.dumps({k: v for k, v in nuevo.items() if k not in ("id",)})),
        )
    return nuevo


def completar_intercambio(intercambio_id):
    intercambio = next((i for i in intercambios_todos() if i["id"] == intercambio_id), None)
    if intercambio is None or intercambio["estado"] != "pendiente":
        return
    odoo_completar_intercambio({
        "intercambioId": intercambio_id, "empleadoId": EMPLEADO["id"],
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
