"""El proyecto COMPRAS del equipo VIV de Linear: el tablero de lo que se
le compra a los proveedores.

Fase 1 de la pestaña Compras. Este módulo es la **única puerta** al
proyecto COMPRAS, el mismo papel que `linear_leads.py` tiene para el
equipo LEAD: nadie más le habla a esos issues. Lo que esta fase hace es
ver el tablero, crear una compra a mano y corregirla de columna
arrastrando; los movimientos automáticos del embudo, la ficha, el CRM de
proveedores y el calendario son fases de después.

**Un sistema, un trabajo**, igual que con los leads:

- **Linear es el único tablero**: el estado de la compra vive en la
  columna del issue, en ningún otro lado.
- **Odoo es solo dinero y stock**: la orden de compra (`P000xx`), la
  factura del proveedor, los pagos y la entrada al inventario. De Odoo este
  módulo LEE —para pintar cuánto se pagó de cuánto y para sugerir
  proveedores— y lo único que escribe es el contacto del proveedor nuevo,
  con un clic explícito. **La orden de compra y la entrada de stock las
  escribe `compra_odoo.py`**, que es el único que les habla; acá solo queda
  guardado su número (`guardar_orden`).
- **Los proveedores no tocan WhatsApp ni Twenty**: viven como contacto de
  Odoo (`res.partner` con `supplier_rank > 0`). Un proveedor no es un lead.

Lo único que se guarda local es la tabla `compra`, y es **apoyo de
pantalla**: qué se compra, a quién, con qué orden de Odoo y para qué lead.
Nada de estado y nada de plata — esos dos tienen dueño y no es este. Si la
tabla se perdiera, el tablero sigue en pie leyendo Linear: lo que se
pierde es el proveedor y el número de la orden en la tarjeta.

Las reglas del CLAUDE.md que este módulo respeta:

- **Las etiquetas nunca se crean solas.** `_label_id()` solo BUSCA (lo
  mismo que hace `linear_leads._label_id`): si `Resp: Mary` no existe en
  el equipo VIV, queda el aviso en el log y la compra nace sin ella.
- **El responsable va por etiqueta `Resp: <nombre>`, nunca por
  `assignee`.** El assignee es Abraham (`LINEAR_ASSIGNEE_ID`) y jamás el
  bot; sin esa variable el issue nace sin asignar, que sigue sin ser el
  bot.
- **El bot solo firma.** Los comentarios los escribe la key del bot, así
  que el nombre de quien tocó va en el texto (`linear_leads.comentar`).
- **Nada se crea en Linear desde acá salvo el issue de la compra**: ni el
  proyecto, ni las columnas, ni las etiquetas.

Tres modos, los mismos de `linear_leads` y con el MISMO interruptor
(`CALENDARIO_ESCRITURA`), porque es la misma cuenta de Linear:

- Sin `LINEAR_API_KEY`: **modo muestra**, con compras de ejemplo en
  memoria repartidas en las 7 columnas. Es el modo de desarrollo local y
  el de las pruebas.
- Con clave y `CALENDARIO_ESCRITURA` apagado: **solo lectura**.
- Con clave y `CALENDARIO_ESCRITURA=1`: lectura y escritura.

**Medido en el proceso vivo el 30/09/2026 de tarde: el proyecto COMPRAS y
sus 7 columnas SÍ EXISTEN en Linear** (`falta_en_linear()` devuelve "") **y
el módulo `purchase` de Odoo SÍ está instalado**, en producción y en
pruebas desde las 16:15 de ese día, con 0 órdenes todavía.

Hasta esa tarde este párrafo decía lo contrario, y la versión vieja se
citó como estado actual sin volver a medirla. **Lo que un comentario
afirma del mundo envejece; el mundo se le pregunta al proceso vivo.**

La tolerancia del módulo NO se toca por eso: si mañana falta una columna,
las 7 salen vacías con su renglón en palabras simples; si `purchase.order`
no contesta, la tarjeta no pinta barra de plata y nunca inventa un 0. Y no
hay ni un 500 en ninguno de los dos casos. Ver `falta_en_linear` y
`plata_de`.
"""

import os
import time
from datetime import datetime

from . import calculos, calendario, colores, linear_leads, ventas
# `datos` se importa con otro nombre a propósito: varias funciones de este
# módulo ya tienen un parámetro llamado `datos` (el borrador del
# formulario) y dos cosas distintas con el mismo nombre es cómo se cuela un
# bug que solo aparece el día que algo falla. Se importa el MÓDULO y no sus
# funciones para que las pruebas puedan doblar `datos.obtener_inventario`,
# que es lo que hacen hoy.
from . import datos as datos_stock
from .datos import ZONA_PANAMA, _db

TTL_COMPRAS = 60        # segundos de caché de la lista, como en los leads
TTL_CATALOGO = 900      # proyecto, columnas y etiquetas cambian poquísimo
TTL_PLATA = 120         # la cadencia con la que se relee Odoo por detrás


class ErrorCompras(Exception):
    """Falla al hablar con Linear, con el texto que se le muestra a quien
    está usando la pantalla.

    `campo` (opcional) es el name= del campo del formulario que falló,
    cuando el error es de UN campo: la pantalla pinta el mensaje debajo de
    él (regla 5) en vez del banner genérico arriba."""

    def __init__(self, mensaje, campo=""):
        super().__init__(mensaje)
        self.campo = campo


# ---------------------------------------------------------------------------
# Los 7 estados del tablero de compras
#
# `nombre` es el nombre EXACTO de la columna en el equipo VIV de Linear: es
# lo que amarra un estado de aquí con uno de allá. `titulo` es el rótulo
# CORTO de la columna en la pantalla.
#
# Los dos nombres largos son a propósito: el equipo VIV ya tiene las
# columnas `Pedido` y `En camino`, que son de los pedidos en línea de la
# tienda, y mezclarlas sería mover un pedido de un cliente creyendo que se
# mueve una compra a un proveedor. Por eso en Linear se llaman «Pedido a
# proveedor» y «En camino al vivero», y en la pantalla se leen cortas.
# ---------------------------------------------------------------------------

ESTADOS = [
    {"clave": "POR_PEDIR", "nombre": "Por pedir", "titulo": "Por pedir",
     "pie": "hay que pedirlo"},
    {"clave": "COTIZANDO", "nombre": "Cotizando", "titulo": "Cotizando",
     "pie": "esperando precio del proveedor"},
    {"clave": "PEDIDO", "nombre": "Pedido a proveedor", "titulo": "Pedido",
     "pie": "ya se le pidió"},
    {"clave": "ABONADO", "nombre": "Abonado", "titulo": "Abonado",
     "pie": "se le adelantó plata"},
    {"clave": "EN_CAMINO", "nombre": "En camino al vivero", "titulo": "En camino",
     "pie": "viene en camino"},
    {"clave": "RECIBIDO", "nombre": "Recibido", "titulo": "Recibido",
     "pie": "llegó al vivero"},
    {"clave": "CERRADO", "nombre": "Cerrado", "titulo": "Cerrado",
     "pie": "recibido y pagado"},
]

# El color sale de la paleta única, con lookup DIRECTO a propósito: si a
# `asignaciones.estado_compra` de paleta.json le faltara una clave, el
# KeyError salta al IMPORTAR el módulo (la app no arranca y se ve
# enseguida) en vez de dejar una columna sin color que nadie nota. Hay una
# prueba que lo amarra (`tests/test_compras.py`).
for _e in ESTADOS:
    _e["familia"] = colores.ASIGNACIONES["estado_compra"][_e["clave"]]
    _e["color"] = colores.FAMILIAS[_e["familia"]]["solido_hex"]
    _e["chip"] = colores.chip_estilo(_e["familia"])

POR_CLAVE = {e["clave"]: e for e in ESTADOS}
ORDEN = [e["clave"] for e in ESTADOS]

# En qué lugar de la fila va cada columna. Sirve para una sola pregunta —
# «¿esta compra ya se pidió?» (`desde_pedido`)— y no es una escalera que
# degrade sola: las compras se mueven a mano para los dos lados, a
# diferencia del embudo de los leads.
_LUGAR = {clave: i for i, clave in enumerate(ORDEN)}

# La columna donde nace toda compra hecha a mano.
CLAVE_INICIAL = "POR_PEDIR"

# El equipo y el proyecto, por su NOMBRE. El equipo se busca por su `key`
# igual que `linear_leads` busca LEAD: así no hace falta una variable de
# entorno nueva y no se puede apuntar al equipo equivocado por un id mal
# copiado. Linear tiene tope de 2 equipos en el plan actual, y por eso las
# compras viven en VIV (junto al calendario y los pedidos en línea) y no en
# un equipo propio.
EQUIPO_CLAVE = "VIV"
PROYECTO = "COMPRAS"

# El responsable de una compra usa el MISMO prefijo que el de un lead: es
# la misma persona y el mismo vocabulario. La lista de nombres sale de
# `linear_leads.responsables()` (las etiquetas del equipo LEAD), que es la
# única fuente de "quiénes son el equipo" que ya tiene la app; acá solo se
# arma el nombre de la etiqueta que se le busca en VIV.
PREFIJO_RESP = linear_leads.PREFIJO_RESP


# ---------------------------------------------------------------------------
# Configuración
# ---------------------------------------------------------------------------

def configurado():
    """¿Hay credenciales para leer el equipo VIV real?"""
    return linear_leads.configurado()


def escritura_activa():
    """El MISMO interruptor del calendario y de los leads
    (`CALENDARIO_ESCRITURA`): es la misma cuenta de Linear y la misma
    pregunta."""
    return linear_leads.escritura_activa()


def modo():
    if not configurado():
        return "muestra"
    return "escritura" if escritura_activa() else "lectura"


def _exigir_escritura():
    if not configurado():
        return  # modo muestra: se escribe en memoria
    if not escritura_activa():
        raise ErrorCompras(
            "Esta instancia mira el tablero de compras pero no escribe en "
            "Linear. Se enciende con CALENDARIO_ESCRITURA=1.")


def registro_aviso(texto):
    """Un aviso al log. Propio de este módulo a propósito: tomarlo
    prestado de otro es cómo se cuela un NameError que solo aparece el día
    que algo falla de verdad."""
    import logging
    logging.getLogger("control_stock").warning(texto)


def _sin_acentos(texto):
    return linear_leads._sin_acentos(texto)


def _pedir(consulta, variables=None):
    """Un viaje a Linear. Reusa la puerta del calendario: misma API, misma
    key del bot, mismo manejo de errores."""
    try:
        return calendario._pedir(consulta, variables)
    except calendario.ErrorCalendario as fallo:
        raise ErrorCompras(str(fallo)) from fallo


# ---------------------------------------------------------------------------
# La tabla local: apoyo de pantalla y nada más
# ---------------------------------------------------------------------------

def iniciar_tablas():
    with _db() as con:
        # Una fila por compra, con el `VIV-XX` del issue como llave. NO
        # guarda estado (vive en Linear) ni plata (vive en Odoo): solo los
        # datos que la tarjeta necesita mostrar y que Linear no tiene en un
        # campo propio. `lead_ref` es el `LEAD-XX` del lead cliente cuando
        # la compra es para un cliente; vacío cuando es para el vivero.
        con.execute("""
            CREATE TABLE IF NOT EXISTS compra (
                ref TEXT PRIMARY KEY,
                que_compro TEXT NOT NULL DEFAULT '',
                proveedor_id INTEGER,
                proveedor_nombre TEXT NOT NULL DEFAULT '',
                orden_compra_id INTEGER,
                orden_compra_nombre TEXT NOT NULL DEFAULT '',
                lead_ref TEXT NOT NULL DEFAULT '',
                creada TEXT NOT NULL DEFAULT ''
            )
        """)
        # Migración suave: cómo llega la compra al vivero (dueño,
        # 01/10/2026). Nace vacía, o sea «todavía no se dijo»: una compra
        # anotada antes de este cambio no estrena un dato que nadie eligió.
        columnas_compra = {f[1] for f in con.execute(
            "PRAGMA table_info(compra)")}
        if "como_llega" not in columnas_compra:
            con.execute("ALTER TABLE compra ADD COLUMN como_llega TEXT "
                        "NOT NULL DEFAULT ''")
        # QUÉ productos se compran y cuántos. Apoyo de pantalla, igual que
        # la tabla `compra`: el estado sigue en Linear y el dinero en Odoo.
        # Cuando la compra llegue a «Pedido a proveedor» (otra tanda) estas
        # líneas se vuelven la orden de compra de Odoo.
        #
        # `sku` y `nombre` se guardan COPIADOS a propósito: son lo durable.
        # `producto_id` es el id de `product.product` y puede quedar NULL
        # (Odoo no contestó al agregar la línea, o el producto acaba de
        # nacer): la línea no se pierde por eso, y el SKU alcanza para
        # volver a encontrar el producto.
        #
        # `costo` es OPCIONAL y NULL significa «no se sabe», nunca 0 — un 0
        # inventado es la trampa de siempre.
        #
        # El `ref` de una compra a medio llenar es `borrador:<usuario>` (ver
        # `_clave_borrador`): así el carrito del formulario y las líneas de
        # una compra ya creada son LA MISMA tabla y el MISMO código, y
        # crear la compra es re-rotular las filas. Un identifier de Linear
        # nunca lleva ':', así que no hay colisión posible.
        con.execute("""
            CREATE TABLE IF NOT EXISTS compra_linea (
                n INTEGER PRIMARY KEY AUTOINCREMENT,
                ref TEXT NOT NULL,
                producto_id INTEGER,
                sku TEXT NOT NULL DEFAULT '',
                nombre TEXT NOT NULL DEFAULT '',
                cantidad REAL NOT NULL DEFAULT 1,
                costo REAL
            )
        """)
        con.execute("CREATE INDEX IF NOT EXISTS compra_linea_ref "
                    "ON compra_linea (ref)")
        # El formulario a medio llenar, por empleado: sobrevive al viaje a
        # «Crear producto» y vuelta. Mismo patrón que `venta_borrador` de
        # ventas.py. Los PRODUCTOS del borrador no viven acá: viven en
        # `compra_linea` bajo `borrador:<usuario>`.
        con.execute("""
            CREATE TABLE IF NOT EXISTS compra_borrador (
                usuario TEXT PRIMARY KEY,
                que_compro TEXT NOT NULL DEFAULT '',
                proveedor TEXT NOT NULL DEFAULT '',
                resp TEXT NOT NULL DEFAULT '',
                lead_ref TEXT NOT NULL DEFAULT ''
            )
        """)
        # Migración suave del borrador: el teléfono con el que se crea un
        # proveedor nuevo y el «¿cómo llega?» (01/10/2026). Un borrador de
        # antes del cambio sigue abriendo, con los dos en blanco.
        columnas_borrador = {f[1] for f in con.execute(
            "PRAGMA table_info(compra_borrador)")}
        for columna in ("proveedor_tel", "como_llega"):
            if columna not in columnas_borrador:
                con.execute(f"ALTER TABLE compra_borrador ADD COLUMN "
                            f"{columna} TEXT NOT NULL DEFAULT ''")


def _guardar_fila(ref, que_compro="", proveedor_id=None, proveedor_nombre="",
                  lead_ref="", como_llega=""):
    iniciar_tablas()
    with _db() as con:
        con.execute(
            "INSERT INTO compra (ref, que_compro, proveedor_id, "
            "proveedor_nombre, lead_ref, como_llega, creada) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(ref) DO UPDATE SET que_compro = excluded.que_compro, "
            "proveedor_id = excluded.proveedor_id, "
            "proveedor_nombre = excluded.proveedor_nombre, "
            "lead_ref = excluded.lead_ref, "
            "como_llega = excluded.como_llega",
            (ref, (que_compro or "").strip(), proveedor_id,
             (proveedor_nombre or "").strip(), (lead_ref or "").strip(),
             _llegada(como_llega),
             datetime.now(ZONA_PANAMA).isoformat()))


def _filas_locales():
    """{ref: fila} de todo lo guardado, en UNA consulta — el tablero trae
    varias compras y no se le hace una consulta por tarjeta."""
    iniciar_tablas()
    with _db() as con:
        filas = con.execute("SELECT * FROM compra").fetchall()
    return {f["ref"]: dict(f) for f in filas}


def guardar_orden(ref, orden_id, nombre=""):
    """Guarda en la compra el número de su orden de compra de Odoo.

    Lo llama `compra_odoo` justo después de crearla, y se hace ANTES de
    confirmarla a propósito: si confirmar falla, la orden ya existe en Odoo
    y el reintento tiene que encontrarla por su id en vez de crear una
    segunda.

    Parchea la caché y la muestra EN EL MOMENTO, por lo mismo que lo hace
    `_parchear_estado`: sin eso, el aviso diría «orden P00003 creada» y la
    tarjeta seguiría pintando «falta la orden en Odoo» hasta que el refresco
    de fondo terminara.
    """
    ref = (ref or "").strip()
    if not ref:
        return
    orden_id = int(orden_id) if orden_id else None
    nombre = (nombre or "").strip()
    iniciar_tablas()
    with _db() as con:
        cambiadas = con.execute(
            "UPDATE compra SET orden_compra_id = ?, orden_compra_nombre = ? "
            "WHERE ref = ?", (orden_id, nombre, ref)).rowcount
        if not cambiadas:
            # La compra puede no tener fila local: una base recién creada, o
            # un issue que alguien abrió a mano en el proyecto COMPRAS de
            # Linear. Se le abre la fila con lo que se sabe, para que el
            # número de la orden no se pierda por eso.
            con.execute(
                "INSERT INTO compra (ref, orden_compra_id, "
                "orden_compra_nombre, creada) VALUES (?, ?, ?, ?)",
                (ref, orden_id, nombre,
                 datetime.now(ZONA_PANAMA).isoformat()))
    for compra in (_en_cache(ref), None if configurado() else _muestra_uno(ref)):
        if compra is not None:
            compra.update({"orden_compra_id": orden_id,
                           "orden_compra": nombre})
    refrescar()


def desde_pedido(clave):
    """¿Esta columna es «Pedido a proveedor» o una de las de después?

    O sea: ¿esta compra ya se le pidió al proveedor? Es lo que decide si la
    tarjeta tiene que avisar que a Odoo le falta la orden.
    """
    return _LUGAR.get(clave, -1) >= _LUGAR["PEDIDO"]


def falta_la_orden(compra, lineas):
    """¿Esta compra dice «pedida» en Linear y Odoo no se enteró?

    True solo cuando hay algo que pedir de verdad: una compra anotada SIN
    productos no tiene orden que hacer —lo que se compra está en su título,
    y eso es un camino que el formulario ofrece a propósito—, así que no
    estrena una marca de falla que nadie puede resolver.

    La marca no se guarda en ninguna parte: se deduce de lo que hay. Un
    estado derivado no se puede desincronizar de la realidad.
    """
    return bool(desde_pedido((compra or {}).get("estado"))
                and not (compra or {}).get("orden_compra_id")
                and lineas)


# ---------------------------------------------------------------------------
# «¿Cómo llega?» — dato de la compra, no del calendario (dueño, 01/10/2026)
#
# Sirve para saber si hay que mandar a alguien a buscarla, que es la
# pregunta que el dueño hace mirando el tablero. Son cuatro caminos, y los
# dos primeros son sus palabras: «ahora yo traigo por camión o mula» (mula
# es el tráiler).
#
# **OJO, y es el error que hubo que corregir en esta misma tanda:** moto,
# carro y pickup —los de `vehiculos.py`— NO van acá. Esos son los tres
# vehículos con los que el negocio ENTREGA a un cliente, y el camino de la
# mercadería que ENTRA es otro. Mezclarlos ponía a elegir una moto para
# traer 50 sacos de tierra.
#
# Lo que esta tanda NO hace, a propósito: agendar la salida o la llegada en
# el calendario. Eso necesita una etiqueta de Linear que todavía no existe,
# y las etiquetas no se crean desde el código.
# ---------------------------------------------------------------------------

FORMAS_LLEGADA = [
    {"clave": "camion", "titulo": "Viene en camión", "corto": "En camión"},
    {"clave": "mula", "titulo": "Viene en mula (tráiler)", "corto": "En mula"},
    {"clave": "nosotros", "titulo": "La recogemos nosotros",
     "corto": "La recogemos"},
    {"clave": "encomienda", "titulo": "Viene por encomienda",
     "corto": "Por encomienda"},
]

_FORMAS = {f["clave"]: f for f in FORMAS_LLEGADA}


def _llegada(como_llega):
    """La forma de llegada ya limpia, lista para guardar.

    Lo que no sea una de las cuatro cae en "" — «todavía no se dijo». El
    valor llega de un POST y de acá sale a la tarjeta: nada que no esté en
    el vocabulario puede quedarse guardado.
    """
    como_llega = str(como_llega or "").strip()
    return como_llega if como_llega in _FORMAS else ""


def texto_llegada(como_llega):
    """«En camión» para la tarjeta y el panel, o "" si todavía no se dijo.
    Lo decide Python, como todo lo que se pinta."""
    como_llega = _llegada(como_llega)
    return _FORMAS[como_llega]["corto"] if como_llega else ""


# ---------------------------------------------------------------------------
# Las líneas: QUÉ productos se compran y cuántos
#
# Una compra necesita decir qué productos entran, y eso faltaba: el
# formulario solo pedía el texto libre de «qué se compra». Ese texto SE
# QUEDA —es el título del issue, y no todo lo que se compra es un producto
# del catálogo (una herramienta, un flete)—; las líneas son adicionales.
#
# Las líneas del formulario a medio llenar y las de una compra ya creada
# son la MISMA tabla: el borrador usa `borrador:<usuario>` como ref y
# crear la compra es re-rotular esas filas con su `VIV-XX`. Un solo
# camino, un solo juego de funciones.
# ---------------------------------------------------------------------------

_REF_BORRADOR = "borrador:"

# Tope de líneas por compra: ni el formulario ni la tarjeta están pensados
# para cientos, y un POST repetido no debería poder engordar la tabla sin
# fin. Es un límite de pantalla, no una regla del negocio.
MAX_LINEAS = 60


def _clave_borrador(usuario):
    """El `ref` con el que las líneas del formulario de `usuario` viven en
    `compra_linea` mientras la compra todavía no existe."""
    return _REF_BORRADOR + str(usuario or "")


def es_borrador(ref):
    return str(ref or "").startswith(_REF_BORRADOR)


def _cantidad(valor, defecto=1.0):
    """Una cantidad que se pueda comprar: positiva y con tope. Lo ilegible
    cae en `defecto` en vez de reventar el formulario."""
    numero = ventas._num_positivo(valor, defecto=None)
    if numero is None or numero <= 0:
        return defecto
    return min(numero, 100000.0)


def _costo(valor):
    """El costo unitario, o None. **None es «no se sabe» y no 0**: una
    compra se puede anotar antes de saber el precio, y pintar $0.00 ahí
    sería inventar un número."""
    crudo = str(valor if valor is not None else "").strip()
    if not crudo:
        return None
    numero = ventas._num_positivo(crudo, defecto=None, permitir_cero=True)
    if numero is None:
        return None
    return round(numero, 2)


def _linea(fila):
    costo = fila["costo"]
    return {
        "n": fila["n"], "ref": fila["ref"],
        "producto_id": fila["producto_id"], "sku": fila["sku"] or "",
        "nombre": fila["nombre"] or "", "cantidad": fila["cantidad"],
        "costo": costo,
        # El subtotal solo existe si el costo existe: sin costo no se pinta
        # nada, nunca un 0.
        "importe": (None if costo is None
                    else round(fila["cantidad"] * costo, 2)),
        # Lo que va en los campos del formulario, ya con formato: `3` y no
        # `3.0`, y vacío cuando el costo no se sabe. Lo decide Python, como
        # todo lo que se pinta.
        "cantidad_texto": _cantidad_bonita(fila["cantidad"]),
        "costo_texto": "" if costo is None else f"{costo:.2f}",
    }


def lineas_de(ref):
    """Las líneas de esa compra (o del borrador), en el orden en que se
    agregaron."""
    iniciar_tablas()
    with _db() as con:
        filas = con.execute(
            "SELECT * FROM compra_linea WHERE ref = ? ORDER BY n",
            (str(ref or ""),)).fetchall()
    return [_linea(f) for f in filas]


def lineas_de_varias(refs):
    """{ref: [líneas]} en UNA consulta — el tablero trae varias compras y
    no se le hace una consulta por tarjeta (la misma regla que
    `_filas_locales`)."""
    refs = [r for r in {str(r or "") for r in refs} if r]
    if not refs:
        return {}
    iniciar_tablas()
    marcas = ",".join("?" * len(refs))
    with _db() as con:
        filas = con.execute(
            f"SELECT * FROM compra_linea WHERE ref IN ({marcas}) ORDER BY n",
            refs).fetchall()
    por_ref = {}
    for fila in filas:
        por_ref.setdefault(fila["ref"], []).append(_linea(fila))
    return por_ref


def resumen_de_lineas(lineas, cuantos=2):
    """El renglón de la tarjeta: «3 productos · Tierra negra, Abono +1».

    Lo arma Python y no la plantilla, como el resto de lo que se pinta.
    """
    lineas = list(lineas or ())
    if not lineas:
        return ""
    nombres = [l["nombre"] or l["sku"] for l in lineas]
    visibles = nombres[:cuantos]
    sobran = len(nombres) - len(visibles)
    texto = ", ".join(visibles) + (f" +{sobran}" if sobran else "")
    cuenta = f"{len(lineas)} producto" + ("s" if len(lineas) != 1 else "")
    return f"{cuenta} · {texto}"


def total_de_lineas(lineas):
    """Lo que se sabe que va a costar: la suma de las líneas CON costo.

    `{"total", "sin_costo"}`. `sin_costo` es cuántas líneas no tienen
    costo todavía, para que la pantalla pueda decir «al menos» en vez de
    dar un total como si estuviera completo.
    """
    lineas = list(lineas or ())
    total = sum(l["importe"] for l in lineas if l["importe"] is not None)
    return {"total": round(total, 2),
            "sin_costo": sum(1 for l in lineas if l["costo"] is None)}


def agregar_linea(ref, producto_id=None, sku="", nombre="", cantidad=1,
                  costo=None):
    """Una línea más. Devuelve ("aviso", "error").

    Un producto que ya está en la lista no se duplica: se le SUMA la
    cantidad. Agregar dos veces el mismo saco de tierra queriendo decir
    «dos sacos» es lo natural, y dos renglones iguales en la orden de
    compra de Odoo serían un error para alguien después.
    """
    ref = str(ref or "")
    sku = (sku or "").strip()
    nombre = (nombre or "").strip()
    if not ref:
        return "", "No llegó a qué compra agregar el producto."
    if not sku and not nombre:
        return "", "Elegí un producto de la lista."
    cantidad = _cantidad(cantidad)
    iniciar_tablas()
    with _db() as con:
        gemela = None
        if sku:
            gemela = con.execute(
                "SELECT * FROM compra_linea WHERE ref = ? AND sku = ?",
                (ref, sku)).fetchone()
        if gemela is not None:
            nueva = _cantidad(gemela["cantidad"] + cantidad)
            con.execute("UPDATE compra_linea SET cantidad = ? WHERE n = ?",
                        (nueva, gemela["n"]))
            return (f"{gemela['nombre'] or sku}: "
                    f"{_cantidad_bonita(nueva)} en total."), ""
        cuantas = con.execute(
            "SELECT count(*) FROM compra_linea WHERE ref = ?",
            (ref,)).fetchone()[0]
        if cuantas >= MAX_LINEAS:
            return "", (f"Esta compra ya tiene {MAX_LINEAS} productos, que es "
                        f"el tope de la pantalla. Anotá el resto en otra "
                        f"compra.")
        con.execute(
            "INSERT INTO compra_linea (ref, producto_id, sku, nombre, "
            "cantidad, costo) VALUES (?, ?, ?, ?, ?, ?)",
            (ref, producto_id, sku, nombre or sku, cantidad, _costo(costo)))
    return f"{nombre or sku} agregado.", ""


def quitar_linea(ref, n):
    """Saca esa línea. Devuelve ("aviso", "error")."""
    ref = str(ref or "")
    try:
        n = int(n)
    except (TypeError, ValueError):
        return "", "No llegó cuál producto quitar."
    iniciar_tablas()
    with _db() as con:
        fila = con.execute(
            "SELECT * FROM compra_linea WHERE ref = ? AND n = ?",
            (ref, n)).fetchone()
        if fila is None:
            return "", "Ese producto ya no está en la lista."
        con.execute("DELETE FROM compra_linea WHERE n = ?", (n,))
    return f"{fila['nombre'] or fila['sku']} quitado.", ""


def guardar_cantidades(ref, cantidades=None, costos=None):
    """Las cantidades y los costos que el empleado escribió en la lista.

    Las dos llegan como `{n: valor}`. **Un `n` que no sea de esta compra se
    ignora**: el formulario manda lo que tiene en pantalla y un POST a mano
    no puede tocar las líneas de otra.
    """
    ref = str(ref or "")
    if not ref or not (cantidades or costos):
        return
    iniciar_tablas()
    with _db() as con:
        mios = {f["n"]: f for f in con.execute(
            "SELECT * FROM compra_linea WHERE ref = ?", (ref,)).fetchall()}
        for clave, valor in (cantidades or {}).items():
            fila = mios.get(_entero(clave))
            if fila is None:
                continue
            con.execute("UPDATE compra_linea SET cantidad = ? WHERE n = ?",
                        (_cantidad(valor, defecto=fila["cantidad"]), fila["n"]))
        for clave, valor in (costos or {}).items():
            fila = mios.get(_entero(clave))
            if fila is None:
                continue
            con.execute("UPDATE compra_linea SET costo = ? WHERE n = ?",
                        (_costo(valor), fila["n"]))


def _entero(valor):
    try:
        return int(valor)
    except (TypeError, ValueError):
        return None


def _cantidad_bonita(numero):
    """`3` y no `3.0`; `2.5` se queda en `2.5`."""
    numero = float(numero or 0)
    return str(int(numero)) if numero == int(numero) else f"{numero:g}"


# ---------------------------------------------------------------------------
# El borrador: la compra a medio llenar sobrevive al viaje a «Crear producto»
#
# El empleado busca una planta, no está, se va a crearla y vuelve: TODO lo
# que había escrito tiene que seguir ahí, más el producto nuevo ya agregado.
# Eso es «sin sacarlo de la misma pestaña» (dueño, 30/09/2026).
#
# Se resuelve sin una línea de JavaScript: el formulario se guarda en el
# servidor antes de cada viaje (POST que redirige), igual que
# `venta_borrador` en ventas.py.
# ---------------------------------------------------------------------------

CAMPOS_BORRADOR = ("que_compro", "proveedor", "proveedor_tel", "resp",
                   "lead_ref", "como_llega")

LARGO_BORRADOR = {"que_compro": 250, "proveedor": 120, "proveedor_tel": 40,
                  "resp": 60, "lead_ref": 30, "como_llega": 20}


def _limpiar_borrador(datos):
    """Los campos del formulario, recortados y con el «¿cómo llega?»
    pasado por su lista de permitidos.

    El recorte por largo es para todos; `como_llega` además no puede
    guardar nada que no sea del vocabulario, porque llega de un POST y de
    acá sale a la tarjeta.
    """
    limpio = {campo: (str(datos.get(campo) or "").strip()
                      [:LARGO_BORRADOR[campo]])
              for campo in CAMPOS_BORRADOR}
    limpio["como_llega"] = _llegada(limpio["como_llega"])
    return limpio


def guardar_borrador(usuario, datos=None, cantidades=None, costos=None):
    """Lo que hay escrito en el formulario ahora mismo.

    `datos` en None deja los campos como estaban (hay POSTs que solo tocan
    las cantidades). Las líneas no se tocan acá: tienen sus propias
    funciones.
    """
    iniciar_tablas()
    if datos is not None:
        limpio = _limpiar_borrador(datos)
        campos = ", ".join(CAMPOS_BORRADOR)
        marcas = ",".join("?" * (len(CAMPOS_BORRADOR) + 1))
        pone = ", ".join(f"{c}=excluded.{c}" for c in CAMPOS_BORRADOR)
        with _db() as con:
            con.execute(
                f"INSERT INTO compra_borrador (usuario, {campos})"
                f" VALUES ({marcas})"
                f" ON CONFLICT(usuario) DO UPDATE SET {pone}",
                (str(usuario), *(limpio[c] for c in CAMPOS_BORRADOR)))
    guardar_cantidades(_clave_borrador(usuario), cantidades, costos)


def borrador_de(usuario):
    """{que_compro, proveedor, resp, lead_ref, lineas, total} del formulario
    en curso. Siempre devuelve el dict completo, vacío si no hay nada."""
    iniciar_tablas()
    with _db() as con:
        fila = con.execute(
            "SELECT * FROM compra_borrador WHERE usuario = ?",
            (str(usuario),)).fetchone()
    datos = {campo: ((fila[campo] or "") if fila else "")
             for campo in CAMPOS_BORRADOR}
    lineas = lineas_de(_clave_borrador(usuario))
    return {**datos, "lineas": lineas, "total": total_de_lineas(lineas)}


def borrador_con_algo(usuario):
    """¿Hay una compra a medio llenar?

    Decide si la pantalla abre el formulario sola: quien volvió de crear un
    producto —o de cualquier otra pestaña— tiene que encontrar su trabajo,
    no el tablero. Lo descarta «Mejor no».
    """
    borrador = borrador_de(usuario)
    return bool(borrador["lineas"]
                or any(borrador[campo] for campo in CAMPOS_BORRADOR))


def agregar_al_borrador(usuario, producto_id=None, sku="", nombre="",
                        cantidad=1, costo=None):
    return agregar_linea(_clave_borrador(usuario), producto_id=producto_id,
                         sku=sku, nombre=nombre, cantidad=cantidad,
                         costo=costo)


def quitar_del_borrador(usuario, n):
    return quitar_linea(_clave_borrador(usuario), n)


def descartar_borrador(usuario):
    """Tira el formulario a medio llenar, líneas incluidas."""
    iniciar_tablas()
    clave = _clave_borrador(usuario)
    with _db() as con:
        con.execute("DELETE FROM compra_borrador WHERE usuario = ?",
                    (str(usuario),))
        con.execute("DELETE FROM compra_linea WHERE ref = ?", (clave,))


def _mudar_lineas_del_borrador(usuario, ref):
    """Las líneas del borrador pasan a ser las de la compra `ref`.

    Re-rotular en vez de copiar y borrar: no hay un instante en el que las
    líneas estén en los dos lados ni en ninguno, y los `n` se conservan.
    """
    iniciar_tablas()
    with _db() as con:
        con.execute("UPDATE compra_linea SET ref = ? WHERE ref = ?",
                    (str(ref), _clave_borrador(usuario)))


# ---------------------------------------------------------------------------
# El buscador de productos del formulario
#
# Tiene que encontrar PLANTAS, MACETAS e INSUMOS: las tres cosas que el
# vivero le compra a un proveedor. El camino es el MISMO que usa Vender
# (`ventas.buscar_productos`, XML-RPC contra `product.product`), con otros
# dos argumentos — no un segundo buscador.
# ---------------------------------------------------------------------------

# Los tres prefijos de SKU del catálogo (`altas.PREFIJO_DE` + el PL- de las
# plantas, que es de siempre). Hoy el inventario trae 116 SKU entre los
# tres.
PREFIJOS_COMPRA = ("PL-", "MC-", "IN-")

# `sale_ok` NO se le pide al buscador de compras: un insumo que se compra
# no tiene por qué estar a la venta, y filtrarlo lo esconderia justo de la
# pantalla que lo necesita.
SOLO_VENDIBLES_EN_COMPRAS = False


def buscar_productos(texto):
    """Plantas, macetas e insumos por nombre o SKU, **con lo que hay de
    cada uno en el vivero**.

    `{"ok": True, "productos": [...]}`, o `{"ok": False, "error": …}` si
    Odoo no contestó. **Una lista vacía es «no hay», nunca «no sé»**: la
    diferencia tiene que llegar a la pantalla, que es la que ofrece «Crear
    producto» cuando de verdad no hay.
    """
    texto = (texto or "").strip()
    if not texto:
        return {"ok": True, "error": "", "productos": []}
    if not ventas.configurado():
        return {"ok": True, "error": "",
                "productos": con_stock(_muestra_buscar(texto))}
    try:
        productos = ventas.buscar_productos(
            texto, prefijos=PREFIJOS_COMPRA,
            solo_vendibles=SOLO_VENDIBLES_EN_COMPRAS)
    except Exception as error:
        return {"ok": False, "error": _error(error), "productos": []}
    return {"ok": True, "error": "", "productos": con_stock(productos)}


# ---------------------------------------------------------------------------
# Cuánto hay de cada producto (dueño, 01/10/2026)
#
# Es la información que hace falta justo cuando se está decidiendo qué
# comprar, y el buscador no la daba. Sale de donde ya la lee Stock y la
# lee Vender (`datos.obtener_inventario`, mismo caché y mismo TTL): no se
# inventa una segunda fuente de la verdad del stock.
#
# **`disponible` en None es «no se sabe» y JAMÁS un 0.** Pasa en dos casos
# reales: el inventario no contestó, o ese SKU no viene en el inventario
# (el stock-proxy sirve los prefijos de su `CATALOGO_FILTRO`, que es una
# variable del droplet y no se puede afirmar desde acá qué trae hoy). Las
# dos veces la pantalla tiene que decir que no se sabe — un 0 inventado
# manda a comprar lo que ya está lleno, o al revés.
# ---------------------------------------------------------------------------

def _inventario_por_sku():
    """{sku: disponible}, o None si no se pudo leer el inventario.

    None y {} son distintos a propósito: `{}` es un inventario vacío de
    verdad y None es «no se pudo preguntar».
    """
    try:
        inventario, _leido_en = datos_stock.obtener_inventario()
    except Exception:
        return None
    return {p["sku"]: p["disponible"] for p in inventario}


def _stock_pintado(disponible):
    """{disponible, stock_texto, stock_cero} — lo que la plantilla pinta
    tal cual, sin decidir nada."""
    if disponible is None:
        return {"disponible": None, "stock_texto": "stock: no se sabe",
                "stock_cero": False}
    numero = _cantidad_bonita(disponible)
    if disponible <= 0:
        return {"disponible": disponible, "stock_texto": "sin stock",
                "stock_cero": True}
    return {"disponible": disponible, "stock_texto": f"{numero} en stock",
            "stock_cero": False}


def con_stock(productos):
    """Los resultados del buscador con su stock y el texto ya resuelto."""
    productos = list(productos or ())
    if not productos:
        return productos
    stock = _inventario_por_sku()
    return [{**p, **_stock_pintado(None if stock is None
                                   else stock.get(p["sku"]))}
            for p in productos]


# Cuántos renglones de «lo que está bajo» se pintan. Es un límite de
# pantalla: con el umbral en 3 el vivero puede tener decenas de SKU bajos y
# la lista no es un inventario, es un atajo para agregar sin escribir.
MAX_BAJOS = 40


def bajos():
    """Lo que está bajo o en cero, para agregarlo sin escribir el nombre.

    `{"ok", "error", "productos", "cuantos", "umbral", "sobran"}`.

    **Qué es «bajo» NO lo inventa esta pantalla**: es el mismo umbral
    global de Stock (`datos.umbral()`, 3 si nadie lo cambió) con la misma
    regla de `calculos.estado` — agotada, crítico o bajo, o sea menos de
    dos veces el umbral. El día que el dueño cambie ese número en Ajustes,
    esta lista cambia con él. (El mínimo POR PRODUCTO todavía no existe en
    la app; cuando exista, este es el lugar.)

    Lo más vacío arriba: es lo que hay que comprar primero. Un stock
    negativo —físico negativo en Odoo, que pasa— sube igual y no se
    disfraza de 0.
    """
    try:
        # Las dos lecturas DENTRO del try: el umbral sale de la tabla
        # `config` de SQLite y el inventario del proxy, y ni una ni otra
        # pueden volver esta pantalla un 500. Si algo falla, `umbral` sale
        # en None —«no se sabe»— y la lista no se pinta.
        umbral = datos_stock.umbral()
        inventario, _leido_en = datos_stock.obtener_inventario()
    except Exception as error:
        return {"ok": False, "error": _error(error), "productos": [],
                "cuantos": 0, "umbral": None, "sobran": 0}
    flojos = [p for p in inventario
              if calculos.estado(p["disponible"], umbral) != "ok"]
    flojos.sort(key=lambda p: (p["disponible"], _sin_acentos(p["nombre"])))
    productos = [{"sku": p["sku"], "nombre": p["nombre"] or p["sku"],
                  "categoria": p.get("categoria") or "",
                  **_stock_pintado(p["disponible"])}
                 for p in flojos[:MAX_BAJOS]]
    return {"ok": True, "error": "", "productos": productos,
            "cuantos": len(flojos), "umbral": umbral,
            "sobran": max(0, len(flojos) - len(productos))}


def producto_por_sku(sku):
    """{id, sku, nombre, precio} del producto recién creado, o None.

    Se usa al volver de «Crear producto»: el alta devuelve el id del
    `product.template` y las líneas guardan el de `product.product`, que no
    es el mismo. Si Odoo no contesta, **None no pierde la línea**: quien
    llama la agrega con el SKU y el nombre, que es lo durable.
    """
    sku = (sku or "").strip()
    if not sku:
        return None
    if not ventas.configurado():
        return next((p for p in _CATALOGO_MUESTRA if p["sku"] == sku), None)
    try:
        filas = ventas._ejecutar(
            "product.product", "search_read", [[["default_code", "=", sku]]],
            {"fields": ["default_code", "name", "list_price"], "limit": 1,
             "context": {"active_test": False}})
    except Exception as error:
        registro_aviso(f"No se pudo leer en Odoo el producto {sku} recién "
                       f"creado: {_error(error)}")
        return None
    if not filas:
        return None
    fila = filas[0]
    return {"id": fila["id"], "sku": fila.get("default_code") or sku,
            "nombre": fila.get("name") or "",
            "precio": fila.get("list_price") or 0.0}


# ---------------------------------------------------------------------------
# Catálogo del equipo VIV (proyecto, columnas y etiquetas), cacheado
# ---------------------------------------------------------------------------

# `first: 1` en `teams` no es decoración: Linear cobra la complejidad de una
# consulta multiplicando los límites de las listas anidadas, y sin `first`
# asume 50 equipos — con eso la respuesta es "Query too complex" y el
# catálogo se cae entero (ya pasó con el equipo LEAD). Hay un solo equipo
# VIV: pedir uno es lo correcto y cabe de sobra.
CONSULTA_CATALOGO = """
query {
  teams(first: 1, filter: { key: { eq: "%s" } }) {
    nodes {
      id
      states(first: 40) { nodes { id name type } }
      labels(first: 60) { nodes { id name isGroup parent { name } } }
      projects(first: 30) { nodes { id name } }
    }
  }
}
""" % EQUIPO_CLAVE

_catalogo_cache = {"en": 0, "dato": None}


def catalogo(refrescar=False):
    """{"equipo", "proyecto", "estados": {clave: id},
    "etiquetas": {nombre: id}} del equipo VIV.

    `proyecto` es None mientras COMPRAS no exista en Linear, y `estados`
    trae SOLO las columnas que ya existen: es el estado del mundo de hoy y
    no es un error — la pantalla lo cuenta con `falta_en_linear()`.

    Igual que el resto de la app: si hay catálogo guardado sale YA y, si
    venció el TTL, Linear se consulta por detrás.
    """
    if _catalogo_cache["dato"] and not refrescar:
        if time.time() - _catalogo_cache["en"] >= TTL_CATALOGO:
            calendario._en_fondo("compras-catalogo",
                                 lambda: catalogo(refrescar=True))
        return _catalogo_cache["dato"]

    datos = _pedir(CONSULTA_CATALOGO)
    equipos = (datos.get("teams") or {}).get("nodes") or []
    if not equipos:
        raise ErrorCompras(f"Linear no tiene el equipo {EQUIPO_CLAVE}.")
    equipo = equipos[0]

    estados = {}
    for estado in (equipo.get("states") or {}).get("nodes") or []:
        for clave, ficha in POR_CLAVE.items():
            if _sin_acentos(estado.get("name")) == _sin_acentos(ficha["nombre"]):
                estados[clave] = estado["id"]

    proyecto = None
    for p in (equipo.get("projects") or {}).get("nodes") or []:
        if _sin_acentos(p.get("name")) == _sin_acentos(PROYECTO):
            proyecto = p["id"]

    etiquetas = {}
    for label in (equipo.get("labels") or {}).get("nodes") or []:
        if not label.get("isGroup"):
            etiquetas[label["name"]] = label["id"]

    dato = {"equipo": equipo["id"], "proyecto": proyecto, "estados": estados,
            "etiquetas": etiquetas}
    _catalogo_cache.update({"en": time.time(), "dato": dato})
    return dato


def columnas_que_faltan():
    """Los NOMBRES de las columnas del tablero que Linear todavía no tiene.

    Lista vacía cuando están las 7 (y en modo muestra, donde el tablero de
    ejemplo hace de Linear).
    """
    if not configurado():
        return []
    try:
        estados = catalogo()["estados"]
    except ErrorCompras:
        return [e["nombre"] for e in ESTADOS]
    return [e["nombre"] for e in ESTADOS if e["clave"] not in estados]


def falta_en_linear():
    """Qué le falta a Linear para que esta pantalla sirva de verdad, en
    palabras simples, o "" si no falta nada.

    Nunca revienta y nunca es un 500: hoy (30/09/2026) el proyecto COMPRAS
    y sus columnas no existen todavía, y eso es un renglón en la pantalla,
    no una falla. El código no los crea — los crea Abraham en Linear,
    igual que las etiquetas.
    """
    if not configurado():
        return ""
    try:
        cat = catalogo()
    except ErrorCompras as fallo:
        return (f"No se pudo leer Linear, así que el tablero está vacío: "
                f"{fallo}")
    if not cat["proyecto"]:
        return (f"El proyecto {PROYECTO} todavía no existe en el equipo "
                f"{EQUIPO_CLAVE} de Linear. El tablero se queda vacío hasta "
                f"que alguien lo cree allá: el código no crea proyectos ni "
                f"columnas.")
    faltan = columnas_que_faltan()
    if faltan:
        return ("Al equipo %s de Linear le faltan estas columnas: %s. Las "
                "compras que caigan en ellas no se van a poder mover hasta "
                "que existan." % (EQUIPO_CLAVE, ", ".join(faltan)))
    return ""


def listo():
    """¿Linear ya tiene todo lo que este tablero necesita?"""
    return not falta_en_linear()


def _label_id(nombre):
    """El id de la etiqueta `nombre` en el equipo VIV, o None.

    NUNCA la crea (regla que no se rompe): una etiqueta creada al vuelo
    nace suelta, fuera de su grupo, y ensucia el vocabulario — ya pasó una
    vez. Si no existe, queda el aviso en el log y quien llama sigue sin
    ella.
    """
    etiquetas = catalogo()["etiquetas"]
    if nombre in etiquetas:
        return etiquetas[nombre]
    # Puede ser una etiqueta recién creada a mano en Linear: se refresca el
    # catálogo UNA vez antes de darla por inexistente.
    etiquetas = catalogo(refrescar=True)["etiquetas"]
    if nombre in etiquetas:
        return etiquetas[nombre]
    _aviso_sin_etiqueta(nombre)
    return None


def _aviso_sin_etiqueta(nombre):
    registro_aviso(f'La etiqueta "{nombre}" no existe en el equipo '
                   f'{EQUIPO_CLAVE} de Linear y no se crea sola: la compra '
                   f'queda sin ella.')


def resp_label_disponible(nombre):
    """¿Ya existe la etiqueta `Resp: <nombre>` en el equipo VIV?

    Son dos preguntas distintas y no se mezclan: si la persona está en el
    equipo lo dice `linear_leads.responsables()` (las etiquetas del equipo
    LEAD, la única lista de "quiénes somos" que tiene la app); si su
    etiqueta ya existe EN VIV lo dice esto. Lo segundo puede ser False con
    lo primero True — las etiquetas las crea Abraham equipo por equipo — y
    entonces la compra se crea igual, sin responsable y con el aviso en el
    log.

    En modo muestra solo ALGUNAS existen a propósito
    (`_MUESTRA_RESP_EN_VIV`), igual que `linear_leads` hace con las
    señales: así se prueba ese candado sin tocar Linear.
    """
    nombre = (nombre or "").strip()
    if not nombre:
        return False
    if not configurado():
        return nombre in _MUESTRA_RESP_EN_VIV
    try:
        return (PREFIJO_RESP + nombre) in catalogo()["etiquetas"]
    except ErrorCompras:
        return False


# ---------------------------------------------------------------------------
# Lectura del tablero
# ---------------------------------------------------------------------------

CAMPOS = """
  id identifier title description url createdAt
  state { id name type }
  labels(first: 20) { nodes { id name } }
"""

# Los issues del proyecto COMPRAS del equipo VIV. Filtra por NOMBRE de
# proyecto para no depender de un id en una variable de entorno: si el
# proyecto todavía no existe, esto devuelve cero nodos y el tablero sale
# vacío con su renglón, que es justo lo que tiene que pasar hoy.
CONSULTA_LISTA = """
query { issues(first: 250, orderBy: updatedAt, filter: {
    team: { key: { eq: "%s" } }
    project: { name: { eq: "%s" } }
  }) { nodes { %s } }
}
""" % (EQUIPO_CLAVE, PROYECTO, CAMPOS)


def _estado_de(issue):
    """La clave del tablero de un issue, por el NOMBRE de su columna.

    Por nombre y no por tipo porque varias columnas son del mismo tipo
    `started` en Linear: solo el nombre las distingue. Una columna que no
    es del tablero de compras cae en "".
    """
    nombre = (issue.get("state") or {}).get("name") or ""
    for clave, ficha in POR_CLAVE.items():
        if _sin_acentos(nombre) == _sin_acentos(ficha["nombre"]):
            return clave
    return ""


def _normalizar(issue, locales=None):
    etiquetas = [l.get("name") or ""
                 for l in ((issue.get("labels") or {}).get("nodes") or [])]
    resp = next((n for n in etiquetas if n.startswith(PREFIJO_RESP)), "")
    clave = _estado_de(issue)
    ref = issue.get("identifier") or ""
    fila = (locales or {}).get(ref) or {}
    dias = linear_leads._dias_desde(issue.get("createdAt"))
    # El título del issue es la verdad durable de QUÉ se compra; la fila
    # local es el respaldo (una base recién creada no tiene la fila).
    que_compro = (issue.get("title") or "").strip() or fila.get("que_compro") or ""
    return {
        "id": issue["id"],
        "ref": ref,
        "url": issue.get("url") or "",
        "que_compro": que_compro,
        "estado": clave,
        "estado_nombre": (issue.get("state") or {}).get("name") or "",
        "estado_ficha": POR_CLAVE.get(clave),
        "etiquetas": etiquetas,
        "resp": resp[len(PREFIJO_RESP):] if resp else "",
        "proveedor_id": fila.get("proveedor_id"),
        "proveedor": fila.get("proveedor_nombre") or "",
        "orden_compra_id": fila.get("orden_compra_id"),
        "orden_compra": fila.get("orden_compra_nombre") or "",
        "lead_ref": fila.get("lead_ref") or "",
        "como_llega": fila.get("como_llega") or "",
        "llegada": texto_llegada(fila.get("como_llega")),
        "creado": issue.get("createdAt") or "",
        "dias": dias,
        "hace": linear_leads.hace_bonito(dias),
    }


_lista_cache = {"en": 0, "dato": None}

# La generación de la caché: sube en cada `refrescar()`, o sea en cada
# escritura. `_buscar()` anota con qué generación arrancó y, si cambió para
# cuando termina, TIRA lo que trajo en vez de guardarlo — es una foto de
# ANTES de esa escritura. Sin esto, un refresco de fondo que arrancó antes
# de una escritura puede terminar después y pisarla con datos viejos: el
# bug de "hay que apretar el botón dos veces" (28/09/2026, en Control).
_generacion = {"n": 0}


def listar(refrescar=False):
    """Todas las compras del proyecto COMPRAS, normalizadas.

    Mismo patrón de velocidad del resto de la app: lo guardado sale al
    instante y, si venció el TTL, Linear se consulta por detrás. La
    pantalla nunca espera a la red.
    """
    if not configurado():
        return [dict(c) for c in _muestra()]
    if _lista_cache["dato"] is not None and not refrescar:
        if time.time() - _lista_cache["en"] >= TTL_COMPRAS:
            calendario._en_fondo("compras-lista", _buscar)
        return [dict(c) for c in _lista_cache["dato"]]
    return [dict(c) for c in _buscar()]


def listar_o_vacio(refrescar=False):
    """Como `listar()`, pero si Linear no contesta devuelve lista vacía en
    vez de reventar — es lo que usa la PANTALLA.

    La diferencia entre "no hay compras" y "no se pudo leer Linear" no se
    pierde: la cuenta `falta_en_linear()`, que la pantalla pinta en su
    renglón. Lo que no puede pasar es que la pestaña sea un 500 porque
    Linear tuvo un mal rato — y menos hoy, con el proyecto COMPRAS todavía
    sin crear.
    """
    try:
        return listar(refrescar=refrescar)
    except ErrorCompras as fallo:
        registro_aviso(f"No se pudo leer el tablero de compras: {fallo}")
        return []


def _buscar():
    generacion = _generacion["n"]
    locales = _filas_locales()
    filas = []
    for issue in (_pedir(CONSULTA_LISTA).get("issues") or {}).get("nodes") or []:
        compra = _normalizar(issue, locales)
        if not compra["estado"]:
            continue  # una columna que no es del tablero de compras
        filas.append(compra)
    filas.sort(key=lambda c: -c["dias"])  # la más vieja arriba: es la urgente
    if _generacion["n"] == generacion:
        _lista_cache.update({"en": time.time(), "dato": filas})
    return filas


def refrescar():
    """Marca la caché vencida y sube su generación. Lo llama toda
    escritura, para que la próxima pintada reconcilie con Linear."""
    _generacion["n"] += 1
    _lista_cache["en"] = 0


def _en_cache(ref):
    """El dict de la compra TAL CUAL vive en la caché (el objeto, no una
    copia) — para parchearlo in situ después de escribir en Linear."""
    for compra in _lista_cache["dato"] or []:
        if compra["ref"] == ref or compra["id"] == ref:
            return compra
    return None


def _insertar_en_cache(compra):
    """Mete una compra recién creada en la caché guardada.

    `refrescar()` a propósito NO tira lo que tiene (ver su docstring), así
    que sin esto la compra nueva no aparecería en el tablero hasta que el
    refresco de fondo terminara — y el aviso «VIV-301 anotada» quedaría
    hablando de una tarjeta que no está. Con la caché fría no hace falta:
    la próxima pintada va a Linear igual.
    """
    if _lista_cache["dato"] is None:
        return
    _lista_cache["dato"].insert(0, compra)


def _parchear_estado(ref, clave):
    """Aplica a la caché, EN EL MOMENTO, un cambio de columna que Linear ya
    confirmó — para que la pintada de después del arrastre muestre la
    verdad al instante, sin esperar el refresco de fondo."""
    compra = _en_cache(ref)
    if compra is None:
        return
    ficha = POR_CLAVE[clave]
    compra.update({"estado": clave, "estado_nombre": ficha["nombre"],
                   "estado_ficha": ficha})


def uno(ref, lista=None):
    """La compra por su referencia VIV-NN (o por su id de Linear)."""
    buscado = (ref or "").strip()
    if not buscado:
        return None
    for compra in (lista if lista is not None else listar()):
        if compra["ref"] == buscado or compra["id"] == buscado:
            return compra
    return None


def mensaje_compra_ausente(ref):
    """El error de «no encontré esa compra», con el ref adentro.

    Son DOS casos, no uno (la misma lección que costó caro en Control el
    29/09/2026): un ref VACÍO no es una compra borrada — es un POST que
    llegó sin decir cuál, porque el navegador arrastró el enlace de adentro
    de la tarjeta y no la tarjeta. Decir «ya no está en Linear» mandaba al
    empleado a buscar algo borrado que no existe.
    """
    ref = (ref or "").strip()
    if not ref:
        return ("No llegó qué compra tocar. Probá arrastrando la tarjeta "
                "entera, no el enlace de adentro.")
    return f"La compra {ref} ya no está en Linear."


# Los anclas de la pantalla, en UN solo lugar. Quien redirige los pide
# por nombre y la plantilla los pinta como `id=`: si algún día cambian,
# cambian en los dos lados a la vez.
ANCLA_LINEAS = "#cp-lineas"      # la lista de productos del formulario
ANCLA_BUSCADOR = "#cp-buscar"    # el buscador de productos
ANCLA_TABLERO = "#cp-tablero"    # las 7 columnas
ANCLA_PROVEEDOR = "#cp-prov"     # el campo del proveedor y su «crearlo»
ANCLA_BAJOS = "#cp-bajos"        # la lista de lo que está bajo o en cero
ANCLA_RECIBIR = "#cp-recibir"    # la lista de la pantalla de recibir


def ancla_de_compra(ref):
    """`#c-VIV-204` para traer esa tarjeta a la vista al volver, o el
    tablero si el ref no es uno sano.

    Se filtra el ref a propósito: llega de un POST y va PEGADO a la URL del
    redirect, así que un valor raro no puede colarse ahí.
    """
    ref = (ref or "").strip()
    if ref and all(c.isalnum() or c == "-" for c in ref):
        return "#c-" + ref
    return ANCLA_TABLERO


def _tarjeta(compra, plata_por_ref, lineas_por_ref=None):
    """La compra lista para la tarjeta: lo que se ve y nada más.

    El «hace N días» en ámbar lo decide Python y no la plantilla, con el
    MISMO umbral que las tarjetas de Control (`control.DIAS_HACE_ALERTA`):
    cinco días son cinco días en las dos pantallas, y el día que él cambie
    ese número cambia en las dos. El import es tardío a propósito —
    `control` arrastra medio mundo (Odoo, Twenty, el calendario) y este
    módulo no lo necesita para nada más.
    """
    from . import control
    lineas = (lineas_por_ref or {}).get(compra["ref"]) or []
    return dict(compra, **{
        "hace_alerta": control.hace_alerta(compra.get("dias")),
        "plata": plata_por_ref.get(compra["ref"]),
        "lineas": lineas,
        "cuantas_lineas": len(lineas),
        "lineas_resumen": resumen_de_lineas(lineas),
        "lineas_total": total_de_lineas(lineas),
        # «Pedida en Linear y Odoo no se enteró»: la desincronización se ve
        # en la tarjeta, nunca se esconde.
        "falta_orden": falta_la_orden(compra, lineas),
    })


def con_lineas(compra):
    """Una compra con sus líneas, para el panel que las muestra. None si no
    hay tal compra."""
    if compra is None:
        return None
    lineas = lineas_de(compra["ref"])
    return dict(compra, **{
        "lineas": lineas, "cuantas_lineas": len(lineas),
        "lineas_resumen": resumen_de_lineas(lineas),
        "lineas_total": total_de_lineas(lineas),
        "falta_orden": falta_la_orden(compra, lineas),
        "pedida": desde_pedido(compra.get("estado")),
    })


def tablero(lista=None):
    """[{clave, titulo, color, chip, pie, compras}] — las 7 columnas, en
    orden. La más vieja arriba dentro de cada columna: es la que lleva más
    tiempo esperando."""
    # El orden se decide ACÁ y no en la consulta, para que el modo muestra
    # y el real se vean igual: la que lleva más días arriba.
    todas = sorted(lista if lista is not None else listar(),
                   key=lambda c: -c["dias"])
    plata_por_ref = plata_de_varias(todas)
    # Las líneas de TODAS las tarjetas en una consulta, no una por tarjeta.
    lineas_por_ref = lineas_de_varias([c["ref"] for c in todas])
    todas = [_tarjeta(c, plata_por_ref, lineas_por_ref) for c in todas]
    columnas = []
    for estado in ESTADOS:
        columnas.append({
            "clave": estado["clave"], "titulo": estado["titulo"],
            "nombre": estado["nombre"], "color": estado["color"],
            "chip": estado["chip"], "pie": estado["pie"],
            "compras": [c for c in todas if c["estado"] == estado["clave"]],
        })
    return columnas


# ---------------------------------------------------------------------------
# Escritura: crear una compra y moverla de columna
# ---------------------------------------------------------------------------

MUTACION_CREAR = """
mutation($datos: IssueCreateInput!) {
  issueCreate(input: $datos) { success issue { id identifier url } }
}
"""

MUTACION_ESTADO = """
mutation($id: String!, $estado: String!) {
  issueUpdate(id: $id, input: { stateId: $estado }) { success }
}
"""


def _descripcion(proveedor, lead_ref, autor, llegada=""):
    """La tarjeta del issue, en texto legible para quien la abra en Linear.

    La pantalla NO lee esto: los datos de la tarjeta salen de la tabla
    local (`_filas_locales`). Está acá para que el issue se entienda solo
    desde Linear, no como formato a parsear — un marcador escondido que
    dos lados tengan que interpretar igual es una fuente de bugs que esta
    fase no necesita.
    """
    lineas = []
    if proveedor:
        lineas.append(f"**Proveedor:** {proveedor}")
    if llegada:
        lineas.append(f"**Cómo llega:** {llegada}")
    if lead_ref:
        lineas.append(f"**Para el lead:** {lead_ref}")
    if autor:
        lineas.append(f"**La creó:** {autor} desde Control Viverorose")
    return "\n\n".join(lineas)


def crear(que_compro, proveedor_nombre="", proveedor_id=None, resp="",
          lead_ref="", autor="", usuario_borrador="", como_llega=""):
    """Una compra nueva, en «Por pedir». Devuelve {"ref", "url", "id"}.

    `usuario_borrador` es de quién son las líneas que la compra se lleva:
    las del formulario a medio llenar pasan a ser las de esta compra y el
    borrador se descarta. Se hace **al final y solo si Linear aceptó**: una
    compra que no nació no puede quedarse con el trabajo del empleado.

    El responsable va por etiqueta `Resp: <nombre>` y el assignee es
    Abraham (`LINEAR_ASSIGNEE_ID`), nunca el bot: son dos cosas distintas
    y no se mezclan. Si la etiqueta todavía no existe en el equipo VIV, la
    compra se crea IGUAL sin ella y queda el aviso en el log — nunca se
    crea una etiqueta al vuelo.
    """
    que_compro = (que_compro or "").strip()
    if not que_compro:
        raise ErrorCompras("Escribí qué se compra.", campo="que_compro")
    # «¿Cómo llega?» se limpia ACÁ, antes de tocar Linear: lo que no sea
    # del vocabulario cae en «todavía no se dijo». Nunca rebota la compra
    # por esto — es un dato de apoyo, no un requisito para anotar lo que
    # hay que comprar.
    como_llega = _llegada(como_llega)
    resp = (resp or "").strip()
    if resp and resp not in linear_leads.responsables():
        # Quién ES del equipo lo dicen las etiquetas `Resp:` del equipo
        # LEAD, la única lista que la app ya tiene. Un nombre que no está
        # ahí se rebota en vez de inventarlo — esto NO es lo mismo que la
        # etiqueta que todavía no existe en VIV, que sí deja pasar (abajo).
        raise ErrorCompras(
            f"«{resp}» no está en el equipo. El responsable sale de las "
            f"etiquetas «{PREFIJO_RESP}…» de Linear, y esas no se crean "
            f"desde acá.", campo="resp")
    lead_ref = (lead_ref or "").strip().upper()
    if lead_ref and linear_leads.uno(lead_ref) is None:
        raise ErrorCompras(f"El lead {lead_ref} no está en el tablero.",
                           campo="lead_ref")
    _exigir_escritura()

    proveedor_nombre = (proveedor_nombre or "").strip()
    issue_nuevo = None      # el issue que acaba de nacer, para la caché
    if not configurado():
        # El responsable ES la etiqueta: si `Resp: <nombre>` todavía no
        # existe en VIV, la compra se crea SIN responsable (y con el aviso
        # en el log) en vez de mostrar un nombre que en Linear no está
        # escrito en ninguna parte. En modo real ese aviso lo deja
        # `_label_id`, que además refresca el catálogo una vez antes de dar
        # la etiqueta por inexistente.
        resp_puesto = resp if resp_label_disponible(resp) else ""
        if resp and not resp_puesto:
            _aviso_sin_etiqueta(PREFIJO_RESP + resp)
        nueva = _muestra_crear(que_compro, proveedor_nombre, resp_puesto,
                               lead_ref, como_llega,
                               proveedor_id=proveedor_id)
    else:
        cat = catalogo()
        if not cat["proyecto"]:
            raise ErrorCompras(
                f"El proyecto {PROYECTO} todavía no existe en el equipo "
                f"{EQUIPO_CLAVE} de Linear: hay que crearlo allá antes de "
                f"anotar compras.")
        estado = cat["estados"].get(CLAVE_INICIAL)
        if not estado:
            raise ErrorCompras(
                f'El equipo {EQUIPO_CLAVE} de Linear no tiene la columna '
                f'"{POR_CLAVE[CLAVE_INICIAL]["nombre"]}".')
        datos = {
            "teamId": cat["equipo"],
            "projectId": cat["proyecto"],
            "title": que_compro[:250],
            "description": _descripcion(proveedor_nombre, lead_ref, autor,
                                        texto_llegada(como_llega)),
            "stateId": estado,
        }
        # Los issues se asignan a Abraham, jamás al bot (regla que no se
        # rompe). Sin la variable el issue nace SIN asignar, que sigue sin
        # ser el bot: `issueCreate` no asigna solo a quien tiene la key.
        asignado = (os.environ.get("LINEAR_ASSIGNEE_ID") or "").strip()
        if asignado:
            datos["assigneeId"] = asignado
        if resp:
            label = _label_id(PREFIJO_RESP + resp)
            if label:
                datos["labelIds"] = [label]
        hecho = (_pedir(MUTACION_CREAR, {"datos": datos})
                 .get("issueCreate") or {})
        if not hecho.get("success"):
            raise ErrorCompras("Linear no pudo crear la compra.")
        issue = hecho.get("issue") or {}
        nueva = {"ref": issue.get("identifier") or "",
                 "url": issue.get("url") or "", "id": issue.get("id") or ""}
        if not nueva["ref"]:
            # Sin el VIV-XX la fila local nacería con la llave vacía y la
            # tarjeta no se podría mover nunca: mejor decirlo.
            raise ErrorCompras(
                "Linear creó la compra pero no dijo su número. Hay que "
                "buscarla en el proyecto COMPRAS.")
        issue_nuevo = {
            "id": nueva["id"], "identifier": nueva["ref"],
            "title": que_compro, "description": "", "url": nueva["url"],
            "createdAt": datetime.now(ZONA_PANAMA).isoformat(),
            "state": {"id": estado,
                      "name": POR_CLAVE[CLAVE_INICIAL]["nombre"],
                      "type": "unstarted"},
            "labels": {"nodes": [{"id": i, "name": PREFIJO_RESP + resp}
                                 for i in datos.get("labelIds") or []]},
        }

    _guardar_fila(nueva["ref"], que_compro=que_compro,
                  proveedor_id=proveedor_id,
                  proveedor_nombre=proveedor_nombre, lead_ref=lead_ref,
                  como_llega=como_llega)
    if usuario_borrador:
        _mudar_lineas_del_borrador(usuario_borrador, nueva["ref"])
        descartar_borrador(usuario_borrador)
    if issue_nuevo is not None:
        _insertar_en_cache(_normalizar(issue_nuevo, _filas_locales()))
    refrescar()
    return nueva


def mover(ref, clave, autor=""):
    """Mueve la compra a la columna `clave`. Devuelve ("aviso", "error").

    A diferencia del embudo de los leads, acá mover a mano es el camino
    NORMAL y no una excepción: en esta fase nada mueve una compra sola, así
    que no se le pide un motivo a quien arrastra. Lo que sí queda es el
    comentario firmado en el issue diciendo quién la movió y de dónde a
    dónde — la historia durable vive en Linear, como siempre.
    """
    ref = (ref or "").strip()
    compra = uno(ref)
    if compra is None:
        return "", mensaje_compra_ausente(ref)
    if clave not in POR_CLAVE:
        return "", "Esa columna no existe en el tablero de compras."
    if compra["estado"] == clave:
        return "", ""
    desde = (POR_CLAVE.get(compra["estado"]) or {}).get("titulo") or "—"
    hasta = POR_CLAVE[clave]["titulo"]
    try:
        _exigir_escritura()
        if not configurado():
            _muestra_mover(compra["ref"], clave)
        else:
            estado = catalogo()["estados"].get(clave)
            if not estado:
                raise ErrorCompras(
                    f'El equipo {EQUIPO_CLAVE} de Linear no tiene la columna '
                    f'"{POR_CLAVE[clave]["nombre"]}".')
            datos = _pedir(MUTACION_ESTADO,
                           {"id": compra["id"], "estado": estado})
            if not (datos.get("issueUpdate") or {}).get("success"):
                raise ErrorCompras("Linear no pudo mover la compra.")
            _parchear_estado(compra["ref"], clave)
        refrescar()
    except ErrorCompras as fallo:
        return "", str(fallo)

    # El comentario es un extra: si Linear no lo guarda, la compra YA se
    # movió y decir lo contrario sería mentir.
    try:
        linear_leads.comentar(
            compra["id"], f"Movida de **{desde}** a **{hasta}**.", autor=autor)
    except linear_leads.ErrorLeads as fallo:
        registro_aviso(f"La compra {compra['ref']} se movió pero el "
                       f"comentario no se pudo guardar: {fallo}")
    return f"{compra['que_compro']}: {desde} → {hasta}.", ""


# ---------------------------------------------------------------------------
# Odoo: los proveedores y la plata de la orden de compra
#
# Odoo es solo dinero y stock. Este módulo LEE y nunca escribe, y la puerta
# es SIEMPRE `ventas._ejecutar` (XML-RPC), llamada así —`ventas._ejecutar(...)`,
# nunca `from .ventas import _ejecutar`— para que las pruebas puedan
# reemplazarla con `monkeypatch.setattr(ventas, "_ejecutar", falso)`.
#
# Fail-soft con criterio: toda lectura devuelve `{"ok": False, "error": …}`
# si Odoo no contesta. **Una lista vacía nunca significa «Odoo falló»**: la
# diferencia entre "no hay" y "no sé" tiene que llegar a quien muestre.
#
# El módulo `purchase` quedó INSTALADO el 30/09/2026 a las 16:15 (medido en
# el proceso vivo, producción y pruebas; 0 órdenes todavía). Antes de eso
# `purchase.order` no existía y la consulta devolvía un `Fault`; el camino
# que lo aguanta se queda igual, porque un modelo puede desaparecer de un
# Odoo de pruebas o una base nueva: ese `Fault` cae en el `except` y sale
# como `{"ok": False}`, la tarjeta no pinta barra y nadie ve un 500.
# ---------------------------------------------------------------------------

# OJO con Odoo 19: `res.partner` ya NO tiene el campo `mobile` — pedirlo
# revienta la consulta entera. El teléfono es `phone`.
CAMPOS_PROVEEDOR = ["name", "phone", "email", "city"]

CAMPOS_ORDEN_COMPRA = ["name", "state", "amount_total", "invoice_ids",
                       "date_order", "partner_id"]


def _error(error):
    """El mensaje humano de una falla de Odoo, con el mismo criterio que el
    resto de la app (la última línea del traceback de un Fault)."""
    return ventas._mensaje_de_error(error)


def proveedores():
    """Los contactos de Odoo marcados como proveedor
    (`supplier_rank > 0`), por nombre.

    `{"ok": True, "proveedores": [...]}`, o `{"ok": False, "error": …}` si
    Odoo no contestó. **Hoy no hay ninguno** y eso es un `ok` con lista
    vacía, no una falla: el selector del formulario aguanta la lista vacía
    y deja escribir el nombre a mano.
    """
    if not ventas.configurado():
        return {"ok": True, "error": "", "proveedores": []}
    try:
        filas = ventas._ejecutar(
            "res.partner", "search_read",
            [[["supplier_rank", ">", 0]]],
            {"fields": CAMPOS_PROVEEDOR, "order": "name", "limit": 200})
    except Exception as error:
        return {"ok": False, "error": _error(error), "proveedores": []}
    return {"ok": True, "error": "", "proveedores": [
        {"id": f["id"], "nombre": f.get("name") or "",
         "telefono": f.get("phone") or "", "correo": f.get("email") or "",
         "ciudad": f.get("city") or ""}
        for f in filas]}


# ---------------------------------------------------------------------------
# El proveedor deja de ser texto suelto (dueño, 01/10/2026)
#
# El campo sigue siendo de texto con sugerencias —un `<select>` no serviría
# mientras la lista de proveedores de Odoo esté corta—, pero lo escrito ya
# no se pierde: si no calza con ninguno, la pantalla ofrece CREARLO.
#
# «Ofrece» y no «lo crea»: un dedo resbalado no puede dejar dos
# «Agroservicios» distintos y partir en dos el historial de lo que se le
# compró. Es el mismo criterio de las etiquetas de Linear, que tampoco
# nacen solas — con la diferencia de que acá sí hay un botón, porque un
# proveedor es dato del día a día y no vocabulario del sistema.
# ---------------------------------------------------------------------------

# El `supplier_rank` con el que nace un proveedor. Odoo lo usa como
# contador (lo sube solo con cada compra) y cualquier valor > 0 es «este
# contacto es proveedor»: es justo lo que `proveedores()` filtra.
RANK_PROVEEDOR = 1

# Lo único que se escribe en `res.partner`, y a propósito nada más: nombre,
# la marca de proveedor y el teléfono si lo escribieron. Ni impuestos (no
# los decide el código), ni correo, ni ciudad, ni términos de pago —
# inventarle campos a un contacto es inventarle datos al negocio.
#
# OJO con Odoo 19: el teléfono es `phone`. `mobile` YA NO EXISTE en
# `res.partner` y pedirlo o escribirlo revienta la llamada entera.
CAMPO_TELEFONO = "phone"


def proveedor_que_calza(nombre, lista=None):
    """El proveedor de Odoo que se llama así, o None.

    Compara sin mayúsculas ni tildes y sin espacios de sobra, que es como
    la gente escribe el mismo nombre dos veces. `lista` es para no volver
    a preguntarle a Odoo cuando quien llama ya tiene los proveedores.
    """
    nombre = str(nombre or "").strip()
    if not nombre:
        return None
    if lista is None:
        resultado = proveedores()
        if not resultado["ok"]:
            # No se pudo preguntar: eso NO es «no existe», y por eso no se
            # devuelve None a secas sin que quien llama sepa la diferencia
            # — la sabe por `proveedores()["ok"]`, que es quien la tiene.
            return None
        lista = resultado["proveedores"]
    buscado = _sin_acentos(nombre)
    for p in lista:
        if _sin_acentos((p.get("nombre") or "").strip()) == buscado:
            return p
    return None


def crear_proveedor(nombre, telefono=""):
    """Un contacto de Odoo marcado como proveedor.

    `{"ok", "error", "proveedor"}`. Nace con su nombre, `supplier_rank` y
    el teléfono si lo escribieron: nada más.

    Vuelve a mirar si ya existe JUSTO ANTES de crearlo, con la lista
    fresca: entre que la pantalla se pintó y el clic pudo haberlo creado
    otra persona, y dos contactos con el mismo nombre parten en dos el
    historial de ese proveedor. En ese caso devuelve el que ya estaba, que
    es lo que quien apretó el botón quería.
    """
    nombre = str(nombre or "").strip()[:120]
    telefono = str(telefono or "").strip()[:40]
    if not nombre:
        return _no_se_creo("Escribí el nombre del proveedor.")
    if not ventas.configurado():
        return _no_se_creo("Odoo no está conectado en este servidor, así que "
                           "el proveedor no se puede crear.")
    actuales = proveedores()
    if not actuales["ok"]:
        # Sin poder leer la lista no se puede saber si ya existe, y crear a
        # ciegas es justo lo que se está evitando.
        return _no_se_creo(f"No se pudo leer los proveedores de Odoo: "
                           f"{actuales['error']}.")
    ya = proveedor_que_calza(nombre, actuales["proveedores"])
    if ya is not None:
        return {"ok": True, "error": "", "proveedor": ya, "ya_estaba": True}
    valores = {"name": nombre, "supplier_rank": RANK_PROVEEDOR}
    if telefono:
        valores[CAMPO_TELEFONO] = telefono
    try:
        nuevo = ventas._ejecutar("res.partner", "create", [valores])
    except Exception as error:
        return _no_se_creo(_error(error))
    if isinstance(nuevo, list):
        nuevo = nuevo[0] if nuevo else 0
    if not nuevo:
        return _no_se_creo("Odoo no dijo el número del proveedor nuevo.")
    return {"ok": True, "error": "", "ya_estaba": False, "proveedor": {
        "id": int(nuevo), "nombre": nombre, "telefono": telefono,
        "correo": "", "ciudad": ""}}


def _no_se_creo(error):
    """El «no quedó creado», siempre con la misma forma: quien llama no
    tiene que adivinar qué llaves vienen según por dónde falló."""
    return {"ok": False, "error": error, "proveedor": None,
            "ya_estaba": False}


def _plata_de_ordenes(ids):
    """{orden_compra_id: plata} para varias órdenes de compra, en DOS
    consultas (las órdenes y sus facturas de proveedor) y no dos por
    tarjeta.

    `pagado` sale de las facturas del proveedor: lo facturado menos lo que
    queda pendiente (`amount_total - amount_residual` de cada
    `account.move` no cancelada). Sin factura todavía, pagado es 0 y ese 0
    es REAL —no se le ha pagado nada— no un 0 inventado por no saber.

    **Propaga lo que Odoo tire**: quien llama decide si lo calla (el
    tablero, con `_plata_de_ordenes_o_vacio`) o lo cuenta (`plata_de`, que
    lo devuelve como `ok: False`). Tragárselo acá borraría la diferencia
    entre "no hay" y "no sé".
    """
    ids = [int(i) for i in ids if i]
    if not ids:
        return {}
    ordenes = ventas._ejecutar(
        "purchase.order", "read", [ids], {"fields": CAMPOS_ORDEN_COMPRA})

    facturas_ids = sorted({fid for o in ordenes
                           for fid in (o.get("invoice_ids") or [])})
    pagado_por_factura = {}
    if facturas_ids:
        for f in ventas._ejecutar(
                "account.move", "read", [facturas_ids],
                {"fields": ["amount_total", "amount_residual", "state"]}):
            if (f.get("state") or "") == "cancel":
                continue
            pagado_por_factura[f["id"]] = (
                (f.get("amount_total") or 0.0) - (f.get("amount_residual") or 0.0))

    resultado = {}
    for o in ordenes:
        total = float(o.get("amount_total") or 0.0)
        pagado = sum(pagado_por_factura.get(fid, 0.0)
                     for fid in (o.get("invoice_ids") or []))
        resultado[o["id"]] = _plata(o.get("name") or "", total, pagado)
    return resultado


def _plata_de_ordenes_o_vacio(ids):
    """Como `_plata_de_ordenes`, pero se calla con un aviso al log: es la
    que usa el TABLERO, que no puede reventar porque Odoo tuvo un mal rato
    ni porque en ese Odoo falte el módulo `purchase`."""
    try:
        return _plata_de_ordenes(ids)
    except Exception as error:
        registro_aviso(f"No se pudo leer las órdenes de compra de Odoo: "
                       f"{_error(error)}")
        return {}


def _plata(nombre, total, pagado):
    """La plata de una orden, lista para la tarjeta. `porcentaje` es entero
    y nunca pasa de 100: una barra que se sale del riel se ve como un bug."""
    total = round(float(total or 0.0), 2)
    pagado = round(float(pagado or 0.0), 2)
    return {
        "hay": True, "orden": nombre, "total": total, "pagado": pagado,
        "saldo": round(total - pagado, 2),
        "porcentaje": min(100, max(0, int(round(pagado * 100 / total))))
                      if total > 0 else 0,
    }


_SIN_ORDEN = {"hay": False, "orden": "", "total": None, "pagado": None,
              "saldo": None, "porcentaje": None}


def plata_de(ref):
    """La plata de la orden de compra conectada a `ref`:
    `{"ok", "error", "hay", "orden", "total", "pagado", "saldo",
    "porcentaje"}`.

    - Sin orden conectada: `ok` True y `hay` False. Eso es «no hay», no
      «no sé», y la tarjeta simplemente no pinta barra.
    - Odoo caído, o `purchase.order` inexistente porque el módulo
      `purchase` no está instalado: `ok` False con el motivo. Tampoco se
      pinta barra, y nunca se inventa un total en 0.
    """
    compra = uno(ref)
    if compra is None:
        return {"ok": True, "error": "", **_SIN_ORDEN}
    if not configurado():
        return {"ok": True, "error": "", **_muestra_plata(compra["ref"])}
    orden_id = compra.get("orden_compra_id")
    if not orden_id:
        return {"ok": True, "error": "", **_SIN_ORDEN}
    if not ventas.configurado():
        return {"ok": False, "error": "Odoo no está conectado.", **_SIN_ORDEN}
    try:
        todas = _plata_de_ordenes([orden_id])
    except Exception as error:
        return {"ok": False, "error": _error(error), **_SIN_ORDEN}
    plata = todas.get(int(orden_id))
    if plata is None:
        # Odoo contestó y esa orden no está: la borraron, o el id que
        # guardamos es de otra época. Es "no hay", no "no sé".
        return {"ok": True, "error": "", **_SIN_ORDEN}
    return {"ok": True, "error": "", **plata}


_plata_cache = {"en": 0, "dato": {}}


def plata_de_varias(lista):
    """{ref: plata} de las compras que tienen orden conectada, LEÍDO DE LA
    CACHÉ, y de paso pide el refresco por detrás.

    La pantalla nunca espera a Odoo (regla de velocidad del 22/09/2026):
    con la caché fría la tarjeta sale sin barra y la siguiente pintada ya
    la trae. En modo muestra no hay red, así que sale directo y completa.
    """
    if not configurado():
        return {c["ref"]: _muestra_plata(c["ref"]) for c in lista
                if c.get("orden_compra_id")}
    por_ref = {c["ref"]: int(c["orden_compra_id"]) for c in lista
               if c.get("orden_compra_id")}
    if not por_ref:
        return {}
    if not ventas.configurado():
        return {}
    ids = sorted(set(por_ref.values()))
    if time.time() - _plata_cache["en"] >= TTL_PLATA:
        def tarea():
            _plata_cache.update({"en": time.time(),
                                 "dato": _plata_de_ordenes_o_vacio(ids)})
        calendario._en_fondo("compras-plata", tarea)
    guardado = _plata_cache["dato"]
    return {ref: guardado[oid] for ref, oid in por_ref.items()
            if oid in guardado}


# ---------------------------------------------------------------------------
# Modo muestra (sin Linear): todo vive en memoria del proceso.
#
# Son las mismas compras que usan las pruebas y el desarrollo local: ocho
# repartidas en las 7 columnas, con y sin proveedor, con y sin orden de
# Odoo, con y sin `Resp:`, y una para un lead cliente — para que la
# pantalla y las pruebas puedan mirar los dos caminos sin red.
# ---------------------------------------------------------------------------

# En modo muestra el equipo VIV "tiene" SOLO estas dos etiquetas de
# responsable, y a Ruben le falta la suya a propósito: así una compra
# pedida para Ruben se crea igual y queda sin responsable, que es el
# candado de «las etiquetas nunca se crean solas» y hay que poder probarlo
# sin Linear. Mismo truco que `linear_leads._MUESTRA_SENALES_EXISTENTES`.
_MUESTRA_RESP_EN_VIV = {"Abraham", "Mary"}

# El catálogo de muestra del buscador: los TRES tipos que el vivero compra,
# para que el formulario y las pruebas vean los tres caminos sin Odoo. Los
# SKU llevan los prefijos de verdad (`PREFIJOS_COMPRA`).
_CATALOGO_MUESTRA = [
    {"id": 101, "sku": "PL-PALMA-ARECA", "nombre": "Palma Areca",
     "precio": 15.0},
    {"id": 102, "sku": "PL-CROTON-PETRA", "nombre": "Croton Petra",
     "precio": 5.5},
    {"id": 103, "sku": "MC-MACETA-BARRO-30", "nombre": "Maceta barro 30 cm",
     "precio": 18.0},
    {"id": 104, "sku": "IN-TIERRA-NEGRA", "nombre": "Tierra negra",
     "precio": 4.25},
    {"id": 105, "sku": "IN-ABONO-ORGANICO", "nombre": "Abono orgánico",
     "precio": 7.0},
]


def _muestra_buscar(texto):
    """El mismo filtro del buscador real —nombre o SKU, sin distinguir
    mayúsculas ni tildes— hecho en Python sobre el catálogo de muestra."""
    buscado = _sin_acentos(texto)
    return [dict(p) for p in _CATALOGO_MUESTRA
            if buscado in _sin_acentos(p["nombre"])
            or buscado in _sin_acentos(p["sku"])]

_SEMILLA = [
    # ref, qué se compra, proveedor, resp, orden, total, pagado, lead, días,
    # cómo llega. Las cuatro formas aparecen, y dos compras quedan SIN
    # decir cómo llega (es lo que pasa con todo lo anotado antes del
    # 01/10/2026, y la pantalla tiene que verse bien así).
    ("VIV-201", "50 sacos de tierra negra", "Agroservicios del Istmo",
     "Abraham", "", 0.0, 0.0, "", 1, "camion"),
    ("VIV-202", "Macetas de barro 12\"", "Cerámica Chorrera",
     "", "", 0.0, 0.0, "", 3, "nosotros"),
    ("VIV-203", "Palmas areca para el lobby del Bristol", "Vivero El Roble",
     "Mary", "P00014", 840.0, 0.0, "LEAD-88", 5, "mula"),
    ("VIV-204", "Abono orgánico a granel", "Agroservicios del Istmo",
     "Abraham", "P00015", 320.0, 160.0, "", 8, "camion"),
    ("VIV-205", "Grama San Agustín, 200 m²", "Grama Panamá",
     "Mary", "P00016", 1250.0, 625.0, "", 2, "encomienda"),
    ("VIV-206", "Piedra blanca decorativa", "Canteras Pacora",
     "", "P00017", 480.0, 480.0, "", 6, ""),
    ("VIV-207", "Mangueras y aspersores", "Ferretería Central",
     "Mary", "P00012", 210.0, 210.0, "", 11, "nosotros"),
    ("VIV-208", "Bolsas de vivero 6x8", "Plásticos Nacionales",
     "Abraham", "", 0.0, 0.0, "", 14, ""),
]

_ESTADOS_MUESTRA = ["POR_PEDIR", "COTIZANDO", "PEDIDO", "ABONADO",
                    "EN_CAMINO", "RECIBIDO", "CERRADO", "POR_PEDIR"]

_MUESTRA = None
_MUESTRA_PLATA = {}
_MUESTRA_SIGUIENTE = {"n": 209}


def _muestra():
    global _MUESTRA
    if _MUESTRA is None:
        _MUESTRA = []
        _MUESTRA_PLATA.clear()
        for (ref, que, prov, resp, orden, total, pagado, lead, dias,
             como), clave in zip(_SEMILLA, _ESTADOS_MUESTRA):
            ficha = POR_CLAVE[clave]
            como = _llegada(como)
            _MUESTRA.append({
                "id": "muestra-" + ref, "ref": ref, "url": "",
                "que_compro": que, "estado": clave,
                "estado_nombre": ficha["nombre"], "estado_ficha": ficha,
                "etiquetas": ([PREFIJO_RESP + resp] if resp else []),
                "resp": resp, "proveedor_id": None, "proveedor": prov,
                "orden_compra_id": (int(orden[1:]) if orden else None),
                "orden_compra": orden, "lead_ref": lead,
                "como_llega": como, "llegada": texto_llegada(como),
                "creado": "", "dias": dias,
                "hace": linear_leads.hace_bonito(dias),
            })
            if orden:
                _MUESTRA_PLATA[ref] = _plata(orden, total, pagado)
    return _MUESTRA


def reiniciar_muestra():
    """Cada prueba arranca con el tablero de ejemplo limpio.

    Vacía TODAS las cachés, `_lista_cache["dato"]` incluida. `refrescar()`
    a secas no alcanza: ese solo marca la caché vencida y a propósito NO
    tira lo guardado (la pintada siguiente sigue sirviendo lo que tenía
    mientras el refresco viaja por detrás). Acá sí hay que tirarlo — una
    prueba que corra después con Linear doblado leía la lista de la prueba
    anterior y no la de su doble.
    """
    global _MUESTRA
    _MUESTRA = None
    _MUESTRA_PLATA.clear()
    _MUESTRA_SIGUIENTE["n"] = 209
    _catalogo_cache.update({"en": 0, "dato": None})
    _plata_cache.update({"en": 0, "dato": {}})
    _lista_cache.update({"en": 0, "dato": None})
    refrescar()


def _muestra_plata(ref):
    _muestra()
    return dict(_MUESTRA_PLATA.get(ref) or _SIN_ORDEN)


def _muestra_uno(ref):
    for compra in _muestra():
        if compra["ref"] == ref or compra["id"] == ref:
            return compra
    return None


def _muestra_mover(ref, clave):
    compra = _muestra_uno(ref)
    if compra is None:
        return False
    compra.update({"estado": clave, "estado_nombre": POR_CLAVE[clave]["nombre"],
                   "estado_ficha": POR_CLAVE[clave]})
    return True


def _muestra_crear(que_compro, proveedor_nombre, resp, lead_ref,
                   como_llega="", proveedor_id=None):
    ficha = POR_CLAVE[CLAVE_INICIAL]
    ref = "VIV-%d" % _MUESTRA_SIGUIENTE["n"]
    _MUESTRA_SIGUIENTE["n"] += 1
    como_llega = _llegada(como_llega)
    _muestra().insert(0, {
        "id": "muestra-" + ref, "ref": ref, "url": "",
        "que_compro": que_compro, "estado": CLAVE_INICIAL,
        "estado_nombre": ficha["nombre"], "estado_ficha": ficha,
        "etiquetas": ([PREFIJO_RESP + resp] if resp else []),
        # El id del proveedor viaja también en la muestra: en modo real la
        # tarjeta lo lee de la tabla local, y si acá quedara en None el
        # modo muestra mentiría sobre un dato que el formulario SÍ guarda.
        "resp": resp, "proveedor_id": proveedor_id,
        "proveedor": proveedor_nombre,
        "orden_compra_id": None, "orden_compra": "", "lead_ref": lead_ref,
        "como_llega": como_llega, "llegada": texto_llegada(como_llega),
        "creado": "", "dias": 0, "hace": linear_leads.hace_bonito(0),
    })
    return {"ref": ref, "url": "", "id": "muestra-" + ref}
