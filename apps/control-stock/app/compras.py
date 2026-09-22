"""Compras y gastos del vivero: la plata que sale.

La pestaña muestra las compras agrupadas por **a quién se le cargan**
(decisión de Abraham, 18/09/2026, eligiendo entre tres diseños): una solapa
de Proyectos, una de Ventas y una del vivero. En las dos primeras, cada
proyecto o cada venta aparece con su cuenta — cotizado, gastado y ganancia —
y sus compras colgando debajo, que es lo que permite contestar "¿cuánto llevo
metido aquí?" sin abrir Odoo.

Las compras viven en Odoo (modelo ``vivero.rose.proyecto.gasto`` del addon
``vivero_rose_pedidos``) y no en la base de esta app, para que la ficha del
proyecto en Odoo y el celular no puedan contar distinto. Aquí no se calcula
casi nada: los totales del proyecto salen de los campos que Odoo mantiene.

La regla que separa los dos mundos: **una compra de proyecto no toca el
inventario** (las plantas vienen en un contenedor comprado para ese proyecto
y nunca fueron stock del vivero), mientras que una compra normal sí lo sube,
eligiendo la planta o el insumo del catálogo con su cantidad. El stock no se
mueve desde aquí: lo aplica el order-api (``POST /api/stock/entradas``), el
único camino de escritura al inventario que deja rastro.
"""

import base64
import os
from datetime import datetime

import httpx

from .datos import ZONA_PANAMA, SinConexion
from . import cotizaciones, ventas

MODELO = "vivero.rose.proyecto.gasto"
MODELO_LINEA = "vivero.rose.gasto.linea"

# Espejo de las listas del addon (models/servicio.py). Se repiten aquí y no
# se leen de Odoo en cada pantalla porque son fijas y cambiarlas es un
# despliegue de las dos partes de todos modos.
CATEGORIAS = [
    ("plantas", "Plantas"),
    ("insumos", "Insumos"),
    ("materiales", "Materiales"),
    ("paisajismo", "Paisajismo"),
    ("mantenimiento", "Mantenimiento"),
    ("mano_obra", "Mano de obra"),
    ("transporte", "Transporte"),
    ("combustible", "Combustible"),
    ("herramientas", "Herramientas"),
    ("planilla", "Planilla"),
    ("otros", "Otros"),
]
FORMAS_PAGO = [
    ("efectivo", "Efectivo"),
    ("yappy", "Yappy"),
    ("tarjeta", "Tarjeta"),
    ("credito", "Crédito"),
]
NOMBRE_CATEGORIA = dict(CATEGORIAS)
NOMBRE_FORMA_PAGO = dict(FORMAS_PAGO)

# Las tres solapas de la pantalla, en orden.
VISTAS = (("proyectos", "Proyectos"), ("ventas", "Ventas"), ("vivero", "Del vivero"))

CAMPOS = ["concepto", "monto", "fecha", "categoria", "proveedor", "forma_pago",
          "empleada", "nota", "destino", "lead_id", "order_id",
          "recibo_nombre", "entro_inventario"]

# Una compra puede traer varias cosas, pero no cientos: el formulario del
# celular deja de ser usable mucho antes.
MAX_LINEAS = 20


def activo():
    """Sin conexión a Odoo la pestaña se muestra apagada, como Vender.

    COMPRAS_ACTIVAS=0 la apaga por completo (dueño, 22/09/2026): la pestaña
    viajó a producción en el deploy del calendario sin estar terminada, así
    que en el real queda escondida hasta que él la dé por lista. Sin la
    variable queda encendida (pruebas y desarrollo siguen igual)."""
    if os.environ.get("COMPRAS_ACTIVAS", "1") == "0":
        return False
    return ventas.configurado()


# ---------------------------------------------------------------------------
# Leer
# ---------------------------------------------------------------------------

def _fecha_corta(iso):
    """2026-09-18 -> 18/09/2026, como el resto del sistema."""
    if not iso:
        return ""
    texto = str(iso).replace(" ", "T")[:19]
    try:
        return datetime.fromisoformat(texto).strftime("%d/%m/%Y")
    except ValueError:
        return ""


def _compra_bonita(fila):
    lead = fila.get("lead_id") or None
    orden = fila.get("order_id") or None
    return {
        "n": fila["id"],
        "concepto": fila.get("concepto") or "",
        "monto": fila.get("monto") or 0.0,
        "fecha": fila.get("fecha") or "",
        "fecha_bonita": _fecha_corta(fila.get("fecha")),
        "categoria": fila.get("categoria") or "",
        "categoria_nombre": NOMBRE_CATEGORIA.get(fila.get("categoria"), ""),
        "proveedor": fila.get("proveedor") or "",
        "forma_pago": fila.get("forma_pago") or "",
        "forma_pago_nombre": NOMBRE_FORMA_PAGO.get(fila.get("forma_pago"), ""),
        "empleada": fila.get("empleada") or "",
        "nota": fila.get("nota") or "",
        "destino": fila.get("destino") or "vivero",
        "proyecto_id": lead[0] if lead else None,
        "proyecto_nombre": lead[1] if lead else "",
        "orden_id": orden[0] if orden else None,
        "orden_nombre": orden[1] if orden else "",
        "recibo": bool(fila.get("recibo_nombre")),
        "entro_inventario": bool(fila.get("entro_inventario")),
    }


def listar(destino=None, texto=""):
    """Las compras, filtradas por destino y por el texto del buscador."""
    dominio = []
    if destino:
        dominio.append(["destino", "=", destino])
    texto = (texto or "").strip()
    if texto:
        dominio += ["|", "|", ["concepto", "ilike", texto],
                    ["proveedor", "ilike", texto], ["nota", "ilike", texto]]
    filas = ventas._ejecutar(MODELO, "search_read", [dominio],
                             {"fields": CAMPOS, "order": "fecha desc, id desc"})
    return [_compra_bonita(f) for f in filas]


def obtener(n):
    filas = ventas._ejecutar(MODELO, "search_read", [[["id", "=", int(n)]]],
                             {"fields": CAMPOS, "limit": 1})
    if not filas:
        return None
    compra = _compra_bonita(filas[0])
    compra["lineas"] = lineas_de(n)
    return compra


def lineas_de(n):
    filas = ventas._ejecutar(
        MODELO_LINEA, "search_read", [[["gasto_id", "=", int(n)]]],
        {"fields": ["product_id", "sku", "cantidad", "costo_unitario"],
         "order": "id"})
    return [{"producto_id": f["product_id"][0] if f.get("product_id") else None,
             "nombre": f["product_id"][1] if f.get("product_id") else "",
             "sku": f.get("sku") or "",
             "cantidad": int(f.get("cantidad") or 0),
             "costo": f.get("costo_unitario") or 0.0}
            for f in filas]


def resumen_del_mes():
    """Cuánto se gastó este mes, partido por destino — la línea de arriba."""
    hoy = datetime.now(ZONA_PANAMA).date()
    desde = hoy.replace(day=1).isoformat()
    filas = ventas._ejecutar(
        MODELO, "search_read", [[["fecha", ">=", desde]]],
        {"fields": ["monto", "destino", "categoria"]})
    total = sum(f.get("monto") or 0.0 for f in filas)
    por_destino = {"proyecto": 0.0, "venta": 0.0, "vivero": 0.0}
    por_categoria = {}
    for fila in filas:
        monto = fila.get("monto") or 0.0
        por_destino[fila.get("destino") or "vivero"] = (
            por_destino.get(fila.get("destino") or "vivero", 0.0) + monto)
        clave = fila.get("categoria") or "otros"
        por_categoria[clave] = por_categoria.get(clave, 0.0) + monto
    categorias = sorted(
        ({"clave": c, "nombre": NOMBRE_CATEGORIA.get(c, "Otros"), "monto": m}
         for c, m in por_categoria.items()),
        key=lambda c: c["monto"], reverse=True)
    return {
        "total": total,
        "cantidad": len(filas),
        "proyectos": por_destino.get("proyecto", 0.0),
        "ventas": por_destino.get("venta", 0.0),
        "vivero": por_destino.get("vivero", 0.0),
        "categorias": categorias,
        "mes": _nombre_del_mes(hoy),
    }


MESES = ("enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
         "agosto", "septiembre", "octubre", "noviembre", "diciembre")


def _nombre_del_mes(fecha):
    return MESES[fecha.month - 1]


def por_proyecto():
    """Los proyectos con su cuenta y sus compras debajo.

    Los números (cotizado, gastado, ganancia) son los que Odoo ya calcula en
    la oportunidad: así el celular, la ficha del proyecto de Odoo y esta
    pantalla dicen siempre lo mismo.
    """
    proyectos = ventas._ejecutar(
        "crm.lead", "search_read",
        [[["lead_ref", "=like", "PROYECTO-%"]]],
        {"fields": ["lead_ref", "name", "total_cotizado", "total_gastado",
                    "ganancia_proyecto", "total_vendido"],
         "order": "lead_ref desc"})
    compras = listar("proyecto")
    por_id = {}
    for compra in compras:
        por_id.setdefault(compra["proyecto_id"], []).append(compra)
    tarjetas = []
    for proyecto in proyectos:
        tarjetas.append({
            "tipo": "proyecto",
            "id": proyecto["id"],
            "ref": proyecto.get("lead_ref") or "",
            "nombre": proyecto.get("name") or "",
            "cotizado": proyecto.get("total_cotizado") or 0.0,
            "gastado": proyecto.get("total_gastado") or 0.0,
            "ganancia": proyecto.get("ganancia_proyecto") or 0.0,
            "compras": por_id.get(proyecto["id"], []),
        })
    return tarjetas


def por_venta():
    """Las ventas que tienen compras cargadas, con su cuenta.

    Se listan solo las que tienen gasto (a diferencia de los proyectos, que
    salen todos): en Odoo hay cientos de órdenes y la pantalla es para ver
    qué costó lo que se vendió, no el historial de ventas — ese ya vive en
    la pestaña Vender.
    """
    compras = listar("venta")
    por_id = {}
    for compra in compras:
        por_id.setdefault(compra["orden_id"], []).append(compra)
    if not por_id:
        return []
    ordenes = ventas._ejecutar(
        "sale.order", "read", [list(por_id)],
        {"fields": ["name", "partner_id", "amount_total", "total_gastado"]})
    tarjetas = []
    for orden in ordenes:
        vendido = orden.get("amount_total") or 0.0
        gastado = orden.get("total_gastado") or 0.0
        tarjetas.append({
            "tipo": "venta",
            "id": orden["id"],
            "ref": orden.get("name") or "",
            "nombre": (orden.get("partner_id") or [0, ""])[1],
            "cotizado": vendido,
            "gastado": gastado,
            "ganancia": vendido - gastado,
            "compras": por_id.get(orden["id"], []),
        })
    tarjetas.sort(key=lambda t: t["ref"], reverse=True)
    return tarjetas


def proyectos_para_elegir():
    filas = ventas._ejecutar(
        "crm.lead", "search_read", [[["lead_ref", "=like", "PROYECTO-%"]]],
        {"fields": ["lead_ref", "name"], "order": "lead_ref desc"})
    return [{"id": f["id"], "ref": f.get("lead_ref") or "",
             "nombre": f.get("name") or ""} for f in filas]


def buscar_ventas(texto):
    """Ventas y cotizaciones para colgarles una compra (buscador en vivo)."""
    texto = (texto or "").strip()
    dominio = [["state", "!=", "cancel"]]
    if texto:
        dominio += ["|", ["name", "ilike", texto], ["partner_id", "ilike", texto]]
    filas = ventas._ejecutar("sale.order", "search_read", [dominio],
                             {"fields": ["name", "partner_id", "amount_total"],
                              "limit": 15, "order": "id desc"})
    return [{"id": f["id"], "ref": f["name"],
             "cliente": (f.get("partner_id") or [0, ""])[1],
             "total": f.get("amount_total") or 0.0} for f in filas]


def buscar_productos(texto):
    """Plantas (PL-) e insumos (IN-) del catálogo para la lista de lo que
    entra al inventario. Vender solo busca PL-; aquí también se compran
    insumos, que son productos igual de reales en Odoo."""
    texto = (texto or "").strip()
    if not texto:
        return []
    dominio = [
        "|", ["default_code", "like", "PL-"], ["default_code", "like", "IN-"],
        "|", ["name", "ilike", texto], ["default_code", "ilike", texto],
    ]
    filas = ventas._ejecutar("product.product", "search_read", [dominio],
                             {"fields": ["default_code", "name", "list_price",
                                         "qty_available"],
                              "limit": 20, "order": "name"})
    return [{"id": f["id"], "sku": f["default_code"], "nombre": f["name"],
             "precio": f["list_price"],
             "hay": int(f.get("qty_available") or 0)} for f in filas]


def recibo_de(n):
    """(bytes, nombre) del recibo, o (None, '') si la compra no tiene."""
    filas = ventas._ejecutar(MODELO, "read", [[int(n)]],
                             {"fields": ["recibo", "recibo_nombre"]})
    if not filas or not filas[0].get("recibo"):
        return None, ""
    return base64.b64decode(filas[0]["recibo"]), filas[0].get("recibo_nombre") or "recibo"


# ---------------------------------------------------------------------------
# Escribir
# ---------------------------------------------------------------------------

def _valores(datos_form, empleada=None):
    """Valida lo que llegó del formulario y arma los valores para Odoo.

    Solo el monto y la descripción son obligatorios (Abraham, 18/09/2026):
    categoría, proveedor, forma de pago y fecha se pueden dejar vacíos y
    completar después.
    """
    concepto = (datos_form.get("concepto") or "").strip()
    if not concepto:
        raise ValueError("Escribe qué se compró.")
    monto = cotizaciones._num(datos_form.get("monto"))
    if monto is None:
        raise ValueError("El monto de la compra tiene que ser mayor que cero.")

    proyecto_id = _entero(datos_form.get("proyecto_id"))
    orden_id = _entero(datos_form.get("orden_id"))
    destino = (datos_form.get("destino") or "vivero").strip()
    if destino != "proyecto":
        proyecto_id = None
    if destino != "venta":
        orden_id = None
    if destino == "proyecto" and not proyecto_id:
        raise ValueError("Elige a qué proyecto se le carga la compra.")
    if destino == "venta" and not orden_id:
        raise ValueError("Elige a qué venta se le carga la compra.")

    categoria = (datos_form.get("categoria") or "").strip()
    if categoria and categoria not in NOMBRE_CATEGORIA:
        raise ValueError("Esa categoría no existe.")
    forma_pago = (datos_form.get("forma_pago") or "").strip()
    if forma_pago and forma_pago not in NOMBRE_FORMA_PAGO:
        raise ValueError("Esa forma de pago no existe.")

    fecha = (datos_form.get("fecha") or "").strip()
    if fecha:
        try:
            datetime.fromisoformat(fecha)
        except ValueError:
            raise ValueError("La fecha no se entiende.")
    else:
        fecha = datetime.now(ZONA_PANAMA).date().isoformat()

    valores = {
        "concepto": concepto,
        "monto": monto,
        "fecha": fecha,
        "categoria": categoria or False,
        "proveedor": (datos_form.get("proveedor") or "").strip() or False,
        "forma_pago": forma_pago or False,
        "nota": (datos_form.get("nota") or "").strip() or False,
        "lead_id": proyecto_id or False,
        "order_id": orden_id or False,
    }
    if empleada is not None:
        valores["empleada"] = empleada.get("nombre") or empleada.get("id") or False
    return valores


def _entero(valor):
    try:
        numero = int(str(valor).strip())
    except (TypeError, ValueError):
        return None
    return numero if numero > 0 else None


def lineas_del_form(form):
    """Las plantas o insumos que entran al inventario, tal como vienen del
    formulario: pares producto_id / cantidad. Una compra de proyecto nunca
    los lleva, así que quien llama los descarta antes."""
    lineas = []
    for clave in form:
        if not clave.startswith("producto_"):
            continue
        producto_id = _entero(form.get(clave))
        cantidad = _entero(form.get("cantidad_" + clave.split("_", 1)[1]))
        if not producto_id or not cantidad:
            continue
        costo = cotizaciones._num(form.get("costo_" + clave.split("_", 1)[1])) or 0.0
        lineas.append({"producto_id": producto_id, "cantidad": cantidad,
                       "costo": costo})
        if len(lineas) >= MAX_LINEAS:
            break
    return lineas


def crear(empleada, datos_form, lineas=None, recibo=None, recibo_nombre=""):
    """Anota la compra en Odoo y devuelve su id.

    Si la compra trae plantas o insumos, se guardan como líneas; subir el
    stock es un paso aparte (``subir_al_inventario``) para que un fallo del
    order-api no deje la compra sin registrar.
    """
    valores = _valores(datos_form, empleada)
    lineas = [] if valores["lead_id"] else (lineas or [])
    if lineas:
        valores["linea_ids"] = [
            (0, 0, {"product_id": l["producto_id"], "cantidad": l["cantidad"],
                    "costo_unitario": l["costo"]})
            for l in lineas]
    if recibo:
        valores["recibo"] = base64.b64encode(recibo).decode()
        valores["recibo_nombre"] = recibo_nombre or "recibo.jpg"
    return ventas._ejecutar(MODELO, "create", [valores])


def editar(n, datos_form, recibo=None, recibo_nombre=""):
    """Corrige una compra ya cargada. Las líneas de inventario no se tocan
    aquí: el stock ya se movió y cambiarlas no lo desharía."""
    valores = _valores(datos_form)
    if recibo:
        valores["recibo"] = base64.b64encode(recibo).decode()
        valores["recibo_nombre"] = recibo_nombre or "recibo.jpg"
    ventas._ejecutar(MODELO, "write", [[int(n)], valores])


def borrar(n):
    ventas._ejecutar(MODELO, "unlink", [[int(n)]])


def subir_al_inventario(compra_id, lineas, empleado):
    """Suma al stock de Odoo lo que trajo la compra.

    Pasa por el order-api (``POST /api/stock/entradas``) y no por XML-RPC
    directo: es el único camino de escritura al inventario que deja rastro,
    el mismo del ajuste de la pestaña Stock. Suma en el servidor (no fija un
    total) para que dos compras cargadas a la vez no se pisen.

    Devuelve la respuesta del order-api. Si no está configurado (desarrollo)
    simula la entrada, igual que hace el ajuste.
    """
    if not lineas:
        return {"ok": True, "resultados": []}
    url = os.environ.get("ORDER_API_URL")
    clave = os.environ.get("ORDER_API_KEY")
    skus = {l["sku"]: l["cantidad"] for l in lineas if l.get("sku")}
    if not skus:
        return {"ok": True, "resultados": []}
    if not url or not clave:
        ventas._ejecutar(MODELO, "write", [[int(compra_id)],
                                           {"entro_inventario": True}])
        return {"ok": True, "simulado": True,
                "resultados": [{"sku": s, "cantidad": c, "resultado": "aplicado"}
                               for s, c in skus.items()]}
    cuerpo = {
        "entradas": [{"sku": sku, "cantidad": cantidad}
                     for sku, cantidad in skus.items()],
        "empleadoId": empleado,
        "fechaHora": datetime.now(ZONA_PANAMA).isoformat(),
        "motivo": f"compra_{compra_id}",
    }
    try:
        respuesta = httpx.post(f"{url.rstrip('/')}/api/stock/entradas",
                               headers={"X-API-Key": clave}, json=cuerpo, timeout=30)
    except Exception:
        raise SinConexion("No hay conexión con el servidor de pedidos")
    if respuesta.status_code != 200:
        raise SinConexion(
            f"El servidor de pedidos respondió {respuesta.status_code}")
    datos_respuesta = respuesta.json()
    if datos_respuesta.get("ok"):
        ventas._ejecutar(MODELO, "write", [[int(compra_id)],
                                           {"entro_inventario": True}])
    return datos_respuesta
