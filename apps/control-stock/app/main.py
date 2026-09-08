"""Rutas de la app de control de stock (server-rendered con Jinja2).

Una sola pantalla con tres pestañas (Inicio, Stock, Inventario), como el
prototipo aprobado: el servidor arma los datos y la pestañas se mueven con
el JS del prototipo. Las acciones (ajustar stock, atender alertas, conteos)
son POSTs de vuelta a este mismo servidor; la app nunca toca Odoo directo.
"""

import json
import os
from datetime import datetime
from urllib.parse import quote

from fastapi import FastAPI, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import calculos, conteos, datos, fotos, seguridad, ventas

app = FastAPI(title="Control de Stock")

RUTA_APP = os.path.dirname(__file__)
app.mount("/static", StaticFiles(directory=os.path.join(RUTA_APP, "static")), name="static")

plantillas = Jinja2Templates(directory=os.path.join(RUTA_APP, "plantillas"))

# Cache-busting de estáticos: el navegador guarda app.js/styles.css por
# heurística y tras un deploy puede quedarse con la versión vieja (JS viejo
# contra datos nuevos pinta mal la pantalla). La versión es el mtime más
# reciente de los estáticos al arrancar el proceso: cambia con cada deploy
# y las URLs ?v= nuevas fuerzan la descarga.
VERSION_ESTATICOS = int(max(
    os.path.getmtime(os.path.join(RUTA_APP, "static", nombre))
    for nombre in os.listdir(os.path.join(RUTA_APP, "static"))
))
plantillas.env.globals["v_estaticos"] = VERSION_ESTATICOS


def fecha_bonita(iso):
    """2026-09-02T10:05:00-05:00 -> 02/09/2026."""
    fecha = datetime.fromisoformat(iso)
    return fecha.strftime("%d/%m/%Y")


plantillas.env.filters["fecha_bonita"] = fecha_bonita


def dinero_venta(monto):
    """Mismo formato de moneda del resto de la app: $3.50."""
    return f"${monto:.2f}"


plantillas.env.filters["dinero"] = dinero_venta

# Las tablas se crean al importar: es idempotente y así el proceso (o los
# tests) nunca corren contra una base sin esquema.
datos.iniciar_db()
ventas.iniciar_tablas()


# ---------------------------------------------------------------------------
# Autenticación: toda la app exige sesión, salvo el login y los estáticos.
# ---------------------------------------------------------------------------

def _cookie_segura():
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


# ---------------------------------------------------------------------------
# Pantalla principal
# ---------------------------------------------------------------------------

def _dias_desde(iso):
    entonces = datetime.fromisoformat(iso)
    return (datetime.now(datos.ZONA_PANAMA) - entonces).days


def _resumen_categorias(inventario, umbral):
    """[{nombre, productos, unidades, criticos, emoji}] para las tarjetas."""
    porcategoria = {}
    for producto in inventario:
        resumen = porcategoria.setdefault(producto["categoria"], {
            "nombre": producto["categoria"], "productos": 0, "unidades": 0,
            "criticos": 0, "emoji": calculos.emoji_categoria(producto["categoria"]),
        })
        resumen["productos"] += 1
        resumen["unidades"] += max(0, producto["disponible"])
        if (calculos.es_negativo(producto)
                or calculos.estado(producto["disponible"], umbral) == "critico"):
            resumen["criticos"] += 1
    return sorted(porcategoria.values(), key=lambda c: c["nombre"])


@app.get("/")
def inicio(request: Request, refrescar: int = 0):
    umbral = datos.umbral()
    try:
        inventario, leido_en = datos.obtener_inventario(refrescar=bool(refrescar))
        sin_proxy = None
    except datos.SinConexion as error:
        inventario, leido_en, sin_proxy = [], None, str(error)
    datos.refrescar_alertas(inventario, umbral)

    cuentas = calculos.clasificar(inventario, umbral)
    ultimo = datos.ultimo_conteo_confirmado()
    dias_conteo = _dias_desde(ultimo["creado_en"]) if ultimo else None
    conteo_vencido = dias_conteo is None or dias_conteo > calculos.DIAS_CONTEO_QUINCENAL
    puntos = calculos.score(cuentas["criticos"], cuentas["bajos"], conteo_vencido)

    plantas = [
        {
            "sku": p["sku"], "n": p["nombre"], "c": p["categoria"],
            "q": p["disponible"], "f": p["fisico"],
            "e": calculos.emoji_de(p["nombre"]),
            # Precio ya formateado en el servidor: el list_price de Odoo tal
            # cual, igual que en la tienda (null = precio pendiente en Odoo).
            # img: foto de Cloudinary (null = sin foto, la tarjeta cae al
            # emoji).
            "po": calculos.precio_online(p.get("precio_centavos", 0)),
            "img": fotos.url_foto(p["sku"]),
        }
        for p in inventario
    ]
    alertas = datos.alertas_pendientes()
    return plantillas.TemplateResponse(request, "app.html", {
        "empleada": request.state.empleada,
        "puntos": puntos,
        # El anillo del score: circunferencia 402, se descubre según el score.
        "anillo": round(402 * (1 - puntos / 100)),
        "cuentas": cuentas,
        "dias_conteo": dias_conteo,
        "conteo_vencido": conteo_vencido,
        "categorias": _resumen_categorias(inventario, umbral),
        "umbral": umbral,
        "alertas": alertas,
        "conteos": datos.conteos_recientes(),
        "ultima_hoja": datos.ultima_hoja_pdf(),
        "hora_actualizado": (
            datetime.fromtimestamp(leido_en, tz=datos.ZONA_PANAMA)
            .strftime("%I:%M %p").lower().lstrip("0") if leido_en else None
        ),
        "sin_proxy": sin_proxy,
        "datos_json": json.dumps({
            "plantas": plantas,
            "umbral": umbral,
            "alertas": alertas,
        }, ensure_ascii=False),
    })


# ---------------------------------------------------------------------------
# Acciones sobre el stock
# ---------------------------------------------------------------------------

@app.post("/ajustar")
async def ajustar(request: Request):
    """El ajuste rápido del modal. El guardado real pasa por el order-api,
    que compara `esperada` contra Odoo: si alguien movió el stock en el
    medio, vuelve `conflicto` con el valor fresco y nada se escribe."""
    cuerpo = await request.json()
    sku = cuerpo.get("sku")
    cantidad = cuerpo.get("cantidad")
    esperada = cuerpo.get("esperada")
    if (not isinstance(sku, str) or not isinstance(cantidad, int) or cantidad < 0
            or not isinstance(esperada, int)):
        return Response(json.dumps({"error": "peticion_invalida"}), status_code=400,
                        media_type="application/json")
    try:
        respuesta = datos.ajustar_en_odoo(
            [{"sku": sku, "cantidad": cantidad, "esperada": esperada}],
            request.state.empleada["id"], "ajuste_rapido",
        )
    except datos.SinConexion as error:
        return Response(json.dumps({"error": "sin_conexion", "mensaje": str(error)}),
                        status_code=502, media_type="application/json")
    resultado = respuesta["resultados"][0]
    if resultado["resultado"] == "aplicado":
        # El stock cambió: la alerta pendiente del producto (si había) se
        # cierra a nombre de quien ajustó; si sigue crítico, la próxima
        # carga la vuelve a abrir con la cantidad nueva.
        datos.atender_alerta(sku, request.state.empleada["id"])
    return resultado


@app.post("/alertas/atender")
async def atender(request: Request):
    form = await request.form()
    datos.atender_alerta(form.get("sku") or "", request.state.empleada["id"])
    return RedirectResponse("/", status_code=303)


@app.post("/umbral")
async def cambiar_umbral(request: Request):
    form = await request.form()
    try:
        valor = int(form.get("umbral") or "")
    except ValueError:
        return RedirectResponse("/", status_code=303)
    if 1 <= valor <= 50:
        datos.fijar_umbral(valor)
    return RedirectResponse("/", status_code=303)


# ---------------------------------------------------------------------------
# Conteos: hoja PDF y ciclo quincenal con Excel
# ---------------------------------------------------------------------------

@app.post("/revisiones")
def revision_hecha(request: Request):
    """El botón "Revisión hecha" de la hoja semanal: constancia de fecha y
    empleada en el historial, sin números (la hoja es solo revisión)."""
    datos.crear_conteo("revision", "hecha", request.state.empleada["id"], {})
    return RedirectResponse("/", status_code=303)


@app.post("/conteos/pdf")
def generar_hoja(request: Request):
    try:
        inventario, _ = datos.obtener_inventario(refrescar=True)
    except datos.SinConexion:
        return RedirectResponse("/", status_code=303)
    n = datos.crear_conteo("hoja_pdf", "generado", request.state.empleada["id"],
                           {"productos": len(inventario)})
    archivo = f"hoja-conteo-{n}.pdf"
    conteos.generar_pdf(inventario, os.path.join(datos.ruta_archivos(), archivo))
    datos.fijar_archivo_conteo(n, archivo)
    return RedirectResponse(f"/conteos/{n}/pdf", status_code=303)


@app.get("/conteos/{n}/pdf")
def ver_hoja(n: int):
    conteo = datos.conteo(n)
    if conteo is None or not conteo["archivo"]:
        return RedirectResponse("/", status_code=303)
    return FileResponse(os.path.join(datos.ruta_archivos(), conteo["archivo"]),
                        media_type="application/pdf",
                        content_disposition_type="inline",
                        filename=conteo["archivo"])


@app.get("/plantilla.xlsx")
def plantilla(request: Request):
    try:
        inventario, _ = datos.obtener_inventario(refrescar=True)
    except datos.SinConexion:
        return RedirectResponse("/", status_code=303)
    return Response(
        conteos.plantilla_excel(inventario),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="conteo-quincenal.xlsx"'},
    )


@app.post("/conteos/importar")
async def importar(request: Request, archivo: UploadFile):
    contenido = await archivo.read()
    try:
        inventario, _ = datos.obtener_inventario(refrescar=True)
    except datos.SinConexion as error:
        return plantillas.TemplateResponse(request, "revisar.html", {
            "empleada": request.state.empleada, "conteo": None,
            "errores": [f"No se pudo leer el inventario: {error}"],
            "diferencias": [], "sin_contar": 0,
        })
    diferencias, sin_contar, errores = conteos.leer_conteo_excel(contenido, inventario)
    if errores:
        return plantillas.TemplateResponse(request, "revisar.html", {
            "empleada": request.state.empleada, "conteo": None,
            "errores": errores, "diferencias": [], "sin_contar": sin_contar,
        })
    detalle = {"diferencias": diferencias, "sin_contar": sin_contar,
               "contados": len(inventario) - sin_contar}
    if not diferencias:
        # Sin diferencias también es un conteo hecho: reinicia el reloj
        # quincenal del score.
        n = datos.crear_conteo("quincenal", "confirmado",
                               request.state.empleada["id"],
                               {**detalle, "resultados": []})
    else:
        n = datos.crear_conteo("quincenal", "pendiente",
                               request.state.empleada["id"], detalle)
    return RedirectResponse(f"/conteos/{n}/revisar", status_code=303)


@app.get("/conteos/{n}/revisar")
def revisar(request: Request, n: int):
    conteo = datos.conteo(n)
    if conteo is None or conteo["tipo"] != "quincenal":
        return RedirectResponse("/", status_code=303)
    return plantillas.TemplateResponse(request, "revisar.html", {
        "empleada": request.state.empleada, "conteo": conteo, "errores": [],
        "diferencias": conteo["datos"]["diferencias"],
        "sin_contar": conteo["datos"].get("sin_contar", 0),
    })


@app.post("/conteos/{n}/confirmar")
def confirmar(request: Request, n: int):
    conteo = datos.conteo(n)
    if conteo is None or conteo["estado"] != "pendiente":
        return RedirectResponse("/", status_code=303)
    ajustes = [
        {"sku": d["sku"], "cantidad": d["contado"], "esperada": d["en_sistema"]}
        for d in conteo["datos"]["diferencias"]
    ]
    try:
        respuesta = datos.ajustar_en_odoo(ajustes, request.state.empleada["id"],
                                          "conteo_quincenal")
    except datos.SinConexion as error:
        return plantillas.TemplateResponse(request, "revisar.html", {
            "empleada": request.state.empleada, "conteo": conteo,
            "errores": [f"No se pudo aplicar: {error}. El conteo sigue pendiente."],
            "diferencias": conteo["datos"]["diferencias"],
            "sin_contar": conteo["datos"].get("sin_contar", 0),
        })
    datos.actualizar_conteo(n, "confirmado", {
        **conteo["datos"], "resultados": respuesta["resultados"],
    })
    return RedirectResponse(f"/conteos/{n}/revisar", status_code=303)


@app.post("/conteos/{n}/descartar")
def descartar(request: Request, n: int):
    conteo = datos.conteo(n)
    if conteo is not None and conteo["estado"] == "pendiente":
        datos.actualizar_conteo(n, "descartado", conteo["datos"])
    return RedirectResponse("/", status_code=303)


# ---------------------------------------------------------------------------
# Crear Venta: ventas locales directas contra Odoo (app/ventas.py). Es una
# página propia (/venta) con la misma navegación inferior; nada del flujo
# Super Extra ni del stock-proxy/order-api se toca.
# ---------------------------------------------------------------------------

def _fecha_venta(iso):
    """2026-09-08T15:42:00-05:00 -> 08/09/2026 3:42 p.m."""
    momento = datetime.fromisoformat(iso)
    hora = momento.strftime("%-I:%M %p").lower().replace("am", "a.m.").replace("pm", "p.m.")
    return momento.strftime("%d/%m/%Y ") + hora


def _redirigir_venta(error=None):
    destino = "/venta" + (f"?error={quote(error)}" if error else "")
    return RedirectResponse(destino, status_code=303)


@app.get("/venta")
def venta(request: Request, q: str = "", error: str = ""):
    contexto = {
        "ventas_activo": ventas.configurado(), "q": q.strip(),
        "resultados": None, "carrito": [], "total_carrito": 0.0,
        "error_venta": error or None,
        "ventas": [{**v, "fecha_texto": _fecha_venta(v["creado_en"]),
                    "etiqueta_estado": ventas.ETIQUETAS_ESTADO[v["estado"]]}
                   for v in ventas.ventas_todas()],
    }
    if contexto["ventas_activo"]:
        try:
            if contexto["q"]:
                contexto["resultados"] = ventas.buscar_productos(contexto["q"])
            contexto["carrito"], contexto["total_carrito"] = \
                ventas.carrito_de(request.state.empleada["id"])
        except Exception:
            contexto["error_venta"] = ("Sin conexión con Odoo en este momento. "
                                       "El carrito y el historial local siguen aquí.")
    return plantillas.TemplateResponse(request, "venta.html", contexto)


@app.get("/venta/buscar")
def venta_buscar(request: Request, q: str = ""):
    # Alimenta el buscador en vivo (venta.js): mismo resultado que la
    # búsqueda server-rendered, en JSON y con el precio ya formateado.
    try:
        resultados = ventas.buscar_productos(q)
    except Exception:
        return {"error": "Sin conexión con Odoo en este momento."}
    return {"resultados": [{**p, "precio": dinero_venta(p["precio"])} for p in resultados]}


@app.post("/venta/carrito/agregar")
async def venta_agregar(request: Request):
    form = await request.form()
    try:
        ventas.agregar_al_carrito(request.state.empleada["id"],
                                  int(form.get("producto_id", "")),
                                  int(form.get("cantidad", 1)))
    except (TypeError, ValueError):
        pass
    # Conservar la búsqueda activa: así se pueden agregar varias plantas
    # seguidas sin volver a escribir.
    q = (form.get("q") or "").strip()
    return RedirectResponse("/venta" + (f"?q={quote(q)}" if q else ""), status_code=303)


@app.post("/venta/carrito/cantidad")
async def venta_cantidad(request: Request):
    form = await request.form()
    try:
        ventas.cambiar_cantidad(request.state.empleada["id"],
                                int(form.get("producto_id", "")),
                                int(form.get("cantidad", "")))
    except (TypeError, ValueError):
        pass
    return RedirectResponse("/venta", status_code=303)


@app.post("/venta/carrito/quitar")
async def venta_quitar(request: Request):
    form = await request.form()
    try:
        ventas.quitar_del_carrito(request.state.empleada["id"],
                                  int(form.get("producto_id", "")))
    except (TypeError, ValueError):
        pass
    return RedirectResponse("/venta", status_code=303)


@app.post("/venta/cotizar")
async def venta_cotizar(request: Request):
    form = await request.form()
    try:
        registro = ventas.crear_cotizacion(request.state.empleada, form.get("cliente", ""))
    except ValueError as error:
        return _redirigir_venta(str(error))
    except Exception as error:
        return _redirigir_venta(f"Odoo no aceptó la cotización: {ventas._mensaje_de_error(error)}")
    return plantillas.TemplateResponse(request, "venta_exito.html", {
        "titulo": "Cotización creada",
        "sub": f"{registro['orden']} · {registro['cliente']}",
        "filas": [("Total", dinero_venta(registro["total"]), None),
                  ("Estado", "Cotización (borrador en Odoo)", "dorado")],
        "pdf_href": f"/venta/{registro['n']}/cotizacion.pdf",
        "pdf_texto": "Descargar cotización (PDF)",
    })


@app.post("/venta/pagar")
async def venta_pagar(request: Request):
    # El botón grande "PAGADO Y CONFIRMAR PEDIDO": crea la orden desde el
    # carrito y pasa a elegir el método de pago (el cobro corre después).
    form = await request.form()
    try:
        registro = ventas.crear_cotizacion(request.state.empleada, form.get("cliente", ""))
    except ValueError as error:
        return _redirigir_venta(str(error))
    except Exception as error:
        return _redirigir_venta(f"Odoo no aceptó el pedido: {ventas._mensaje_de_error(error)}")
    return RedirectResponse(f"/venta/cobrar/{registro['n']}", status_code=303)


@app.get("/venta/cobrar/{n}")
def venta_cobrar(request: Request, n: int):
    registro = ventas.obtener_venta(n)
    if registro is None or registro["estado"] == "pagado":
        return RedirectResponse("/venta", status_code=303)
    return plantillas.TemplateResponse(request, "venta_cobrar.html", {
        "v": {**registro, "fecha_texto": _fecha_venta(registro["creado_en"]),
              "etiqueta_estado": ventas.ETIQUETAS_ESTADO[registro["estado"]]},
    })


@app.post("/venta/cobrar/{n}")
async def venta_cobrar_confirmar(request: Request, n: int):
    form = await request.form()
    metodo = form.get("metodo", "")
    if metodo not in ("yappy", "efectivo"):
        return RedirectResponse(f"/venta/cobrar/{n}", status_code=303)
    registro = ventas.cobrar(n, metodo)
    if registro is None:
        return RedirectResponse("/venta", status_code=303)
    if registro["estado"] != "pagado":
        # Quedó a medias: la pantalla de cobro muestra el estado real y el
        # error, y el mismo botón reintenta desde el paso que faltó.
        return RedirectResponse(f"/venta/cobrar/{n}", status_code=303)
    metodo_texto = "Yappy" if metodo == "yappy" else "Efectivo"
    return plantillas.TemplateResponse(request, "venta_exito.html", {
        "titulo": "Venta cobrada",
        "sub": f"{registro['orden']} · {registro['cliente']}",
        "filas": [("Factura", registro["factura"], None),
                  ("Total", dinero_venta(registro["total"]), "ok"),
                  ("Método", metodo_texto, None)],
        "pdf_href": f"/venta/{n}/factura.pdf",
        "pdf_texto": "Descargar factura (PDF)",
    })


def _respuesta_pdf(reporte, objetivo_id, nombre):
    # Un PDF que falla (ej. credenciales web sin configurar) no debe tirar un
    # error 500 pelado: se vuelve a /venta con el aviso en pantalla.
    try:
        contenido = ventas.descargar_pdf(reporte, objetivo_id)
    except Exception as error:
        return _redirigir_venta(f"No se pudo descargar el PDF: {ventas._mensaje_de_error(error)}")
    return Response(contenido, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{nombre}"'})


@app.get("/venta/{n}/cotizacion.pdf")
def venta_pdf_cotizacion(request: Request, n: int):
    registro = ventas.obtener_venta(n)
    if registro is None or not registro["orden_id"]:
        return RedirectResponse("/venta", status_code=303)
    return _respuesta_pdf("sale.report_saleorder", registro["orden_id"],
                          f"cotizacion-{registro['orden'].replace('/', '-')}.pdf")


@app.get("/venta/{n}/factura.pdf")
def venta_pdf_factura(request: Request, n: int):
    registro = ventas.obtener_venta(n)
    if registro is None or not registro["factura_id"]:
        return RedirectResponse("/venta", status_code=303)
    return _respuesta_pdf("account.report_invoice", registro["factura_id"],
                          f"factura-{(registro['factura'] or str(n)).replace('/', '-')}.pdf")


@app.get("/venta/foto/{producto_id}")
def venta_foto(request: Request, producto_id: int):
    foto = ventas.foto_producto(producto_id)
    if foto is None:
        return Response(status_code=404)
    contenido, tipo = foto
    return Response(contenido, media_type=tipo,
                    headers={"Cache-Control": "private, max-age=86400"})
