"""Capa de datos: SQLite local, inventario del stock-proxy y ajustes vía order-api.

La app nunca toca Odoo directo (regla fija): lee el inventario del
stock-proxy (`GET /v1/inventario`) y escribe ajustes por el order-api
(`POST /api/stock/ajustes`). El SQLite guarda lo que es de la app: usuarios,
sesiones, alertas atendidas, historial de conteos y configuración.

Degradación de lecturas, igual que Recepción: caché en memoria; si el proxy
falla se sirve el último valor bueno aunque haya vencido; sin configuración
(desarrollo local) se usan datos de prueba. Los ajustes NO se degradan ni se
encolan: son interactivos (el empleado espera la respuesta con el candado de
cantidad esperada) y un error se muestra en pantalla.
"""

import json
import os
import sqlite3
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx

ZONA_PANAMA = ZoneInfo("America/Panama")

TTL_INVENTARIO = 60  # segundos

# Datos de prueba para desarrollo sin STOCK_PROXY_URL: los del prototipo.
INVENTARIO_DE_PRUEBA = [
    {"sku": "PL-HIERBA-BUENA", "nombre": "Hierba Buena", "categoria": "Aromáticas", "disponible": 1, "fisico": 1},
    {"sku": "PL-ROMERO", "nombre": "Romero", "categoria": "Aromáticas", "disponible": 2, "fisico": 2},
    {"sku": "PL-CROTON-PETRA", "nombre": "Croton Petra", "categoria": "Ornamentales", "disponible": 2, "fisico": 2},
    {"sku": "PL-ALBAHACA", "nombre": "Albahaca", "categoria": "Aromáticas", "disponible": 0, "fisico": 0},
    {"sku": "PL-LIMON-PERSA", "nombre": "Limón Persa", "categoria": "Frutales", "disponible": 4, "fisico": 4},
    {"sku": "PL-OREGANO", "nombre": "Orégano", "categoria": "Aromáticas", "disponible": 5, "fisico": 5},
    {"sku": "PL-IXORA-ROJA", "nombre": "Ixora Roja", "categoria": "Ornamentales", "disponible": 5, "fisico": 6},
    {"sku": "PL-CINTA-VERDE", "nombre": "Cinta Verde", "categoria": "Ornamentales", "disponible": 6, "fisico": 6},
    {"sku": "PL-PAPAYA", "nombre": "Papaya", "categoria": "Frutales", "disponible": 11, "fisico": 11},
    {"sku": "PL-VERANERA-FUCSIA", "nombre": "Veranera Fucsia", "categoria": "Ornamentales", "disponible": 14, "fisico": 14},
    {"sku": "PL-CULANTRO", "nombre": "Culantro", "categoria": "Aromáticas", "disponible": 26, "fisico": 26},
    {"sku": "PL-PALMA-ARECA", "nombre": "Palma Areca", "categoria": "Ornamentales", "disponible": 41, "fisico": 41},
]


class SinConexion(Exception):
    """El servicio remoto no respondió y no hay valor previo que servir."""


# ---------------------------------------------------------------------------
# Cliente del stock-proxy (lecturas)
# ---------------------------------------------------------------------------

_cache_proxy = {}  # {"inventario": {"valor": ..., "en": epoch}}


def reiniciar_cache_proxy():
    """Solo para pruebas."""
    _cache_proxy.clear()


def _pedir_al_proxy(recurso):
    """None si el proxy no está configurado (modo datos de prueba)."""
    url = os.environ.get("STOCK_PROXY_URL")
    clave = os.environ.get("STOCK_API_KEY")
    if not url or not clave:
        return None
    respuesta = httpx.get(f"{url.rstrip('/')}/{recurso}",
                          headers={"X-API-Key": clave}, timeout=4)
    respuesta.raise_for_status()
    return respuesta.json()


def obtener_inventario(refrescar=False):
    """Inventario completo con categoría. Devuelve (productos, actualizado_en).

    productos: [{sku, nombre, categoria, disponible, fisico}] con nombres en
    español; actualizado_en: epoch de cuándo se leyó del proxy.
    """
    # Es una herramienta interna de una o dos personas: SIEMPRE se lee fresco
    # de Odoo (refresh=true, rompiendo tambien el cache del proxy). Asi el
    # numero en pantalla es el real y el candado del ajuste nunca choca por
    # comparar contra un valor viejo. El cache local solo sirve de respaldo
    # si el proxy se cae.
    entrada = _cache_proxy.get("inventario")
    try:
        crudo = _pedir_al_proxy("inventario?refresh=true")
    except Exception:
        if entrada:
            # Proxy caído: el último valor bueno vale más que un error.
            return entrada["valor"], entrada["en"]
        raise SinConexion("El stock-proxy no responde y no hay datos previos")
    if crudo is None:
        return list(INVENTARIO_DE_PRUEBA), time.time()
    productos = [
        {
            "sku": item["sku"],
            "nombre": item["name"],
            "categoria": item["category"] or "Sin categoría",
            "disponible": item["available"],
            "fisico": item["on_hand"],
        }
        for item in crudo["items"]
    ]
    _cache_proxy["inventario"] = {"valor": productos, "en": time.time()}
    return productos, _cache_proxy["inventario"]["en"]


# ---------------------------------------------------------------------------
# Cliente del order-api (la única escritura: ajustes de inventario)
# ---------------------------------------------------------------------------

def ajustar_en_odoo(ajustes, empleado, motivo):
    """POST /api/stock/ajustes. ajustes: [{sku, cantidad, esperada}].

    Devuelve la respuesta del order-api ({ok, resultados: [...]}) o simula
    los ajustes como aplicados si el order-api no está configurado (modo
    datos de prueba). Errores de conexión suben como SinConexion: el
    ajuste es interactivo y el empleado tiene que enterarse.
    """
    url = os.environ.get("ORDER_API_URL")
    clave = os.environ.get("ORDER_API_KEY")
    if not url or not clave:
        return {"ok": True, "resultados": [
            {"sku": a["sku"], "cantidad": a["cantidad"], "resultado": "aplicado",
             "anterior": a["esperada"]}
            for a in ajustes
        ]}
    cuerpo = {
        "ajustes": ajustes,
        "empleadoId": empleado,
        "fechaHora": datetime.now(ZONA_PANAMA).isoformat(),
        "motivo": motivo,
    }
    try:
        respuesta = httpx.post(f"{url.rstrip('/')}/api/stock/ajustes",
                               headers={"X-API-Key": clave}, json=cuerpo, timeout=30)
    except Exception:
        raise SinConexion("No hay conexión con el servidor de pedidos")
    if respuesta.status_code != 200:
        raise SinConexion(f"El servidor de pedidos respondió {respuesta.status_code}")
    datos = respuesta.json()
    # Un ajuste aplicado cambia el stock: la próxima lectura va fresca.
    if any(r["resultado"] == "aplicado" for r in datos.get("resultados", [])):
        reiniciar_cache_proxy()
    return datos


# ---------------------------------------------------------------------------
# SQLite
# ---------------------------------------------------------------------------

def _ruta_db():
    # Se resuelve en cada conexión (no al importar) para que las pruebas
    # apunten a bases temporales con solo cambiar la variable de entorno.
    return os.environ.get(
        "CONTROL_STOCK_DB", os.path.join(os.path.dirname(__file__), "..", "control-stock.db")
    )


def _db():
    con = sqlite3.connect(_ruta_db())
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    return con


def iniciar_db():
    with _db() as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS empleadas (
            usuario TEXT PRIMARY KEY,
            nombre TEXT NOT NULL,
            hash TEXT NOT NULL,
            activa INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS sesiones (
            token TEXT PRIMARY KEY,
            usuario TEXT NOT NULL,
            creada_en TEXT NOT NULL,
            expira_en TEXT NOT NULL
        );
        -- Historial de alertas de stock crítico. Una alerta pendiente por
        -- SKU como mucho; atendida_en registra quién/cuándo la cerró
        -- ('auto' si el stock se recuperó solo).
        CREATE TABLE IF NOT EXISTS alertas (
            n INTEGER PRIMARY KEY AUTOINCREMENT,
            sku TEXT NOT NULL,
            nombre TEXT NOT NULL,
            cantidad INTEGER NOT NULL,
            creada_en TEXT NOT NULL,
            atendida_en TEXT,
            atendida_por TEXT
        );
        -- Historial de conteos: la hoja PDF semanal y el ciclo quincenal de
        -- Excel. datos guarda el JSON del conteo (diferencias, resultados).
        CREATE TABLE IF NOT EXISTS conteos (
            n INTEGER PRIMARY KEY AUTOINCREMENT,
            tipo TEXT NOT NULL,             -- hoja_pdf | quincenal
            estado TEXT NOT NULL,           -- generado | pendiente | confirmado | descartado
            empleada TEXT NOT NULL,
            creado_en TEXT NOT NULL,
            datos TEXT NOT NULL,
            archivo TEXT
        );
        CREATE TABLE IF NOT EXISTS config (
            clave TEXT PRIMARY KEY,
            valor TEXT NOT NULL
        );
        """)


def ahora_iso():
    return datetime.now(ZONA_PANAMA).isoformat()


# ---------------------------------------------------------------------------
# Configuración
# ---------------------------------------------------------------------------

def umbral():
    """Umbral global de stock crítico (3 si nadie lo cambió)."""
    with _db() as con:
        fila = con.execute("SELECT valor FROM config WHERE clave='umbral'").fetchone()
    return int(fila["valor"]) if fila else 3


def fijar_umbral(valor):
    with _db() as con:
        con.execute("INSERT INTO config (clave, valor) VALUES ('umbral', ?) "
                    "ON CONFLICT(clave) DO UPDATE SET valor=excluded.valor", (str(valor),))


# ---------------------------------------------------------------------------
# Alertas
# ---------------------------------------------------------------------------

def refrescar_alertas(inventario, umbral_actual):
    """Cuadra las alertas con el inventario: crea una pendiente por producto
    crítico nuevo y cierra como 'auto' las de productos que se recuperaron.
    Un físico NEGATIVO (venta sin existencias registradas) también alerta:
    es justo lo que el encargado debe corregir. Se llama en cada carga.
    Alerta desde que un producto entra en 'bajo' (disponible < 2×umbral),
    no solo en crítico, para avisar con más anticipación."""
    criticos = {p["sku"]: p for p in inventario
                if 0 < p["disponible"] < umbral_actual * 2 or p["fisico"] < 0}
    with _db() as con:
        pendientes = {f["sku"]: f for f in con.execute(
            "SELECT n, sku FROM alertas WHERE atendida_en IS NULL")}
        for sku, producto in criticos.items():
            # En una alerta por negativo, la cantidad ES el físico negativo:
            # eso es lo que hay que corregir (y lo que ve el empleado).
            cantidad = producto["fisico"] if producto["fisico"] < 0 else producto["disponible"]
            if sku not in pendientes:
                con.execute(
                    "INSERT INTO alertas (sku, nombre, cantidad, creada_en) VALUES (?,?,?,?)",
                    (sku, producto["nombre"], cantidad, ahora_iso()),
                )
            else:
                # La cantidad de la alerta sigue al inventario.
                con.execute("UPDATE alertas SET cantidad=? WHERE n=?",
                            (cantidad, pendientes[sku]["n"]))
        for sku, fila in pendientes.items():
            if sku not in criticos:
                con.execute(
                    "UPDATE alertas SET atendida_en=?, atendida_por='auto' WHERE n=?",
                    (ahora_iso(), fila["n"]),
                )


def alertas_pendientes():
    with _db() as con:
        return [dict(f) for f in con.execute(
            "SELECT n, sku, nombre, cantidad, creada_en FROM alertas "
            "WHERE atendida_en IS NULL ORDER BY cantidad, nombre")]


def atender_alerta(sku, empleada):
    with _db() as con:
        con.execute(
            "UPDATE alertas SET atendida_en=?, atendida_por=? "
            "WHERE sku=? AND atendida_en IS NULL",
            (ahora_iso(), empleada, sku),
        )


# ---------------------------------------------------------------------------
# Conteos
# ---------------------------------------------------------------------------

def crear_conteo(tipo, estado, empleada, datos_conteo, archivo=None):
    with _db() as con:
        cursor = con.execute(
            "INSERT INTO conteos (tipo, estado, empleada, creado_en, datos, archivo) "
            "VALUES (?,?,?,?,?,?)",
            (tipo, estado, empleada, ahora_iso(), json.dumps(datos_conteo), archivo),
        )
        return cursor.lastrowid


def conteo(n):
    with _db() as con:
        fila = con.execute("SELECT * FROM conteos WHERE n=?", (n,)).fetchone()
    if fila is None:
        return None
    conteo = dict(fila)
    conteo["datos"] = json.loads(conteo["datos"])
    return conteo


def fijar_archivo_conteo(n, archivo):
    with _db() as con:
        con.execute("UPDATE conteos SET archivo=? WHERE n=?", (archivo, n))


def actualizar_conteo(n, estado, datos_conteo):
    with _db() as con:
        con.execute("UPDATE conteos SET estado=?, datos=? WHERE n=?",
                    (estado, json.dumps(datos_conteo), n))


def conteos_recientes(limite=10):
    with _db() as con:
        filas = [dict(f) for f in con.execute(
            "SELECT * FROM conteos ORDER BY n DESC LIMIT ?", (limite,))]
    for fila in filas:
        fila["datos"] = json.loads(fila["datos"])
    return filas


def ultimo_conteo_confirmado():
    """El conteo quincenal confirmado más reciente (para el score)."""
    with _db() as con:
        fila = con.execute(
            "SELECT * FROM conteos WHERE tipo='quincenal' AND estado='confirmado' "
            "ORDER BY n DESC LIMIT 1").fetchone()
    return dict(fila) if fila else None


def ultima_hoja_pdf():
    with _db() as con:
        fila = con.execute(
            "SELECT * FROM conteos WHERE tipo='hoja_pdf' ORDER BY n DESC LIMIT 1").fetchone()
    return dict(fila) if fila else None


def ruta_archivos():
    ruta = os.environ.get(
        "CONTROL_STOCK_ARCHIVOS",
        os.path.join(os.path.dirname(__file__), "..", "archivos"),
    )
    os.makedirs(ruta, exist_ok=True)
    return ruta
