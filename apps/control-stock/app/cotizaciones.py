"""Cotizaciones de servicios (Alquiler, Boda, Mantenimiento…) desde la
pestaña "Vender", junto a la venta local de "Nueva Venta".

Reusa la misma conexión XML-RPC de ventas.py (app/ventas.py:_ejecutar) —
misma excepción a la regla "la app nunca toca Odoo directo" que ya cubre
Crear Venta. Cada tipo corresponde a una plantilla de cotización que ya
existe en el addon vivero_rose_pedidos (la misma que usa el link "Cotizar
en Odoo" de las tarjetas de Linear): en vez de depender del onchange
privado que copia las líneas de la plantilla (solo se puede llamar desde
dentro de Odoo), las líneas se arman aquí mismo con los servicios que
la empleada describió en el mini-formulario (cada uno con su monto) —
el resultado es la misma estructura que produciría la plantilla.

Los renglones de servicio (SV-ALQUILER, SV-INSTALACION…) se resuelven por
su referencia XML (ir.model.data) en vez de por id fijo, para no duplicar
ids entre odoo-pruebas y el Odoo real.
"""

import re
from datetime import date, datetime, timedelta

from .datos import ZONA_PANAMA, _db
from . import ventas

# tipo -> metadatos de la plantilla, calcados de PLANTILLA_POR_TIPO /
# TIPOS_SERVICIO / ETIQUETA_ORDEN_POR_TIPO del addon vivero_rose_pedidos
# (controllers/cotizar.py, models/servicio.py) y de la estructura real de
# cada plantilla (data/servicios_data.xml). 'venta' no está aquí: ya lo
# cubre "+ NUEVA VENTA". 'general' es el botón "Personalizado" (sin
# plantilla), manejado aparte en crear_personalizada().
#
# La sección con "servicios" es el bloque de renglones repetibles del
# mini-formulario: la empleada escribe el servicio en sus palabras (un
# párrafo que crece) y le pone su monto, y puede añadir tantos como haga
# falta. Reemplaza los campos de monto con etiqueta fija que había antes
# (decisión de Abraham del 17/09/2026: la etiqueta enlatada no describía
# el trabajo real). Cada renglón sale como una línea del producto de
# servicio del tipo, con el párrafo como descripción; un renglón sin
# párrafo hereda el nombre del producto.
TIPOS = {
    "renta": {
        "etiqueta": "Alquiler",
        "etiqueta_cliente": "Renta / Alquiler",
        "etiqueta_orden": "RENTAL",
        "plantilla": "vivero_rose_pedidos.plantilla_servicio_renta",
        "secciones": [
            {"titulo": "Alquiler del evento", "servicios": {
                "producto": "vivero_rose_pedidos.producto_sv_alquiler_evento",
                "requerido": True,
                "ejemplo": "Alquiler de 20 plantas para el evento del sábado, "
                           "incluye transporte, montaje y retiro",
            }},
            {"titulo": "Plantas alquiladas (se llevan y regresan)",
             "catalogo": "informativo"},
        ],
    },
    "boda": {
        "etiqueta": "Boda",
        "etiqueta_cliente": "Boda",
        "etiqueta_orden": "BODA",
        "plantilla": "vivero_rose_pedidos.plantilla_servicio_boda",
        "secciones": [
            {"titulo": "Ambientación", "servicios": {
                "producto": "vivero_rose_pedidos.producto_sv_boda",
                "requerido": True,
                "ejemplo": "Ambientación con plantas para la ceremonia y el "
                           "salón, incluye montaje y retiro",
            }},
        ],
    },
    "evento": {
        "etiqueta": "Evento",
        "etiqueta_cliente": "Evento",
        "etiqueta_orden": "EVENTO",
        "plantilla": "vivero_rose_pedidos.plantilla_servicio_evento",
        "secciones": [
            {"titulo": "Ambientación", "servicios": {
                "producto": "vivero_rose_pedidos.producto_sv_evento",
                "requerido": True,
                "ejemplo": "Ambientación con plantas del evento, incluye "
                           "transporte, montaje y retiro",
            }},
        ],
    },
    "mantenimiento": {
        "etiqueta": "Mantenimiento",
        "etiqueta_cliente": "Mantenimiento",
        "etiqueta_orden": "MANTENIMIENTO",
        "plantilla": "vivero_rose_pedidos.plantilla_servicio_mantenimiento",
        "secciones": [
            {"titulo": "Mantenimiento del contrato", "servicios": {
                "producto": "vivero_rose_pedidos.producto_sv_mantenimiento_total",
                "requerido": True,
                "ejemplo": "Mantenimiento mensual: 2 visitas, riego, abono, "
                           "poda y control de plagas",
            }},
            {"titulo": "Qué cubre (referencia)", "catalogo": "informativo"},
        ],
    },
    "paisajismo": {
        "etiqueta": "Paisajismo",
        "etiqueta_cliente": "Paisajismo",
        "etiqueta_orden": "PAISAJISMO",
        "plantilla": "vivero_rose_pedidos.plantilla_servicio_paisajismo",
        "secciones": [
            {"titulo": "Plantas y materiales", "catalogo": "precio"},
            {"titulo": "Servicio del proyecto", "servicios": {
                "producto": "vivero_rose_pedidos.producto_sv_instalacion",
                "requerido": True,
                "ejemplo": "Diseño del jardín, preparación de suelo, siembra "
                           "e instalación, transporte y mantenimiento inicial",
            }},
        ],
    },
    "proyecto": {
        "etiqueta": "Proyecto",
        "etiqueta_cliente": "Proyecto",
        "etiqueta_orden": "PROYECTO",
        "plantilla": "vivero_rose_pedidos.plantilla_servicio_proyecto",
        "secciones": [
            {"titulo": "Plantas y materiales", "catalogo": "precio"},
            {"titulo": "Servicio del proyecto", "servicios": {
                "producto": "vivero_rose_pedidos.producto_sv_instalacion",
                "requerido": True,
                "ejemplo": "Instalación en sitio, transporte y mantenimiento "
                           "inicial de prendimiento",
            }},
        ],
    },
    "instalacion": {
        "etiqueta": "Instalación",
        "etiqueta_cliente": "Instalación",
        "etiqueta_orden": "INSTALACION",
        "plantilla": "vivero_rose_pedidos.plantilla_servicio_instalacion",
        "secciones": [
            {"titulo": "Plantas y materiales", "catalogo": "precio"},
            {"titulo": "Servicio de instalación", "servicios": {
                "producto": "vivero_rose_pedidos.producto_sv_instalacion",
                "requerido": True,
                "ejemplo": "Instalación de 12 palmas en el jardín frontal, "
                           "incluye tierra, abono y transporte",
            }},
        ],
    },
}

# El orden en que aparecen los botones en /venta.
ORDEN_TIPOS = ("renta", "boda", "evento", "mantenimiento", "paisajismo",
              "proyecto", "instalacion")

DIAS_VALIDEZ_DEFECTO = 15


# ---------------------------------------------------------------------------
# Referencias XML (ir.model.data): resuelve "modulo.nombre" -> id real, con
# caché en memoria (no cambian mientras el proceso vive).
# ---------------------------------------------------------------------------

_cache_referencias = {}


def _id_ref(xml_id):
    if xml_id in _cache_referencias:
        return _cache_referencias[xml_id]
    modulo, nombre = xml_id.split(".", 1)
    filas = ventas._ejecutar(
        "ir.model.data", "search_read",
        [[["module", "=", modulo], ["name", "=", nombre]]],
        {"fields": ["res_id"], "limit": 1})
    if not filas:
        raise RuntimeError(
            f"Falta la referencia {xml_id} en Odoo (¿addon vivero_rose_pedidos "
            "desactualizado?).")
    _cache_referencias[xml_id] = filas[0]["res_id"]
    return _cache_referencias[xml_id]


def reiniciar_cache():
    """Solo para pruebas."""
    _cache_referencias.clear()


# ---------------------------------------------------------------------------
# Cliente: a diferencia de Nueva Venta (que solo busca por nombre exacto),
# aquí se busca primero por teléfono (últimos 8 dígitos) y luego por
# nombre, porque el cliente de un servicio normalmente ya dejó su celular
# en una cotización anterior. Se crea si no aparece por ninguno de los dos.
#
# Se usa el campo "phone" (no "mobile"): es el que ya usa el flujo de
# Linear/Twenty para estos mismos clientes de servicio
# (vivero_rose_pedidos/controllers/cotizar.py:_buscar_o_crear_cliente) — y
# el único que existe de forma garantizada (en odoo-pruebas "mobile" ni
# siquiera está en el modelo res.partner).
# ---------------------------------------------------------------------------

def _variantes_telefono(digitos):
    """"65673062" -> {"65673062", "6567-3062"}: sin acceso a SQL crudo por
    XML-RPC (a diferencia de _buscar_o_crear_cliente del addon, que sí lo
    tiene y normaliza con regexp_replace), un simple ilike no encuentra un
    número guardado con guion si se busca sin él. Cubre las dos formas en
    que hoy quedan los teléfonos en Odoo: tal cual lo digitó la empleada
    (Nueva Venta, con guion) o solo dígitos (lo que este módulo guarda)."""
    ultimos = digitos[-8:]
    variantes = {ultimos}
    if len(ultimos) == 8:
        variantes.add(f"{ultimos[:4]}-{ultimos[4:]}")
    return variantes


def _dominio_telefono(digitos):
    condiciones = [["phone", "ilike", variante] for variante in _variantes_telefono(digitos)]
    return ["|"] * (len(condiciones) - 1) + condiciones


def _cliente_id(nombre, celular):
    nombre = (nombre or "").strip()
    digitos = re.sub(r"\D", "", celular or "")
    if digitos:
        ids = ventas._ejecutar(
            "res.partner", "search", [_dominio_telefono(digitos)], {"limit": 1})
        if ids:
            return ids[0]
    if nombre:
        ids = ventas._ejecutar(
            "res.partner", "search", [[["name", "=ilike", nombre]]], {"limit": 1})
        if ids:
            return ids[0]
    valores = {"name": nombre, "customer_rank": 1, "company_type": "person"}
    if digitos:
        # Solo dígitos (a diferencia de Nueva Venta, que guarda tal cual lo
        # digitó la empleada): así un buscar_clientes posterior por
        # cualquiera de las dos variantes lo encuentra sin ambigüedad.
        valores["phone"] = digitos
    return ventas._ejecutar("res.partner", "create", [valores])


def buscar_clientes(texto):
    """Clientes existentes por nombre o teléfono, para que la empleada vea
    si ya hay uno antes de escribir uno nuevo por error (máx. 8)."""
    texto = (texto or "").strip()
    if not texto:
        return []
    digitos = re.sub(r"\D", "", texto)
    condiciones = [["name", "ilike", texto]]
    if digitos:
        condiciones += [["phone", "ilike", variante]
                        for variante in _variantes_telefono(digitos)]
    dominio = ["|"] * (len(condiciones) - 1) + condiciones
    filas = ventas._ejecutar(
        "res.partner", "search_read", [dominio],
        {"fields": ["name", "phone"], "limit": 8})
    return [{"id": f["id"], "nombre": f["name"], "celular": f.get("phone") or ""}
            for f in filas]


def _etiquetar_cliente(partner_id, etiqueta):
    ids = ventas._ejecutar(
        "res.partner.category", "search", [[["name", "=", etiqueta]]], {"limit": 1})
    categoria_id = ids[0] if ids else ventas._ejecutar(
        "res.partner.category", "create", [{"name": etiqueta}])
    ventas._ejecutar("res.partner", "write",
                     [[partner_id], {"category_id": [[4, categoria_id]]}])


def _id_etiqueta_negocio(etiqueta):
    ids = ventas._ejecutar(
        "crm.tag", "search", [[["name", "=ilike", etiqueta]]], {"limit": 1})
    return ids[0] if ids else ventas._ejecutar("crm.tag", "create", [{"name": etiqueta}])


def _etiquetar_orden(orden_id, etiqueta):
    tag_id = _id_etiqueta_negocio(etiqueta)
    ventas._ejecutar("sale.order", "write", [[orden_id], {"tag_ids": [[4, tag_id]]}])


def _etiquetar_oportunidad(oportunidad_id, etiqueta):
    tag_id = _id_etiqueta_negocio(etiqueta)
    ventas._ejecutar("crm.lead", "write", [[oportunidad_id], {"tag_ids": [[4, tag_id]]}])


# ---------------------------------------------------------------------------
# Oportunidad del Flujo del CRM: la misma tarjeta que crea el link "Cotizar
# en Odoo" de Linear (vivero_rose_pedidos/controllers/cotizar.py), pero
# nace directo en "Cotizado" — aquí la cotización ya existe en el mismo
# paso, a diferencia del lead web que nace en "Nuevo" antes de cotizar.
# ---------------------------------------------------------------------------

def _crear_oportunidad(partner_id, nombre, etiqueta_tipo):
    etapa_id = _id_ref("vivero_rose_pedidos.etapa_flujo_cotizado")
    oportunidad_id = ventas._ejecutar("crm.lead", "create", [{
        "name": nombre,
        "type": "opportunity",
        "partner_id": partner_id,
        "stage_id": etapa_id,
    }])
    if isinstance(oportunidad_id, list):
        oportunidad_id = oportunidad_id[0]
    _etiquetar_oportunidad(oportunidad_id, etiqueta_tipo)
    return oportunidad_id


# ---------------------------------------------------------------------------
# Armado de las líneas y creación de la cotización
# ---------------------------------------------------------------------------

def _num(valor):
    """Un monto digitado (string del formulario) a float, o None si viene
    vacío/0 — un renglón opcional en $0 no se agrega a la cotización."""
    texto = (valor or "").strip().replace(",", "")
    if not texto:
        return None
    try:
        numero = float(texto)
    except ValueError:
        return None
    return numero if numero > 0 else None


def _monto_servicio(valor):
    """El monto de un renglón de servicio: como _num, pero respeta el 0
    escrito a mano (un servicio incluido sin cargo) y lo distingue del
    campo en blanco (None), que sí es un descuido que hay que avisar."""
    texto = (valor or "").strip().replace(",", "")
    if not texto:
        return None
    try:
        numero = float(texto)
    except ValueError:
        return None
    return max(numero, 0.0)


def servicios_del_formulario(textos, montos):
    """Los renglones repetibles del mini-formulario (servicio_texto[] +
    servicio_monto[]) emparejados en el orden en que se muestran."""
    textos = list(textos or [])
    montos = list(montos or [])
    total = max(len(textos), len(montos))
    return [{"texto": textos[i] if i < len(textos) else "",
             "monto": montos[i] if i < len(montos) else ""}
            for i in range(total)]


def _servicios_limpios(servicios):
    """Descarta los renglones que quedaron totalmente en blanco (la
    empleada añadió uno y no lo llenó) y avisa de los que tienen párrafo
    sin monto o monto ilegible."""
    limpios = []
    for renglon in servicios or []:
        texto = (renglon.get("texto") or "").strip()
        crudo = (renglon.get("monto") or "").strip()
        monto = _monto_servicio(crudo)
        if not texto and monto is None:
            continue
        if monto is None:
            resumen = texto if len(texto) <= 40 else texto[:40].rstrip() + "…"
            raise ValueError(f"Falta el monto del servicio: «{resumen}».")
        limpios.append({"texto": texto, "monto": monto})
    return limpios


def _lineas_por_tipo(tipo, servicios, lineas_catalogo):
    meta = TIPOS[tipo]
    servicios = _servicios_limpios(servicios)
    lineas = []
    secuencia = 1
    for seccion in meta["secciones"]:
        cuerpo = []
        config = seccion.get("servicios")
        if config:
            if not servicios and config["requerido"]:
                raise ValueError("Agrega al menos un servicio con su monto.")
            for renglon in servicios:
                linea = {
                    "product_id": _id_ref(config["producto"]),
                    "product_uom_qty": 1,
                    "price_unit": renglon["monto"],
                }
                # Sin párrafo, Odoo hereda la descripción del producto.
                if renglon["texto"]:
                    linea["name"] = renglon["texto"]
                cuerpo.append(linea)
        if seccion.get("catalogo"):
            if not lineas_catalogo and seccion["catalogo"] == "precio":
                raise ValueError("Agrega al menos una planta o material.")
            for linea in lineas_catalogo or []:
                cuerpo.append({"product_id": linea["producto_id"],
                               "product_uom_qty": linea["cantidad"]})
        if not cuerpo:
            continue
        lineas.append({"display_type": "line_section",
                       "name": seccion["titulo"], "sequence": secuencia})
        secuencia += 1
        for item in cuerpo:
            lineas.append({**item, "sequence": secuencia})
            secuencia += 1
    if not lineas:
        raise ValueError("La cotización no tiene ningún renglón con monto.")
    return lineas


def crear_cotizacion(empleada, tipo, nombre, celular, servicios, lineas_catalogo=None):
    """Crea la cotización de servicio en Odoo: cliente (por teléfono o
    nombre; se crea si no existe), sale.order con la plantilla del tipo y
    las líneas armadas con los servicios que la empleada describió (cada
    uno con su monto) más las plantas del carrito. Devuelve el registro
    local."""
    if tipo not in TIPOS:
        raise ValueError("Tipo de servicio desconocido.")
    nombre = (nombre or "").strip()
    if not nombre:
        raise ValueError("El nombre del cliente es obligatorio.")
    meta = TIPOS[tipo]
    lineas = _lineas_por_tipo(tipo, servicios, lineas_catalogo)

    partner = _cliente_id(nombre, celular)
    oportunidad_id = _crear_oportunidad(partner, nombre, meta["etiqueta_orden"])
    plantilla_id = _id_ref(meta["plantilla"])
    plantilla = ventas._ejecutar(
        "sale.order.template", "read", [[plantilla_id]],
        {"fields": ["note", "number_of_days"]})[0]
    dias = plantilla.get("number_of_days") or DIAS_VALIDEZ_DEFECTO
    orden_id = ventas._ejecutar("sale.order", "create", [{
        "partner_id": partner,
        "sale_order_template_id": plantilla_id,
        "tipo_servicio": tipo,
        "opportunity_id": oportunidad_id,
        "note": plantilla.get("note") or "",
        "validity_date": (date.today() + timedelta(days=dias)).isoformat(),
        "order_line": [[0, 0, linea] for linea in lineas],
    }])
    if isinstance(orden_id, list):
        orden_id = orden_id[0]
    _etiquetar_cliente(partner, meta["etiqueta_cliente"])
    _etiquetar_orden(orden_id, meta["etiqueta_orden"])
    leido = ventas._ejecutar("sale.order", "read", [[orden_id]],
                             {"fields": ["name", "amount_total"]})[0]
    ventas._ejecutar("crm.lead", "write",
                     [[oportunidad_id], {"expected_revenue": leido["amount_total"]}])
    return _guardar_local(empleada, tipo, nombre, celular, orden_id,
                          leido["name"], leido["amount_total"])


# ---------------------------------------------------------------------------
# Registro local (solo para el historial de /venta; el resto del flujo —
# enviar, confirmar, facturar — sigue en Odoo).
# ---------------------------------------------------------------------------

def iniciar_tablas():
    with _db() as con:
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS cotizaciones_servicio (
                n INTEGER PRIMARY KEY AUTOINCREMENT,
                creado_en TEXT NOT NULL,
                empleada TEXT NOT NULL,
                tipo TEXT NOT NULL,
                cliente TEXT NOT NULL,
                celular TEXT,
                orden_id INTEGER NOT NULL,
                orden TEXT NOT NULL,
                total REAL
            );
            """
        )


def _guardar_local(empleada, tipo, cliente, celular, orden_id, orden, total):
    with _db() as con:
        cursor = con.execute(
            "INSERT INTO cotizaciones_servicio (creado_en, empleada, tipo,"
            " cliente, celular, orden_id, orden, total) VALUES (?,?,?,?,?,?,?,?)",
            (datetime.now(ZONA_PANAMA).isoformat(), empleada["nombre"], tipo,
             cliente, (celular or "").strip() or None, orden_id, orden, total))
        n = cursor.lastrowid
    return obtener(n)


def cotizaciones_todas():
    with _db() as con:
        filas = con.execute(
            "SELECT * FROM cotizaciones_servicio ORDER BY n DESC").fetchall()
    return [dict(f) for f in filas]


def obtener(n):
    with _db() as con:
        fila = con.execute(
            "SELECT * FROM cotizaciones_servicio WHERE n=?", (n,)).fetchone()
    return dict(fila) if fila else None


def etiqueta_de(tipo):
    return TIPOS[tipo]["etiqueta"] if tipo in TIPOS else "Personalizado"


# ---------------------------------------------------------------------------
# "Personalizado": sin plantilla (tipo_servicio='general'), solo el
# buscador de catálogo con precio normal — mismo mecanismo de cliente.
# ---------------------------------------------------------------------------

def crear_personalizada(empleada, nombre, celular, lineas_catalogo):
    nombre = (nombre or "").strip()
    if not nombre:
        raise ValueError("El nombre del cliente es obligatorio.")
    if not lineas_catalogo:
        raise ValueError("Agrega al menos una planta o material.")
    partner = _cliente_id(nombre, celular)
    oportunidad_id = _crear_oportunidad(partner, nombre, "SERVICIO")
    orden_id = ventas._ejecutar("sale.order", "create", [{
        "partner_id": partner,
        "tipo_servicio": "general",
        "opportunity_id": oportunidad_id,
        "order_line": [[0, 0, {"product_id": l["producto_id"],
                               "product_uom_qty": l["cantidad"]}]
                       for l in lineas_catalogo],
    }])
    if isinstance(orden_id, list):
        orden_id = orden_id[0]
    leido = ventas._ejecutar("sale.order", "read", [[orden_id]],
                             {"fields": ["name", "amount_total"]})[0]
    ventas._ejecutar("crm.lead", "write",
                     [[oportunidad_id], {"expected_revenue": leido["amount_total"]}])
    return _guardar_local(empleada, "general", nombre, celular, orden_id,
                          leido["name"], leido["amount_total"])
