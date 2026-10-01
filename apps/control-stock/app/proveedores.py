"""La vista de PROVEEDORES de la pestaña Compras: la segunda pantalla del
diseño que aprobó el dueño.

Por cada proveedor (un `res.partner` de Odoo con `supplier_rank > 0`) se
muestra lo que de verdad se le compró — **todo calculado de Odoo, nada
escrito a mano** — salvo una sola marca, «Preferido», que es decisión del
dueño y vive en una tabla local chica.

**Un sistema, un trabajo**, igual que `compras.py`:

- **Odoo es la fuente de verdad** de quién es proveedor y de cada compra
  (`purchase.order`). Este módulo LEE y nunca escribe en Odoo.
- Lo único que se guarda local es `proveedor_preferido`: un id de partner y
  si el dueño lo marcó. Nada de plata, nada de estado calculable — eso
  siempre sale de Odoo, nunca de esta tabla.
- La puerta a Odoo es SIEMPRE `ventas._ejecutar` (XML-RPC), llamada así
  —`ventas._ejecutar(...)`, nunca `from .ventas import _ejecutar`— para
  que las pruebas puedan reemplazarla con
  `monkeypatch.setattr(ventas, "_ejecutar", falso)`.
- La lista de contactos-proveedor se pide a través de `compras.proveedores()`
  (que ya resuelve la trampa de Odoo 19: `res.partner` perdió `mobile`, el
  teléfono es `phone`): este módulo no vuelve a escribir esa consulta, solo
  la importa y la lee. `compras.py` no se toca.

**Medido en el proceso vivo, 30/09/2026 de tarde: CERO contactos marcados
proveedor y CERO órdenes de compra** (`purchase.order` SÍ existe desde el
30/09 a las 16:15, con 0 registros). O sea que esta pantalla nace vacía de
verdad, y eso NO es una falla: la vacía-por-no-haber-nada-todavía y la
vacía-porque-Odoo-no-contestó son dos casos distintos que no se pueden
confundir — es la misma lección que ya le costó cara al proyecto con
«no hay compras» vs. «no se pudo leer Linear». Ver `listar()`.

Dos datos que la pantalla pide y que **todavía no existen en ninguna
parte** — cuántas compras llegaron a tiempo y cuánto llegó dañado — porque
la recepción con cantidades y unidades dañadas es una tanda futura. Salen
siempre como `None` (la plantilla los pinta «—» con su nota), nunca como un
0 que parezca medido.
"""

import time
from datetime import datetime, timezone

from . import compras, linear_leads, ventas
from .datos import ZONA_PANAMA, _db

TTL_PROVEEDORES = 300  # 5 minutos: la plata del año no cambia minuto a minuto


class ErrorProveedores(Exception):
    """Falla al leer Odoo, con el texto que se le muestra a quien usa la
    pantalla."""


def registro_aviso(texto):
    """Un aviso al log. Propio de este módulo a propósito, igual que en
    `compras.py`: tomarlo prestado de otro es cómo se cuela un NameError
    que solo aparece el día que algo falla de verdad."""
    import logging
    logging.getLogger("control_stock").warning(texto)


# ---------------------------------------------------------------------------
# Los 5 estados, calculados — salvo «Preferido», que es la ÚNICA marca a
# mano. El orden de la lista es el orden en que se muestran en la pantalla:
# primero lo que el dueño ya decidió que importa, al final lo que hay que
# mirar.
# ---------------------------------------------------------------------------

DIAS_PAUSA = 90  # "90 días sin comprarle" / "en los últimos 90 días"

ESTADOS = [
    {"clave": "PREFERIDO", "titulo": "Preferido", "clase": "prov-preferido",
     "pie": "lo marcó Abraham a mano"},
    {"clave": "ACTIVO", "titulo": "Activo", "clase": "prov-activo",
     "pie": f"le compraste en los últimos {DIAS_PAUSA} días"},
    {"clave": "EVALUANDO", "titulo": "Evaluando", "clase": "prov-evaluando",
     "pie": "una o dos compras, todavía sin historia"},
    {"clave": "PROSPECTO", "titulo": "Prospecto", "clase": "prov-prospecto",
     "pie": "creado, nunca se le compró"},
    {"clave": "PAUSA", "titulo": "En pausa", "clase": "prov-pausa",
     "pie": f"más de {DIAS_PAUSA} días sin comprarle, o un reclamo abierto"},
]
POR_CLAVE = {e["clave"]: e for e in ESTADOS}
_PRIORIDAD = {e["clave"]: i for i, e in enumerate(ESTADOS)}


def estado_de(preferido, compras_totales, dias_desde_ultima, reclamo_abierto=False):
    """La clave del estado de un proveedor.

    Todo calculado salvo `preferido`, que viene de la marca a mano y le
    gana a todo lo demás — es la única cosa de esta pantalla que el código
    no decide.

    `reclamo_abierto` llega siempre en `False` hoy: no existe todavía
    ninguna fuente de reclamos (la recepción con cantidades y unidades
    dañadas es una tanda futura). El parámetro queda listo para cuando
    exista, y se mira ANTES que «Evaluando»: un reclamo abierto importa más
    que la falta de historia.

    Los bordes: `compras_totales` de 0 es Prospecto; de 1 o 2, Evaluando
    (sin mirar la fecha: con tan poca historia no hay nada que evaluar
    todavía); de 3 en adelante, la fecha decide entre Activo y En pausa.
    «En los últimos 90 días» se lee inclusive (89 → Activo, 90 → Activo,
    91 → En pausa): son los mismos 90 días contados desde los dos lados de
    la regla, nunca un hueco de un día que no sea ninguno de los dos.
    """
    if preferido:
        return "PREFERIDO"
    compras_totales = int(compras_totales or 0)
    if compras_totales <= 0:
        return "PROSPECTO"
    if reclamo_abierto:
        return "PAUSA"
    if compras_totales <= 2:
        return "EVALUANDO"
    if dias_desde_ultima is not None and dias_desde_ultima > DIAS_PAUSA:
        return "PAUSA"
    return "ACTIVO"


# ---------------------------------------------------------------------------
# La tabla local: la ÚNICA escritura de este módulo, y nunca toca Odoo.
# ---------------------------------------------------------------------------

def iniciar_tablas():
    with _db() as con:
        # Una fila por proveedor MARCADO, con el id de `res.partner` como
        # llave. `preferido` guarda el valor real (0/1) y no se borra la
        # fila al apagar la marca: así queda quién la prendió y apagó, sin
        # inventar una segunda tabla de historia.
        con.execute("""
            CREATE TABLE IF NOT EXISTS proveedor_preferido (
                partner_id INTEGER PRIMARY KEY,
                preferido INTEGER NOT NULL DEFAULT 1,
                marcado_por TEXT NOT NULL DEFAULT '',
                marcado_en TEXT NOT NULL DEFAULT ''
            )
        """)


def _preferidos():
    """{partner_id} de los proveedores marcados Preferido HOY (no los que
    alguna vez se marcaron y se apagaron: esos se quedan en la tabla con
    `preferido = 0` como historia, y no cuentan)."""
    iniciar_tablas()
    with _db() as con:
        filas = con.execute(
            "SELECT partner_id FROM proveedor_preferido WHERE preferido = 1"
        ).fetchall()
    return {f["partner_id"] for f in filas}


def marcar_preferido(partner_id, preferido, autor=""):
    """Prende o apaga la marca «Preferido». Devuelve "" o un error.

    Es la ÚNICA escritura de todo el módulo, y va a la tabla local — nunca
    a Odoo. `partner_id` tiene que ser un id real de Odoo (un número); un
    POST con cualquier otra cosa se descarta sin tocar la base.
    """
    try:
        partner_id = int(partner_id)
    except (TypeError, ValueError):
        return "No llegó qué proveedor marcar."
    iniciar_tablas()
    with _db() as con:
        con.execute(
            "INSERT INTO proveedor_preferido "
            "(partner_id, preferido, marcado_por, marcado_en) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(partner_id) DO UPDATE SET "
            "preferido = excluded.preferido, marcado_por = excluded.marcado_por, "
            "marcado_en = excluded.marcado_en",
            (partner_id, 1 if preferido else 0, (autor or "").strip(),
             datetime.now(ZONA_PANAMA).isoformat()))
    return ""


# ---------------------------------------------------------------------------
# Odoo: las compras confirmadas de cada proveedor
#
# «Lo que de verdad se le compró» son las órdenes CONFIRMADAS
# (`state in ('purchase', 'done')`): una cotización en borrador o enviada
# todavía no es una compra, y una cancelada tampoco. `purchase.order` puede
# no existir (un Odoo de pruebas viejo, o una base nueva sin el módulo
# `purchase`) — esta función PROPAGA esa falla y no la calla: quien llama
# decide cómo mostrarla (`listar()`).
# ---------------------------------------------------------------------------

ESTADOS_COMPRA_DE_VERDAD = ("purchase", "done")

CAMPOS_ORDEN_COMPRA = ["partner_id", "amount_total", "date_order", "state"]


def _ordenes_confirmadas(ids_partner):
    """Las órdenes de compra confirmadas de esos proveedores, en UNA
    consulta — no una por tarjeta."""
    ids_partner = [int(i) for i in ids_partner if i]
    if not ids_partner:
        return []
    dominio = [["state", "in", list(ESTADOS_COMPRA_DE_VERDAD)],
               ["partner_id", "in", ids_partner]]
    return ventas._ejecutar(
        "purchase.order", "search_read", [dominio],
        {"fields": CAMPOS_ORDEN_COMPRA, "limit": 5000})


def _fecha_utc(texto):
    """La fecha de Odoo (UTC, sin zona explícita) como datetime aware, o
    None si no se pudo leer. Nunca revienta por una fecha rara."""
    try:
        return datetime.strptime(str(texto), "%Y-%m-%d %H:%M:%S").replace(
            tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _dias_desde(texto_fecha_utc):
    """Días corridos desde esa fecha de Odoo hasta hoy, en hora de Panamá.
    None si la fecha no se pudo leer — "no sé", nunca un 0 inventado."""
    fecha = _fecha_utc(texto_fecha_utc)
    if fecha is None:
        return None
    hoy = datetime.now(timezone.utc).astimezone(ZONA_PANAMA).date()
    return (hoy - fecha.astimezone(ZONA_PANAMA).date()).days


def _agregados_por_proveedor(ordenes):
    """{partner_id: {"compras_totales", "ultima_compra" (texto de Odoo o
    ""), "compras_ano", "total_ano"}} a partir de las órdenes confirmadas.

    `compras_totales` y `ultima_compra` son de TODA la historia (así
    calcula el estado); `compras_ano` y `total_ano` son del año en curso,
    en hora de Panamá (lo que se pinta en la tarjeta como "comprado este
    año").
    """
    anio_actual = datetime.now(ZONA_PANAMA).year
    agregados = {}
    for orden in ordenes:
        crudo = orden.get("partner_id")
        partner_id = crudo[0] if isinstance(crudo, (list, tuple)) and crudo else None
        if not partner_id:
            continue
        fecha_texto = orden.get("date_order") or ""
        a = agregados.setdefault(partner_id, {
            "compras_totales": 0, "ultima_compra": "",
            "compras_ano": 0, "total_ano": 0.0})
        a["compras_totales"] += 1
        # La más reciente en texto ISO ordena igual que en el tiempo
        # (YYYY-MM-DD HH:MM:SS), así que comparar strings alcanza.
        if fecha_texto and fecha_texto > a["ultima_compra"]:
            a["ultima_compra"] = fecha_texto
        fecha = _fecha_utc(fecha_texto)
        if fecha is not None and fecha.astimezone(ZONA_PANAMA).year == anio_actual:
            a["compras_ano"] += 1
            a["total_ano"] += float(orden.get("amount_total") or 0.0)
    return agregados


_SIN_COMPRAS = {"compras_totales": 0, "ultima_compra": "",
                "compras_ano": 0, "total_ano": 0.0}


def _ficha(proveedor, agregado, preferido):
    """El proveedor listo para la tarjeta: lo que se ve y nada más."""
    agregado = agregado or _SIN_COMPRAS
    dias = _dias_desde(agregado["ultima_compra"])
    clave = estado_de(preferido, agregado["compras_totales"], dias)
    ficha_estado = POR_CLAVE[clave]
    return {
        **proveedor,
        "preferido": preferido,
        "estado": clave,
        "estado_titulo": ficha_estado["titulo"],
        "estado_clase": ficha_estado["clase"],
        "estado_pie": ficha_estado["pie"],
        "compras_totales": agregado["compras_totales"],
        "compras_ano": agregado["compras_ano"],
        "total_ano": round(agregado["total_ano"], 2),
        "hay_compra_alguna": agregado["compras_totales"] > 0,
        "dias_desde_ultima": dias,
        # «—» y nunca un 0: sin compras no hay fecha que contar.
        "ultima_compra_texto": (linear_leads.hace_bonito(dias)
                                if dias is not None else "—"),
        # Los dos datos que esta pantalla pide y todavía no existen en
        # ninguna parte (la recepción con cantidades y dañadas es una tanda
        # futura): SIEMPRE "—", nunca un 0 ni un 100% inventados.
        "a_tiempo_texto": "—",
        "danado_texto": "—",
    }


def listar():
    """{"ok", "error", "proveedores": [...]} — todo calculado de Odoo.

    Tres caminos, y los tres se distinguen en la pantalla:

    - Odoo no está configurado en esta instancia, o no contestó: `ok`
      False con el motivo. **Nunca** una lista vacía — confundir esto con
      "no hay proveedores" fue justo el error que ya costó caro en otra
      parte del proyecto (la trampa de «0 chats etiquetados» del
      sincronizador, la misma familia).
    - Odoo contestó y de verdad no hay ningún contacto marcado proveedor
      (**el caso de HOY**, 30/09/2026): `ok` True con lista vacía. Es un
      estado real del negocio, no una falla, y la pantalla lo explica en
      palabras simples.
    - Odoo contestó con proveedores: `ok` True con la lista, cada uno con
      su estado calculado y "—" donde todavía no se puede saber nada.
    """
    if not ventas.configurado():
        return {"ok": False, "error": "Odoo no está conectado.",
                "proveedores": []}
    resultado = compras.proveedores()
    if not resultado["ok"]:
        return {"ok": False, "error": resultado["error"], "proveedores": []}
    base = resultado["proveedores"]
    if not base:
        return {"ok": True, "error": "", "proveedores": []}
    try:
        ordenes = _ordenes_confirmadas([p["id"] for p in base])
    except Exception as error:
        return {"ok": False, "error": ventas._mensaje_de_error(error),
                "proveedores": []}
    agregados = _agregados_por_proveedor(ordenes)
    preferidos = _preferidos()
    fichas = [_ficha(p, agregados.get(p["id"]), p["id"] in preferidos)
             for p in base]
    fichas.sort(key=lambda f: (_PRIORIDAD[f["estado"]], f["nombre"] or ""))
    return {"ok": True, "error": "", "proveedores": fichas}


def listar_o_vacio():
    """Como `listar()`, pero fail-soft total: lo que use la PANTALLA.

    La diferencia entre "no hay proveedores" y "Odoo no contestó" no se
    pierde — la trae `listar()` en su `ok` — pero esta función nunca deja
    reventar la pestaña con un 500 porque Odoo tuvo un mal rato.
    """
    resultado = listar()
    if not resultado["ok"]:
        registro_aviso(f"No se pudo leer la vista de proveedores: "
                       f"{resultado['error']}")
    return resultado


def resumen(proveedores):
    """Lo que se compró este año, sumado entre todos los proveedores de la
    lista. Para el renglón de arriba de la pantalla."""
    proveedores = list(proveedores or ())
    return {
        "total_ano": round(sum(p["total_ano"] for p in proveedores), 2),
        "compras_ano": sum(p["compras_ano"] for p in proveedores),
        "preferidos": sum(1 for p in proveedores if p["preferido"]),
    }


def uno(partner_id, lista=None):
    """El proveedor (ya con su ficha armada) por su id de `res.partner`, o
    None. Mismo patrón que `compras.uno()`: filtra sobre `listar()` en vez
    de pedirle una sola fila a Odoo — a esta escala no hace falta otra
    consulta, y así el estado calculado es siempre el mismo en la lista y
    en la ficha."""
    try:
        partner_id = int(partner_id)
    except (TypeError, ValueError):
        return None
    for p in (lista if lista is not None else listar()["proveedores"]):
        if p["id"] == partner_id:
            return p
    return None


# ---------------------------------------------------------------------------
# La ficha de UN proveedor: lo que le compramos, con su precio
#
# Pedido del dueño (30/09/2026, ampliando el alcance de esta misma pantalla
# mientras estaba en construcción): «quiero poder asignar plantas a cada
# proveedor, precio, etc.» Lo que ya existe en Odoo para esto es
# `product.supplierinfo` (producto + proveedor + precio + cantidad mínima),
# que vino con el módulo `purchase` instalado el 30/09.
#
# **01/10/2026: ya se puede editar.** `productos_de()` sigue siendo la
# única lectura (y sigue solo lectura); lo nuevo son las cuatro escrituras
# de más abajo — agregar, cambiar precio, quitar, y el delay compartido —
# cada una pensada para el botón exacto que la pide.
#
# MEDIDO CONTRA EL ODOO REAL (30/09/2026, por la coordinadora): el
# `fields_get` de `product.supplierinfo` tiene los seis campos de abajo,
# con esos nombres y esos tipos exactos — `price` es Float y por eso NUNCA
# llega `None` desde Odoo, así que tratar el 0 como "todavía no tiene
# precio" es lo correcto (nadie cobra de verdad $0). 0 registros, 0
# proveedores y 0 órdenes de compra ese día. Esta función ya no es un
# supuesto: es lo que el Odoo real tiene.
# ---------------------------------------------------------------------------

CAMPOS_SUPPLIERINFO = ["product_tmpl_id", "product_name", "product_code",
                       "min_qty", "price", "delay"]


def productos_de(partner_id):
    """{"ok", "error", "productos": [{"nombre", "sku", "precio",
    "cantidad_minima", "dias_entrega", "nombre_proveedor",
    "codigo_proveedor"}]} de lo que ese proveedor vende, leído de
    `product.supplierinfo`. SOLO LECTURA.

    `nombre`/`sku` son los NUESTROS (el catálogo); `nombre_proveedor`/
    `codigo_proveedor` son como ESE proveedor le llama a la planta — las
    dos cosas se guardan por separado a propósito, para que el dueño pueda
    leer la factura del proveedor y reconocer de cuál producto propio se
    trata, aunque el proveedor le haya puesto otro nombre u otro código.

    - Sin productos asignados (hoy, el caso más probable: `ok` True con
      lista vacía. No es una falla.
    - Odoo caído, o `product.supplierinfo` que no contesta como se espera:
      `ok` False con el motivo, nunca una lista vacía disfrazada.
    """
    try:
        partner_id = int(partner_id)
    except (TypeError, ValueError):
        return {"ok": False, "error": "No llegó qué proveedor mirar.",
                "productos": []}
    if not ventas.configurado():
        return {"ok": False, "error": "Odoo no está conectado.",
                "productos": []}
    try:
        filas = ventas._ejecutar(
            "product.supplierinfo", "search_read",
            [[["partner_id", "=", partner_id]]],
            {"fields": CAMPOS_SUPPLIERINFO, "limit": 500})
    except Exception as error:
        return {"ok": False, "error": ventas._mensaje_de_error(error),
                "productos": []}
    if not filas:
        return {"ok": True, "error": "", "productos": []}

    # El nombre y el SKU DEL CATÁLOGO son ADORNO de esta lectura: si esta
    # segunda vuelta a Odoo falla, la línea se muestra igual —con el
    # nombre que ya trae `product_tmpl_id` y sin SKU propio— porque perder
    # el nombre bonito no puede tapar que SÍ hay una línea. `product_name`/
    # `product_code` (los del proveedor) no dependen de esta consulta: ya
    # vinieron en la primera.
    ids_tmpl = sorted({f["product_tmpl_id"][0] for f in filas
                       if f.get("product_tmpl_id")})
    nombres, skus = {}, {}
    if ids_tmpl:
        try:
            for t in ventas._ejecutar("product.template", "read", [ids_tmpl],
                                      {"fields": ["name"]}):
                nombres[t["id"]] = t.get("name") or ""
            for p in ventas._ejecutar(
                    "product.product", "search_read",
                    [[["product_tmpl_id", "in", ids_tmpl]]],
                    {"fields": ["product_tmpl_id", "default_code"],
                     "context": {"active_test": False}}):
                tmpl_id = (p.get("product_tmpl_id") or [None])[0]
                if tmpl_id and tmpl_id not in skus:
                    skus[tmpl_id] = p.get("default_code") or ""
        except Exception as error:
            registro_aviso(f"No se pudo leer nombre/SKU de los productos "
                           f"del proveedor {partner_id}: "
                           f"{ventas._mensaje_de_error(error)}")

    productos = []
    for f in filas:
        tmpl = f.get("product_tmpl_id") or [None, ""]
        tmpl_id = tmpl[0]
        # `nombre`/`sku` son LOS NUESTROS: el nombre del catálogo primero
        # (la lectura de `product.template`), y si esa segunda consulta
        # falló, el nombre que el propio `product_tmpl_id` de la línea ya
        # trae (`product_tmpl_id` es obligatorio en Odoo, así que esto casi
        # nunca falta). Nunca se usa el texto del proveedor para esto —
        # mezclar los dos vocabularios es justo lo que se quiere evitar.
        nombre = (nombres.get(tmpl_id)
                 or (tmpl[1] if len(tmpl) > 1 else "") or "")
        sku = skus.get(tmpl_id) or ""
        # `price` es un Float de Odoo: nunca llega `None` por XML-RPC, así
        # que 0 (puesto o de fábrica) y "no se sabe" se ven IGUAL ahí.
        # Nadie vende de verdad a $0, así que un precio en 0 se trata como
        # "todavía no se puso" — nunca se pinta un $0.00 que nadie cobra.
        precio = f.get("price") or None
        productos.append({
            # El id de la LÍNEA (`product.supplierinfo`), no el del
            # producto: es lo que identifica a cambiar-precio y a quitar —
            # dos proveedores con el mismo producto tienen cada uno su
            # propia línea, y esto es lo que las distingue.
            "linea_id": f["id"],
            "producto_tmpl_id": tmpl_id, "nombre": nombre, "sku": sku,
            "precio": (round(float(precio), 2) if precio else None),
            "cantidad_minima": f.get("min_qty") or 0,
            # Cuántos días dice el PROVEEDOR que tarda en entregar esto.
            # Es un Integer con valor real siempre puesto (Odoo lo deja en
            # 1 de fábrica si nadie lo toca): se muestra tal cual, sin
            # tratar ningún número como "no se sabe".
            "dias_entrega": f.get("delay"),
            # Cómo LE DICE el proveedor a esto (su propio nombre y su
            # propio código) — separado a propósito de `nombre`/`sku`:
            # sirve para leer SU factura y reconocer cuál producto propio
            # es, aunque le haya puesto otro nombre u otro código. Vacío
            # cuando el proveedor no escribió ninguno de los dos.
            "nombre_proveedor": f.get("product_name") or "",
            "codigo_proveedor": f.get("product_code") or "",
        })
    return {"ok": True, "error": "", "productos": productos}


def uno_de(productos, linea_id):
    """Una línea de `productos_de(...)["productos"]` por su `linea_id`, o
    None. Mismo patrón que `uno()`: filtra sobre lo ya leído, no pide otra
    consulta a Odoo solo para encontrar cuál es cuál."""
    try:
        linea_id = int(linea_id)
    except (TypeError, ValueError):
        return None
    for p in (productos or ()):
        if p["linea_id"] == linea_id:
            return p
    return None


def delay_comun(productos):
    """El «días que tarda» que YA comparten TODAS las líneas de la lista,
    o None si no hay ninguna línea o si no están todas de acuerdo. Nunca
    se inventa un valor «común» promediando: o todas dicen lo mismo, o no
    hay un común que mostrar."""
    valores = {p["dias_entrega"] for p in (productos or ())
              if p.get("dias_entrega") is not None}
    return valores.pop() if len(valores) == 1 else None


# ---------------------------------------------------------------------------
# Escribir el catálogo de un proveedor (01/10/2026)
#
# Pedido literal del dueño: «quiero poder asignar plantas a cada
# proveedor, precio, etc.» Cuatro escrituras, una por cada cosa que pidió:
#
#   1. agregar_producto      — agregarle un producto con su precio
#   2. actualizar_linea      — cambiarle el precio a uno que ya está
#   3. quitar_producto       — quitarle un producto (la RELACIÓN, nunca el
#                              producto propio)
#   4. cambiar_dias_entrega  — los días que tarda, que son del PROVEEDOR y
#                              se escriben en TODAS sus líneas a la vez
#
# Las cuatro son fail-soft: devuelven "" en éxito o el texto del error,
# nunca revientan un 500 por un Odoo que tuvo un mal rato. Ninguna crea un
# `product.template`/`product.product` — si el producto no existe, la
# pantalla manda a `/productos/crear`, que ya sabe hacerlo con sus reglas
# (sin impuestos); escribir un segundo camino de alta acá sería
# exactamente lo que la consigna pidió no hacer.
#
# `min_qty`, `delay` y `currency_id` son OBLIGATORIOS en `product.
# supplierinfo`: un `create` sin los tres revienta. Los valores que esta
# pantalla pone por defecto, y por qué:
#
# - `min_qty` → 0.0 cuando no se escribió nada. Es el propio default de
#   fábrica de Odoo (su formulario nace igual) y significa «sin mínimo» —
#   nunca «cero plantas».
# - `currency_id` → la moneda de la COMPAÑÍA (`_moneda_de_la_compania`,
#   cacheada 1 hora: no cambia nunca en la práctica). `product.
#   supplierinfo` no tiene una moneda «del proveedor» separada en este
#   Odoo, así que no hay otra fuente razonable.
# - `delay` → el que YA comparten todas las demás líneas de ESE proveedor
#   (`_delay_para_nueva_linea`), o `DELAY_DEFECTO` (1, el mismo default de
#   fábrica de Odoo) si todavía no tiene ninguna o no están de acuerdo.
#   Es la decisión del punto 4 de la consigna: **el delay es del
#   proveedor, no de cada producto**, así que una planta nueva no lo pide
#   — hereda la cadencia que el proveedor ya tenga, y la ÚNICA forma de
#   cambiarlo después es `cambiar_dias_entrega`, que escribe las líneas
#   TODAS de una vez, nunca una sola. Si el dueño quiere un delay distinto
#   desde el primer producto, lo ajusta ahí mismo apenas lo agrega.
# ---------------------------------------------------------------------------

DELAY_DEFECTO = 1  # el default de fábrica de Odoo para `product.supplierinfo.delay`

_CACHE_MONEDA = {"id": None, "en": 0.0}
TTL_MONEDA = 3600  # 1 hora: la moneda de la compañía no cambia en la práctica


def reiniciar_cache_moneda():
    """Solo para pruebas: sin esto, la moneda resuelta en una prueba queda
    pegada para las siguientes (mismo motivo que `ventas.reiniciar_cache`)."""
    _CACHE_MONEDA["id"] = None
    _CACHE_MONEDA["en"] = 0.0


def _moneda_de_la_compania():
    """El id de `res.currency` de la compañía, o None si no se pudo leer.

    None es «todavía no se puede escribir» — nunca se inventa un id de
    moneda para no dejar un `create` a medias.
    """
    ahora = time.time()
    if _CACHE_MONEDA["id"] and ahora - _CACHE_MONEDA["en"] < TTL_MONEDA:
        return _CACHE_MONEDA["id"]
    try:
        filas = ventas._ejecutar(
            "res.company", "search_read", [[]],
            {"fields": ["currency_id"], "limit": 1, "order": "id"})
    except Exception as error:
        registro_aviso("No se pudo leer la moneda de la compañía: "
                       f"{ventas._mensaje_de_error(error)}")
        return None
    crudo = filas and filas[0].get("currency_id")
    if not crudo:
        return None
    _CACHE_MONEDA["id"] = crudo[0]
    _CACHE_MONEDA["en"] = ahora
    return crudo[0]


def _delay_para_nueva_linea(partner_id):
    """El delay que le toca a una línea NUEVA de este proveedor (ver nota
    de arriba): el que ya comparten TODAS sus líneas existentes, o
    `DELAY_DEFECTO` si no tiene ninguna todavía, si no están de acuerdo, o
    si Odoo no contestó esta consulta — nunca se deja de crear la línea
    por esto, el peor caso es que nazca con el default de fábrica."""
    try:
        filas = ventas._ejecutar(
            "product.supplierinfo", "search_read",
            [[["partner_id", "=", int(partner_id)]]],
            {"fields": ["delay"], "limit": 1000})
    except Exception:
        return DELAY_DEFECTO
    valores = {f.get("delay") for f in filas if f.get("delay") is not None}
    return valores.pop() if len(valores) == 1 else DELAY_DEFECTO


def _numero_o_cero(crudo):
    """Un precio o una cantidad mínima escritos a mano: vacío → 0.0 («sin
    precio todavía» / «sin mínimo», los dos defaults correctos de esta
    pantalla); ilegible o negativo → None, que quien llama trata como
    error y nunca como 0."""
    return ventas._num_positivo(crudo, defecto=0.0, permitir_cero=True)


def agregar_producto(partner_id, producto_tmpl_id, precio="",
                     cantidad_minima="", codigo_proveedor="",
                     nombre_proveedor=""):
    """Punto 1: agregarle un producto a un proveedor, con su precio.

    **Nunca duplica.** Si ESTE proveedor ya tenía una línea para ESTE
    producto (la pantalla lo ofrece igual si el buscador lo vuelve a
    traer, o por un doble clic), se actualiza esa misma fila en vez de
    crear una segunda — `product.supplierinfo` es una fila por PAR
    proveedor-producto, nunca dos.

    Devuelve "" en éxito, o el texto del error.
    """
    try:
        partner_id = int(partner_id)
        producto_tmpl_id = int(producto_tmpl_id)
    except (TypeError, ValueError):
        return "No llegó qué proveedor o qué producto asignar."
    if not ventas.configurado():
        return "Odoo no está conectado."
    precio_valor = _numero_o_cero(precio)
    if precio_valor is None:
        return "El precio tiene que ser un número (o quedarse vacío)."
    minimo_valor = _numero_o_cero(cantidad_minima)
    if minimo_valor is None:
        return "La cantidad mínima tiene que ser un número (o quedarse vacía)."
    valores = {
        "price": precio_valor,
        "min_qty": minimo_valor,
        "product_code": (codigo_proveedor or "").strip(),
        "product_name": (nombre_proveedor or "").strip(),
    }
    try:
        existentes = ventas._ejecutar(
            "product.supplierinfo", "search_read",
            [[["partner_id", "=", partner_id],
              ["product_tmpl_id", "=", producto_tmpl_id]]],
            {"fields": ["id"], "limit": 1})
    except Exception as error:
        return ventas._mensaje_de_error(error)
    try:
        if existentes:
            ventas._ejecutar("product.supplierinfo", "write",
                             [[existentes[0]["id"]], valores])
        else:
            moneda_id = _moneda_de_la_compania()
            if not moneda_id:
                return ("No se pudo leer la moneda de la compañía en Odoo; "
                        "probá de nuevo.")
            valores.update({
                "partner_id": partner_id,
                "product_tmpl_id": producto_tmpl_id,
                "currency_id": moneda_id,
                "delay": _delay_para_nueva_linea(partner_id),
            })
            ventas._ejecutar("product.supplierinfo", "create", [valores])
    except Exception as error:
        return ventas._mensaje_de_error(error)
    return ""


def actualizar_linea(partner_id, linea_id, precio="", cantidad_minima="",
                     codigo_proveedor="", nombre_proveedor=""):
    """Punto 2: cambiarle el precio (o el mínimo, o cómo le llama) a una
    línea que YA ESTÁ. Nunca toca otra: `linea_id` tiene que pertenecer a
    ESE `partner_id` o la escritura se rechaza antes de llegar a Odoo —
    así un id de línea de OTRO proveedor nunca se pisa por un formulario
    mandado a mano o un dato viejo.

    Devuelve "" en éxito, o el texto del error.
    """
    try:
        partner_id = int(partner_id)
        linea_id = int(linea_id)
    except (TypeError, ValueError):
        return "No llegó qué línea cambiar."
    if not ventas.configurado():
        return "Odoo no está conectado."
    precio_valor = _numero_o_cero(precio)
    if precio_valor is None:
        return "El precio tiene que ser un número (o quedarse vacío)."
    minimo_valor = _numero_o_cero(cantidad_minima)
    if minimo_valor is None:
        return "La cantidad mínima tiene que ser un número (o quedarse vacía)."
    try:
        filas = ventas._ejecutar(
            "product.supplierinfo", "read", [[linea_id]],
            {"fields": ["partner_id"]})
    except Exception as error:
        return ventas._mensaje_de_error(error)
    if not filas:
        return "Esa línea ya no está en Odoo."
    dueno = (filas[0].get("partner_id") or [None])[0]
    if dueno != partner_id:
        return "Esa línea no es de este proveedor."
    try:
        ventas._ejecutar("product.supplierinfo", "write", [[linea_id], {
            "price": precio_valor,
            "min_qty": minimo_valor,
            "product_code": (codigo_proveedor or "").strip(),
            "product_name": (nombre_proveedor or "").strip(),
        }])
    except Exception as error:
        return ventas._mensaje_de_error(error)
    return ""


def quitar_producto(partner_id, linea_id):
    """Punto 3: quitarle un producto a un proveedor — borra la RELACIÓN
    (`product.supplierinfo`), **nunca el producto**: lo que se `unlink`-ea
    es la línea de precio, no `product.template`.

    Mismo candado que `actualizar_linea`: la línea tiene que ser de ESE
    proveedor. Una línea que ya no existe (otro clic, otra pestaña) no es
    un error — ya se logró lo que se pedía.

    Devuelve "" en éxito, o el texto del error.
    """
    try:
        partner_id = int(partner_id)
        linea_id = int(linea_id)
    except (TypeError, ValueError):
        return "No llegó qué línea quitar."
    if not ventas.configurado():
        return "Odoo no está conectado."
    try:
        filas = ventas._ejecutar(
            "product.supplierinfo", "read", [[linea_id]],
            {"fields": ["partner_id"]})
    except Exception as error:
        return ventas._mensaje_de_error(error)
    if not filas:
        return ""
    dueno = (filas[0].get("partner_id") or [None])[0]
    if dueno != partner_id:
        return "Esa línea no es de este proveedor."
    try:
        ventas._ejecutar("product.supplierinfo", "unlink", [[linea_id]])
    except Exception as error:
        return ventas._mensaje_de_error(error)
    return ""


def cambiar_dias_entrega(partner_id, delay):
    """Punto 4: los días que tarda. **Es un dato del PROVEEDOR, no de cada
    producto** (lo pidió la consigna), así que esto escribe el MISMO
    número en TODAS las líneas de ese proveedor a la vez — nunca en una
    sola. Una planta nueva que se agregue después hereda este valor sola
    (`_delay_para_nueva_linea`), sin que nadie tenga que volver a tocarlo.

    Si el proveedor todavía no tiene ninguna línea no hay nada que
    escribir: no es un error, simplemente no hay dónde poner el número
    todavía (nace en la primera línea que se agregue).

    Devuelve "" en éxito, o el texto del error.
    """
    try:
        partner_id = int(partner_id)
    except (TypeError, ValueError):
        return "No llegó qué proveedor cambiar."
    delay_texto = str(delay if delay is not None else "").strip()
    try:
        delay_valor = int(float(delay_texto.replace(",", ".")))
    except ValueError:
        return "Los días que tarda tienen que ser un número entero."
    if delay_valor < 0:
        return "Los días que tarda no pueden ser negativos."
    if not ventas.configurado():
        return "Odoo no está conectado."
    try:
        ids = [f["id"] for f in ventas._ejecutar(
            "product.supplierinfo", "search_read",
            [[["partner_id", "=", partner_id]]],
            {"fields": ["id"], "limit": 1000})]
        if ids:
            ventas._ejecutar("product.supplierinfo", "write",
                             [ids, {"delay": delay_valor}])
    except Exception as error:
        return ventas._mensaje_de_error(error)
    return ""


# ---------------------------------------------------------------------------
# Buscar un producto para asignarlo (01/10/2026)
#
# El MISMO buscador que usa el formulario de compra —tres prefijos
# (PL-/MC-/IN-), sin pedir `sale_ok`, porque un insumo que se compra no
# tiene por qué estar a la venta— pero sin tocar `compras.py`: ese archivo
# lo está trabajando otra tanda en paralelo. Esta función reusa
# `ventas.dominio_de_busqueda` (público, sin tocar) y las dos constantes
# de `compras.py` (`PREFIJOS_COMPRA`/`SOLO_VENDIBLES_EN_COMPRAS`, que solo
# se LEEN) para que los dos buscadores nunca se desincronicen.
#
# Distinto de `compras.buscar_productos`: este trae `product_tmpl_id`
# (lo que pide `product.supplierinfo`, nunca el id de la variante) y
# dedup por plantilla — un producto con variantes aparece UNA sola vez.
# ---------------------------------------------------------------------------

def buscar_para_asignar(texto):
    """{"ok", "error", "productos": [{"producto_tmpl_id", "sku", "nombre",
    "precio_lista"}]}. Una lista vacía es «no hay», nunca «no sé» — igual
    que `compras.buscar_productos`."""
    texto = (texto or "").strip()
    if not texto:
        return {"ok": True, "error": "", "productos": []}
    if not ventas.configurado():
        return {"ok": False, "error": "Odoo no está conectado.",
                "productos": []}
    try:
        dominio = ventas.dominio_de_busqueda(
            texto, prefijos=compras.PREFIJOS_COMPRA,
            solo_vendibles=compras.SOLO_VENDIBLES_EN_COMPRAS)
        filas = ventas._ejecutar(
            "product.product", "search_read", [dominio],
            {"fields": ["default_code", "name", "list_price",
                        "product_tmpl_id"], "limit": 20, "order": "name"})
    except Exception as error:
        return {"ok": False, "error": ventas._mensaje_de_error(error),
                "productos": []}
    vistos, productos = set(), []
    for f in filas:
        tmpl = f.get("product_tmpl_id") or [None]
        tmpl_id = tmpl[0]
        if not tmpl_id or tmpl_id in vistos:
            # Un producto con variantes trae más de una fila de
            # `product.product`; `product.supplierinfo` va por la
            # PLANTILLA, así que se ofrece una sola vez.
            continue
        vistos.add(tmpl_id)
        productos.append({
            "producto_tmpl_id": tmpl_id,
            "sku": f.get("default_code") or "",
            "nombre": f.get("name") or "",
            "precio_lista": f.get("list_price") or 0.0,
        })
    return {"ok": True, "error": "", "productos": productos}
