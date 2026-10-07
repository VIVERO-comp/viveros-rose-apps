"""Los 3 estados de una venta y sus dos hechos (items 5-6-7 de Jay).

Especificación: el decision record (docs/vivero-scope-return-to-abraham-
2026-10-04.md §The only states that matter / §How a type converts / §Two
numbers) y la reply del 5/10 §3. Una venta está en exactamente UNO de:

    1 · Acordada, esperando depósito o pago
    2 · Plata confirmada, esperando la entrega o el trabajo
    3 · Entregada y pagada — cerrada

Reglas que este módulo NO negocia:

- **El pago NUNCA escribe «entregada»; la entrega NUNCA escribe «pagada».**
  Son dos HECHOS separados (`pago_confirmado` y `entrega_marcada`), cada
  uno con su rastro (quién, cuándo, monto), y cada escritura toca SOLO sus
  columnas. Hay pruebas que lo clavan en los dos sentidos.
- **El estado 3 exige los dos hechos** — pago confirmado Y entrega
  marcada — nunca uno solo. Ni el system manager puede saltárselo.
- **Conversión por tipo** (decidida acá, en Python, por el tipo de venta
  de la cotización): en plantas (plant retail) el pago del flujo es
  completo, así que pago → 2 y la entrega cierra → 3. En evento, jardín,
  proyecto, PH y mantenimiento **un depósito JAMÁS cierra**: el 3 llega
  solo con la obligación cumplida (entrega marcada) Y los términos
  satisfechos (el saldo cobrado: `pago_completo`).
- **Marcar entregada (y cerrar el 3) es del system manager** — quien
  ocupe el deber en datos_roles (`quien_ocupa("system_manager")`), nunca
  un nombre en código. Subir 1→2 a mano puede cualquiera, pero solo con
  el pago ya registrado.
- Los **términos** de la cotización se guardan con la venta; el default
  sale del tipo de venta (datos_roles.tipos_venta) y un texto distinto
  queda registrado como OVERRIDE con quién, cuándo y qué decía el default.
- Todo cambio de estado deja su fila en `venta_estado_cambio` (historial).

El estado vive en SQLite local (la tabla `venta_estado`), NUNCA en Odoo:
Odoo sigue siendo solo dinero y stock. `origen` distingue las dos listas
locales de Vender: 'venta' (ventas_locales, plantas) y 'servicio'
(cotizaciones_servicio). Los montos que se guardan junto a los hechos
(`pago_monto`, `entrega_monto`) alimentan las dos cifras de `cifras.py`
(pending/delivered revenue) y van SIN ITBMS cuando el que registra lo
sabe — rotulados provisionales allá.

**LA FRONTERA CON EL EMBUDO DE LINEAR (revisión del Arquitecto, 5/10):
esta es una máquina PARALELA al embudo del lead, no su reemplazo.** El
embudo de Linear sigue EXACTAMENTE como está: el pago real en Odoo pone
«Por agendar», la «Hecha» del calendario pone «Entregado», y «Ganado» =
entregado + saldo 0 — nada de eso se toca desde aquí. Este módulo NO
escribe en Linear, ni en Twenty, ni en Odoo (régimen del BLOQUE 12:
hacia los sistemas reales, solo lectura); «Marcar entregada» tampoco —
valida la salida de Odoo vía ventas.validar_salida desde entregas.py,
que es dinero/stock, y nada más. Cómo (y si) se reconcilian el estado
de la venta y el estado del lead es una PREGUNTA ABIERTA para Jay
(docs/DECISIONES-PENDIENTES.md del repo plantaspanama).

**Tiempo (revisión del Arquitecto): los `*_en` de venta_estado y
venta_estado_cambio van en EPOCH (segundos UTC, time.time()).** La
trampa de zonas ya mordió una vez (edad_horas negativa por comparar
texto entre máquinas): nada que compare horas usa texto — quien muestra
decide la zona (texto_de_epoch). `fecha_entrega` es la excepción a
propósito: es una FECHA de calendario elegida por una persona
(AAAA-MM-DD), no un instante — no hay reloj que comparar.

**El historial (venta_estado_cambio) es INMUTABLE: solo INSERT.** Nunca
un UPDATE ni un DELETE sobre esa tabla — una corrección es otra fila
(hay prueba que escanea el código fuente).
"""

import time
from datetime import datetime

from .datos import ZONA_PANAMA, _db, ahora_iso
from . import calculos, datos_roles
from .datos_roles import LARGO_TERMINO, _plano


def _ahora_epoch():
    """El instante de un hecho o un cambio de estado: EPOCH UTC-aware
    (time.time() ya es segundos UTC). Comparar tiempos es con ESTE
    número; el texto es solo de pantalla (texto_de_epoch)."""
    return time.time()


def texto_de_epoch(epoch):
    """Para MOSTRAR un epoch como fecha-hora de Panamá. Nunca para
    comparar: la zona la decide quien muestra, no quien guarda."""
    if not epoch:
        return ""
    try:
        momento = datetime.fromtimestamp(float(epoch), ZONA_PANAMA)
    except (TypeError, ValueError, OSError, OverflowError):
        return ""
    return momento.strftime("%d/%m/%Y %H:%M")

ORIGENES = ("venta", "servicio")

ESTADOS = {
    1: "Acordada · esperando pago",
    2: "Plata confirmada · esperando entrega",
    3: "Entregada y pagada · cerrada",
}

# El chip corto de la lista de Vender (la etiqueta larga es de la ficha).
ETIQUETA_CORTA = {1: "1 · Acordada", 2: "2 · Plata confirmada",
                  3: "3 · Cerrada"}

# Por qué un estado no se puede poner a mano, en palabras de pantalla.
MOTIVO_BLOQUEO = {
    "falta_pago": "falta el pago registrado",
    "faltan_hechos": "exige pago confirmado Y entrega marcada",
    "falta_saldo": "falta cobrar el saldo (los términos del trato)",
    "solo_system_manager": "solo el system manager",
}

# El nombre SEMILLA del tipo que cierra con pago + entrega (su término
# default es pagar completo, así que el pago del flujo ES el saldo). Se
# compara normalizado; un tipo renombrado en Ajustes cae al lado
# CONSERVADOR (el depósito no cierra), nunca al revés.
TIPO_PLANTAS = "plant retail"

# El tipo de venta (vocabulario del decision record, la semilla de
# datos_roles.SEMILLA_TIPOS) de cada tipo de cotización de servicio local
# (cotizaciones.TIPOS). 'general' es el botón Personalizado.
TIPO_DE_SERVICIO = {
    "renta": "rental event",
    "boda": "rental event",
    "evento": "rental event",
    "mantenimiento": "maintenance",
    "paisajismo": "garden",
    "proyecto": "commercial project",
    "instalacion": "garden",
    "general": "other",
}


def iniciar_tablas():
    with _db() as con:
        con.executescript("""
        -- El estado (1|2|3) y los DOS hechos de cada venta local. Cada
        -- hecho escribe SOLO sus columnas: pago_* es del pago, entrega_*
        -- es de la entrega, y ninguno toca las del otro.
        CREATE TABLE IF NOT EXISTS venta_estado (
            origen TEXT NOT NULL,            -- 'venta' | 'servicio'
            venta INTEGER NOT NULL,          -- n local de su tabla
            estado INTEGER NOT NULL DEFAULT 1,
            tipo_venta TEXT NOT NULL DEFAULT '',
            pago_confirmado INTEGER NOT NULL DEFAULT 0,
            pago_completo INTEGER NOT NULL DEFAULT 0,  -- saldo en 0
            pago_monto REAL,                 -- sin ITBMS cuando se sabe
            pago_por TEXT,
            pago_en REAL,                    -- EPOCH UTC (time.time())
            entrega_marcada INTEGER NOT NULL DEFAULT 0,
            fecha_entrega TEXT,              -- la fecha REAL (AAAA-MM-DD)
            entrega_monto REAL,              -- sin ITBMS cuando se sabe
            entrega_por TEXT,
            entrega_en REAL,                 -- EPOCH UTC (time.time())
            PRIMARY KEY (origen, venta)
        );
        -- Historial: todo cambio de estado, con el hecho que lo movió.
        -- INMUTABLE: solo INSERT, nunca UPDATE ni DELETE (una corrección
        -- es otra fila). Hay una prueba que escanea el código fuente.
        CREATE TABLE IF NOT EXISTS venta_estado_cambio (
            n INTEGER PRIMARY KEY AUTOINCREMENT,
            origen TEXT NOT NULL,
            venta INTEGER NOT NULL,
            de INTEGER,
            a INTEGER NOT NULL,
            hecho TEXT NOT NULL,             -- nace | pago | entrega | manual
            detalle TEXT NOT NULL DEFAULT '',
            puesto_por TEXT NOT NULL DEFAULT '',
            puesto_en REAL NOT NULL          -- EPOCH UTC (time.time())
        );
        -- El término guardado con la venta al cotizar (item 5).
        CREATE TABLE IF NOT EXISTS venta_termino (
            origen TEXT NOT NULL,
            venta INTEGER NOT NULL,
            tipo_venta TEXT NOT NULL DEFAULT '',
            termino TEXT NOT NULL DEFAULT '',
            puesto_por TEXT NOT NULL DEFAULT '',
            puesto_en TEXT NOT NULL,
            PRIMARY KEY (origen, venta)
        );
        -- El registro del override: quién cambió el término, cuándo y
        -- qué decía el default en ese momento.
        CREATE TABLE IF NOT EXISTS termino_override (
            n INTEGER PRIMARY KEY AUTOINCREMENT,
            origen TEXT NOT NULL,
            venta INTEGER NOT NULL,
            texto TEXT NOT NULL,
            default_que_habia TEXT NOT NULL DEFAULT '',
            por TEXT NOT NULL DEFAULT '',
            en TEXT NOT NULL
        );
        """)


# ---------------------------------------------------------------------------
# Quién puede qué
# ---------------------------------------------------------------------------

def es_system_manager(usuario):
    """¿Este usuario del login ocupa HOY el deber system_manager? La
    respuesta sale de datos_roles (quien_ocupa), nunca de un nombre en
    código: si Abraham mueve el deber a otro rol, esto lo sigue solo."""
    ocupante = datos_roles.quien_ocupa("system_manager")
    if not ocupante:
        return False
    return any(p["usuario"] == usuario for p in ocupante["personas"])


def _cierra_con_pago(tipo_venta):
    """¿El pago del flujo de este tipo es el pago COMPLETO? Solo plantas
    (plant retail: su default es 100% antes de proceder). Cualquier otro
    tipo —o uno renombrado que ya no calce— cae al lado conservador: el
    depósito deja la venta en 2 y el saldo lo marca alguien después."""
    return _plano(tipo_venta) == _plano(TIPO_PLANTAS)


# ---------------------------------------------------------------------------
# Lectura
# ---------------------------------------------------------------------------

_VACIA = {"estado": 1, "tipo_venta": "", "pago_confirmado": 0,
          "pago_completo": 0, "pago_monto": None, "pago_por": None,
          "pago_en": None, "entrega_marcada": 0, "fecha_entrega": None,
          "entrega_monto": None, "entrega_por": None, "entrega_en": None}


def estado_de(origen, venta):
    """La fila de una venta, o la foto por defecto (estado 1, sin hechos)
    si nunca se abrió — leer jamás crea nada."""
    with _db() as con:
        fila = con.execute(
            "SELECT * FROM venta_estado WHERE origen=? AND venta=?",
            (origen, int(venta))).fetchone()
    if fila is None:
        return {"origen": origen, "venta": int(venta), **_VACIA}
    return dict(fila)


def estados_de():
    """{(origen, venta): fila} de todas las ventas con estado, para pintar
    los chips de la lista de Vender en UNA consulta."""
    with _db() as con:
        filas = con.execute("SELECT * FROM venta_estado").fetchall()
    return {(f["origen"], f["venta"]): dict(f) for f in filas}


def historial_de(origen, venta):
    with _db() as con:
        filas = con.execute(
            "SELECT * FROM venta_estado_cambio WHERE origen=? AND venta=?"
            " ORDER BY n", (origen, int(venta))).fetchall()
    return [dict(f) for f in filas]


# ---------------------------------------------------------------------------
# Escritura: los hechos y el estado
# ---------------------------------------------------------------------------

def _derivado(fila):
    """El estado que los hechos sostienen por sí solos. El 3 exige los DOS
    hechos Y el saldo en 0 — para plantas el pago del flujo ya es completo
    (registrar_pago lo marca así por el tipo)."""
    if (fila["pago_confirmado"] and fila["pago_completo"]
            and fila["entrega_marcada"]):
        return 3
    if fila["pago_confirmado"]:
        return 2
    return 1


def _asegurar(con, origen, venta, tipo_venta="", por="", ahora=None):
    """La fila de la venta, creándola en estado 1 si no existe (con su
    fila 'nace' en el historial). Devuelve el dict actual."""
    if origen not in ORIGENES:
        raise ValueError(f"origen desconocido: {origen}")
    ahora = ahora or _ahora_epoch()
    fila = con.execute(
        "SELECT * FROM venta_estado WHERE origen=? AND venta=?",
        (origen, int(venta))).fetchone()
    if fila is not None:
        if tipo_venta and not fila["tipo_venta"]:
            con.execute("UPDATE venta_estado SET tipo_venta=? WHERE origen=?"
                        " AND venta=?", (tipo_venta, origen, int(venta)))
            return {**dict(fila), "tipo_venta": tipo_venta}
        return dict(fila)
    con.execute(
        "INSERT INTO venta_estado (origen, venta, estado, tipo_venta)"
        " VALUES (?,?,1,?)", (origen, int(venta), tipo_venta or ""))
    con.execute(
        "INSERT INTO venta_estado_cambio (origen, venta, de, a, hecho,"
        " detalle, puesto_por, puesto_en) VALUES (?,?,NULL,1,'nace',?,?,?)",
        (origen, int(venta), f"tipo {tipo_venta or '—'}", por, ahora))
    return {"origen": origen, "venta": int(venta), **_VACIA,
            "tipo_venta": tipo_venta or ""}


def _subir_estado(con, fila_nueva, hecho, detalle, por, ahora):
    """estado = máx(actual, derivado de los hechos): un hecho nunca BAJA
    el estado (bajar es corrección manual del system manager)."""
    nuevo = max(int(fila_nueva["estado"]), _derivado(fila_nueva))
    if nuevo != fila_nueva["estado"]:
        con.execute("UPDATE venta_estado SET estado=? WHERE origen=? AND venta=?",
                    (nuevo, fila_nueva["origen"], fila_nueva["venta"]))
        con.execute(
            "INSERT INTO venta_estado_cambio (origen, venta, de, a, hecho,"
            " detalle, puesto_por, puesto_en) VALUES (?,?,?,?,?,?,?,?)",
            (fila_nueva["origen"], fila_nueva["venta"], fila_nueva["estado"],
             nuevo, hecho, detalle, por, ahora))


def abrir(origen, venta, tipo_venta, por=""):
    """Al crear la venta/cotización: la fila nace en estado 1 con su tipo.
    Idempotente; un tipo que llega tarde solo rellena el hueco."""
    with _db() as con:
        _asegurar(con, origen, venta, tipo_venta, por)


def registrar_pago(origen, venta, por, monto=None, completo=None,
                   detalle=""):
    """El HECHO del pago: quién lo registró, cuándo, cuánto. Escribe SOLO
    las columnas pago_* — jamás la entrega. `completo=None` lo decide el
    TIPO de la venta (conversión por tipo): en plantas el pago del flujo
    es el total; en evento/jardín/proyecto/PH/mantenimiento un pago sin
    más datos es un depósito y NO deja la venta lista para cerrar.
    Sube el estado a lo que los hechos sostengan (nunca lo baja).

    **`pago_monto` ACUMULA los hechos** (fix del review, 5/10): cada
    llamada es un hecho con SU plata —la de ese pago, no el total— y la
    columna guarda la SUMA (depósito $100 + saldo $400 = $500; el
    COALESCE viejo dejaba $400 y las cifras subcontaban). Se eligió
    acumular acá —y no derivar de pago_confirmado— porque es la misma
    regla para TODOS los callers (la cola pasa la plata nueva de cada
    confirmación; Vender pasa el total una sola vez) y cifras.py sigue
    leyendo una columna. Un monto None no toca el acumulado.
    `pago_completo` nunca BAJA: cobrado el saldo, queda cobrado.
    Cada hecho deja SU fila en venta_estado_cambio —con el monto del
    hecho, no el acumulado— aunque el estado no se mueva."""
    ahora = _ahora_epoch()
    with _db() as con:
        fila = _asegurar(con, origen, venta, por=por, ahora=ahora)
        if completo is None:
            completo = _cierra_con_pago(fila["tipo_venta"])
        con.execute(
            "UPDATE venta_estado SET pago_confirmado=1,"
            " pago_completo=MAX(pago_completo, ?),"
            " pago_monto=CASE WHEN ? IS NULL THEN pago_monto"
            "   ELSE ROUND(COALESCE(pago_monto, 0) + ?, 2) END,"
            " pago_por=?, pago_en=? WHERE origen=? AND venta=?",
            (1 if completo else 0, monto, monto, por, ahora,
             origen, int(venta)))
        fila = dict(con.execute(
            "SELECT * FROM venta_estado WHERE origen=? AND venta=?",
            (origen, int(venta))).fetchone())
        actual = int(fila["estado"])
        nuevo = max(actual, _derivado(fila))
        if nuevo != actual:
            con.execute(
                "UPDATE venta_estado SET estado=? WHERE origen=? AND venta=?",
                (nuevo, origen, int(venta)))
        texto = detalle or ("pago completo" if completo else "depósito")
        if monto is not None:
            texto = f"{texto} · {calculos.dinero(round(float(monto), 2))} este hecho"
        con.execute(
            "INSERT INTO venta_estado_cambio (origen, venta, de, a, hecho,"
            " detalle, puesto_por, puesto_en) VALUES (?,?,?,?,'pago',?,?,?)",
            (origen, int(venta), actual, nuevo, texto, por, ahora))
    return estado_de(origen, venta)


def marcar_entregada(origen, venta, por_usuario, por_nombre=None,
                     fecha=None, monto=None):
    """El HECHO de la entrega: SOLO el system manager (quien ocupe el
    deber hoy). Fija la fecha REAL de entrega — la que usará delivered
    revenue — y escribe SOLO las columnas entrega_* — jamás el pago.
    Devuelve (error, fila): error 'solo_system_manager' si quien llama no
    carga el deber (y entonces NO se escribe nada)."""
    if not es_system_manager(por_usuario):
        return "solo_system_manager", estado_de(origen, venta)
    por = por_nombre or por_usuario
    ahora = _ahora_epoch()
    # La fecha es de calendario (la elige el system manager); su default
    # es HOY en Panamá — nunca un reloj de otra máquina.
    fecha = (fecha or datetime.now(ZONA_PANAMA).date().isoformat())[:10]
    with _db() as con:
        fila = _asegurar(con, origen, venta, por=por, ahora=ahora)
        con.execute(
            "UPDATE venta_estado SET entrega_marcada=1, fecha_entrega=?,"
            " entrega_monto=COALESCE(?, entrega_monto), entrega_por=?,"
            " entrega_en=? WHERE origen=? AND venta=?",
            (fecha, monto, por, ahora, origen, int(venta)))
        fila = dict(con.execute(
            "SELECT * FROM venta_estado WHERE origen=? AND venta=?",
            (origen, int(venta))).fetchone())
        _subir_estado(con, fila, "entrega", f"entregada el {fecha}",
                      por, ahora)
    return None, estado_de(origen, venta)


def corregir_fecha_entrega(origen, venta, fecha, por_usuario,
                           por_nombre=None):
    """La entrega real fue OTRO día: el system manager corrige la fecha
    de calendario (la que usa delivered revenue). Solo sobre una entrega
    ya marcada; el historial gana su fila (INSERT, nunca se toca la
    vieja) y el instante del acto (`entrega_en`) queda como estaba — el
    acto pasó cuando pasó."""
    if not es_system_manager(por_usuario):
        return "solo_system_manager"
    fecha = (fecha or "").strip()[:10]
    if len(fecha) != 10:
        return "fecha_invalida"
    por = por_nombre or por_usuario
    with _db() as con:
        fila = con.execute(
            "SELECT estado, entrega_marcada, fecha_entrega FROM venta_estado"
            " WHERE origen=? AND venta=?", (origen, int(venta))).fetchone()
        if fila is None or not fila["entrega_marcada"]:
            return "sin_entrega"
        con.execute(
            "UPDATE venta_estado SET fecha_entrega=? WHERE origen=? AND venta=?",
            (fecha, origen, int(venta)))
        con.execute(
            "INSERT INTO venta_estado_cambio (origen, venta, de, a, hecho,"
            " detalle, puesto_por, puesto_en) VALUES (?,?,?,?,?,?,?,?)",
            (origen, int(venta), fila["estado"], fila["estado"], "entrega",
             f"fecha corregida: {fila['fecha_entrega']} → {fecha}",
             por, _ahora_epoch()))
    return None


def bloqueo_manual(fila, estado, por_usuario):
    """Por qué ESTE usuario no puede poner ese estado a mano sobre esta
    fila (None = puede). La ficha lo usa para pintar los botones con su
    motivo; poner_estado_manual lo usa como EL candado — una sola regla.
    """
    estado = int(estado)
    actual = int(fila["estado"])
    if estado == actual:
        return None
    if estado < actual and not es_system_manager(por_usuario):
        return "solo_system_manager"
    if estado == 2 and not fila["pago_confirmado"]:
        return "falta_pago"
    if estado == 3:
        if not es_system_manager(por_usuario):
            return "solo_system_manager"
        if not (fila["pago_confirmado"] and fila["entrega_marcada"]):
            return "faltan_hechos"
        if not fila["pago_completo"]:
            return "falta_saldo"
    return None


def poner_estado_manual(origen, venta, estado, por_usuario, por_nombre=None):
    """El chip a mano, con candado. Cualquiera sube 1→2 si el pago está
    registrado; el 3 es SOLO del system manager y EXIGE los dos hechos
    (y el saldo en 0); bajar un estado también es del system manager
    (es una corrección). Devuelve None si quedó, o el código de error:
    'falta_pago' · 'faltan_hechos' · 'falta_saldo' · 'solo_system_manager'.
    """
    estado = int(estado)
    if estado not in ESTADOS:
        return "estado_invalido"
    por = por_nombre or por_usuario
    ahora = _ahora_epoch()
    with _db() as con:
        fila = _asegurar(con, origen, venta, por=por, ahora=ahora)
        actual = int(fila["estado"])
        if estado == actual:
            return None
        bloqueo = bloqueo_manual(fila, estado, por_usuario)
        if bloqueo:
            return bloqueo
        con.execute("UPDATE venta_estado SET estado=? WHERE origen=? AND venta=?",
                    (estado, origen, int(venta)))
        con.execute(
            "INSERT INTO venta_estado_cambio (origen, venta, de, a, hecho,"
            " detalle, puesto_por, puesto_en) VALUES (?,?,?,?,'manual','',?,?)",
            (origen, int(venta), actual, estado, por, ahora))
    return None


# ---------------------------------------------------------------------------
# Términos (item 5): el default del tipo, guardado con la venta; el
# override queda registrado con su rastro.
# ---------------------------------------------------------------------------

def tipo_info(tipo_venta):
    """La fila del catálogo tipos_venta que calza con este tipo (por
    nombre normalizado), o None: de ahí salen el término por defecto y si
    el override se ofrece a la vista."""
    for tipo in datos_roles.tipos_venta_activos():
        if _plano(tipo["nombre"]) == _plano(tipo_venta):
            return tipo
    return None


def termino_default_de(tipo_venta):
    """El término por defecto del tipo, leído de datos_roles (Settings es
    quien lo edita). '' si el tipo no está en el catálogo."""
    info = tipo_info(tipo_venta)
    return (info or {}).get("termino_default") or ""


def guardar_termino(origen, venta, tipo_venta, termino, por):
    """Guarda el término de la venta al cotizar. Vacío = el default del
    tipo. Un texto DISTINTO al default queda además registrado en
    termino_override (quién, cuándo, qué decía el default). Devuelve el
    término que quedó guardado."""
    default = termino_default_de(tipo_venta)
    termino = (termino or "").strip()[:LARGO_TERMINO] or default
    ahora = ahora_iso()
    with _db() as con:
        con.execute(
            "INSERT INTO venta_termino (origen, venta, tipo_venta, termino,"
            " puesto_por, puesto_en) VALUES (?,?,?,?,?,?)"
            " ON CONFLICT (origen, venta) DO UPDATE SET tipo_venta=?,"
            " termino=?, puesto_por=?, puesto_en=?",
            (origen, int(venta), tipo_venta, termino, por, ahora,
             tipo_venta, termino, por, ahora))
        if _plano(termino) != _plano(default):
            con.execute(
                "INSERT INTO termino_override (origen, venta, texto,"
                " default_que_habia, por, en) VALUES (?,?,?,?,?,?)",
                (origen, int(venta), termino, default, por, ahora))
    return termino


def termino_de(origen, venta):
    with _db() as con:
        fila = con.execute(
            "SELECT * FROM venta_termino WHERE origen=? AND venta=?",
            (origen, int(venta))).fetchone()
    return dict(fila) if fila else None


def overrides_de(origen, venta):
    with _db() as con:
        filas = con.execute(
            "SELECT * FROM termino_override WHERE origen=? AND venta=?"
            " ORDER BY n", (origen, int(venta))).fetchall()
    return [dict(f) for f in filas]
