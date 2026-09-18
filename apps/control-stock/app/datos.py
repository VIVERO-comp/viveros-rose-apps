"""Capa de datos: SQLite local, inventario del stock-proxy y ajustes vía order-api.

La app nunca toca Odoo directo (regla fija): lee el inventario del
stock-proxy (`GET /v1/inventario`) y escribe por el order-api: los ajustes
(`POST /api/stock/ajustes`), la altura de la planta de la ficha
(`PUT /api/productos/{sku}/altura`) y el interruptor de la tienda
(`PUT /api/productos/{sku}/publicacion`). El SQLite guarda lo que es de la app: usuarios,
sesiones, alertas atendidas, historial de conteos y configuración.

Degradación de lecturas, igual que Recepción: caché en memoria; si el proxy
falla se sirve el último valor bueno aunque haya vencido; sin configuración
(desarrollo local) se usan datos de prueba. Los ajustes NO se degradan ni se
encolan: son interactivos (el empleado espera la respuesta con el candado de
cantidad esperada) y un error se muestra en pantalla.
"""

import json
import os
import re
import sqlite3
import time
import unicodedata
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx

ZONA_PANAMA = ZoneInfo("America/Panama")

TTL_INVENTARIO = 60  # segundos

# Datos de prueba para desarrollo sin STOCK_PROXY_URL: los del prototipo.
# PL-ALBAHACA sin precio a propósito, para ver el "Precio pendiente".
INVENTARIO_DE_PRUEBA = [
    {"sku": "PL-HIERBA-BUENA", "nombre": "Hierba Buena", "categoria": "Exterior", "disponible": 1, "fisico": 1, "precio_centavos": 350},
    {"sku": "PL-ROMERO", "nombre": "Romero", "categoria": "Exterior", "disponible": 2, "fisico": 2, "precio_centavos": 350},
    {"sku": "PL-CROTON-PETRA", "nombre": "Croton Petra", "categoria": "Exterior", "disponible": 2, "fisico": 2, "precio_centavos": 550},
    {"sku": "PL-ALBAHACA", "nombre": "Albahaca", "categoria": "Exterior", "disponible": 0, "fisico": 0, "precio_centavos": 0},
    {"sku": "PL-LIMON-PERSA", "nombre": "Limón Persa", "categoria": "Exterior", "disponible": 4, "fisico": 4, "precio_centavos": 1200},
    {"sku": "PL-OREGANO", "nombre": "Orégano", "categoria": "Exterior", "disponible": 5, "fisico": 5, "precio_centavos": 350},
    {"sku": "PL-IXORA-ROJA", "nombre": "Ixora Roja", "categoria": "Florales", "disponible": 5, "fisico": 6, "precio_centavos": 650},
    {"sku": "PL-CINTA-VERDE", "nombre": "Cinta Verde", "categoria": "Interior", "disponible": 6, "fisico": 6, "precio_centavos": 450},
    {"sku": "PL-PAPAYA", "nombre": "Papaya", "categoria": "Exterior", "disponible": 11, "fisico": 11, "precio_centavos": 800},
    {"sku": "PL-VERANERA-FUCSIA", "nombre": "Veranera Fucsia", "categoria": "Florales", "disponible": 14, "fisico": 14, "precio_centavos": 750},
    {"sku": "PL-CULANTRO", "nombre": "Culantro", "categoria": "Exterior", "disponible": 26, "fisico": 26, "precio_centavos": 250},
    {"sku": "PL-PALMA-ARECA", "nombre": "Palma Areca", "categoria": "Exterior", "disponible": 41, "fisico": 41, "precio_centavos": 1500, "altura_min": 70, "altura_max": 110},
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
            # list_price de Odoo en centavos; .get por si el proxy en
            # producción aún no expone el campo (0 = precio pendiente).
            "precio_centavos": item.get("price_cents", 0),
            # Altura en cm tal como esta en Odoo (0 = sin dato). .get por si
            # el proxy en produccion aun no expone el campo.
            "altura_min": item.get("height_min_cm", 0),
            "altura_max": item.get("height_max_cm", 0),
            # Casilla "Publicada en la tienda" de Odoo. .get con True por si
            # el proxy en produccion aun no expone el campo: sin el dato la
            # planta cuenta como publicada, que es como estaba el catalogo
            # antes de existir la casilla.
            "publicado": item.get("published", True),
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


def fijar_altura_en_odoo(sku, altura_min, altura_max):
    """PUT /api/productos/{sku}/altura. Alturas en centimetros; 0 = sin dato.

    Devuelve la respuesta del order-api o la simula cuando no esta
    configurado (modo datos de prueba). Un rechazo del order-api sube como
    SinConexion con su mensaje: la altura se edita a mano y quien la guarda
    tiene que enterarse de que no quedo.
    """
    url = os.environ.get("ORDER_API_URL")
    clave = os.environ.get("ORDER_API_KEY")
    if not url or not clave:
        return {"ok": True, "sku": sku, "altura_min": altura_min,
                "altura_max": altura_max, "resultado": "aplicado"}
    try:
        respuesta = httpx.put(
            f"{url.rstrip('/')}/api/productos/{sku}/altura",
            headers={"X-API-Key": clave},
            json={"alturaMin": altura_min, "alturaMax": altura_max},
            timeout=30,
        )
    except Exception:
        raise SinConexion("No hay conexión con el servidor de pedidos")
    if respuesta.status_code != 200:
        detalle = _mensaje_de_error(respuesta)
        raise SinConexion(detalle or
                          f"El servidor de pedidos respondió {respuesta.status_code}")
    # La altura cambió en Odoo: la próxima lectura del inventario va fresca.
    reiniciar_cache_proxy()
    return respuesta.json()


def fijar_publicacion_en_odoo(sku, publicado):
    """PUT /api/productos/{sku}/publicacion. El interruptor de la tienda.

    Desmarcarlo saca la planta de plantaspanama.com en la siguiente
    reconstruccion del sitio y NADA MAS: no archiva el producto, no toca su
    stock ni su precio, y no borra ni desasocia una sola foto (ni las del
    catalogo en Cloudinary, ni las internas de estas apps). Volver a
    marcarlo la repone.

    Devuelve la respuesta del order-api o la simula cuando no esta
    configurado (modo datos de prueba). Un rechazo sube como SinConexion:
    quien aprieta el interruptor tiene que enterarse de que no quedo.
    """
    url = os.environ.get("ORDER_API_URL")
    clave = os.environ.get("ORDER_API_KEY")
    if not url or not clave:
        return {"ok": True, "sku": sku, "publicado": publicado,
                "resultado": "aplicado"}
    try:
        respuesta = httpx.put(
            f"{url.rstrip('/')}/api/productos/{sku}/publicacion",
            headers={"X-API-Key": clave},
            json={"publicado": publicado},
            timeout=30,
        )
    except Exception:
        raise SinConexion("No hay conexión con el servidor de pedidos")
    if respuesta.status_code != 200:
        detalle = _mensaje_de_error(respuesta)
        raise SinConexion(detalle or
                          f"El servidor de pedidos respondió {respuesta.status_code}")
    # La casilla cambió en Odoo: la próxima lectura del inventario va fresca.
    reiniciar_cache_proxy()
    return respuesta.json()


def _mensaje_de_error(respuesta):
    """El motivo que manda el order-api, o None. Su forma de error es
    {"error": codigo, "message": texto}."""
    try:
        return respuesta.json().get("message")
    except Exception:
        return None


# Las categorías que ofrece el formulario de alta: las del sitio (Interior,
# Exterior, Florales) más Paquete para los combos. Son las mismas que acepta
# el order-api; si allá cambian, aquí también.
CATEGORIAS_PLANTA = ("Interior", "Exterior", "Florales", "Paquete")


def sku_sugerido(nombre):
    """PL-NOMBRE-DE-LA-PLANTA a partir del nombre escrito.

    Sin tildes ni eñes (Odoo y las URLs del sitio se llevan mejor con ASCII)
    y sin palabras vacías al final: "Palma Areca" -> PL-PALMA-ARECA.
    """
    limpio = unicodedata.normalize("NFD", nombre or "")
    limpio = "".join(c for c in limpio if unicodedata.category(c) != "Mn")
    limpio = limpio.replace("ñ", "n").replace("Ñ", "N").upper()
    partes = [t for t in re.split(r"[^A-Z0-9]+", limpio) if t]
    return ("PL-" + "-".join(partes))[:79].rstrip("-") if partes else ""


def crear_planta_en_odoo(sku, nombre, categoria, precio_centavos,
                         altura_min=0, altura_max=0, sin_moto=False,
                         costo_centavos=0, nombre_secundario="",
                         nombre_cientifico=""):
    """POST /api/productos. Crea la planta en Odoo y devuelve la respuesta.

    No pone stock (eso va aparte, por ajustar_en_odoo) ni publica nada en la
    tienda. Un rechazo del order-api —SKU repetido, precio absurdo— sube como
    SinConexion con su mensaje, que es lo que ve el empleado en el
    formulario. Sin order-api configurado se simula, como el resto de las
    escrituras en modo datos de prueba.
    """
    url = os.environ.get("ORDER_API_URL")
    clave = os.environ.get("ORDER_API_KEY")
    if not url or not clave:
        return {"ok": True, "sku": sku, "id": 0, "nombre": nombre,
                "resultado": "creado"}
    try:
        respuesta = httpx.post(
            f"{url.rstrip('/')}/api/productos",
            headers={"X-API-Key": clave},
            json={"sku": sku, "nombre": nombre, "categoria": categoria,
                  "precioCentavos": precio_centavos,
                  "costoCentavos": costo_centavos, "alturaMin": altura_min,
                  "alturaMax": altura_max, "sinMoto": sin_moto,
                  "nombreSecundario": nombre_secundario,
                  "nombreCientifico": nombre_cientifico},
            timeout=30,
        )
    except Exception:
        raise SinConexion("No hay conexión con el servidor de pedidos")
    if respuesta.status_code != 200:
        raise SinConexion(_mensaje_de_error(respuesta) or
                          f"El servidor de pedidos respondió {respuesta.status_code}")
    # La planta nueva tiene que aparecer en la lista al volver.
    reiniciar_cache_proxy()
    return respuesta.json()


# ---------------------------------------------------------------------------
# Catálogo publicado en plantaspanama.com (qué plantas están "online")
# ---------------------------------------------------------------------------
# La pestaña "Stock online" es un ESPEJO del sitio, no un interruptor: cada
# build del frontend deja en /catalogo-publicado.json los SKU que salieron
# publicados, y aquí se leen tal cual. Así "online" significa lo que el
# cliente ve de verdad, sin un campo nuevo en Odoo que se pueda desincronizar.
#
# Una planta creada desde esta app NO aparece online por existir: para llegar
# al sitio necesita su foto y una regeneración del catálogo. Por eso vive en
# Stock global hasta que el sitio la publique.

URL_CATALOGO_PUBLICADO = "https://www.plantaspanama.com/catalogo-publicado.json"
TTL_PUBLICADOS = 600  # 10 min: el sitio se reconstruye como mucho cada hora

_cache_publicados = {}


def reiniciar_cache_publicados():
    """Solo para pruebas."""
    _cache_publicados.clear()


def obtener_publicados():
    """SKU publicados hoy en plantaspanama.com. Devuelve (skus, error).

    skus es un set; error es None cuando el dato es bueno. Si el sitio no
    responde se sirve el último valor conocido; si nunca hubo uno, se
    devuelve (None, motivo) y la pestaña lo dice en pantalla en vez de
    mostrar una lista incompleta como si fuera la verdad.
    """
    url = os.environ.get("CATALOGO_PUBLICADO_URL", URL_CATALOGO_PUBLICADO)
    entrada = _cache_publicados.get("skus")
    if entrada and time.time() - entrada["en"] < TTL_PUBLICADOS:
        return entrada["valor"], None
    try:
        respuesta = httpx.get(url, timeout=6)
        respuesta.raise_for_status()
        publicados = set(respuesta.json()["publicados"])
    except Exception as error:
        if entrada:
            return entrada["valor"], None
        return None, f"no se pudo leer el catálogo del sitio ({error})"
    _cache_publicados["skus"] = {"valor": publicados, "en": time.time()}
    return publicados, None


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
        -- Invitaciones del login con Google, gestionadas desde la pestaña
        -- Ajustes. Cada una tiene un link compartible (/invitacion/{token},
        -- de un solo uso) y opcionalmente un email: con email, esa cuenta
        -- también entra directo sin abrir el link. aceptada_en marca cuándo
        -- se usó (la pendiente se puede cancelar; la aceptada se revoca
        -- desactivando a la empleada).
        CREATE TABLE IF NOT EXISTS invitaciones (
            token TEXT PRIMARY KEY,
            email TEXT,
            nombre TEXT NOT NULL DEFAULT '',
            invitada_por TEXT NOT NULL,
            creada_en TEXT NOT NULL,
            aceptada_en TEXT,
            aceptada_email TEXT
        );
        -- Fotos cambiadas desde la propia app (el pincel del modal de foto):
        -- puntero sku -> hash de Cloudinary bajo apps/{sku}/. Gana sobre los
        -- json empaquetados y vive en el volumen /datos, así sobrevive a los
        -- deploys. La foto anterior no se borra de Cloudinary.
        CREATE TABLE IF NOT EXISTS fotos_subidas (
            sku TEXT PRIMARY KEY,
            hash TEXT NOT NULL,
            subida_en TEXT NOT NULL,
            subida_por TEXT NOT NULL
        );
        """)
        # Migración: las empleadas que entran con Google se identifican por
        # email; las de contraseña quedan con email NULL. email_verificado
        # distingue el email confirmado entrando con Google (1) del que la
        # empleada anotó a mano en Mi cuenta (0): solo el verificado cuenta
        # para privilegios de admin (AJUSTES_ADMINS).
        columnas = {c["name"] for c in con.execute("PRAGMA table_info(empleadas)")}
        if "email" not in columnas:
            con.execute("ALTER TABLE empleadas ADD COLUMN email TEXT")
        if "email_verificado" not in columnas:
            con.execute("ALTER TABLE empleadas ADD COLUMN email_verificado "
                        "INTEGER NOT NULL DEFAULT 0")


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
# Fotos cambiadas desde la app
# ---------------------------------------------------------------------------

def fotos_subidas():
    """{sku: hash} de las fotos cambiadas desde la app (una consulta por
    pantalla, no una por producto)."""
    with _db() as con:
        return {f["sku"]: f["hash"] for f in con.execute(
            "SELECT sku, hash FROM fotos_subidas")}


def fijar_foto_subida(sku, hash_foto, empleada):
    with _db() as con:
        con.execute(
            "INSERT INTO fotos_subidas (sku, hash, subida_en, subida_por) "
            "VALUES (?,?,?,?) ON CONFLICT(sku) DO UPDATE SET "
            "hash=excluded.hash, subida_en=excluded.subida_en, "
            "subida_por=excluded.subida_por",
            (sku, hash_foto, ahora_iso(), empleada),
        )


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
