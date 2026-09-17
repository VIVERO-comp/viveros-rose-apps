"""Proyectos: el contenedor de las varias cotizaciones de un mismo cliente.

Un proyecto de paisajismo no se cotiza una sola vez — al mismo cliente se le
arman varias cotizaciones a lo largo del tiempo (el diseño, después la
instalación, después el mantenimiento), y hasta ahora ninguna quedaba
agrupada. El Proyecto es ese agrupador.

No hay modelo nuevo: el proyecto ES la oportunidad del CRM (``crm.lead``),
que ya tiene la relación nativa ``order_ids`` con las cotizaciones. Así el
CRM muestra una sola tarjeta por proyecto — nunca una por cotización, porque
las cotizaciones son ``sale.order``, otro modelo.

Esta pantalla es la puerta desde el celular; la pantalla completa es la app
Proyectos de Odoo (addon ``vivero_rose_pedidos``, ``views/proyecto_views.xml``).
Por eso aquí no se calcula casi nada: las columnas, los totales y los gastos
salen de los campos que Odoo ya mantiene (``etapa_proyecto``,
``total_cotizado``, ``total_gastado``, ``ganancia_proyecto``), para que el
celular y Odoo no puedan contar distinto.

Las plantas de un proyecto NO se descuentan del inventario: normalmente se
trae un contenedor comprado para ese proyecto, que nunca fue stock del
vivero. Por eso el gasto se registra como compra y no como salida de almacén.
"""

import re
from datetime import datetime

from .datos import ZONA_PANAMA, _db
from . import cotizaciones, ventas

# El código legible de un proyecto nacido en el vivero: PROYECTO-01,
# PROYECTO-02… A diferencia de los leads del sitio (PP-XXXXX, que genera el
# tracker del navegador), estos se numeran corridos porque se crean de a uno
# y a mano. Comparten el campo llave `lead_ref` del addon, que es texto
# libre; el patrón PP- solo lo exige el controlador web /vivero/cotizar, por
# el que estos proyectos no pasan.
PREFIJO_REF = "PROYECTO"
PATRON_REF = re.compile(r"^PROYECTO-(\d+)$", re.IGNORECASE)

# Las cuatro columnas del tablero, en orden. Son las de `etapa_proyecto` del
# addon y NO las etapas del Flujo del CRM: un proyecto no pasa por Facturado
# ni Pagado, cierra en Ganado. "Nuevo" es que solo nos contactaron una vez y
# "En conversación" que ya se acordó una reunión.
ETAPAS = (
    ("nuevo", "Nuevo"),
    ("conversacion", "En conversación"),
    ("cotizado", "Cotizado"),
    ("ganado", "Ganado"),
)

# Los campos de plata que Odoo calcula en la oportunidad.
CAMPOS_PLATA = ["total_cotizado", "total_vendido", "total_gastado",
                "ganancia_proyecto", "total_facturado", "por_cobrar",
                "proximo_cobro"]

# Un presupuesto cancelado no suma a ningún total del proyecto.
ESTADOS_VIVOS = ("draft", "sent", "sale")

# La etapa del Flujo del CRM a la que sube un proyecto cuando recibe su
# primera cotización (referencia XML del addon).
ETAPA_FLUJO_COTIZADO = "vivero_rose_pedidos.etapa_flujo_cotizado"


def iniciar_tablas():
    """La tabla de compras quedó sin uso el 17/09/2026: las compras se
    guardan en Odoo para que la ficha del proyecto muestre el gasto y la
    ganancia. Se sigue creando para no romper una base vieja que la tenga."""
    with _db() as con:
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS proyecto_compras (
                n INTEGER PRIMARY KEY AUTOINCREMENT,
                creado_en TEXT NOT NULL,
                empleada TEXT NOT NULL,
                proyecto_ref TEXT NOT NULL,
                concepto TEXT NOT NULL,
                monto REAL NOT NULL,
                fecha TEXT NOT NULL,
                nota TEXT
            );
            """
        )


# ---------------------------------------------------------------------------
# Odoo: la oportunidad que hace de proyecto
# ---------------------------------------------------------------------------

def _etiqueta_id(etiqueta):
    """La crm.tag del tipo de negocio, creada si falta — la misma mecánica
    del addon y de las cotizaciones, para que PAISAJISMO sea una sola
    etiqueta en todo el sistema y no una por app."""
    ids = ventas._ejecutar(
        "crm.tag", "search", [[["name", "=ilike", etiqueta]]], {"limit": 1})
    return ids[0] if ids else ventas._ejecutar("crm.tag", "create", [{"name": etiqueta}])


def siguiente_ref():
    """PROYECTO-07 si el mayor existente es el 06. Se calcula leyendo Odoo
    (no un contador local) para que el número siga siendo correcto aunque la
    base de control-stock se pierda o la app se reinstale."""
    filas = ventas._ejecutar(
        "crm.lead", "search_read",
        [[["lead_ref", "=like", f"{PREFIJO_REF}-%"]]],
        {"fields": ["lead_ref"], "context": {"active_test": False}})
    mayor = 0
    for fila in filas:
        coincide = PATRON_REF.match((fila.get("lead_ref") or "").strip())
        if coincide:
            mayor = max(mayor, int(coincide.group(1)))
    return f"{PREFIJO_REF}-{mayor + 1:02d}"


def _cliente_del_proyecto(nombre, celular):
    """El contacto del proyecto, buscado por NOMBRE y no por teléfono.

    Las cotizaciones de servicio buscan primero por teléfono, porque el
    cliente de un servicio normalmente ya dejó su celular antes. Aquí eso
    hace daño: probándolo el 17/09/2026, un proyecto de "Carlos Morris"
    quedó a nombre de "Administrator" solo porque ese número ya estaba en
    otro contacto de la base. Un proyecto lleva el nombre de su dueño en la
    tarjeta y en la propuesta, así que vale más crear un contacto repetido
    (que se ve y se arregla) que colgarle el proyecto al cliente equivocado
    sin que nadie lo note.
    """
    nombre = (nombre or "").strip()
    ids = ventas._ejecutar(
        "res.partner", "search", [[["name", "=ilike", nombre]]], {"limit": 1})
    if ids:
        return ids[0]
    valores = {"name": nombre, "customer_rank": 1, "company_type": "person"}
    digitos = re.sub(r"\D", "", celular or "")
    if digitos:
        # En Odoo 19 res.partner ya no tiene "mobile": el celular va en
        # "phone" (escribir mobile revienta con "Invalid field").
        valores["phone"] = digitos
    return ventas._ejecutar("res.partner", "create", [valores])


def nombre_de_proyecto(nombre_proyecto, nombre_cliente):
    """El título del proyecto: "Proyecto Morris".

    Regla de Abraham (17/09/2026): el proyecto se llama "Proyecto <algo>", no
    con el nombre completo del cliente — el cliente vive en su propio bloque
    de la ficha. Si no escriben un nombre, se arma con el NOMBRE DE PILA del
    cliente y no con el apellido: su ejemplo, "Proyecto Morris", es de un
    cliente que se llama Morris Cohen, y así es como lo nombra de palabra.
    """
    nombre_proyecto = (nombre_proyecto or "").strip()
    if nombre_proyecto:
        if nombre_proyecto.lower().startswith("proyecto"):
            return nombre_proyecto
        return f"Proyecto {nombre_proyecto}"
    partes = (nombre_cliente or "").strip().split()
    return f"Proyecto {partes[0]}" if partes else "Proyecto"


def crear(empleada, nombre_cliente, celular, tipo, nota="", nombre_proyecto=""):
    """Crea el proyecto como oportunidad del CRM, con su cliente y su
    etiqueta de tipo. Aparece de una vez en la app Proyectos de Odoo."""
    nombre_cliente = (nombre_cliente or "").strip()
    if not nombre_cliente:
        raise ValueError("El nombre del cliente es obligatorio.")
    if tipo not in cotizaciones.TIPOS:
        raise ValueError("Tipo de proyecto desconocido.")
    meta = cotizaciones.TIPOS[tipo]

    partner = _cliente_del_proyecto(nombre_cliente, celular)
    ref = siguiente_ref()
    valores = {
        # El título es el nombre del PROYECTO ("Proyecto Morris"); el cliente
        # queda en partner_id y la referencia en lead_ref.
        "name": nombre_de_proyecto(nombre_proyecto, nombre_cliente),
        "type": "opportunity",
        "lead_ref": ref,
        "partner_id": partner,
        "etapa_proyecto": "nuevo",
        "tag_ids": [[4, _etiqueta_id(meta["etiqueta_orden"])]],
    }
    if (nota or "").strip():
        valores["description"] = f"<p>{(nota or '').strip()}</p>"
    proyecto_id = ventas._ejecutar("crm.lead", "create", [valores])
    if isinstance(proyecto_id, list):
        proyecto_id = proyecto_id[0]
    cotizaciones._etiquetar_cliente(partner, meta["etiqueta_cliente"])
    return ref


def enlazar_cotizacion(orden_id, ref):
    """Cuelga una cotización recién creada del proyecto. Es lo único que hace
    que la cotización cuente para los totales y que el CRM siga mostrando una
    sola tarjeta (la del proyecto) en vez de una por cotización."""
    proyecto = _buscar(ref)
    if not proyecto:
        raise ValueError(f"No existe el proyecto {ref}.")
    ventas._ejecutar("sale.order", "write",
                     [[orden_id], {"opportunity_id": proyecto["id"]}])
    _sincronizar_flujo(proyecto["id"])


def _sincronizar_flujo(proyecto_id):
    """Con la primera cotización, el proyecto pasa a "Cotizado" — tanto en su
    propia columna como en el Flujo del CRM — y el ingreso esperado queda en
    la suma de sus cotizaciones.

    El addon ya hace algo así, pero solo cuando la acción ocurre dentro de
    Odoo: su write() de sale.order escucha state/order_line/amount_total, y
    colgar la orden de la oportunidad no toca ninguno de los tres. Sin esto,
    una cotización hecha desde el celular dejaría la tarjeta en "Nuevo" y en
    $0. Se respeta la regla de oro del Flujo: solo avanza, nunca retrocede.
    """
    ordenes = ventas._ejecutar(
        "sale.order", "search_read", [[["opportunity_id", "=", proyecto_id]]],
        {"fields": ["amount_total", "state"]})
    valores = {"expected_revenue": sum(o["amount_total"] for o in ordenes
                                       if o["state"] in ESTADOS_VIVOS)}
    actual = ventas._ejecutar(
        "crm.lead", "read", [[proyecto_id]],
        {"fields": ["etapa_proyecto", "stage_id"]})[0]

    # La columna del proyecto: cotizar sí deja rastro, así que no hay por qué
    # pedirle a nadie que arrastre la tarjeta. "Nuevo" y "En conversación" se
    # mueven a mano; de Ganado no se vuelve.
    if ordenes and actual.get("etapa_proyecto") in ("nuevo", "conversacion", False):
        valores["etapa_proyecto"] = "cotizado"

    # Y el Flujo del CRM, por su secuencia de etapas.
    destino = _etapa_flujo_cotizado()
    etapa = actual.get("stage_id")
    if destino and etapa:
        filas = ventas._ejecutar("crm.stage", "read", [[destino, etapa[0]]],
                                 {"fields": ["sequence"]})
        secuencias = {f["id"]: f["sequence"] for f in filas}
        if secuencias.get(destino, 0) > secuencias.get(etapa[0], 0):
            valores["stage_id"] = destino
    ventas._ejecutar("crm.lead", "write", [[proyecto_id], valores])


def _etapa_flujo_cotizado():
    """El id de la etapa "Cotizado" del Flujo, o None si el addon no está
    actualizado en esta base (no debe tumbar la pantalla)."""
    try:
        return cotizaciones._id_ref(ETAPA_FLUJO_COTIZADO)
    except RuntimeError:
        return None


def _buscar(ref):
    filas = ventas._ejecutar(
        "crm.lead", "search_read", [[["lead_ref", "=ilike", (ref or "").strip()]]],
        {"fields": ["name", "lead_ref", "partner_id", "tag_ids", "etapa_proyecto",
                    "phone", "create_date", "description"] + CAMPOS_PLATA,
         "limit": 1})
    return filas[0] if filas else None


# ---------------------------------------------------------------------------
# Las dos pantallas
# ---------------------------------------------------------------------------

def kanban():
    """Los proyectos en columnas: cada tarjeta con su cliente, su etiqueta,
    su monto y su fecha. Los montos son los que Odoo ya calcula."""
    proyectos = ventas._ejecutar(
        "crm.lead", "search_read", [[["lead_ref", "=like", f"{PREFIJO_REF}-%"]]],
        {"fields": ["name", "lead_ref", "tag_ids", "etapa_proyecto",
                    "create_date"] + CAMPOS_PLATA,
         "order": "id desc"})
    etiquetas = _nombres_de_etiquetas(
        {tag for p in proyectos for tag in (p.get("tag_ids") or [])})

    columnas = [{"clave": clave, "titulo": titulo, "tarjetas": []}
                for clave, titulo in ETAPAS]
    por_etapa = {c["clave"]: c for c in columnas}
    for proyecto in proyectos:
        ref = proyecto.get("lead_ref") or ""
        tarjeta = {
            "ref": ref,
            "nombre": proyecto.get("name") or ref,
            "etiquetas": [etiquetas.get(t, "") for t in (proyecto.get("tag_ids") or [])],
            "fecha": _fecha_corta(proyecto.get("create_date")),
            **_plata(proyecto),
        }
        destino = por_etapa.get(proyecto.get("etapa_proyecto") or "nuevo")
        (destino or columnas[0])["tarjetas"].append(tarjeta)
    return columnas


def detalle(ref):
    """La ficha del proyecto: sus cotizaciones, sus compras y el resumen de
    plata."""
    proyecto = _buscar(ref)
    if not proyecto:
        return None
    ordenes = ventas._ejecutar(
        "sale.order", "search_read", [[["opportunity_id", "=", proyecto["id"]]]],
        {"fields": ["name", "amount_total", "state", "create_date", "tipo_servicio"],
         "order": "id desc"})
    compras = compras_de(ref)
    return {
        "ref": proyecto.get("lead_ref") or ref,
        "nombre": proyecto.get("name") or ref,
        "cliente": (proyecto.get("partner_id") or [None, ""])[1],
        "celular": proyecto.get("phone") or "",
        "etiquetas": [e for e in _nombres_de_etiquetas(
            proyecto.get("tag_ids") or []).values()],
        "etapa": dict(ETAPAS).get(proyecto.get("etapa_proyecto") or "nuevo", "Nuevo"),
        "fecha": _fecha_corta(proyecto.get("create_date")),
        "cotizaciones": [{
            "orden": o["name"],
            "orden_id": o["id"],
            "total": o["amount_total"],
            "estado": ESTADOS_BONITOS.get(o["state"], o["state"]),
            "cancelada": o["state"] == "cancel",
            "fecha": _fecha_corta(o.get("create_date")),
            "tipo": cotizaciones.etiqueta_de(o.get("tipo_servicio") or ""),
        } for o in ordenes],
        "compras": compras,
        "totales": _plata(proyecto),
        "cuantas": len([o for o in ordenes if o["state"] in ESTADOS_VIVOS]),
    }


def _plata(proyecto):
    """Los números del proyecto, tal como los calcula Odoo."""
    return {
        "cotizado": proyecto.get("total_cotizado") or 0.0,
        "vendido": proyecto.get("total_vendido") or 0.0,
        "gastado": proyecto.get("total_gastado") or 0.0,
        "ganancia": proyecto.get("ganancia_proyecto") or 0.0,
        "facturado": proyecto.get("total_facturado") or 0.0,
        "por_cobrar": proyecto.get("por_cobrar") or 0.0,
        "proximo_cobro": _fecha_corta(proyecto.get("proximo_cobro")),
    }


ESTADOS_BONITOS = {"draft": "Borrador", "sent": "Enviada", "sale": "Confirmada",
                   "cancel": "Cancelada"}


def _nombres_de_etiquetas(ids):
    ids = [i for i in ids if i]
    if not ids:
        return {}
    filas = ventas._ejecutar("crm.tag", "read", [list(ids)], {"fields": ["name"]})
    return {f["id"]: f["name"] for f in filas}


def _fecha_corta(iso):
    """17/09/2026 — día/mes, como el resto del sistema."""
    if not iso:
        return ""
    texto = str(iso).replace(" ", "T")[:19]
    try:
        return datetime.fromisoformat(texto).strftime("%d/%m/%Y")
    except ValueError:
        return ""


# ---------------------------------------------------------------------------
# Compras del proyecto (viven en Odoo)
# ---------------------------------------------------------------------------

def agregar_compra(empleada, ref, concepto, monto, fecha="", nota=""):
    """Anota una compra del proyecto en Odoo.

    Va a Odoo y no a la base de esta app (cambio del 17/09/2026) porque la
    ficha del proyecto en Odoo tiene que mostrar el gasto y la ganancia: si
    la compra se quedara aquí, lo que carga la empleada desde el celular no
    aparecería en esa pantalla y el margen saldría mal en un lado o en otro.
    """
    concepto = (concepto or "").strip()
    if not concepto:
        raise ValueError("Escribe qué se compró.")
    numero = cotizaciones._num(monto)
    if numero is None:
        raise ValueError("El monto de la compra tiene que ser mayor que cero.")
    proyecto = _buscar(ref)
    if not proyecto:
        raise ValueError(f"No existe el proyecto {ref}.")
    # Quién la cargó viaja en la nota: el gasto en Odoo no tiene campo de
    # empleada, y agregarlo obligaría a otro reinicio de Odoo.
    firma = f"Cargado por {empleada['nombre']}" if empleada.get("nombre") else ""
    apunte = (nota or "").strip()
    ventas._ejecutar("vivero.rose.proyecto.gasto", "create", [{
        "lead_id": proyecto["id"],
        "concepto": concepto,
        "monto": numero,
        "fecha": (fecha or "").strip() or datetime.now(ZONA_PANAMA).date().isoformat(),
        "nota": " · ".join(p for p in (apunte, firma) if p) or False,
    }])


def quitar_compra(ref, n):
    """Borra una compra. `n` es el id del gasto en Odoo; se comprueba que sea
    de este proyecto para que un id de otro no se borre por error."""
    proyecto = _buscar(ref)
    if not proyecto:
        return
    suyos = ventas._ejecutar(
        "vivero.rose.proyecto.gasto", "search",
        [[["id", "=", int(n)], ["lead_id", "=", proyecto["id"]]]], {"limit": 1})
    if suyos:
        ventas._ejecutar("vivero.rose.proyecto.gasto", "unlink", [suyos])


def compras_de(ref):
    proyecto = _buscar(ref)
    if not proyecto:
        return []
    filas = ventas._ejecutar(
        "vivero.rose.proyecto.gasto", "search_read",
        [[["lead_id", "=", proyecto["id"]]]],
        {"fields": ["concepto", "monto", "fecha", "nota"],
         "order": "fecha desc, id desc"})
    return [{"n": f["id"], "concepto": f["concepto"], "monto": f["monto"],
             "fecha": f["fecha"], "nota": f.get("nota") or "",
             "fecha_bonita": _fecha_corta(f["fecha"])}
            for f in filas]
