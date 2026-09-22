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
from . import crm_leads, ventas

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
                "ejemplo": "Alquiler de 20 plantas para el evento del sábado",
                "ejemplo_descripcion": "Incluye transporte, montaje y "
                                       "retiro al finalizar el evento",
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
                "ejemplo": "Ambientación con plantas para la ceremonia y el salón",
                "ejemplo_descripcion": "Incluye montaje antes de la "
                                       "ceremonia y retiro al terminar",
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
                "ejemplo": "Ambientación con plantas del evento",
                "ejemplo_descripcion": "Incluye transporte, montaje y retiro",
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
                "ejemplo": "Mantenimiento mensual del jardín",
                "ejemplo_descripcion": "2 visitas al mes: riego, abono, "
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
                "ejemplo": "Diseño e instalación del jardín",
                "ejemplo_descripcion": "Diseño, preparación de suelo, siembra, "
                                       "transporte y mantenimiento inicial",
            }},
        ],
    },
    "proyecto": {
        "etiqueta": "Proyecto",
        # En los botones de "Cotizar un servicio" el nombre pelado se lee
        # como si fuera a crear un proyecto (pedido de Abraham, 17/09/2026:
        # "en + proyecto pon cotización de proyecto"). Al elegir el tipo de
        # un proyecto nuevo sigue diciendo "Proyecto" a secas, que ahí sí es
        # lo que significa.
        "etiqueta_cotizar": "Cotización de proyecto",
        "etiqueta_cliente": "Proyecto",
        "etiqueta_orden": "PROYECTO",
        "plantilla": "vivero_rose_pedidos.plantilla_servicio_proyecto",
        "secciones": [
            {"titulo": "Plantas y materiales", "catalogo": "precio"},
            {"titulo": "Servicio del proyecto", "servicios": {
                "producto": "vivero_rose_pedidos.producto_sv_instalacion",
                "requerido": True,
                "ejemplo": "Instalación de sistema de riego",
                "ejemplo_descripcion": "Suministro e instalación, incluyendo "
                                       "materiales, mano de obra y pruebas "
                                       "de funcionamiento",
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
                "ejemplo": "Instalación de 12 palmas en el jardín frontal",
                "ejemplo_descripcion": "Incluye tierra, abono y transporte",
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


# El producto de los renglones libres de la cotización personalizada. No
# viene del addon (no tiene referencia XML): se resuelve por su código y se
# crea la primera vez que hace falta, así funciona igual en el Odoo real y
# en odoo-pruebas sin depender de un -u del addon.
CODIGO_PERSONALIZADO = "SV-PERSONALIZADO"


def _id_producto_personalizado():
    if CODIGO_PERSONALIZADO in _cache_referencias:
        return _cache_referencias[CODIGO_PERSONALIZADO]
    ids = ventas._ejecutar(
        "product.product", "search", [[["default_code", "=", CODIGO_PERSONALIZADO]]],
        {"limit": 1, "context": {"active_test": False}})
    producto_id = ids[0] if ids else ventas._ejecutar("product.product", "create", [{
        "name": "Servicio o concepto personalizado",
        "default_code": CODIGO_PERSONALIZADO,
        "type": "service",
        "list_price": 0.0,
        "taxes_id": [[6, 0, []]],
        "invoice_policy": "order",
    }])
    if isinstance(producto_id, list):
        producto_id = producto_id[0]
    _cache_referencias[CODIGO_PERSONALIZADO] = producto_id
    return producto_id


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


def _cliente_id(nombre, celular, datos=None):
    nombre = (nombre or "").strip()
    valores_extra = ventas.valores_de_cliente(datos)
    digitos = re.sub(r"\D", "", celular or "")
    if digitos:
        ids = ventas._ejecutar(
            "res.partner", "search", [_dominio_telefono(digitos)], {"limit": 1})
        if ids:
            ventas.completar_cliente(ids[0], valores_extra)
            return ids[0]
    if nombre:
        ids = ventas._ejecutar(
            "res.partner", "search", [[["name", "=ilike", nombre]]], {"limit": 1})
        if ids:
            ventas.completar_cliente(ids[0], valores_extra)
            return ids[0]
    valores = {"name": nombre, "customer_rank": 1, "company_type": "person",
               **valores_extra}
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

def _oportunidad_para(partner_id, nombre, etiqueta_tipo, proyecto_ref):
    """La oportunidad de la que cuelga la cotización.

    Si nace dentro de un proyecto (el botón de su ficha o el selector
    "Proyecto" del formulario) se usa la oportunidad DEL PROYECTO: así la
    cotización sale adentro del proyecto en Odoo y el CRM sigue mostrando
    una sola tarjeta por proyecto, nunca una por cotización (regla del
    dueño). Sin proyecto se abre una oportunidad propia en "Cotizado", como
    hasta ahora.
    """
    ref = (proyecto_ref or "").strip()
    if not ref:
        return _crear_oportunidad(partner_id, nombre, etiqueta_tipo)
    from . import proyectos  # diferido: app/proyectos.py importa este módulo
    proyecto = proyectos.buscar(ref)
    if not proyecto:
        raise ValueError(f"No existe el proyecto {ref}.")
    return proyecto["id"]


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


def _oportunidad_espejada(partner_id, nombre, etiqueta_tipo, espejo):
    """La oportunidad cuando la venta se espejó al CRM (Linear/Twenty).

    Si el espejo devolvió un codigoRef PP-XXXXX, la oportunidad es la que
    tiene ese lead_ref — la que el pipeline acaba de abrir, o la del lead
    de WhatsApp reutilizado (decisión del 22/09/2026: nunca dos tarjetas
    del mismo cliente). Se busca aun archivada (el barrido de 15 días pudo
    dormirla; el espejo ya pidió revivirla) y se le escribe el cliente y
    la etapa Cotizado. Si no aparece o no hubo espejo, se crea la propia
    como siempre, y se le graba el lead_ref para que los dos lados queden
    amarrados igual."""
    ref = (espejo or {}).get("codigoRef") or ""
    if ref:
        ids = ventas._ejecutar(
            "crm.lead", "search", [[["lead_ref", "=", ref]]],
            {"limit": 1, "context": {"active_test": False}})
        if ids:
            oportunidad_id = ids[0]
            ventas._ejecutar("crm.lead", "write", [[oportunidad_id], {
                "partner_id": partner_id,
                "stage_id": _id_ref("vivero_rose_pedidos.etapa_flujo_cotizado"),
            }])
            _etiquetar_oportunidad(oportunidad_id, etiqueta_tipo)
            crm_leads.marcar_odoo(ref)
            return oportunidad_id
    oportunidad_id = _crear_oportunidad(partner_id, nombre, etiqueta_tipo)
    if ref:
        ventas._ejecutar("crm.lead", "write",
                         [[oportunidad_id], {"lead_ref": ref}])
        crm_leads.marcar_odoo(ref)
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


def servicios_del_formulario(textos, montos, descripciones=None):
    """Los renglones repetibles del mini-formulario (servicio_texto[] +
    servicio_descripcion[] + servicio_monto[]) emparejados en el orden en
    que se muestran. El texto es el titulo del servicio (la linea en
    negrita del PDF) y la descripcion el parrafo que sale debajo, como en
    las cotizaciones de City Mall (pedido de Abraham, 22/09/2026)."""
    textos = list(textos or [])
    montos = list(montos or [])
    descripciones = list(descripciones or [])
    total = max(len(textos), len(montos), len(descripciones))

    def dato(lista, i):
        return lista[i] if i < len(lista) else ""

    return [{"texto": dato(textos, i), "monto": dato(montos, i),
             "descripcion": dato(descripciones, i)}
            for i in range(total)]


def renglones_del_formulario(textos, cantidades, precios, descripciones=None):
    """Los renglones libres de la cotización personalizada
    (renglon_texto[] + renglon_cantidad[] + renglon_precio[] +
    renglon_descripcion[]) emparejados en el orden en que se muestran.
    La descripción es el párrafo gris bajo el título en el PDF, igual que
    en los servicios (Abraham, 22/09/2026)."""
    textos, cantidades, precios = list(textos or []), list(cantidades or []), list(precios or [])
    descripciones = list(descripciones or [])
    total = max(len(textos), len(cantidades), len(precios), len(descripciones))

    def dato(lista, i):
        return lista[i] if i < len(lista) else ""

    return [{"texto": dato(textos, i), "cantidad": dato(cantidades, i),
             "precio": dato(precios, i), "descripcion": dato(descripciones, i)}
            for i in range(total)]


def _cantidad(valor):
    """La cantidad de un renglón libre: 1 si viene vacía, y nunca 0 o
    negativa (una línea de 0 unidades no cobra nada)."""
    texto = (valor or "").strip().replace(",", ".")
    if not texto:
        return 1.0
    try:
        numero = float(texto)
    except ValueError:
        return None
    return numero if numero > 0 else None


def _renglones_limpios(renglones):
    """Descarta los renglones libres en blanco y avisa de los que tienen
    descripción sin precio, precio ilegible o cantidad inválida."""
    limpios = []
    for renglon in renglones or []:
        texto = (renglon.get("texto") or "").strip()
        descripcion = (renglon.get("descripcion") or "").strip()
        crudo_precio = (renglon.get("precio") or "").strip()
        crudo_cantidad = (renglon.get("cantidad") or "").strip()
        if not texto and not descripcion and not crudo_precio and not crudo_cantidad:
            continue
        if not texto:
            raise ValueError("Falta la descripción de un renglón.")
        precio = _monto_servicio(crudo_precio)
        if precio is None:
            raise ValueError(f"Falta el precio del renglón: «{_resumen(texto)}».")
        cantidad = _cantidad(crudo_cantidad)
        if cantidad is None:
            raise ValueError(f"Cantidad inválida en el renglón: «{_resumen(texto)}».")
        limpios.append({"texto": texto, "cantidad": cantidad, "precio": precio,
                        "descripcion": descripcion})
    return limpios


def _resumen(texto):
    return texto if len(texto) <= 40 else texto[:40].rstrip() + "…"


def _servicios_limpios(servicios):
    """Descarta los renglones que quedaron totalmente en blanco (la
    empleada añadió uno y no lo llenó) y avisa de los que tienen párrafo
    sin monto o monto ilegible. Una descripción sola tampoco alcanza: sin
    monto no hay renglón que cobrar."""
    limpios = []
    for renglon in servicios or []:
        texto = (renglon.get("texto") or "").strip()
        descripcion = (renglon.get("descripcion") or "").strip()
        crudo = (renglon.get("monto") or "").strip()
        monto = _monto_servicio(crudo)
        if not texto and not descripcion and monto is None:
            continue
        if monto is None:
            raise ValueError(f"Falta el monto del servicio: «{_resumen(texto or descripcion)}».")
        limpios.append({"texto": texto, "monto": monto,
                        "descripcion": descripcion})
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
                # La descripción del servicio viaja como su propio renglón
                # line_subsection, pegado al servicio: es lo que el PDF
                # pinta como párrafo gris debajo del título, igual que en
                # las cotizaciones de City Mall (S00077).
                if renglon.get("descripcion"):
                    cuerpo.append({"display_type": "line_subsection",
                                   "name": renglon["descripcion"]})
        if seccion.get("catalogo"):
            # Sin mínimo de plantas (pedido del dueño 17/09/2026): una
            # cotización puede ser solo de servicio, con 0 plantas.
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


def crear_cotizacion(empleada, tipo, nombre, celular, servicios,
                     lineas_catalogo=None, datos_cliente=None,
                     proyecto_ref=None):
    """Crea la cotización de servicio en Odoo: cliente (por teléfono o
    nombre; se crea si no existe), sale.order con la plantilla del tipo y
    las líneas armadas con los servicios que la empleada describió (cada
    uno con su monto) más las plantas del carrito. Devuelve el registro
    local.

    Con `proyecto_ref` la cotización queda colgada de ese proyecto: sale
    adentro de su ficha en Odoo y suma a sus totales."""
    if tipo not in TIPOS:
        raise ValueError("Tipo de servicio desconocido.")
    nombre = (nombre or "").strip()
    if not nombre:
        raise ValueError("El nombre del cliente es obligatorio.")
    meta = TIPOS[tipo]
    lineas = _lineas_por_tipo(tipo, servicios, lineas_catalogo)

    partner = _cliente_id(nombre, celular, datos_cliente)
    proyecto = (proyecto_ref or "").strip()
    # Con proyecto la oportunidad es la DEL PROYECTO y no se espeja al
    # equipo LEAD (una sola tarjeta por proyecto, regla del dueño). Sin
    # proyecto, la orden nace primero y la oportunidad se resuelve después
    # con lo que diga el espejo del CRM (¿cliente ya conocido?).
    oportunidad_id = _oportunidad_para(
        partner, nombre, meta["etiqueta_orden"], proyecto) if proyecto else None
    plantilla_id = _id_ref(meta["plantilla"])
    plantilla = ventas._ejecutar(
        "sale.order.template", "read", [[plantilla_id]],
        {"fields": ["note", "number_of_days"]})[0]
    dias = plantilla.get("number_of_days") or DIAS_VALIDEZ_DEFECTO
    valores_orden = {
        "partner_id": partner,
        "sale_order_template_id": plantilla_id,
        "tipo_servicio": tipo,
        "note": plantilla.get("note") or "",
        "validity_date": (date.today() + timedelta(days=dias)).isoformat(),
        "order_line": [[0, 0, linea] for linea in lineas],
    }
    if oportunidad_id:
        valores_orden["opportunity_id"] = oportunidad_id
    orden_id = ventas._ejecutar("sale.order", "create", [valores_orden])
    if isinstance(orden_id, list):
        orden_id = orden_id[0]
    _etiquetar_cliente(partner, meta["etiqueta_cliente"])
    _etiquetar_orden(orden_id, meta["etiqueta_orden"])
    leido = ventas._ejecutar("sale.order", "read", [[orden_id]],
                             {"fields": ["name", "amount_total"]})[0]
    espejo = None
    if proyecto:
        # En un proyecto el ingreso esperado es la SUMA de sus cotizaciones
        # (y la tarjeta pasa a Cotizado): eso lo hace proyectos, no este
        # módulo, para no pisar el total del proyecto con el de esta sola.
        from . import proyectos  # diferido: proyectos importa este módulo
        proyectos.enlazar_cotizacion(orden_id, proyecto)
    else:
        pendiente = ventas.tomar_lead_pendiente(empleada["id"])
        espejo = crm_leads.espejar_venta(
            nombre, celular, tipo, leido["name"], leido["amount_total"],
            empleada["nombre"], issue=(pendiente or {}).get("ref", ""))
        if pendiente and not (espejo or {}).get("identifier"):
            # El puente no respondió, pero la empleada venía de la ficha de
            # un lead concreto: la cotización queda vinculada igual.
            espejo = {**(espejo or {}), "identifier": pendiente["ref"]}
        oportunidad_id = _oportunidad_espejada(
            partner, nombre, meta["etiqueta_orden"], espejo)
        ventas._ejecutar("sale.order", "write",
                         [[orden_id], {"opportunity_id": oportunidad_id}])
        ventas._ejecutar("crm.lead", "write",
                         [[oportunidad_id],
                          {"expected_revenue": leido["amount_total"]}])
    return _guardar_local(empleada, tipo, nombre, celular, orden_id,
                          leido["name"], leido["amount_total"],
                          espejo=espejo)


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
        # Migración suave: el espejo en el CRM (22/09/2026) — la referencia
        # PP-XXXXX del lead y el link a su issue de Linear.
        columnas = [fila[1] for fila in con.execute(
            "PRAGMA table_info(cotizaciones_servicio)")]
        if "lead_ref" not in columnas:
            con.execute("ALTER TABLE cotizaciones_servicio ADD COLUMN lead_ref TEXT")
        if "lead_url" not in columnas:
            con.execute("ALTER TABLE cotizaciones_servicio ADD COLUMN lead_url TEXT")
        # Migración suave: el issue del kanban Retail (LEAD-NN), la llave
        # con la que la ficha del lead encuentra sus cotizaciones.
        if "lead_issue" not in columnas:
            con.execute("ALTER TABLE cotizaciones_servicio ADD COLUMN lead_issue TEXT")


def _guardar_local(empleada, tipo, cliente, celular, orden_id, orden, total,
                   espejo=None):
    with _db() as con:
        cursor = con.execute(
            "INSERT INTO cotizaciones_servicio (creado_en, empleada, tipo,"
            " cliente, celular, orden_id, orden, total, lead_ref, lead_url,"
            " lead_issue)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (datetime.now(ZONA_PANAMA).isoformat(), empleada["nombre"], tipo,
             cliente, (celular or "").strip() or None, orden_id, orden, total,
             (espejo or {}).get("codigoRef"), (espejo or {}).get("url"),
             (espejo or {}).get("identifier")))
        n = cursor.lastrowid
    # El lead pendiente de "Cotizar en Vender" (ficha de Retail) se consume
    # SIEMPRE aquí (que no se le pegue a la próxima cotización ajena); si el
    # espejo no resolvió un issue, ese lead es el amarre.
    lead = ventas.tomar_lead_pendiente(empleada.get("id") or "")
    if lead and not (espejo or {}).get("identifier"):
        vincular_lead(n, lead["ref"])
    return obtener(n)


def vincular_lead(n, issue):
    """Amarra (o con issue None desamarra) una cotización a un issue del
    kanban Retail (LEAD-NN): la corrección manual desde la ficha."""
    with _db() as con:
        con.execute("UPDATE cotizaciones_servicio SET lead_issue=? WHERE n=?",
                    (issue or None, n))


def vinculadas_por_lead():
    """{LEAD-NN: [cotizaciones vinculadas, la más nueva primero]}."""
    with _db() as con:
        filas = con.execute(
            "SELECT * FROM cotizaciones_servicio WHERE lead_issue IS NOT NULL"
            " ORDER BY n DESC").fetchall()
    resultado = {}
    for fila in filas:
        resultado.setdefault(fila["lead_issue"], []).append(dict(fila))
    return resultado


def sin_lead(limite=6):
    """Las cotizaciones recientes sin lead (candidatas a vincular desde la
    ficha de Retail)."""
    with _db() as con:
        filas = con.execute(
            "SELECT * FROM cotizaciones_servicio WHERE lead_issue IS NULL"
            " ORDER BY n DESC LIMIT ?", (limite,)).fetchall()
    return [dict(f) for f in filas]


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


def etiqueta_para_cotizar(tipo):
    """El nombre del tipo en los botones que ABREN una cotización. Casi
    siempre es el mismo del tipo; solo "proyecto" dice "Cotización de
    proyecto", para que no se confunda con crear el proyecto."""
    if tipo not in TIPOS:
        return "Personalizado"
    meta = TIPOS[tipo]
    return meta.get("etiqueta_cotizar") or meta["etiqueta"]


# ---------------------------------------------------------------------------
# "Personalizado": sin plantilla (tipo_servicio='general'), solo el
# buscador de catálogo con precio normal — mismo mecanismo de cliente.
# ---------------------------------------------------------------------------

def _lineas_personalizada(servicios, renglones, lineas_catalogo):
    """Los renglones de una cotización personalizada, en sus tres
    secciones. Lo usan crear_personalizada y la edición, para que una
    cotización editada quede con la MISMA estructura que una recién
    creada."""
    cuerpos = [
        ("Plantas y materiales",
         [{"product_id": linea["producto_id"], "product_uom_qty": linea["cantidad"]}
          for linea in lineas_catalogo or []]),
        ("Servicios",
         [linea
          for servicio in _servicios_limpios(servicios)
          for linea in (
              [{"product_id": _id_producto_personalizado(),
                "product_uom_qty": 1, "price_unit": servicio["monto"],
                **({"name": servicio["texto"]} if servicio["texto"] else {})}]
              # La descripción, como renglón line_subsection pegado al
              # servicio (el párrafo gris bajo el título en el PDF).
              + ([{"display_type": "line_subsection",
                   "name": servicio["descripcion"]}]
                 if servicio.get("descripcion") else []))]),
        ("Renglones",
         [linea
          for renglon in _renglones_limpios(renglones)
          for linea in (
              [{"product_id": _id_producto_personalizado(),
                "product_uom_qty": renglon["cantidad"],
                "price_unit": renglon["precio"], "name": renglon["texto"]}]
              + ([{"display_type": "line_subsection",
                   "name": renglon["descripcion"]}]
                 if renglon.get("descripcion") else []))]),
    ]
    lineas = []
    secuencia = 1
    for titulo, cuerpo in cuerpos:
        if not cuerpo:
            continue
        lineas.append({"display_type": "line_section", "name": titulo,
                       "sequence": secuencia})
        secuencia += 1
        for item in cuerpo:
            lineas.append({**item, "sequence": secuencia})
            secuencia += 1
    if not lineas:
        raise ValueError("Agrega al menos un renglón, un servicio o una planta.")
    return lineas


def crear_personalizada(empleada, nombre, celular, lineas_catalogo=None,
                        renglones=None, datos_cliente=None, servicios=None):
    """La cotización personalizada: todo lo escribe la empleada. Va en tres
    secciones separadas, como las plantillas de los otros tipos (pedido del
    dueño 17/09/2026): las plantas y materiales del catálogo (con el precio
    de Odoo), los servicios (párrafo + monto) y los renglones libres
    (descripción + cantidad + precio). Los servicios y los renglones libres
    usan el producto SV-PERSONALIZADO con su texto como descripción. Sin
    plantilla (tipo_servicio='general')."""
    nombre = (nombre or "").strip()
    if not nombre:
        raise ValueError("El nombre del cliente es obligatorio.")
    lineas = _lineas_personalizada(servicios, renglones, lineas_catalogo)
    partner = _cliente_id(nombre, celular, datos_cliente)
    orden_id = ventas._ejecutar("sale.order", "create", [{
        "partner_id": partner,
        "tipo_servicio": "general",
        "order_line": [[0, 0, linea] for linea in lineas],
    }])
    if isinstance(orden_id, list):
        orden_id = orden_id[0]
    leido = ventas._ejecutar("sale.order", "read", [[orden_id]],
                             {"fields": ["name", "amount_total"]})[0]
    pendiente = ventas.tomar_lead_pendiente(empleada["id"])
    espejo = crm_leads.espejar_venta(
        nombre, celular, "general", leido["name"], leido["amount_total"],
        empleada["nombre"], issue=(pendiente or {}).get("ref", ""))
    if pendiente and not (espejo or {}).get("identifier"):
        espejo = {**(espejo or {}), "identifier": pendiente["ref"]}
    oportunidad_id = _oportunidad_espejada(partner, nombre, "SERVICIO", espejo)
    ventas._ejecutar("sale.order", "write",
                     [[orden_id], {"opportunity_id": oportunidad_id}])
    ventas._ejecutar("crm.lead", "write",
                     [[oportunidad_id], {"expected_revenue": leido["amount_total"]}])
    return _guardar_local(empleada, "general", nombre, celular, orden_id,
                          leido["name"], leido["amount_total"],
                          espejo=espejo)


# ---------------------------------------------------------------------------
# Propuesta de ejemplo: para revisar cómo sale el PDF sin tener que crear
# una cotización de verdad (pedido del dueño 17/09/2026, para no depender
# de la instancia de pruebas). Arma la cotización en Odoo, renderiza el
# reporte y la borra en el mismo paso.
# ---------------------------------------------------------------------------

RENGLONES_DE_MUESTRA = (
    ("Plantas y materiales", (
        ("12 Palma Areca en maceta #12", 12, 15.00),
        ("8 Ixora Roja en maceta #10", 8, 6.50),
    )),
    ("Servicio de instalación", (
        ("Instalación en sitio: preparación del suelo, siembra y abono "
         "inicial. Incluye la primera visita de mantenimiento a los 15 días.",
         1, 250.00),
        ("Transporte, montaje y retiro de escombros", 1, 50.00),
    )),
    ("Otros renglones", (
        ("50 sacos de tierra negra cernida", 50, 4.00),
    )),
)


def _cliente_de_muestra():
    """El cliente para la propuesta de ejemplo: el "Cliente Local" que ya
    usa Nueva Venta si está configurado y, si no, el que aparezca con ese
    nombre en Odoo. Nunca crea un contacto nuevo — la muestra no debe
    ensuciar la libreta de clientes."""
    try:
        return ventas._id_config("VENTA_CLIENTE_LOCAL")
    except (KeyError, ValueError):
        ids = ventas._ejecutar("res.partner", "search",
                               [[["name", "ilike", "Cliente Local"]]], {"limit": 1})
        if not ids:
            ids = ventas._ejecutar("res.partner", "search",
                                   [[["customer_rank", ">", 0]]], {"limit": 1})
        if not ids:
            raise RuntimeError(
                "No hay ningún cliente en Odoo para armar la propuesta de ejemplo.")
        return ids[0]


REF_MUESTRA = "MUESTRA-PDF"


def _lineas_de_muestra():
    producto = _id_producto_personalizado()
    lineas = []
    secuencia = 1
    for titulo, renglones in RENGLONES_DE_MUESTRA:
        lineas.append({"display_type": "line_section", "name": titulo,
                       "sequence": secuencia})
        secuencia += 1
        for texto, cantidad, precio in renglones:
            lineas.append({"product_id": producto, "name": texto,
                           "product_uom_qty": cantidad, "price_unit": precio,
                           "sequence": secuencia})
            secuencia += 1
    return lineas


def _orden_de_muestra():
    """La cotización de ejemplo en Odoo: UNA sola, reutilizada siempre. Se
    reconoce por su referencia MUESTRA-PDF y solo se crea la primera vez.

    El primer diseño la creaba y la borraba en cada clic, pero el usuario
    de la app no tiene permiso para borrar pedidos en Odoo (eso es de
    Sales/Administrator) y cada descarga dejaba una cotización suelta.
    Reutilizar una sola no necesita permisos de borrado y nada se acumula.
    """
    lineas = _lineas_de_muestra()
    ids = ventas._ejecutar(
        "sale.order", "search",
        [[["client_order_ref", "=", REF_MUESTRA], ["state", "=", "draft"]]],
        {"limit": 1})
    if ids:
        try:
            # Refrescar las líneas, para que el ejemplo siga al día si se
            # cambian aquí; si Odoo no deja, sirve igual como está.
            ventas._ejecutar("sale.order", "write", [[ids[0]], {
                "order_line": [[5, 0, 0]] + [[0, 0, linea] for linea in lineas]}])
        except Exception:
            pass
        return ids[0]
    plantilla_id = _id_ref(TIPOS["instalacion"]["plantilla"])
    plantilla = ventas._ejecutar(
        "sale.order.template", "read", [[plantilla_id]],
        {"fields": ["note", "number_of_days"]})[0]
    dias = plantilla.get("number_of_days") or DIAS_VALIDEZ_DEFECTO
    orden_id = ventas._ejecutar("sale.order", "create", [{
        "partner_id": _cliente_de_muestra(),
        "sale_order_template_id": plantilla_id,
        "tipo_servicio": "instalacion",
        "client_order_ref": REF_MUESTRA,
        "note": plantilla.get("note") or "",
        "validity_date": (date.today() + timedelta(days=dias)).isoformat(),
        "order_line": [[0, 0, linea] for linea in lineas],
    }])
    return orden_id[0] if isinstance(orden_id, list) else orden_id


def pdf_de_muestra():
    """El PDF de la propuesta de ejemplo, para revisar cómo sale el
    documento sin cotizarle a nadie. No crea clientes, no abre oportunidad
    en el CRM, no toca el historial local y no acumula cotizaciones: en
    Odoo vive una sola, marcada MUESTRA-PDF, que se reutiliza."""
    return ventas.descargar_pdf("vivero_rose_pedidos.reporte_propuesta_venta",
                                _orden_de_muestra())


# ---------------------------------------------------------------------------
# Edición de una cotización (pedido de Abraham, 22/09/2026): se pueden
# corregir los servicios (título, descripción, monto) y las plantas
# (cantidades; 0 la quita) mientras la cotización siga siendo cotización.
# En cuanto Odoo le conoce una factura, se acabó: solo lectura.
# ---------------------------------------------------------------------------

def estados_en_odoo(orden_ids):
    """{orden_id: {"facturada", "cancelada", "editable"}} en UNA consulta.

    "Facturada" es que el sale.order tenga cualquier factura ligada; ahí
    la cotización deja de poder editarse (regla de Abraham, 22/09/2026).
    Una orden que ya no existe en Odoo simplemente no viene en el dict."""
    if not orden_ids:
        return {}
    filas = ventas._ejecutar(
        "sale.order", "search_read", [[["id", "in", list(orden_ids)]]],
        {"fields": ["state", "invoice_ids"]})
    estados = {}
    for fila in filas:
        facturada = bool(fila.get("invoice_ids"))
        cancelada = fila.get("state") == "cancel"
        estados[fila["id"]] = {
            "facturada": facturada,
            "cancelada": cancelada,
            "editable": not facturada and not cancelada,
        }
    return estados


def _titulo_de_linea(linea, producto):
    """El texto que la empleada escribió en una línea de servicio, limpio
    del nombre enlatado del producto (Odoo antepone "[SV-...] Nombre" a lo
    que la plantilla copia). Mismo criterio que _parrafo_de_servicio del
    addon."""
    nombre_producto = (producto or {}).get("name") or ""
    pedazos = [p.strip() for p in (linea.get("name") or "").split("\n") if p.strip()]
    if pedazos and nombre_producto and nombre_producto in pedazos[0]:
        pedazos = pedazos[1:]
    return " ".join(p for p in pedazos if p != nombre_producto).strip() or nombre_producto


def _numero_form(valor):
    """Un número listo para el value= de un input: 850 en vez de 850.0."""
    if valor is None:
        return ""
    return ("%d" % valor) if float(valor) == int(valor) else ("%.2f" % valor)


def cargar_para_editar(n):
    """El registro local + lo que la cotización tiene HOY en Odoo, en la
    forma que esperan los formularios: servicios [{texto, descripcion,
    monto}], plantas [{producto_id, nombre, cantidad}] y, en la
    personalizada, renglones [{texto, cantidad, precio}]. None si el
    registro no existe o la orden ya no está en Odoo."""
    registro = obtener(n)
    if not registro:
        return None
    orden_id = registro["orden_id"]
    estado = estados_en_odoo([orden_id]).get(orden_id)
    if estado is None:
        return None
    lineas = ventas._ejecutar(
        "sale.order.line", "search_read", [[["order_id", "=", orden_id]]],
        {"fields": ["name", "display_type", "product_id",
                    "product_uom_qty", "price_unit"],
         "order": "sequence, id"})
    ids = list({l["product_id"][0] for l in lineas if l.get("product_id")})
    productos = {}
    if ids:
        productos = {p["id"]: p for p in ventas._ejecutar(
            "product.product", "read", [ids],
            {"fields": ["name", "type", "default_code"]})}
    servicios, plantas, renglones = [], [], []
    seccion, ultimo = "", None
    for linea in lineas:
        tipo_linea = linea.get("display_type")
        if tipo_linea == "line_section":
            seccion = linea.get("name") or ""
        elif tipo_linea in ("line_subsection", "line_note"):
            # La descripción: el párrafo pegado debajo del último renglón
            # (servicio o renglón libre, lo que se haya agregado último).
            if ultimo is not None and not ultimo.get("descripcion"):
                ultimo["descripcion"] = linea.get("name") or ""
        elif not tipo_linea:
            producto = productos.get(linea["product_id"][0]) if linea.get("product_id") else None
            if producto and producto.get("type") != "service":
                plantas.append({
                    "producto_id": producto["id"],
                    "nombre": producto.get("name") or "",
                    "cantidad": _numero_form(linea.get("product_uom_qty") or 0),
                })
                # Una descripción después de una planta no es de nadie.
                ultimo = None
                continue
            titulo = _titulo_de_linea(linea, producto)
            if registro["tipo"] not in TIPOS and seccion != "Servicios":
                # Personalizada: lo que no está bajo "Servicios" es un
                # renglón libre (descripción + cantidad + precio).
                ultimo = {
                    "texto": titulo,
                    "descripcion": "",
                    "cantidad": _numero_form(linea.get("product_uom_qty") or 1),
                    "precio": _numero_form(linea.get("price_unit") or 0),
                }
                renglones.append(ultimo)
            else:
                ultimo = {
                    "texto": titulo,
                    "descripcion": "",
                    "monto": _numero_form(linea.get("price_unit") or 0),
                }
                servicios.append(ultimo)
    return {
        "registro": registro,
        "editable": estado["editable"],
        "facturada": estado["facturada"],
        "cancelada": estado["cancelada"],
        "servicios": servicios or [{"texto": "", "monto": "", "descripcion": ""}],
        "plantas": plantas,
        "renglones": renglones or [{"texto": "", "cantidad": "", "precio": "",
                                    "descripcion": ""}],
    }


def plantas_del_formulario(ids, cantidades):
    """Las filas de plantas del form de edición (planta_id[] +
    planta_cantidad[]) emparejadas."""
    ids, cantidades = list(ids or []), list(cantidades or [])
    total = max(len(ids), len(cantidades))

    def dato(lista, i):
        return lista[i] if i < len(lista) else ""

    return [{"producto_id": dato(ids, i), "cantidad": dato(cantidades, i)}
            for i in range(total)]


def _plantas_limpias(plantas):
    """[{producto_id, cantidad}] listos para Odoo. Cantidad 0 o vacía
    QUITA la planta (así se quita sin más botones); una cantidad ilegible
    avisa en vez de adivinar."""
    limpias = []
    for planta in plantas or []:
        try:
            producto_id = int(planta.get("producto_id"))
        except (TypeError, ValueError):
            continue
        crudo = str(planta.get("cantidad") or "").strip().replace(",", ".")
        if not crudo:
            continue
        try:
            cantidad = float(crudo)
        except ValueError:
            raise ValueError("Cantidad inválida en una planta.")
        if cantidad < 0:
            raise ValueError("La cantidad de una planta no puede ser negativa.")
        if cantidad > 0:
            limpias.append({"producto_id": producto_id, "cantidad": cantidad})
    return limpias


def editar_cotizacion(n, servicios, plantas, renglones=None):
    """Reescribe los renglones de la cotización en Odoo (misma estructura
    que al crearla, descripciones incluidas) y actualiza el total local y
    el ingreso esperado de la oportunidad. Antes de escribir re-verifica
    que siga editable: si alguien la facturó en el medio, no toca nada."""
    registro = obtener(n)
    if not registro:
        raise ValueError("No existe esa cotización.")
    orden_id = registro["orden_id"]
    estado = estados_en_odoo([orden_id]).get(orden_id)
    if estado is None:
        raise ValueError("La cotización ya no está en Odoo.")
    if estado["facturada"]:
        raise ValueError("Esta cotización ya está facturada: ya no se puede editar.")
    if estado["cancelada"]:
        raise ValueError("Esta cotización está cancelada: ya no se puede editar.")
    lineas_catalogo = _plantas_limpias(plantas)
    if registro["tipo"] in TIPOS:
        # Un tipo sin sección de catálogo (boda, evento) no sabe dónde
        # poner plantas: si alguien se las metió a mano en Odoo, mejor
        # avisar que descartarlas en silencio.
        if lineas_catalogo and not any(
                s.get("catalogo") for s in TIPOS[registro["tipo"]]["secciones"]):
            raise ValueError(
                "Este tipo de cotización no lleva plantas; edítalas en Odoo.")
        lineas = _lineas_por_tipo(registro["tipo"], servicios, lineas_catalogo)
    else:
        lineas = _lineas_personalizada(servicios, renglones, lineas_catalogo)
    # [5,0,0] vacía los renglones actuales y los [0,0,...] crean los nuevos,
    # en el mismo write: la orden nunca queda a medias.
    ventas._ejecutar("sale.order", "write", [[orden_id], {
        "order_line": [[5, 0, 0]] + [[0, 0, linea] for linea in lineas]}])
    leido = ventas._ejecutar(
        "sale.order", "read", [[orden_id]],
        {"fields": ["name", "amount_total", "opportunity_id"]})[0]
    with _db() as con:
        con.execute("UPDATE cotizaciones_servicio SET total=? WHERE n=?",
                    (leido["amount_total"], n))
    _actualizar_ingreso_esperado(leido.get("opportunity_id"))
    return obtener(n)


def _actualizar_ingreso_esperado(oportunidad):
    """El ingreso esperado de la oportunidad = la SUMA de sus cotizaciones
    vivas. Para una cotización suelta eso es su propio total; para un
    proyecto (varias cotizaciones colgadas de la misma oportunidad) es la
    suma, sin pisar el total del proyecto con el de una sola (misma regla
    que proyectos._sincronizar_flujo, sin tocar etapas)."""
    oportunidad_id = (oportunidad[0] if isinstance(oportunidad, (list, tuple))
                      else oportunidad)
    if not oportunidad_id:
        return
    from .proyectos import ESTADOS_VIVOS
    ordenes = ventas._ejecutar(
        "sale.order", "search_read",
        [[["opportunity_id", "=", oportunidad_id]]],
        {"fields": ["amount_total", "state"]})
    ventas._ejecutar("crm.lead", "write", [[oportunidad_id], {
        "expected_revenue": sum(o["amount_total"] for o in ordenes
                                if o["state"] in ESTADOS_VIVOS)}])
