"""Rutas de pantallas de la app de recepción (server-rendered con Jinja2)."""

import os
import re

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import datos, seguridad
from .calculos import acotar_aceptado, calcular_orden, dinero, unidades

app = FastAPI(title="Recepción de Supermercados")

RUTA_APP = os.path.dirname(__file__)
app.mount("/static", StaticFiles(directory=os.path.join(RUTA_APP, "static")), name="static")

plantillas = Jinja2Templates(directory=os.path.join(RUTA_APP, "plantillas"))
plantillas.env.filters["dinero"] = dinero
plantillas.env.filters["unidades"] = unidades


def fecha_bonita(iso):
    """2026-06-18 -> 18/06/2026, como el prototipo."""
    a, m, d = iso.split("-")
    return f"{d}/{m}/{a}"


plantillas.env.filters["fecha_bonita"] = fecha_bonita
# En la pantalla de la orden los nombres van sin el sufijo " VR", como el prototipo.
plantillas.env.filters["sin_vr"] = lambda nombre: re.sub(r" VR$", "", nombre)


def quitar_prefijo(nombre, prefijo):
    """'Super Extra Villalobos' con prefijo 'Super Extra' -> 'Villalobos'."""
    if prefijo and nombre.lower().startswith(prefijo.lower()) and len(nombre) > len(prefijo):
        return nombre[len(prefijo):].strip() or nombre
    return nombre


plantillas.env.filters["quitar_prefijo"] = quitar_prefijo


def numero_pedido(nombre):
    """'S00774' -> '00774': en pantalla el pedido va sin el prefijo; por
    dentro (URLs, SQLite, proxy) se conserva completo."""
    return re.sub(r"^S(?=\d)", "", nombre)


plantillas.env.filters["numero_pedido"] = numero_pedido

# Las tablas se crean al importar: es idempotente y así el proceso (o los
# tests) nunca corren contra una base sin esquema.
datos.iniciar_db()


# ---------------------------------------------------------------------------
# Autenticación: toda la app exige sesión, salvo el login y los estáticos.
# ---------------------------------------------------------------------------

def _cookie_segura():
    # En producción (detrás de nginx con HTTPS) se pone COOKIE_SEGURA=1; en
    # desarrollo local por http una cookie Secure nunca llegaría de vuelta.
    return os.environ.get("COOKIE_SEGURA") == "1"


@app.middleware("http")
async def exigir_sesion(request: Request, call_next):
    ruta = request.url.path
    if ruta == "/login" or ruta.startswith("/static"):
        return await call_next(request)
    empleada = seguridad.empleada_de_sesion(request.cookies.get("sesion"))
    if empleada is None:
        return RedirectResponse("/login", status_code=303)
    request.state.empleada = empleada
    return await call_next(request)


@app.get("/login")
def login(request: Request):
    if seguridad.empleada_de_sesion(request.cookies.get("sesion")):
        return RedirectResponse("/", status_code=303)
    return plantillas.TemplateResponse(request, "login.html", {"error": None, "usuario": ""})


@app.post("/login")
async def entrar(request: Request):
    form = await request.form()
    usuario = (form.get("usuario") or "").strip().lower()
    empleada = seguridad.verificar(usuario, form.get("contrasena") or "")
    if empleada is None:
        respuesta = plantillas.TemplateResponse(request, "login.html", {
            "error": "Usuario o contraseña incorrectos.", "usuario": usuario,
        })
        respuesta.status_code = 401
        return respuesta
    respuesta = RedirectResponse("/", status_code=303)
    respuesta.set_cookie(
        "sesion", seguridad.crear_sesion(empleada["id"]),
        max_age=seguridad.DIAS_SESION * 24 * 3600,
        httponly=True, samesite="lax", secure=_cookie_segura(),
    )
    return respuesta


@app.post("/logout")
def salir(request: Request):
    seguridad.cerrar_sesion(request.cookies.get("sesion"))
    respuesta = RedirectResponse("/login", status_code=303)
    respuesta.delete_cookie("sesion")
    return respuesta


def _contadores():
    return {
        "pendientes": len(datos.ordenes_pendientes()),
        "devoluciones": len(datos.devoluciones_pendientes()),
        "intercambios": sum(1 for i in datos.intercambios_todos() if i["estado"] == "pendiente"),
    }


def _render_home(request, pestana, **extra):
    return plantillas.TemplateResponse(request, "home.html", {
        "pestana": pestana,
        "empleado": request.state.empleada,
        "contadores": _contadores(),
        **extra,
    })


# ---------------------------------------------------------------------------
# Pestañas de inicio
# ---------------------------------------------------------------------------

@app.get("/")
def entregas(request: Request):
    pendientes = [
        {**o, "total": sum(l["enviado"] * l["precio"] for l in o["lineas"])}
        for o in datos.ordenes_pendientes()
    ]
    return _render_home(request, "pendientes", pendientes=pendientes)


@app.get("/devoluciones")
def devoluciones(request: Request):
    return _render_home(request, "devoluciones", devoluciones=datos.devoluciones_pendientes())


@app.get("/intercambios")
def intercambios(request: Request):
    lista = [
        {**i, "total_danadas": sum(l["danadas"] for l in i["lineas"])}
        for i in datos.intercambios_todos()
    ]
    return _render_home(request, "intercambios", intercambios=lista)


@app.get("/historial")
def historial(request: Request):
    return _render_home(request, "historial", historial=datos.historial_movimientos())


@app.post("/devoluciones/{pedido}/regreso")
def regreso(request: Request, pedido: str):
    datos.confirmar_regreso(pedido, request.state.empleada)
    return RedirectResponse("/devoluciones", status_code=303)


@app.post("/intercambios/{intercambio_id}/completar")
def completar(request: Request, intercambio_id: str):
    datos.completar_intercambio(intercambio_id, request.state.empleada)
    return RedirectResponse("/intercambios", status_code=303)


# ---------------------------------------------------------------------------
# Recepción de una orden
# ---------------------------------------------------------------------------

async def _aceptado_del_form(request, orden):
    """{sku: aceptado} desde los campos a_<sku>, acotado a [0, enviado]."""
    form = await request.form()
    aceptado = {}
    for linea in orden["lineas"]:
        crudo = form.get(f"a_{linea['sku']}", linea["enviado"])
        try:
            aceptado[linea["sku"]] = acotar_aceptado(int(crudo), linea["enviado"])
        except (TypeError, ValueError):
            aceptado[linea["sku"]] = linea["enviado"]
    return aceptado


def _orden_abierta(pedido):
    orden = datos.obtener_orden(pedido)
    if orden is None or datos.orden_confirmada(pedido):
        return None
    return orden


@app.api_route("/orden/{pedido}", methods=["GET", "POST"])
async def orden(request: Request, pedido: str):
    # El POST es el "volver a revisar": re-pinta los contadores con lo editado.
    abierta = _orden_abierta(pedido)
    if abierta is None:
        return RedirectResponse("/", status_code=303)
    aceptado = await _aceptado_del_form(request, abierta) if request.method == "POST" else {}
    return plantillas.TemplateResponse(request, "orden.html", {"orden": abierta, "aceptado": aceptado})


@app.post("/orden/{pedido}/revisar")
async def revisar(request: Request, pedido: str):
    abierta = _orden_abierta(pedido)
    if abierta is None:
        return RedirectResponse("/", status_code=303)
    aceptado = await _aceptado_del_form(request, abierta)
    resultado = calcular_orden(abierta["lineas"], aceptado)
    return plantillas.TemplateResponse(request, "revisar.html", {
        "orden": abierta, "r": resultado, "aceptado": aceptado,
    })


@app.post("/orden/{pedido}/confirmar")
async def confirmar(request: Request, pedido: str):
    abierta = _orden_abierta(pedido)
    if abierta is None:
        return RedirectResponse("/", status_code=303)
    aceptado = await _aceptado_del_form(request, abierta)
    r = datos.confirmar_recepcion(pedido, aceptado, request.state.empleada)
    if r is None:
        return RedirectResponse("/", status_code=303)
    hay_devolucion = r["dev"] > 0
    return plantillas.TemplateResponse(request, "exito.html", {
        "titulo": "Entrega registrada",
        "sub": f"{abierta['cliente']} - {quitar_prefijo(abierta['sucursal'], abierta['cliente'])} · Pedido {numero_pedido(pedido)}",
        "filas": [
            ("Aceptadas", unidades(r["acep"]), "verde"),
            ("Devueltas", unidades(r["dev"]), "naranja" if hay_devolucion else "apagado"),
            ("Aceptado", dinero(r["t_acep"]), "verde"),
            ("Devuelto", dinero(r["t_dev"]), "naranja" if hay_devolucion else "apagado"),
        ],
        "boton_texto": "Volver a entregas",
        "boton_href": "/devoluciones" if hay_devolucion else "/historial",
    })


# ---------------------------------------------------------------------------
# Intercambios (flujo de creación)
# ---------------------------------------------------------------------------

@app.get("/intercambios/nuevo")
def int_clientes(request: Request):
    return plantillas.TemplateResponse(request, "int_sucursal.html", {
        "clientes": datos.obtener_clientes_supermercado(),
    })


@app.get("/intercambios/nuevo/sucursales")
def int_sucursales(request: Request, cliente: str = ""):
    elegido = next(
        (c for c in datos.obtener_clientes_supermercado() if c["clave"] == cliente), None
    )
    if elegido is None:
        return RedirectResponse("/intercambios/nuevo", status_code=303)
    return plantillas.TemplateResponse(request, "int_sucursales.html", {"cliente": elegido})


async def _intercambio_del_form(request):
    """(cliente, sucursal, danadas {sku: cantidad}) desde el form o la query."""
    if request.method == "POST":
        form = await request.form()
    else:
        form = request.query_params
    cliente = form.get("cliente", "")
    sucursal = form.get("sucursal", "")
    danadas = {}
    for producto in datos.obtener_catalogo():
        crudo = form.get(f"d_{producto['sku']}")
        if crudo is None:
            continue
        try:
            cantidad = max(0, min(int(crudo), 99))
        except (TypeError, ValueError):
            cantidad = 0
        if cantidad > 0:
            danadas[producto["sku"]] = cantidad
    return cliente, sucursal, danadas


@app.api_route("/intercambios/nuevo/plantas", methods=["GET", "POST"])
async def int_plantas(request: Request):
    # El POST es el "volver a revisar" del intercambio, con lo ya elegido.
    cliente, sucursal, danadas = await _intercambio_del_form(request)
    if not cliente or not sucursal:
        return RedirectResponse("/intercambios/nuevo", status_code=303)
    return plantillas.TemplateResponse(request, "int_plantas.html", {
        "cliente": cliente, "sucursal": sucursal,
        "catalogo": datos.obtener_catalogo(), "danadas": danadas,
    })


def _lineas_danadas(danadas):
    lineas = []
    for sku, cantidad in danadas.items():
        producto = datos.buscar_producto(sku)
        if producto is not None:
            lineas.append({"sku": sku, "nombre": producto["nombre"],
                           "precio": producto["precio"], "danadas": cantidad})
    return lineas


@app.post("/intercambios/nuevo/revisar")
async def int_revisar(request: Request):
    cliente, sucursal, danadas = await _intercambio_del_form(request)
    lineas = _lineas_danadas(danadas)
    if not lineas:
        return RedirectResponse("/intercambios/nuevo", status_code=303)
    return plantillas.TemplateResponse(request, "int_revisar.html", {
        "cliente": cliente, "sucursal": sucursal, "lineas": lineas,
        "total": sum(l["danadas"] for l in lineas),
        "valor": sum(l["danadas"] * l["precio"] for l in lineas),
    })


@app.post("/intercambios/crear")
async def int_crear(request: Request):
    cliente, sucursal, danadas = await _intercambio_del_form(request)
    lineas = _lineas_danadas(danadas)
    if not lineas:
        return RedirectResponse("/intercambios/nuevo", status_code=303)
    datos.crear_intercambio(cliente, sucursal, lineas, request.state.empleada)
    total = sum(l["danadas"] for l in lineas)
    return plantillas.TemplateResponse(request, "exito.html", {
        "titulo": "Intercambio registrado",
        "sub": f"{cliente} · {sucursal}",
        "filas": [
            ("Dañadas recogidas", unidades(total), "naranja"),
            ("Reemplazo", "Pendiente de entregar", "naranja"),
        ],
        "boton_texto": "Volver a intercambios",
        "boton_href": "/intercambios",
    })
