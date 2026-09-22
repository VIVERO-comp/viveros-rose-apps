"""Rutas de la app de control de stock (server-rendered con Jinja2).

Una sola pantalla con pestañas (Inicio, Stock y, para los editores, Fichas;
Inventario sigue vivo en /?tab=inv pero fuera del menú), como el prototipo
aprobado: el servidor arma los datos y las pestañas se mueven con el JS del
prototipo. Las acciones (ajustar stock, atender alertas, conteos, fichas)
son POSTs de vuelta a este mismo servidor; la app nunca toca Odoo directo.
"""

import json
import os
import re
import secrets
from datetime import datetime, timedelta
from urllib.parse import quote

from fastapi import FastAPI, Request, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import (acceso_google, calculos, calendario, calendario_google, retail,
               calendario_ics, compras, conteos, cotizaciones, crm_twenty,
               datos, fichas, fotos, proyectos, seguridad, ventas)

app = FastAPI(title="Control Viverorose")

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
# El navbar compartido pregunta por Proyectos en cada render: la pestaña
# aparece en TODOS los menús cuando la bandera está encendida (el dueño
# vio que al pasar a Stock "se escondía", 22/09/2026).
from . import proyectos as _proyectos_nav
plantillas.env.globals["proyectos_en_nav"] = _proyectos_nav.activos


def fecha_bonita(iso):
    """2026-09-02T10:05:00-05:00 -> 02/09/2026."""
    fecha = datetime.fromisoformat(iso)
    return fecha.strftime("%d/%m/%Y")


plantillas.env.filters["fecha_bonita"] = fecha_bonita


def dinero_venta(monto):
    """Mismo formato de moneda del resto de la app: $3.50."""
    return f"${monto:.2f}"


plantillas.env.filters["dinero"] = dinero_venta

# El Inicio pinta el calendario con el color y el nombre que decide
# app/calendario.py; la plantilla no conoce los tipos.
plantillas.env.globals["cal_color"] = calendario.color_de
plantillas.env.globals["cal_tipo"] = calendario.nombre_de_tipo
plantillas.env.filters["fecha_dmy"] = calendario.dmy

# Las tablas se crean al importar: es idempotente y así el proceso (o los
# tests) nunca corren contra una base sin esquema.
datos.iniciar_db()
ventas.iniciar_tablas()
cotizaciones.iniciar_tablas()
proyectos.iniciar_tablas()
retail.iniciar_tablas()
calendario_ics.iniciar_tablas()
calendario_google.iniciar_tablas()
calendario_google.arrancar_hilo()
# El calendario arranca calentándose en fondo (catálogo + mes en curso):
# ni la primera visita del día espera a Linear (velocidad, 22/09/2026).
calendario.calentar_en_fondo()


# ---------------------------------------------------------------------------
# Autenticación: toda la app exige sesión, salvo el login y los estáticos.
# ---------------------------------------------------------------------------

def _cookie_segura():
    return os.environ.get("COOKIE_SEGURA") == "1"


def _admins():
    """Emails (o usuarios) que ven la pestaña Ajustes e invitan gente
    (AJUSTES_ADMINS, separados por coma)."""
    return {a.strip().lower() for a in os.environ.get("AJUSTES_ADMINS", "").split(",")
            if a.strip()}


def _es_admin(empleada):
    admins = _admins()
    # El email cuenta solo VERIFICADO (confirmado entrando con Google): el
    # que la empleada anota a mano en Mi cuenta no da privilegios, si no
    # cualquiera se anotaría el email de una admin.
    return (empleada["id"].lower() in admins
            or ((empleada.get("email") or "").lower() in admins
                and bool(empleada.get("email_verificado"))))


def _redirect_uri(request):
    """El callback de Google. En producción PUBLIC_BASE_URL (detrás de nginx
    la URL que ve la app es la interna http); en desarrollo, la del request."""
    base = os.environ.get("PUBLIC_BASE_URL") or str(request.base_url)
    return base.rstrip("/") + "/auth/google/callback"


@app.middleware("http")
async def exigir_sesion(request: Request, call_next):
    ruta = request.url.path
    # /f/ es el enlace público de la factura (con token): lo abre el cliente
    # desde WhatsApp, sin sesión. /auth/google es el ida y vuelta del login
    # con Google e /invitacion/ el link compartible, ambos antes de que
    # exista la sesión.
    # /calendario.ics es la suscripción del teléfono: la autoriza su token
    # secreto (empleada_del_token), no la cookie de sesión.
    if (ruta == "/login" or ruta == "/calendario.ics" or ruta == "/crm/login"
            or ruta.startswith("/static") or ruta.startswith("/f/")
            or ruta.startswith("/auth/google") or ruta.startswith("/invitacion/")
            or ruta.startswith("/crm/auth/google")):
        respuesta = await call_next(request)
        # Los estáticos versionados (?v=mtime) se guardan un año: cambiar de
        # pestaña no vuelve a bajar CSS/JS (pedido de velocidad, 22/09/2026).
        # Sin ?v (el logo del favicon) una hora, por si algún día cambia.
        if ruta.startswith("/static"):
            if "v=" in (request.url.query or ""):
                respuesta.headers["Cache-Control"] = "public, max-age=31536000, immutable"
            else:
                respuesta.headers["Cache-Control"] = "public, max-age=3600"
        return respuesta
    # SIN_LOGIN=<usuario> (SOLO desarrollo local, pedido del dueño
    # 22/09/2026: "quita los logins en localhost"): la app entra sola como
    # ese usuario, sin pantalla de login. En el droplet la variable no
    # existe; si el usuario no está o está inactivo, manda el login normal.
    usuario_dev = os.environ.get("SIN_LOGIN", "").strip()
    if usuario_dev:
        empleada = seguridad.empleada_por_usuario(usuario_dev)
        if empleada is not None:
            request.state.empleada = empleada
            return await call_next(request)
    empleada = seguridad.empleada_de_sesion(request.cookies.get("sesion"))
    if empleada is None:
        # La cara del CRM (bajo crm.plantaspanama.com) tiene su propio login:
        # el de siempre vive en otro dominio y su cookie no sirve aquí.
        destino = "/crm/login" if ruta.startswith("/crm/") else "/login"
        return RedirectResponse(destino, status_code=303)
    request.state.empleada = empleada
    return await call_next(request)


def _enlaces_suscripcion(request):
    """Los enlaces de la tarjeta de sync de Ajustes, resueltos en el servidor."""
    enlace = (_base_publica(request) + "/calendario.ics?t="
              + calendario_ics.token_de(request.state.empleada["id"]))
    webcal = enlace.replace("https://", "webcal://", 1).replace("http://", "webcal://", 1)
    return {
        "suscripcion_calendario": enlace,
        "suscripcion_webcal": webcal,
        # El deep link de "agregar por URL" de Google Calendar.
        "suscripcion_google": ("https://calendar.google.com/calendar/render?cid="
                               + quote(webcal, safe="")),
    }


def _base_publica(request):
    """La URL que ve el mundo (detrás de nginx la del request es interna)."""
    return (os.environ.get("PUBLIC_BASE_URL") or str(request.base_url)).rstrip("/")


def _pagina_login(request, error=None, usuario="", status=200):
    # Si llegó por un link de invitación vigente, la pantalla lo saluda y
    # empuja al botón de Google (el link solo sirve para ese camino).
    invitacion = seguridad.invitacion_pendiente(request.cookies.get("invitacion"))
    respuesta = plantillas.TemplateResponse(request, "login.html", {
        "error": error, "usuario": usuario, "google": acceso_google.configurado(),
        "invitacion": invitacion,
    })
    respuesta.status_code = status
    return respuesta


def _abrir_sesion(empleada):
    respuesta = RedirectResponse("/", status_code=303)
    respuesta.set_cookie(
        "sesion", seguridad.crear_sesion(empleada["id"]),
        max_age=seguridad.DIAS_SESION * 24 * 3600,
        httponly=True, samesite="lax", secure=_cookie_segura(),
    )
    return respuesta


@app.get("/login")
def login(request: Request):
    # Con SIN_LOGIN activo (desarrollo local) ni el login se muestra:
    # directo a la app, que el middleware ya deja pasar.
    usuario_dev = os.environ.get("SIN_LOGIN", "").strip()
    if usuario_dev and seguridad.empleada_por_usuario(usuario_dev):
        return RedirectResponse("/", status_code=303)
    if seguridad.empleada_de_sesion(request.cookies.get("sesion")):
        return RedirectResponse("/", status_code=303)
    return _pagina_login(request)


@app.get("/invitacion/{token}")
def invitacion_abrir(request: Request, token: str):
    """El link compartible: deja el token en una cookie corta y manda al
    login, donde el botón de Google completa la entrada."""
    if seguridad.invitacion_pendiente(token) is None:
        return _pagina_login(request, "Ese link de invitación ya se usó o se "
                             "canceló. Pide uno nuevo al encargado.", status=410)
    respuesta = RedirectResponse("/login", status_code=303)
    respuesta.set_cookie("invitacion", token, max_age=2 * 3600,
                         httponly=True, samesite="lax", secure=_cookie_segura())
    return respuesta


@app.get("/auth/google")
def google_entrar(request: Request):
    """Manda a la pantalla de cuentas de Google, con un state anti-CSRF en
    cookie de corta vida."""
    if not acceso_google.configurado():
        return RedirectResponse("/login", status_code=303)
    estado = secrets.token_urlsafe(24)
    respuesta = RedirectResponse(
        acceso_google.url_entrada(_redirect_uri(request), estado), status_code=303)
    respuesta.set_cookie("oauth_estado", estado, max_age=600,
                         httponly=True, samesite="lax", secure=_cookie_segura())
    return respuesta


@app.get("/auth/google/callback")
def google_callback(request: Request, code: str = "", state: str = ""):
    if (not acceso_google.configurado() or not code or not state
            or state != request.cookies.get("oauth_estado")):
        return _pagina_login(request, "La entrada con Google no se pudo "
                             "completar. Prueba de nuevo.", status=400)
    try:
        cuenta = acceso_google.canjear_codigo(code, _redirect_uri(request))
    except acceso_google.FalloGoogle:
        return _pagina_login(request, "No se pudo verificar la cuenta con "
                             "Google. Prueba de nuevo.", status=502)
    empleada = seguridad.entrar_con_google(
        cuenta["email"], cuenta["nombre"], es_admin=cuenta["email"] in _admins(),
        token=request.cookies.get("invitacion"))
    if empleada is None:
        return _pagina_login(request, f"{cuenta['email']} no tiene invitación. "
                             "Pide una al encargado.", status=401)
    respuesta = _abrir_sesion(empleada)
    respuesta.delete_cookie("oauth_estado")
    respuesta.delete_cookie("invitacion")
    return respuesta


@app.post("/login")
async def entrar(request: Request):
    form = await request.form()
    usuario = (form.get("usuario") or "").strip().lower()
    empleada = seguridad.verificar(usuario, form.get("contrasena") or "")
    if empleada is None:
        return _pagina_login(request, "Usuario o contraseña incorrectos.",
                             usuario=usuario, status=401)
    return _abrir_sesion(empleada)


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
    # Sin pestaña pedida, la app ABRE en el Calendario (dueño, 22/09/2026:
    # "quita inicio y pon calendario de primero" y, al ver que la raíz
    # seguía mostrando el tablero, "todavía inicio está"). El tablero del
    # home queda solo como el lienzo de /?tab=stock y /?tab=ajustes.
    if "tab" not in request.query_params:
        return RedirectResponse("/calendario", status_code=303)
    umbral = datos.umbral()
    try:
        inventario, leido_en = datos.obtener_inventario(refrescar=bool(refrescar))
        sin_proxy = None
    except datos.SinConexion as error:
        inventario, leido_en, sin_proxy = [], None, str(error)
    datos.refrescar_alertas(inventario, umbral)
    # Qué plantas están HOY en plantaspanama.com: la pestaña "Stock online"
    # es un espejo del sitio (ver datos.obtener_publicados). Sin el dato no
    # se adivina: la pestaña lo avisa y muestra el global.
    publicados, sin_publicados = datos.obtener_publicados()

    cuentas = calculos.clasificar(inventario, umbral)
    ultimo = datos.ultimo_conteo_confirmado()
    dias_conteo = _dias_desde(ultimo["creado_en"]) if ultimo else None
    conteo_vencido = dias_conteo is None or dias_conteo > calculos.DIAS_CONTEO_QUINCENAL
    puntos = calculos.score(cuentas["criticos"], cuentas["bajos"], conteo_vencido)

    subidas = datos.fotos_subidas()

    def _fotos_de(p):
        # img: miniatura de Cloudinary (la subida desde la app gana); sin
        # ella, el respaldo /stock/foto sirve la de Odoo (si tampoco hay, el
        # 404 dispara el onerror y la tarjeta cae al emoji). imgG/imgD son
        # la versión grande y la de descarga del modal de foto: para el
        # respaldo de Odoo son la misma URL (es la única imagen que hay).
        info = fotos.info_foto(p["sku"], subidas.get(p["sku"]))
        if info:
            return {"img": info["img"], "imgG": info["grande"], "imgD": info["descarga"]}
        respaldo = f"/stock/foto/{quote(p['sku'])}" if ventas.configurado() else None
        return {"img": respaldo, "imgG": respaldo, "imgD": respaldo}

    plantas = [
        {
            "sku": p["sku"], "n": p["nombre"], "c": p["categoria"],
            "q": p["disponible"], "f": p["fisico"],
            "e": calculos.emoji_de(p["nombre"]),
            # Precio ya formateado en el servidor: el list_price de Odoo tal
            # cual, igual que en la tienda (null = precio pendiente en Odoo).
            "po": calculos.precio_online(p.get("precio_centavos", 0)),
            # Altura en cm desde Odoo (0 = sin dato): la ficha la muestra y
            # la deja editar; el sitio publico la toma al regenerar.
            "hmin": p.get("altura_min", 0), "hmax": p.get("altura_max", 0),
            # on: está publicada en la tienda. None = no se pudo saber.
            "on": (p["sku"] in publicados) if publicados is not None else None,
            # pub: la casilla "Publicada en la tienda" de Odoo, que es la
            # INTENCIÓN del dueño; `on` de arriba es lo que de verdad se ve
            # hoy en plantaspanama.com. Con pub=True y on=False la planta
            # está marcada para publicar pero todavía le falta entrar al
            # catálogo del sitio (foto incluida), y la ficha lo dice.
            "pub": p.get("publicado", True),
            **_fotos_de(p),
        }
        for p in inventario
    ]
    # Resumen del calendario para el Inicio. Si Linear falla, la pestaña
    # sigue mostrando el stock: el calendario simplemente no aparece.
    panel_cal, error_cal = None, None
    try:
        dia_cal = calendario.hoy().isoformat()
        desde_cal = (calendario.hoy() - timedelta(days=14)).isoformat()
        hasta_cal = (calendario.hoy() + timedelta(days=14)).isoformat()
        yo_cal = _yo_en_el_calendario(request.state.empleada)
        del_calendario = calendario.listar(desde_cal, hasta_cal)
        if yo_cal["id"] and not yo_cal["admin"]:
            del_calendario = [a for a in del_calendario if a["resp_id"] == yo_cal["id"]]
        panel_cal = calendario.panel_inicio(del_calendario, dia_cal)
    except calendario.ErrorCalendario as fallo:
        error_cal = str(fallo)

    alertas = datos.alertas_pendientes()
    # La vista de detalle muestra la ficha a todos; editarla sigue limitado
    # a FICHAS_EDITORES (y el POST /fichas lo verifica en el servidor).
    puede_fichas = fichas.es_editora(request.state.empleada["id"])
    # La pestaña Ajustes la ven todos (cada quien guarda su email en Mi
    # cuenta); las invitaciones y accesos, solo los admins (AJUSTES_ADMINS).
    es_admin = _es_admin(request.state.empleada)
    return plantillas.TemplateResponse(request, "app.html", {
        "empleada": request.state.empleada,
        "puede_fichas": puede_fichas,
        "es_admin": es_admin,
        "empleadas": seguridad.listar() if es_admin else [],
        "invitaciones": seguridad.invitaciones_pendientes() if es_admin else [],
        "aviso_ajustes": request.query_params.get("aviso"),
        "inv_nueva": request.query_params.get("inv") if es_admin else None,
        # Para armar los links /invitacion/{token} que se comparten.
        "base_publica": (os.environ.get("PUBLIC_BASE_URL")
                         or str(request.base_url)).rstrip("/"),
        "compras_activas": compras.activo(),
        "gcal_configurado": calendario_google.configurado(),
        "gcal_conexion": calendario_google.conexion_de(request.state.empleada["id"]),
        # La suscripción del calendario (feed ICS) en Ajustes (22/09/2026).
        # El dueño pidió botones directos, no un enlace para copiar: webcal://
        # abre el diálogo de suscribir en iPhone/Mac, y el cid= de Google abre
        # Google Calendar con el calendario listo para aceptar.
        **_enlaces_suscripcion(request),
        "puntos": puntos,
        # El anillo del score: circunferencia 402, se descubre según el score.
        "anillo": round(402 * (1 - puntos / 100)),
        "cuentas": cuentas,
        "dias_conteo": dias_conteo,
        "conteo_vencido": conteo_vencido,
        "categorias": _resumen_categorias(inventario, umbral),
        "umbral": umbral,
        "alertas": alertas,
        "cal": panel_cal,
        "cal_error": error_cal,
        "cal_modo": calendario.modo(),
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
            # Sin credenciales de Cloudinary el pincel del modal de foto no
            # se ofrece (el zoom y la descarga siguen funcionando).
            "puedeSubir": fotos.subida_configurada(),
            "puedeFichas": puede_fichas,
            "fichas": fichas.todas(),
            "referencias": fichas.referencias(),
            "sinPublicados": sin_publicados,
            "categoriasPlanta": datos.CATEGORIAS_PLANTA,
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


@app.post("/productos/nuevo")
async def crear_producto(request: Request):
    """Alta de una planta desde el formulario "Crear planta".

    Dos pasos, en este orden y nunca al revés: primero el order-api crea el
    producto en Odoo (POST /api/productos) y recién después, si el empleado
    puso una cantidad inicial, se aplica con el ajuste de siempre
    (esperada=0: la planta acaba de nacer sin existencias). Si el ajuste
    falla, la planta YA quedó creada y se dice así en pantalla, con el stock
    en cero para corregirlo a mano; crear dos veces la misma planta sería
    peor que dejarla en cero.

    La planta nace SOLO en Odoo: no entra a la tienda hasta que se regenere
    el catálogo del sitio, así que aparece en Stock global y no en online.
    """
    cuerpo = await request.json()
    nombre = (cuerpo.get("nombre") or "").strip()
    sku = (cuerpo.get("sku") or "").strip().upper() or datos.sku_sugerido(nombre)
    categoria = cuerpo.get("categoria")
    precio_centavos = cuerpo.get("precioCentavos")
    cantidad = cuerpo.get("cantidad", 0)
    altura_min = cuerpo.get("alturaMin", 0)
    altura_max = cuerpo.get("alturaMax", 0)
    sin_moto = bool(cuerpo.get("sinMoto", False))
    costo_centavos = cuerpo.get("costoCentavos", 0)
    # Notas internas de la ficha de Odoo: el bloque "nombre segundario /
    # nombre cientifico" que llevan las plantas del catálogo.
    nombre_secundario = (cuerpo.get("nombreSecundario") or "").strip()
    nombre_cientifico = (cuerpo.get("nombreCientifico") or "").strip()

    def error(mensaje, codigo="peticion_invalida", estado=400):
        return Response(json.dumps({"error": codigo, "mensaje": mensaje},
                                   ensure_ascii=False),
                        status_code=estado, media_type="application/json")

    if not nombre:
        return error("Escribe el nombre de la planta.")
    if not sku.startswith("PL-") or len(sku) < 4:
        return error("La referencia debe empezar por PL-.")
    if categoria not in datos.CATEGORIAS_PLANTA:
        return error("Elige la categoría de la planta.")
    if (not isinstance(precio_centavos, int) or precio_centavos < 0
            or not isinstance(costo_centavos, int) or costo_centavos < 0
            or not isinstance(cantidad, int) or cantidad < 0
            or not isinstance(altura_min, int) or not isinstance(altura_max, int)):
        return error("Revisa el precio, el costo, la cantidad y la altura.")

    try:
        creada = datos.crear_planta_en_odoo(
            sku, nombre, categoria, precio_centavos,
            altura_min, altura_max, sin_moto, costo_centavos,
            nombre_secundario, nombre_cientifico,
        )
    except datos.SinConexion as fallo:
        return error(str(fallo), "no_creada", 502)

    stock = "sin_stock"
    if cantidad > 0:
        try:
            respuesta = datos.ajustar_en_odoo(
                [{"sku": sku, "cantidad": cantidad, "esperada": 0}],
                request.state.empleada["id"], "alta_de_planta",
            )
            stock = respuesta["resultados"][0]["resultado"]
        except datos.SinConexion:
            stock = "falló"
    return {"ok": True, "sku": sku, "nombre": nombre, "id": creada.get("id"),
            "cantidad": cantidad, "stock": stock}


@app.post("/productos/{sku}/publicacion")
async def cambiar_publicacion(request: Request, sku: str):
    """El interruptor de la tienda desde la ficha de la planta.

    Marca o desmarca "Publicada en la tienda" en Odoo (por el order-api, que
    es el único camino de escritura de esta app). Desmarcarla saca la planta
    de plantaspanama.com en la siguiente reconstrucción del sitio y nada
    más: el producto, su stock, sus ventas locales, su ficha y TODAS sus
    fotos quedan intactos, así que volver a publicarla es un toque.
    """
    cuerpo = await request.json()
    publicado = cuerpo.get("publicado")
    if not isinstance(publicado, bool):
        return Response(json.dumps({"error": "peticion_invalida",
                                    "mensaje": "Falta si se publica o no."},
                                   ensure_ascii=False),
                        status_code=400, media_type="application/json")
    try:
        respuesta = datos.fijar_publicacion_en_odoo(sku, publicado)
    except datos.SinConexion as fallo:
        return Response(json.dumps({"error": "no_guardado", "mensaje": str(fallo)},
                                   ensure_ascii=False),
                        status_code=502, media_type="application/json")
    # El espejo del sitio (catalogo-publicado.json) recién cambia cuando el
    # frontend se reconstruye: se limpia para que la próxima carga lo relea
    # en vez de mostrar el de hace un rato.
    datos.reiniciar_cache_publicados()
    return {"ok": True, "sku": sku, "publicado": publicado,
            "resultado": respuesta.get("resultado", "aplicado")}


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
# Ajustes: invitaciones y accesos del login con Google (solo admins)
# ---------------------------------------------------------------------------

EMAIL_VALIDO = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _solo_admin(request):
    """403 si quien llama no es admin; None si puede seguir."""
    if not _es_admin(request.state.empleada):
        return Response("Solo para administradores.", status_code=403)
    return None


@app.post("/ajustes/mi-email")
async def ajustes_mi_email(request: Request):
    """Mi cuenta: cualquier empleada guarda (o quita) su email de Google.
    Queda sin verificar hasta que entre con Google con esa cuenta."""
    form = await request.form()
    email = (form.get("email") or "").strip().lower()
    if email and not EMAIL_VALIDO.match(email):
        return RedirectResponse("/?tab=ajustes&aviso=email", status_code=303)
    resultado = seguridad.fijar_email(request.state.empleada["id"], email)
    aviso = "email-ocupado" if resultado == "ocupado" else "email-guardado"
    return RedirectResponse(f"/?tab=ajustes&aviso={aviso}", status_code=303)


@app.post("/ajustes/invitar")
async def ajustes_invitar(request: Request):
    if (rechazo := _solo_admin(request)) is not None:
        return rechazo
    form = await request.form()
    # El email es opcional: sin él la invitación vive solo en su link.
    email = (form.get("email") or "").strip().lower()
    if email and not EMAIL_VALIDO.match(email):
        return RedirectResponse("/?tab=ajustes&aviso=email", status_code=303)
    resultado, token = seguridad.invitar(email, form.get("nombre") or "",
                                         request.state.empleada["id"])
    if resultado == "ya_activa":
        return RedirectResponse("/?tab=ajustes&aviso=ya-activa", status_code=303)
    # El aviso trae el token para mostrar el link listo para copiar.
    return RedirectResponse(f"/?tab=ajustes&aviso=invitada&inv={token}",
                            status_code=303)


@app.post("/ajustes/invitacion/cancelar")
async def ajustes_cancelar_invitacion(request: Request):
    if (rechazo := _solo_admin(request)) is not None:
        return rechazo
    form = await request.form()
    seguridad.cancelar_invitacion(form.get("token") or "")
    return RedirectResponse("/?tab=ajustes", status_code=303)


@app.post("/ajustes/revocar")
async def ajustes_revocar(request: Request):
    if (rechazo := _solo_admin(request)) is not None:
        return rechazo
    form = await request.form()
    usuario = (form.get("usuario") or "").strip()
    # Nadie se revoca a sí misma: siempre queda al menos una admin adentro.
    if not usuario or usuario == request.state.empleada["id"]:
        return RedirectResponse("/?tab=ajustes", status_code=303)
    seguridad.desactivar(usuario)
    return RedirectResponse("/?tab=ajustes", status_code=303)


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
    # Baja el archivo en vez de abrirlo en el visor: "inline" en el celular
    # dejaba al empleado en una pantalla de la que no se podía salir, y esta
    # hoja se hizo justamente para imprimirla (Abraham, 18/09/2026).
    return FileResponse(os.path.join(datos.ruta_archivos(), conteo["archivo"]),
                        media_type="application/pdf",
                        headers=cabeceras_descarga(conteo["archivo"]))


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


def _redirigir_venta(error=None, nueva=False):
    destino = ("/venta/nueva" if nueva else "/venta") + \
        (f"?error={quote(error)}" if error else "")
    return RedirectResponse(destino, status_code=303)


def _enlace_whatsapp(request, venta):
    """El enlace wa.me al celular del cliente con el mensaje y el enlace
    público (con token) de su factura (pagada) o cotización; None si la
    venta no lo permite."""
    if not venta.get("celular"):
        return None
    if venta["estado"] == "pagado" and venta.get("factura_id"):
        mensaje = (f"¡Gracias por su compra en Vivero Rose! 🌿 "
                   f"Aquí está su factura {venta['factura']}: {{enlace}}")
    elif venta["estado"] == "cotizacion":
        mensaje = (f"¡Gracias por su visita a Vivero Rose! 🌿 "
                   f"Aquí está su cotización {venta['orden']}: {{enlace}}")
    else:
        return None
    digitos = "".join(c for c in venta["celular"] if c.isdigit())
    if len(digitos) == 8:
        digitos = "507" + digitos
    base = os.environ.get("PUBLIC_BASE_URL") or str(request.base_url).rstrip("/")
    enlace = f"{base}/f/{ventas.token_de(venta['n'])}"
    return f"https://wa.me/{digitos}?text={quote(mensaje.format(enlace=enlace))}"


@app.get("/venta")
def venta(request: Request, error: str = "", lead: str = "", cliente: str = ""):
    # La pestaña: el botón grande "+ Nueva venta" y el historial local.
    usuario = request.state.empleada["id"]
    if lead:
        # "Cotizar en Vender" desde la ficha de Retail: queda anotado el
        # lead y la próxima cotización/venta de esta empleada nace
        # vinculada a él. Redirige a la URL limpia (recargar no lo repone).
        ventas.poner_lead_pendiente(usuario, lead, cliente)
        return RedirectResponse("/venta", status_code=303)
    en_curso = 0
    if ventas.configurado():
        try:
            en_curso = len(ventas.carrito_de(usuario)[0])
        except Exception:
            pass
    return plantillas.TemplateResponse(request, "venta.html", {
        "lead_pendiente": ventas.lead_pendiente(usuario),
        # El menu de abajo muestra Fichas con la misma regla del principal.
        "puede_fichas": fichas.es_editora(request.state.empleada["id"]),
        "ventas_activo": ventas.configurado(),
        "error_venta": error or None,
        "en_curso": en_curso,
        "tipos_servicio": [(t, cotizaciones.etiqueta_para_cotizar(t))
                          for t in cotizaciones.ORDEN_TIPOS],
        "ventas": [{**v, "fecha_texto": _fecha_venta(v["creado_en"]),
                    "etiqueta_estado": ventas.ETIQUETAS_ESTADO[v["estado"]],
                    "whatsapp": _enlace_whatsapp(request, v)}
                   for v in ventas.ventas_todas()],
        "cotizaciones_servicio": _cotizaciones_con_estado(),
    })


def _cotizaciones_con_estado():
    """Las cotizaciones locales con su estado REAL en Odoo (una sola
    consulta para todas): facturada, cancelada o todavía cotización, y de
    ahí si se puede editar. Si Odoo no contesta, la lista sale como
    siempre, sin botón Editar (mejor sin botón que un botón que rompe)."""
    filas = cotizaciones.cotizaciones_todas()
    estados = {}
    try:
        estados = cotizaciones.estados_en_odoo([c["orden_id"] for c in filas])
    except Exception:
        pass
    resultado = []
    for c in filas:
        estado = estados.get(c["orden_id"])
        resultado.append({
            **c, "fecha_texto": _fecha_venta(c["creado_en"]),
            "etiqueta_tipo": cotizaciones.etiqueta_de(c["tipo"]),
            "facturada": bool(estado and estado["facturada"]),
            "cancelada": bool(estado and estado["cancelada"]),
            "editable": bool(estado and estado["editable"]),
        })
    return resultado


@app.post("/venta/lead/quitar")
def venta_lead_quitar(request: Request):
    # "No es para este lead": la cotización que viene se crea suelta.
    ventas.quitar_lead_pendiente(request.state.empleada["id"])
    return RedirectResponse("/venta", status_code=303)


@app.post("/venta/cancelar/{n}")
def venta_cancelar(request: Request, n: int):
    try:
        ventas.cancelar(n)
    except Exception as error:
        return RedirectResponse(
            "/venta?error=" + quote(f"No se pudo cancelar: {error}"),
            status_code=303)
    return RedirectResponse("/venta", status_code=303)


@app.get("/venta/nueva")
def venta_nueva(request: Request, q: str = "", error: str = ""):
    # El formulario de la venta: cliente (nombre y celular), buscador en
    # vivo para añadir plantas, la lista con cantidades y el total.
    usuario = request.state.empleada["id"]
    contexto = {
        "ventas_activo": ventas.configurado(), "q": q.strip(),
        "resultados": None, "carrito": [], "total_carrito": 0.0,
        "borrador": ventas.borrador_de(usuario),
        "error_venta": error or None,
    }
    if contexto["ventas_activo"]:
        try:
            if contexto["q"]:
                contexto["resultados"] = ventas.buscar_productos(contexto["q"])
            contexto["carrito"], contexto["total_carrito"] = ventas.carrito_de(usuario)
        except Exception:
            contexto["error_venta"] = ("Sin conexión con Odoo en este momento. "
                                       "Vuelve a intentar en un rato.")
    return plantillas.TemplateResponse(request, "venta_nueva.html", contexto)


@app.post("/venta/borrador")
async def venta_borrador(request: Request):
    # venta.js guarda lo que se va escribiendo (cliente, sus datos
    # opcionales y los renglones de servicio o libres) para que sobreviva a
    # los reloads de agregar/quitar plantas.
    form = await request.form()
    ventas.guardar_borrador(request.state.empleada["id"],
                            (form.get("cliente") or "").strip()[:120],
                            (form.get("celular") or "").strip()[:30],
                            _servicios_del_form(form),
                            _datos_cliente_del_form(form),
                            _renglones_del_form(form))
    return Response(status_code=204)


@app.get("/venta/buscar")
def venta_buscar(request: Request, q: str = ""):
    # Alimenta el buscador en vivo (venta.js): mismo resultado que la
    # búsqueda server-rendered, en JSON y con el precio ya formateado.
    try:
        resultados = ventas.buscar_productos(q)
    except Exception:
        return {"error": "Sin conexión con Odoo en este momento."}
    return {"resultados": [{**p, "precio": dinero_venta(p["precio"])} for p in resultados]}


def _datos_cliente_del_form(form):
    """Los datos opcionales del cliente (empresa, RUC, cédula, correo,
    dirección) si el formulario los trae; None si no, para no borrar lo ya
    guardado en el borrador."""
    if not any(campo in form for campo in ventas.CAMPOS_CLIENTE):
        return None
    return {campo: (form.get(campo) or "").strip()[:120]
            for campo in ventas.CAMPOS_CLIENTE}


def _servicios_del_form(form):
    if "servicios" not in form and "servicio_texto" not in form:
        return None
    return cotizaciones.servicios_del_formulario(
        [t[:2000] for t in form.getlist("servicio_texto")],
        [m[:20] for m in form.getlist("servicio_monto")],
        [d[:2000] for d in form.getlist("servicio_descripcion")])


def _renglones_del_form(form):
    if "renglones" not in form and "renglon_texto" not in form:
        return None
    return cotizaciones.renglones_del_formulario(
        [t[:2000] for t in form.getlist("renglon_texto")],
        [c[:20] for c in form.getlist("renglon_cantidad")],
        [p[:20] for p in form.getlist("renglon_precio")],
        [d[:2000] for d in form.getlist("renglon_descripcion")])


def _volver_del_carrito(form):
    """El carrito (app/ventas.py) es el mismo para Nueva Venta y para los
    mini-formularios de cotización de servicio (una sola en curso por
    empleada, igual que hoy): cada pantalla manda de vuelta a sí misma con
    un campo oculto "volver", limitado a rutas propias de Vender.

    El proyecto de la cotización viaja en la URL de vuelta: sin él, agregar
    una planta al carrito sacaría a la cotización de su proyecto sin que
    nadie lo note. La búsqueda ya NO viaja (pedido del dueño, 22/09/2026):
    al elegir una planta el buscador queda limpio. Y la vuelta lleva el
    ancla #plantas, para quedar en la lista de plantas en vez de saltar al
    tope de la página."""
    destino = (form.get("volver") or "").strip()
    if destino != "/venta/servicio-personalizada" \
            and not destino.startswith("/venta/servicio/"):
        destino = "/venta/nueva"
    proyecto = (form.get("proyecto") or "").strip()
    if proyecto:
        destino += f"?proyecto={quote(proyecto)}"
    return destino + "#plantas"


@app.post("/venta/carrito/agregar")
async def venta_agregar(request: Request):
    form = await request.form()
    try:
        ventas.agregar_al_carrito(request.state.empleada["id"],
                                  int(form.get("producto_id", "")),
                                  int(form.get("cantidad", 1)))
    except (TypeError, ValueError):
        pass
    return RedirectResponse(_volver_del_carrito(form), status_code=303)


@app.post("/venta/carrito/cantidad")
async def venta_cantidad(request: Request):
    form = await request.form()
    try:
        ventas.cambiar_cantidad(request.state.empleada["id"],
                                int(form.get("producto_id", "")),
                                int(form.get("cantidad", "")))
    except (TypeError, ValueError):
        pass
    return RedirectResponse(_volver_del_carrito(form), status_code=303)


@app.post("/venta/carrito/quitar")
async def venta_quitar(request: Request):
    form = await request.form()
    try:
        ventas.quitar_del_carrito(request.state.empleada["id"],
                                  int(form.get("producto_id", "")))
    except (TypeError, ValueError):
        pass
    return RedirectResponse(_volver_del_carrito(form), status_code=303)


@app.post("/venta/cotizar")
async def venta_cotizar(request: Request):
    form = await request.form()
    try:
        registro = ventas.crear_cotizacion(
            request.state.empleada, form.get("cliente", ""), form.get("celular", ""),
            _datos_cliente_del_form(form))
    except ValueError as error:
        return _redirigir_venta(str(error), nueva=True)
    except Exception as error:
        return _redirigir_venta(f"Odoo no aceptó la cotización: {ventas._mensaje_de_error(error)}",
                                nueva=True)
    return plantillas.TemplateResponse(request, "venta_exito.html", {
        "titulo": "Cotización creada",
        "sub": f"{registro['orden']} · {registro['cliente']}",
        "filas": [("Total", dinero_venta(registro["total"]), None),
                  ("Estado", "Cotización (borrador en Odoo)", "dorado")],
        "pdf_href": f"/venta/{registro['n']}/cotizacion.pdf",
        "pdf_texto": "Descargar cotización (PDF)",
    })


# ---------------------------------------------------------------------------
# Cotizaciones de servicio (Alquiler, Boda, Mantenimiento…): botones por
# tipo junto a "+ NUEVA VENTA", cada uno con su mini-formulario. Reusan el
# mismo carrito de plantas de Nueva Venta (app/ventas.py) para los tipos
# que llevan catálogo — es el mismo carrito por empleada, así que solo debe
# haber un formulario en curso a la vez (igual que hoy con Nueva Venta).
# ---------------------------------------------------------------------------

def _contexto_servicio(request, tipo, q="", error=None, servicios=None,
                       proyecto=""):
    """El contexto del mini-formulario de un tipo. Lo comparten el GET y el
    POST que no pudo crear la cotización: así un error no borra los
    párrafos de servicio que la empleada ya escribió.

    El proyecto llega de dos formas: ya puesto en la URL (los botones de la
    ficha de un proyecto, `?proyecto=PROYECTO-01`), y entonces se muestra
    fijo; o a elegir de una lista, que solo ofrece "Cotizar Proyecto"
    (decisión del dueño 17/09/2026: en los demás tipos el proyecto solo
    entra si vienes desde su ficha)."""
    usuario = request.state.empleada["id"]
    borrador = ventas.borrador_de(usuario)
    if servicios is None:
        servicios = borrador["servicios"]
    contexto = {
        "ventas_activo": ventas.configurado(), "tipo": tipo,
        "meta": cotizaciones.TIPOS[tipo], "q": (q or "").strip(),
        "resultados": None, "carrito": [], "total_carrito": 0.0,
        "borrador": borrador, "servicios": servicios or [{"texto": "", "monto": "", "descripcion": ""}],
        "error_venta": error or None,
        "proyecto": (proyecto or "").strip(), "proyecto_nombre": None,
        # El selector está SIEMPRE en "Cotizar Proyecto", aunque todavía no
        # haya ningún proyecto en Odoo: si el campo desaparece cuando la
        # lista está vacía, parece que la pantalla no lo tuviera.
        "elegir_proyecto": tipo == "proyecto", "proyectos": [],
    }
    if contexto["ventas_activo"]:
        try:
            if contexto["q"]:
                contexto["resultados"] = ventas.buscar_productos(contexto["q"])
            contexto["carrito"], contexto["total_carrito"] = ventas.carrito_de(usuario)
            if contexto["proyecto"]:
                proyecto = proyectos.buscar(contexto["proyecto"])
                if proyecto:
                    contexto["proyecto_nombre"] = proyecto["name"]
                else:
                    contexto["proyecto"] = ""
                    contexto["error_venta"] = contexto["error_venta"] or (
                        "Ese proyecto ya no existe: la cotización va a quedar "
                        "suelta.")
            if tipo == "proyecto":
                # En "Cotizar Proyecto" el selector está siempre, aunque se
                # entre desde la ficha de un proyecto: así se puede cambiar
                # de proyecto sin volver atrás. En los demás tipos el
                # proyecto solo llega desde su ficha y se muestra fijo.
                contexto["proyectos"] = proyectos.para_elegir()
        except Exception:
            contexto["error_venta"] = ("Sin conexión con Odoo en este momento. "
                                       "Vuelve a intentar en un rato.")
    return contexto


@app.get("/venta/servicio/{tipo}")
def venta_servicio(request: Request, tipo: str, q: str = "", error: str = "",
                   proyecto: str = ""):
    if tipo not in cotizaciones.TIPOS:
        return RedirectResponse("/venta", status_code=303)
    return plantillas.TemplateResponse(
        request, "venta_servicio.html",
        _contexto_servicio(request, tipo, q, error, proyecto=proyecto))


@app.post("/venta/servicio/{tipo}")
async def venta_servicio_crear(request: Request, tipo: str):
    if tipo not in cotizaciones.TIPOS:
        return RedirectResponse("/venta", status_code=303)
    form = await request.form()
    usuario = request.state.empleada["id"]
    servicios = cotizaciones.servicios_del_formulario(
        form.getlist("servicio_texto"), form.getlist("servicio_monto"),
        form.getlist("servicio_descripcion"))
    datos_cliente = _datos_cliente_del_form(form)
    proyecto_ref = (form.get("proyecto") or "").strip()
    carrito, _total = ventas.carrito_de(usuario)
    lineas_catalogo = [{"producto_id": l["producto_id"], "cantidad": l["cantidad"]}
                       for l in carrito]
    try:
        registro = cotizaciones.crear_cotizacion(
            request.state.empleada, tipo, form.get("cliente", ""),
            form.get("celular", ""), servicios, lineas_catalogo, datos_cliente,
            proyecto_ref)
    except ValueError as error:
        return plantillas.TemplateResponse(
            request, "venta_servicio.html",
            _contexto_servicio(request, tipo, error=str(error), servicios=servicios,
                               proyecto=proyecto_ref),
            status_code=200)
    except Exception as error:
        return plantillas.TemplateResponse(
            request, "venta_servicio.html",
            _contexto_servicio(
                request, tipo,
                error=f"Odoo no aceptó la cotización: {ventas._mensaje_de_error(error)}",
                servicios=servicios, proyecto=proyecto_ref),
            status_code=200)
    ventas.vaciar_carrito(usuario)
    ventas._limpiar_borrador(usuario)
    filas = [("Tipo", cotizaciones.etiqueta_de(tipo), None),
             ("Total", dinero_venta(registro["total"]), None),
             ("Estado", "Cotización (borrador en Odoo)", "dorado")]
    if proyecto_ref:
        # Que se vea que quedó dentro del proyecto y no suelta.
        filas.insert(1, ("Proyecto", proyecto_ref, None))
    return plantillas.TemplateResponse(request, "venta_exito.html", {
        "titulo": "Cotización de servicio creada",
        "sub": f"{registro['orden']} · {registro['cliente']}",
        "filas": filas,
        "pdf_href": f"/venta/servicio/{registro['n']}/propuesta.pdf",
        "pdf_texto": "Descargar propuesta (PDF)",
    })


def _contexto_personalizada(request, q="", error=None, renglones=None,
                            servicios=None):
    """El contexto de la cotización personalizada, compartido por el GET y
    el POST que no pudo crearla: así un error no borra los renglones que la
    empleada ya escribió."""
    usuario = request.state.empleada["id"]
    borrador = ventas.borrador_de(usuario)
    if renglones is None:
        renglones = borrador["renglones"]
    if servicios is None:
        servicios = borrador["servicios"]
    contexto = {
        "ventas_activo": ventas.configurado(), "q": (q or "").strip(),
        "resultados": None, "carrito": [], "total_carrito": 0.0,
        "borrador": borrador, "error_venta": error or None,
        "renglones": renglones or [{"texto": "", "cantidad": "", "precio": "", "descripcion": ""}],
        "servicios": servicios or [{"texto": "", "monto": "", "descripcion": ""}],
    }
    if contexto["ventas_activo"]:
        try:
            if contexto["q"]:
                contexto["resultados"] = ventas.buscar_productos(contexto["q"])
            contexto["carrito"], contexto["total_carrito"] = ventas.carrito_de(usuario)
        except Exception:
            contexto["error_venta"] = ("Sin conexión con Odoo en este momento. "
                                       "Vuelve a intentar en un rato.")
    return contexto


@app.get("/venta/servicio-personalizada")
def venta_personalizada_form(request: Request, q: str = "", error: str = ""):
    return plantillas.TemplateResponse(
        request, "venta_personalizada.html",
        _contexto_personalizada(request, q, error))


@app.post("/venta/servicio-personalizada")
async def venta_personalizada_crear(request: Request):
    form = await request.form()
    usuario = request.state.empleada["id"]
    renglones = cotizaciones.renglones_del_formulario(
        form.getlist("renglon_texto"), form.getlist("renglon_cantidad"),
        form.getlist("renglon_precio"), form.getlist("renglon_descripcion"))
    servicios = cotizaciones.servicios_del_formulario(
        form.getlist("servicio_texto"), form.getlist("servicio_monto"),
        form.getlist("servicio_descripcion"))
    carrito, _total = ventas.carrito_de(usuario)
    lineas_catalogo = [{"producto_id": l["producto_id"], "cantidad": l["cantidad"]}
                       for l in carrito]
    try:
        registro = cotizaciones.crear_personalizada(
            request.state.empleada, form.get("cliente", ""),
            form.get("celular", ""), lineas_catalogo, renglones,
            _datos_cliente_del_form(form), servicios)
    except ValueError as error:
        return plantillas.TemplateResponse(
            request, "venta_personalizada.html",
            _contexto_personalizada(request, error=str(error), renglones=renglones,
                                    servicios=servicios))
    except Exception as error:
        return plantillas.TemplateResponse(
            request, "venta_personalizada.html",
            _contexto_personalizada(
                request,
                error=f"Odoo no aceptó la cotización: {ventas._mensaje_de_error(error)}",
                renglones=renglones, servicios=servicios))
    ventas.vaciar_carrito(usuario)
    ventas._limpiar_borrador(usuario)
    return plantillas.TemplateResponse(request, "venta_exito.html", {
        "titulo": "Cotización creada",
        "sub": f"{registro['orden']} · {registro['cliente']}",
        "filas": [("Tipo", "Personalizado", None),
                  ("Total", dinero_venta(registro["total"]), None),
                  ("Estado", "Cotización (borrador en Odoo)", "dorado")],
        "pdf_href": f"/venta/servicio/{registro['n']}/propuesta.pdf",
        "pdf_texto": "Descargar propuesta (PDF)",
    })


@app.get("/venta/propuesta-de-muestra.pdf")
def venta_propuesta_muestra(request: Request):
    # El botón "Ver PDF de ejemplo" de Vender: la propuesta de una
    # cotización de muestra, que se borra de Odoo en el mismo paso.
    try:
        contenido = cotizaciones.pdf_de_muestra()
    except Exception as error:
        return _redirigir_venta(
            f"No se pudo armar el PDF de ejemplo: {ventas._mensaje_de_error(error)}")
    return Response(contenido, media_type="application/pdf",
                    headers=cabeceras_descarga("propuesta-de-ejemplo.pdf"))


@app.get("/venta/servicio/{n}/editar")
def venta_servicio_editar(request: Request, n: int):
    """Editar una cotización que sigue en cotización: los servicios
    (título, descripción, monto) y las cantidades de sus plantas (0 la
    quita). Facturada o cancelada, ni se abre (Abraham, 22/09/2026)."""
    try:
        datos_edicion = cotizaciones.cargar_para_editar(n)
    except Exception as error:
        return _redirigir_venta(
            f"No se pudo abrir la cotización: {ventas._mensaje_de_error(error)}")
    if datos_edicion is None:
        return _redirigir_venta("Esa cotización ya no está en Odoo.")
    if not datos_edicion["editable"]:
        motivo = ("ya está facturada" if datos_edicion["facturada"]
                  else "está cancelada")
        return _redirigir_venta(f"Esta cotización {motivo}: ya no se puede editar.")
    return plantillas.TemplateResponse(request, "venta_servicio_editar.html",
                                       _contexto_editar(request, datos_edicion))


def _contexto_editar(request, datos_edicion, error=None):
    registro = datos_edicion["registro"]
    return {
        "puede_fichas": fichas.es_editora(request.state.empleada["id"]),
        "registro": registro,
        "es_personalizada": registro["tipo"] not in cotizaciones.TIPOS,
        "etiqueta_tipo": cotizaciones.etiqueta_de(registro["tipo"]),
        "servicios": datos_edicion["servicios"],
        "plantas": datos_edicion["plantas"],
        "renglones": datos_edicion["renglones"],
        "error_venta": error or None,
    }


@app.post("/venta/servicio/{n}/editar")
async def venta_servicio_editar_guardar(request: Request, n: int):
    form = await request.form()
    servicios = cotizaciones.servicios_del_formulario(
        [t[:2000] for t in form.getlist("servicio_texto")],
        [m[:20] for m in form.getlist("servicio_monto")],
        [d[:2000] for d in form.getlist("servicio_descripcion")])
    plantas = cotizaciones.plantas_del_formulario(
        form.getlist("planta_id"), form.getlist("planta_cantidad"))
    renglones = cotizaciones.renglones_del_formulario(
        [t[:2000] for t in form.getlist("renglon_texto")],
        [c[:20] for c in form.getlist("renglon_cantidad")],
        [p[:20] for p in form.getlist("renglon_precio")],
        [d[:2000] for d in form.getlist("renglon_descripcion")])
    try:
        cotizaciones.editar_cotizacion(n, servicios, plantas, renglones)
    except ValueError as error:
        # El formulario vuelve con lo escrito, como al crear: un redirect
        # perdería lo que la empleada ya corrigió.
        datos_edicion = cotizaciones.cargar_para_editar(n)
        if datos_edicion is None or not datos_edicion["editable"]:
            return _redirigir_venta(str(error))
        datos_edicion["servicios"] = servicios or datos_edicion["servicios"]
        nombres = {p["producto_id"]: p["nombre"] for p in datos_edicion["plantas"]}
        datos_edicion["plantas"] = [
            {**p, "nombre": nombres.get(_entero_o_none(p["producto_id"]), "")}
            for p in plantas] or datos_edicion["plantas"]
        datos_edicion["renglones"] = renglones or datos_edicion["renglones"]
        return plantillas.TemplateResponse(
            request, "venta_servicio_editar.html",
            _contexto_editar(request, datos_edicion, error=str(error)),
            status_code=200)
    except Exception as error:
        return _redirigir_venta(
            f"No se pudo guardar: {ventas._mensaje_de_error(error)}")
    # De vuelta a la lista, ANCLADO en la tarjeta que se editó: guardar no
    # debe mandar a la empleada al tope de la lista.
    return RedirectResponse(f"/venta#cot-{n}", status_code=303)


def _entero_o_none(valor):
    try:
        return int(valor)
    except (TypeError, ValueError):
        return None


@app.get("/venta/servicio/{n}/propuesta.pdf")
def venta_servicio_pdf(request: Request, n: int):
    registro = cotizaciones.obtener(n)
    if registro is None:
        return RedirectResponse("/venta", status_code=303)
    return _respuesta_pdf("vivero_rose_pedidos.reporte_propuesta_venta",
                          registro["orden_id"],
                          f"propuesta-{registro['orden'].replace('/', '-')}.pdf")


@app.post("/venta/pagar")
async def venta_pagar(request: Request):
    # El botón grande "PAGADO Y CONFIRMAR PEDIDO": crea la orden desde el
    # carrito y pasa a elegir el método de pago (el cobro corre después).
    form = await request.form()
    try:
        registro = ventas.crear_cotizacion(
            request.state.empleada, form.get("cliente", ""), form.get("celular", ""),
            _datos_cliente_del_form(form))
    except ValueError as error:
        return _redirigir_venta(str(error), nueva=True)
    except Exception as error:
        return _redirigir_venta(f"Odoo no aceptó el pedido: {ventas._mensaje_de_error(error)}",
                                nueva=True)
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


def cabeceras_descarga(nombre):
    """Las cabeceras que hacen que un PDF se BAJE al teléfono, en vez de
    abrirse en el visor del navegador.

    En el celular un PDF que el navegador decide previsualizar se traga la
    pantalla: el visor tapa la app, no siempre trae botón de guardar y no hay
    cómo volver (Abraham, 18/09/2026: "me lleva a una pantalla y no puedo
    hacer nada"). Tres cosas lo evitan:

      - `attachment`, que pide descarga y no vista previa;
      - el nombre también en `filename*` (RFC 5987): los nombres llevan
        acentos y tildes, y sin esta forma algunos Android guardan el archivo
        con el nombre roto o directamente lo abren en vez de bajarlo;
      - `X-Content-Type-Options: nosniff`, para que el navegador no se salte
        lo anterior por olfatear el contenido.

    Los enlaces además abren en otra pestaña (target="_blank" en las
    plantillas): así la pantalla de la app queda viva detrás, pase lo que
    pase con la descarga.
    """
    # El repuesto ASCII: si al quitar los acentos no queda nombre de verdad
    # (solo la extensión, o nada), se usa uno genérico en vez de mandar un
    # `filename=".pdf"` que el teléfono guarda como archivo sin nombre.
    seguro = nombre.encode("ascii", "ignore").decode().strip()
    raiz = seguro[:-4] if seguro.lower().endswith(".pdf") else seguro
    if not re.sub(r"\W", "", raiz):
        seguro = "documento.pdf"
    return {
        "Content-Disposition": (
            f'attachment; filename="{seguro}"; '
            f"filename*=UTF-8''{quote(nombre)}"),
        "X-Content-Type-Options": "nosniff",
    }


def _respuesta_pdf(reporte, objetivo_id, nombre):
    # Un PDF que falla (ej. credenciales web sin configurar) no debe tirar un
    # error 500 pelado: se vuelve a /venta con el aviso en pantalla.
    try:
        contenido = ventas.descargar_pdf(reporte, objetivo_id)
    except Exception as error:
        return _redirigir_venta(f"No se pudo descargar el PDF: {ventas._mensaje_de_error(error)}")
    return Response(contenido, media_type="application/pdf",
                    headers=cabeceras_descarga(nombre))


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


MESES = ("enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
         "agosto", "septiembre", "octubre", "noviembre", "diciembre")


@app.get("/f/{token}")
def factura_publica(request: Request, token: str):
    # El documento que el cliente abre desde WhatsApp: la factura si la
    # venta está pagada, la cotización si aún no. Público, solo con el
    # token; no expone nada más de la app.
    registro = ventas.venta_por_token(token)
    if registro is None:
        return Response("Documento no disponible.", status_code=404)
    es_factura = registro["estado"] == "pagado" and registro["factura_id"]
    if not es_factura and registro["estado"] != "cotizacion":
        return Response("Documento no disponible.", status_code=404)
    try:
        lineas = (ventas.lineas_de_factura(registro) if es_factura
                  else ventas.lineas_de_cotizacion(registro))
    except Exception:
        lineas = []
    fecha = datetime.fromisoformat(registro["creado_en"])
    return plantillas.TemplateResponse(request, "factura_publica.html", {
        "v": registro,
        "es_factura": bool(es_factura),
        "lineas": lineas,
        "fecha_larga": f"{fecha.day} de {MESES[fecha.month - 1]} de {fecha.year}",
        "fecha_corta": fecha.strftime("%d/%m/%Y"),
        "metodo": "Yappy" if registro["metodo"] == "yappy" else "Efectivo",
    })


@app.get("/stock/foto/{sku}")
def stock_foto(request: Request, sku: str):
    """Respaldo de la pantalla de Stock: la foto de Odoo para los SKUs que
    no tienen foto en Cloudinary."""
    foto = ventas.foto_por_sku(sku)
    if foto is None:
        return Response(status_code=404)
    contenido, tipo = foto
    return Response(contenido, media_type=tipo,
                    headers={"Cache-Control": "private, max-age=86400"})


@app.post("/fotos/{sku}")
async def cambiar_foto(request: Request, sku: str, archivo: UploadFile):
    """El pincel del modal de foto: sube la imagen a Cloudinary bajo
    apps/{sku}/ (solo apps internas, la tienda no cambia) y deja el puntero
    en la base. La foto anterior no se borra de Cloudinary."""
    def error(codigo, clave, mensaje):
        return Response(json.dumps({"error": clave, "mensaje": mensaje}),
                        status_code=codigo, media_type="application/json")

    if not fotos.subida_configurada():
        return error(503, "sin_configurar",
                     "La subida de fotos no está configurada en este servidor.")
    # El sku viaja en el public_id de Cloudinary: solo el alfabeto de los
    # SKUs reales (PL-..., letras, dígitos y guiones).
    if not re.fullmatch(r"[A-Za-z0-9-]{1,80}", sku):
        return error(400, "sku_invalido", "SKU inválido.")
    if not (archivo.content_type or "").startswith("image/"):
        return error(400, "no_es_imagen", "El archivo no es una imagen.")
    contenido = await archivo.read()
    if not contenido:
        return error(400, "vacio", "El archivo llegó vacío.")
    if len(contenido) > 15 * 1024 * 1024:
        return error(400, "muy_grande", "La foto pesa más de 15 MB.")
    try:
        hash_foto = fotos.subir_foto(contenido, sku)
    except RuntimeError as errores:
        return error(502, "cloudinary", str(errores))
    datos.fijar_foto_subida(sku, hash_foto, request.state.empleada["id"])
    info = fotos.info_foto(sku, hash_foto)
    return {"resultado": "aplicada", **info}


@app.post("/fichas/{sku}")
async def guardar_ficha(request: Request, sku: str):
    """Guardar de la ficha: descripción, guía de cuidado y altura.

    La prosa va a la tabla fichas_producto de la base tienda; la ALTURA va a
    Odoo por el order-api, porque es un dato del producto y Odoo es su fuente
    de verdad. Se escribe primero la altura: si Odoo falla, no se guarda nada
    y quien edita ve el error. Ni una ni otra cambian el sitio al instante
    (el sitio las toma cuando se regenera el catálogo)."""
    def error(codigo, clave, mensaje):
        return Response(json.dumps({"error": clave, "mensaje": mensaje}),
                        status_code=codigo, media_type="application/json")

    if not fichas.es_editora(request.state.empleada["id"]):
        return error(403, "sin_permiso", "Tu usuario no puede editar fichas.")
    if not re.fullmatch(r"[A-Za-z0-9-]{1,80}", sku):
        return error(400, "sku_invalido", "SKU inválido.")
    crudo = await request.json()
    campos = fichas.limpiar(crudo)
    mensaje = fichas.validar(campos)
    if mensaje:
        return error(400, "ficha_invalida", mensaje)
    altura = fichas.limpiar_altura(crudo)
    mensaje = fichas.validar_altura(altura)
    if mensaje:
        return error(400, "altura_invalida", mensaje)
    # Primero Odoo (lo que puede fallar por red o por permisos); recién
    # después la prosa, para no dejar la ficha guardada a medias.
    try:
        datos.fijar_altura_en_odoo(sku, altura["altura_min"], altura["altura_max"])
    except datos.SinConexion as fallo:
        return error(502, "sin_guardar", str(fallo))
    except Exception as excepcion:
        print(f"fichas: error guardando la altura de {sku}: {excepcion!r}", flush=True)
        return error(502, "sin_guardar",
                     "No se pudo guardar la altura en Odoo. Intenta de nuevo.")
    try:
        fichas.guardar(sku, campos, request.state.empleada["id"])
    except Exception as excepcion:
        # A la pantalla va un mensaje simple; el detalle queda en el log.
        print(f"fichas: error guardando {sku}: {excepcion!r}", flush=True)
        return error(502, "sin_guardar",
                      "No se pudo guardar en la base. Intenta de nuevo.")
    return {"resultado": "guardada", "ficha": fichas.todas().get(sku),
            "altura_min": altura["altura_min"], "altura_max": altura["altura_max"]}


@app.get("/venta/foto/{producto_id}")
def venta_foto(request: Request, producto_id: int):
    foto = ventas.foto_producto(producto_id)
    if foto is None:
        return Response(status_code=404)
    contenido, tipo = foto
    return Response(contenido, media_type=tipo,
                    headers={"Cache-Control": "private, max-age=86400"})


# ---------------------------------------------------------------------------
# Proyectos: el contenedor de las varias cotizaciones de un mismo cliente.
# El tablero calca las cuatro columnas del Flujo del CRM de Odoo, porque el
# proyecto ES la oportunidad de ese Flujo (ver app/proyectos.py).
# ---------------------------------------------------------------------------

def _tipos_de_proyecto():
    """Los tipos tal cual, para elegir DE QUÉ es el proyecto al crearlo."""
    return [(t, cotizaciones.TIPOS[t]["etiqueta"]) for t in cotizaciones.ORDEN_TIPOS]


def _tipos_para_cotizar():
    """Los mismos tipos, pero con el nombre de los botones que abren una
    cotización (dentro de la ficha del proyecto)."""
    return [(t, cotizaciones.etiqueta_para_cotizar(t))
            for t in cotizaciones.ORDEN_TIPOS]


@app.get("/proyecto")
def proyecto_tablero(request: Request, error: str = ""):
    if not proyectos.activos():
        return RedirectResponse("/", status_code=303)
    columnas = []
    if ventas.configurado():
        try:
            columnas = proyectos.kanban()
        except Exception as excepcion:
            print(f"proyectos: error armando el tablero: {excepcion!r}", flush=True)
            error = error or "No se pudo leer los proyectos desde Odoo."
    return plantillas.TemplateResponse(request, "proyectos.html", {
        "ventas_activo": ventas.configurado(),
        "columnas": columnas,
        "error": error or None,
    })


@app.get("/proyecto/nuevo")
def proyecto_nuevo(request: Request, error: str = "", nombre: str = "",
                   celular: str = "", nota: str = "", nombre_proyecto: str = ""):
    if not proyectos.activos():
        return RedirectResponse("/", status_code=303)
    return plantillas.TemplateResponse(request, "proyecto_nuevo.html", {
        "tipos": _tipos_de_proyecto(),
        "error": error or None,
        "nombre": nombre, "celular": celular, "nota": nota,
        "nombre_proyecto": nombre_proyecto,
    })


@app.post("/proyecto/nuevo")
async def proyecto_crear(request: Request):
    if not proyectos.activos():
        return RedirectResponse("/", status_code=303)
    form = await request.form()
    nombre = form.get("nombre", "")
    celular = form.get("celular", "")
    nota = form.get("nota", "")
    nombre_proyecto = form.get("nombre_proyecto", "")
    try:
        ref = proyectos.crear(request.state.empleada, nombre, celular,
                              form.get("tipo", ""), nota, nombre_proyecto)
    except ValueError as excepcion:
        # Los datos vuelven a la pantalla para no hacerla escribir de nuevo.
        return RedirectResponse(
            "/proyecto/nuevo?error=" + quote(str(excepcion))
            + f"&nombre={quote(nombre)}&celular={quote(celular)}&nota={quote(nota)}"
            + f"&nombre_proyecto={quote(nombre_proyecto)}",
            status_code=303)
    except Exception as excepcion:
        print(f"proyectos: error creando el proyecto: {excepcion!r}", flush=True)
        return RedirectResponse(
            "/proyecto/nuevo?error="
            + quote("No se pudo crear el proyecto en Odoo. Intenta de nuevo."),
            status_code=303)
    return RedirectResponse(f"/proyecto/{ref}", status_code=303)


@app.get("/proyecto/{ref}")
def proyecto_ficha(request: Request, ref: str, error: str = ""):
    if not proyectos.activos():
        return RedirectResponse("/", status_code=303)
    ficha = proyectos.detalle(ref)
    if not ficha:
        return RedirectResponse(
            "/proyecto?error=" + quote(f"No existe el proyecto {ref}."),
            status_code=303)
    return plantillas.TemplateResponse(request, "proyecto_detalle.html", {
        "p": ficha, "tipos": _tipos_para_cotizar(), "error": error or None,
    })


@app.post("/proyecto/{ref}/compra")
async def proyecto_compra(request: Request, ref: str):
    if not proyectos.activos():
        return RedirectResponse("/", status_code=303)
    form = await request.form()
    try:
        proyectos.agregar_compra(request.state.empleada, ref,
                                 form.get("concepto", ""), form.get("monto", ""),
                                 form.get("fecha", ""), form.get("nota", ""))
    except ValueError as excepcion:
        return RedirectResponse(f"/proyecto/{ref}?error=" + quote(str(excepcion)),
                                status_code=303)
    return RedirectResponse(f"/proyecto/{ref}", status_code=303)


@app.post("/proyecto/{ref}/compra/{n}/quitar")
def proyecto_compra_quitar(request: Request, ref: str, n: int):
    if not proyectos.activos():
        return RedirectResponse("/", status_code=303)
    proyectos.quitar_compra(ref, n)
    return RedirectResponse(f"/proyecto/{ref}", status_code=303)


# ---------------------------------------------------------------------------
# Calendario del equipo (espejo del proyecto CALENDARIO ROSE de Linear).
#
# Pantalla server-rendered como el resto de la app: Python arma la vista
# completa (qué días, qué bloques, en qué posición) y Jinja2 la pinta. La
# navegación son enlaces y las acciones, formularios POST que vuelven al
# mismo día y la misma vista, para no perder el lugar.
# ---------------------------------------------------------------------------

def _yo_en_el_calendario(empleada):
    """Quién es la persona de la sesión dentro del calendario.

    Se amarra por el email verificado de Google con el usuario de Linear: el
    empleado entra viendo lo suyo. Los admins de la app (AJUSTES_ADMINS) ven
    y tocan todo el equipo; el resto solo escribe sobre sus actividades, y
    eso se verifica AQUÍ, en el servidor, no en el navegador.
    """
    email = (empleada.get("email") or "").lower() if empleada.get("email_verificado") else ""
    yo_id, yo_nombre = "", empleada.get("nombre") or empleada["id"]
    if email:
        for persona in calendario.responsables():
            if persona.get("email") == email:
                yo_id, yo_nombre = persona["id"], persona["nombre"]
                break
    return {"id": yo_id, "nombre": yo_nombre, "admin": _es_admin(empleada)}


def _estado_calendario(request, empleada):
    """Lo que la barra de arriba dice que hay que mostrar."""
    q = request.query_params
    dia = q.get("dia", "")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", dia):
        dia = calendario.hoy().isoformat()
    vista = q.get("vista", "semana")
    if vista not in calendario.VISTAS:
        vista = "semana"
    yo = _yo_en_el_calendario(empleada)
    # Un empleado entra viendo lo suyo; el dueño, todo el equipo.
    mio = q.get("mio")
    solo_mio = (mio == "1") if mio in ("0", "1") else not yo["admin"]
    # Sin usuario de Linear enlazado no hay "lo mío" que filtrar: se cae a
    # ver el equipo (si no, la pantalla quedaba vacía y los chips en 0).
    solo_mio = solo_mio and bool(yo["id"])
    apagados = {t for t in q.get("apagados", "").split(",") if t in calendario.POR_CLAVE}
    return {
        "dia": dia, "vista": vista, "yo": yo, "solo_mio": solo_mio,
        "apagados": apagados, "quien": q.get("quien", ""), "q": q.get("q", ""),
        "hechas": q.get("hechas", "1") != "0",
        "mes": q.get("mes", "") if re.fullmatch(r"\d{4}-\d{2}-\d{2}", q.get("mes", "")) else dia,
    }


def _liga(estado, **cambios):
    """Arma el enlace de la pantalla conservando los filtros de ahora."""
    datos = {
        "dia": estado["dia"], "vista": estado["vista"],
        "mio": "1" if estado["solo_mio"] else "0",
        "apagados": ",".join(sorted(estado["apagados"])),
        "quien": estado["quien"], "q": estado["q"],
        "hechas": "1" if estado["hechas"] else "0",
        "mes": estado["mes"],
    }
    datos.update(cambios)
    partes = [f"{k}={quote(str(v))}" for k, v in datos.items() if v not in ("", None)]
    return "/calendario?" + "&".join(partes)


def _actividades_para(estado, refrescar=False):
    ancla = datetime.strptime(estado["dia"], "%Y-%m-%d").date()
    primero = ancla.replace(day=1)
    desde = (primero - timedelta(days=10)).isoformat()
    hasta = (primero + timedelta(days=52)).isoformat()
    return calendario.listar(desde, hasta, refrescar=refrescar)


def _puede_tocar(actividad, yo):
    """Quién puede cambiar una actividad; la regla vive aquí, no en el
    navegador.

    La regla "cada quien lo suyo" está SUSPENDIDA hasta previo aviso
    (dueño, 22/09/2026: "que todos puedan cambiar"): cualquier empleada
    edita cualquier actividad. El candado original queda comentado abajo,
    listo para reactivarse cuando él avise.
    """
    if not calendario.configurado():
        return None  # modo muestra: es una demo, no hay nada real que cuidar
    if not calendario.escritura_activa():
        return "Esta instancia mira el calendario real pero no escribe en Linear."
    return None
    # --- el candado suspendido (no borrar): ---
    # if yo["admin"]:
    #     return None
    # if actividad and yo["id"] and actividad["resp_id"] == yo["id"]:
    #     return None
    # de_quien = actividad["resp"] if actividad else "otra persona"
    # return f"Esa actividad es de {de_quien}: vos la ves, pero no la cambiás."


@app.get("/calendario")
def calendario_pantalla(request: Request):
    empleada = request.state.empleada
    estado = _estado_calendario(request, empleada)
    dia_hoy = calendario.hoy().isoformat()
    ancla = datetime.strptime(estado["dia"], "%Y-%m-%d").date()
    error = request.query_params.get("error") or None

    try:
        todas = _actividades_para(estado, refrescar=request.query_params.get("refrescar") == "1")
    except calendario.ErrorCalendario as fallo:
        todas, error = [], error or str(fallo)

    gente = calendario.responsables()
    yo = estado["yo"]
    visibles = calendario.filtrar(
        todas, tipos_apagados=estado["apagados"], quien=estado["quien"],
        texto=estado["q"], ver_hechas=estado["hechas"],
        solo_de=yo["id"] if estado["solo_mio"] else "")
    dias = calendario.dias_de(estado["vista"], ancla)

    # Cada vista llega armada desde aquí; la plantilla solo recorre.
    semana = carril = mes = grupos = None
    if estado["vista"] == "semana":
        carril = calendario.carril_semana(visibles, dias, dia_hoy)
    elif estado["vista"] == "dia":
        equipo_dia = ([p for p in gente if p["id"] == yo["id"]] if estado["solo_mio"]
                      else [p for p in gente if not estado["quien"] or p["id"] == estado["quien"]])
        carril = calendario.carril_dia(visibles, estado["dia"], equipo_dia, dia_hoy)
    elif estado["vista"] == "mes":
        mes = calendario.rejilla_mes(visibles, ancla, estado["dia"], dia_hoy)
    else:
        grupos = calendario.grupos_lista(visibles, dias, dia_hoy)

    # Cada cosa que se puede tocar lleva su enlace ya armado: la plantilla
    # no calcula direcciones ni el navegador arma estado.
    # (La fila de chips de tipos y el segmento "Mi calendario / Todo el
    # equipo" se quitaron de la pantalla el 22/09/2026 a pedido del dueño;
    # el alcance sigue decidiéndose en el servidor con estado["solo_mio"].)

    # La vista del teléfono: un día a la vez con su tira de semana (pedido
    # del dueño, 22/09/2026). Se arma siempre —es la misma página que en
    # computadora— y el CSS decide cuál de las dos se ve según el ancho.
    movil = calendario.vista_movil(visibles, estado["dia"], dia_hoy)
    for dia_tira in movil["tira"]:
        dia_tira["liga"] = _liga(estado, dia=dia_tira["iso"], mes=dia_tira["iso"])
    for fila in movil["horas"]:
        # Tocar una hora vacía abre el formulario con esa fecha y hora puestas.
        fila["liga_nueva"] = _liga(estado, nueva="1", fecha=estado["dia"],
                                   hora=f"{fila['h']:02d}:00")
    movil["ligas"] = {
        "ant": _liga(estado, dia=movil["semana_ant"], mes=movil["semana_ant"]),
        "sig": _liga(estado, dia=movil["semana_sig"], mes=movil["semana_sig"]),
        "hoy": _liga(estado, dia=dia_hoy, mes=dia_hoy),
        "nueva": _liga(estado, nueva="1", fecha=estado["dia"]),
    }

    if mes:
        for semana_celdas in mes:
            for celda in semana_celdas:
                celda["liga"] = _liga(estado, dia=celda["iso"], mes=celda["iso"])
                celda["liga_dia"] = _liga(estado, dia=celda["iso"], mes=celda["iso"], vista="dia")
                celda["liga_nueva"] = _liga(estado, nueva="1", fecha=celda["iso"])
    if carril:
        for columna in carril["columnas"]:
            columna["liga"] = _liga(estado, dia=columna["iso"], vista="dia")
            columna["liga_nueva"] = _liga(
                estado, nueva="1", fecha=columna["iso"],
                resp=(columna.get("persona") or {}).get("id", ""))

    ligas_vista = {v: _liga(estado, vista=v) for v in calendario.VISTAS}

    # Los 4 filtros (columna derecha del calendario): cada uno lleva el
    # enlace con el conjunto de tipos apagados que deja su toque.
    filtros = calendario.filtros_del_calendario(estado["apagados"])
    for filtro in filtros:
        filtro["liga"] = _liga(estado, apagados=",".join(sorted(filtro["apagados"])))

    # El log de leads de servicio (columna derecha del calendario): un toque
    # abre "Nueva actividad" prellenada con el tipo y el nombre del lead.
    leads_servicio = calendario.leads_de_servicio()
    for lead in leads_servicio:
        lead["liga"] = _liga(estado, nueva="1", tipo=lead["tipo"], cliente=lead["nombre"])

    # Y debajo, el bloque "Por entregar": TODOS los facturados de la
    # pestaña Retail — sin fecha primero (a ponerla), luego por fecha.
    por_entregar = retail.por_entregar()
    for lead in por_entregar:
        lead["liga"] = ("/retail?abrir=" if lead["entrega"] else "/retail?fecha=") + lead["ref"]

    abierta = None
    id_abierta = request.query_params.get("abrir", "")
    if id_abierta:
        abierta = next((a for a in todas if a["id"] == id_abierta), None)

    return plantillas.TemplateResponse(request, "calendario.html", {
        "empleada": empleada,
        "proyectos_activos": proyectos.activos(),
        "cal": calendario,
        "estado": estado,
        "yo": yo,
        "hoy": dia_hoy,
        "error": error,
        "aviso": request.query_params.get("aviso"),
        "titulo_rango": calendario.titulo_de(estado["vista"], ancla),
        "carril": carril,
        "mes": mes,
        "grupos": grupos,
        "movil": movil,
        "leads_servicio": leads_servicio,
        "por_entregar": por_entregar,
        "filtros": filtros,
        "dias": dias,
        "ligas_vista": ligas_vista,
        "franja": [a for a in visibles if calendario.esta_atrasada(a, dia_hoy)][:6],
        "atrasadas_total": len([a for a in visibles if calendario.esta_atrasada(a, dia_hoy)]),
        "gente": gente,
        "abierta": abierta,
        "notas": calendario.comentarios(id_abierta) if abierta else [],
        "puede_tocar_abierta": (_puede_tocar(abierta, yo) is None) if abierta else False,
        "nueva": request.query_params.get("nueva") == "1",
        "pre": {
            "fecha": request.query_params.get("fecha") or estado["dia"],
            "hora": request.query_params.get("hora") or calendario.HORA_POR_DEFECTO,
            "resp": request.query_params.get("resp") or yo["id"],
            # Si el crear falló, el formulario vuelve CON lo escrito: estos
            # llegan en la dirección del rebote (ver calendario_crear).
            "tipo": request.query_params.get("tipo", ""),
            "cliente": request.query_params.get("cliente", ""),
            "lugar": request.query_params.get("lugar", ""),
            "nota": request.query_params.get("nota", ""),
            "dur": request.query_params.get("dur", ""),
            "prioridad": request.query_params.get("prioridad", ""),
            "recogida": request.query_params.get("recogida", ""),
        },
        "modo": calendario.modo(),
        "puede_escribir": calendario.escritura_activa() or not calendario.configurado(),
        "ligas": {
            "hoy": _liga(estado, dia=dia_hoy, mes=dia_hoy),
            "anterior": _liga(estado,
                              dia=calendario.paso_de_vista(estado["vista"], ancla, -1).isoformat(),
                              mes=calendario.paso_de_vista(estado["vista"], ancla, -1).isoformat()),
            "siguiente": _liga(estado,
                               dia=calendario.paso_de_vista(estado["vista"], ancla, 1).isoformat(),
                               mes=calendario.paso_de_vista(estado["vista"], ancla, 1).isoformat()),
            "refrescar": _liga(estado, refrescar="1"),
            "nueva": _liga(estado, nueva="1"),
            "cerrar": _liga(estado),
            "sin_hechas": _liga(estado, hechas="0" if estado["hechas"] else "1"),
            # La franja de atrasadas dice cuántas hay y lleva a la lista,
            # donde se leen enteras, en vez de repetirlas ahí arriba.
            "atrasadas": _liga(estado, vista="lista"),
        },
        "volver": _liga(estado),
    })


@app.get("/retail")
def retail_pantalla(request: Request):
    """La pestaña Retail: kanban de leads de venta (diseño del artefacto)."""
    columnas, por_ref = retail.tablero()
    abierta = por_ref.get(request.query_params.get("abrir", ""))
    candidatas = []
    if abierta:
        abierta["etapa_titulo"] = next(
            e["titulo"] for e in retail.ETAPAS if e["clave"] == abierta["etapa"])
        for c in abierta["cotizaciones"]:
            c["fecha_texto"] = _fecha_venta(c["creado_en"])
        # Las cotizaciones/ventas de Vender que aún no son de ningún lead
        # y que PERTENECEN a este cliente (mismo celular o nombre):
        # candidatas a amarrar desde la ficha. Corrección de Abraham
        # (22/09/2026): nunca se ofrecen cotizaciones de otros clientes.
        candidatas = retail.candidatas_para(abierta)
        for c in candidatas:
            c["fecha_texto"] = _fecha_venta(c["creado_en"])
    con_fecha = por_ref.get(request.query_params.get("fecha", ""))
    dia_hoy = calendario.hoy()
    return plantillas.TemplateResponse(request, "retail.html", {
        "empleada": request.state.empleada,
        "cal": calendario,
        "modo": calendario.modo(),
        "columnas": columnas,
        "etapas": retail.ETAPAS,
        "abierta": abierta,
        "candidatas": candidatas,
        "con_fecha": con_fecha,
        "hoy": dia_hoy.isoformat(),
        "manana": (dia_hoy + timedelta(days=1)).isoformat(),
        "error": request.query_params.get("error") or None,
        "aviso": request.query_params.get("aviso"),
    })


@app.post("/retail/mover")
async def retail_mover(request: Request):
    form = await request.form()
    retail.mover(form.get("ref", ""), form.get("etapa", ""))
    return RedirectResponse("/retail", status_code=303)


@app.post("/retail/vincular")
async def retail_vincular(request: Request):
    """Amarra una cotización/venta existente de Vender al lead abierto; con
    el vínculo la tarjeta cae sola en su columna (mínimo Cotizado)."""
    form = await request.form()
    ref = form.get("ref", "")
    try:
        n = int(form.get("n", ""))
    except ValueError:
        return RedirectResponse("/retail", status_code=303)
    if form.get("clase") == "servicio":
        cotizaciones.vincular_lead(n, ref)
    else:
        ventas.vincular_lead(n, ref)
    return RedirectResponse(f"/retail?abrir={quote(ref)}", status_code=303)


@app.post("/retail/desvincular")
async def retail_desvincular(request: Request):
    form = await request.form()
    ref = form.get("ref", "")
    try:
        n = int(form.get("n", ""))
    except ValueError:
        return RedirectResponse("/retail", status_code=303)
    if form.get("clase") == "servicio":
        cotizaciones.vincular_lead(n, None)
    else:
        ventas.vincular_lead(n, None)
    return RedirectResponse(f"/retail?abrir={quote(ref)}", status_code=303)


@app.post("/retail/fecha")
async def retail_fecha(request: Request):
    form = await request.form()
    ref, entrega = form.get("ref", ""), form.get("entrega", "")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", entrega):
        return RedirectResponse(
            f"/retail?fecha={quote(ref)}&error=" + quote("Esa fecha no se ve válida."),
            status_code=303)
    retail.poner_fecha(ref, form.get("nombre", ""), entrega)
    # El empuje rápido a Google Calendar, como cualquier otra escritura.
    calendario_google.sincronizar_en_fondo()
    return RedirectResponse(
        "/retail?aviso=" + quote(
            f"Entrega el {calendario.dmy(entrega)} guardada y puesta en el calendario."),
        status_code=303)


def _volver_a(request, aviso="", error=""):
    """Después de una acción se vuelve al mismo día, vista y filtros."""
    destino = request.query_params.get("volver") or "/calendario"
    if not destino.startswith("/calendario"):
        destino = "/calendario"
    if aviso:
        destino += ("&" if "?" in destino else "?") + "aviso=" + quote(aviso)
    if error:
        destino += ("&" if "?" in destino else "?") + "error=" + quote(error)
    return RedirectResponse(destino, status_code=303)


async def _accion_calendario(request, id_actividad, hacer):
    """Envoltorio común: revisa el permiso, ejecuta y vuelve con el aviso."""
    yo = _yo_en_el_calendario(request.state.empleada)
    actividad = None
    if id_actividad:
        estado = _estado_calendario(request, request.state.empleada)
        try:
            actividad = next((a for a in _actividades_para(estado) if a["id"] == id_actividad), None)
        except calendario.ErrorCalendario as fallo:
            return _volver_a(request, error=str(fallo))
        negado = _puede_tocar(actividad, yo)
        if negado:
            return _volver_a(request, error=negado)
    try:
        aviso = hacer(yo, actividad) or "Listo."
    except calendario.ErrorCalendario as fallo:
        return _volver_a(request, error=str(fallo))
    # El aviso rápido a Google Calendar (si hay cuentas conectadas); la
    # conciliación de cada 15 minutos cubre lo que este empuje pierda.
    calendario_google.sincronizar_en_fondo()
    return _volver_a(request, aviso=aviso)


@app.post("/calendario/actividad")
async def calendario_crear(request: Request):
    form = await request.form()

    def hacer(yo, _actividad):
        if not (calendario.escritura_activa() or not calendario.configurado()):
            raise calendario.ErrorCalendario(
                "Esta instancia mira el calendario real pero no escribe en Linear.")
        # Sin el campo Responsable (quitado hasta previo aviso, 22/09/2026)
        # todo nace a nombre de quien lo crea; sin correo enlazado a
        # Linear, queda sin asignar.
        resp = form.get("resp_id") or yo["id"]
        creada = calendario.crear(
            tipo=form.get("tipo", "otro"), cliente=form.get("cliente", ""),
            fecha=form.get("fecha", ""), hora=form.get("hora") or calendario.HORA_POR_DEFECTO,
            dur=form.get("dur") or calendario.DURACION_POR_DEFECTO,
            lugar=form.get("lugar", ""), resp_id=resp,
            prioridad=form.get("prioridad") or 3, nota=form.get("nota", ""))
        texto = f"Creada {creada['ref']}: {calendario.nombre_de_tipo(form.get('tipo', 'otro'))}"
        # Un alquiler nace con su recogida: nunca se queda una planta
        # alquilada sin fecha de vuelta.
        recogida = form.get("recogida", "")
        if form.get("tipo") == "alquiler" and re.fullmatch(r"\d{4}-\d{2}-\d{2}", recogida):
            otra = calendario.crear(
                tipo="recogida", cliente=form.get("cliente", ""), fecha=recogida,
                hora="09:00", dur=60, lugar=form.get("lugar", ""), resp_id=resp,
                prioridad=form.get("prioridad") or 3,
                nota=f"Recogida del alquiler {creada['ref']}.")
            texto += f" + la recogida {otra['ref']} el {calendario.dmy(recogida)}"
        return texto + "."

    # No pasa por _accion_calendario: si Linear falla, se vuelve al
    # formulario abierto y CON lo escrito, no a la pantalla pelada.
    yo = _yo_en_el_calendario(request.state.empleada)
    try:
        aviso = hacer(yo, None) or "Listo."
    except calendario.ErrorCalendario as fallo:
        destino = request.query_params.get("volver") or "/calendario"
        if not destino.startswith("/calendario"):
            destino = "/calendario"
        campos = {"nueva": "1", "error": str(fallo)}
        for llave in ("tipo", "cliente", "lugar", "fecha", "hora", "dur",
                      "prioridad", "recogida", "nota", "resp_id"):
            if form.get(llave):
                campos["resp" if llave == "resp_id" else llave] = form.get(llave)
        destino += ("&" if "?" in destino else "?") + "&".join(
            f"{clave}={quote(str(valor))}" for clave, valor in campos.items())
        return RedirectResponse(destino, status_code=303)
    calendario_google.sincronizar_en_fondo()
    return _volver_a(request, aviso=aviso)


@app.post("/calendario/actividad/{id_actividad}/mover")
async def calendario_mover(request: Request, id_actividad: str):
    form = await request.form()

    def hacer(_yo, _actividad):
        calendario.mover(id_actividad, form.get("fecha", ""), form.get("hora") or None)
        return f"Movida al {calendario.dmy(form.get('fecha', ''))}."

    return await _accion_calendario(request, id_actividad, hacer)


@app.post("/calendario/actividad/{id_actividad}/estado")
async def calendario_estado(request: Request, id_actividad: str):
    form = await request.form()

    def hacer(_yo, _actividad):
        nuevo = form.get("estado", "")
        calendario.cambiar_estado(id_actividad, nuevo)
        return calendario.nombre_de_estado(nuevo) + "."

    return await _accion_calendario(request, id_actividad, hacer)


@app.post("/calendario/actividad/{id_actividad}/detalle")
async def calendario_detalle(request: Request, id_actividad: str):
    form = await request.form()

    def hacer(yo, actividad):
        calendario.cambiar_detalle(
            id_actividad, hora=form.get("hora") or None, dur=form.get("dur") or None,
            lugar=form.get("lugar"), prioridad=form.get("prioridad") or None)
        nuevo_resp = form.get("resp_id")
        # Repartir trabajo es cosa del dueño; el empleado guarda lo demás.
        if nuevo_resp is not None and yo["admin"] and actividad and nuevo_resp != actividad["resp_id"]:
            calendario.reasignar(id_actividad, nuevo_resp)
        fecha = form.get("fecha", "")
        if fecha and actividad and fecha != actividad["fecha"]:
            calendario.mover(id_actividad, fecha, form.get("hora") or None)
        return "Guardado."

    return await _accion_calendario(request, id_actividad, hacer)


@app.post("/calendario/actividad/{id_actividad}/nota")
async def calendario_nota(request: Request, id_actividad: str):
    form = await request.form()
    autor = request.state.empleada.get("nombre") or request.state.empleada["id"]

    def hacer(_yo, _actividad):
        calendario.agregar_nota(id_actividad, form.get("texto", ""), autor)
        return "Nota agregada como comentario del issue."

    return await _accion_calendario(request, id_actividad, hacer)


# ---------------------------------------------------------------------------
# El calendario con piel de Twenty (/crm/calendario): la MISMA lógica del
# calendario (Linear manda, mismo alcance por rol, mismas escrituras), con la
# cara del CRM. El nginx del droplet del CRM lo proxya bajo
# crm.plantaspanama.com y una pestaña inyectada en Twenty lo abre como
# iframe, igual que la pestaña Chats. Los colores son los de los labels de
# Twenty (dueño, 22/09/2026): crm_twenty.repintar los traduce.
# ---------------------------------------------------------------------------

VISTAS_CRM = ("dia", "semana", "mes", "lista")


def _base_crm(request):
    """La URL pública de la cara del CRM (detrás del nginx del droplet CRM)."""
    return (os.environ.get("CRM_PUBLIC_BASE_URL")
            or str(request.base_url)).rstrip("/")


@app.get("/crm/login")
def crm_login(request: Request):
    """El login de la cara del CRM. El OAuth de Google no corre dentro de un
    iframe, así que el botón sale del marco (target=_top), entra y vuelve a
    /crm/calendario; la pestaña de Twenty ya encuentra la sesión puesta."""
    usuario_dev = os.environ.get("SIN_LOGIN", "").strip()
    if usuario_dev and seguridad.empleada_por_usuario(usuario_dev):
        return RedirectResponse("/crm/calendario", status_code=303)
    if seguridad.empleada_de_sesion(request.cookies.get("sesion")):
        return RedirectResponse("/crm/calendario", status_code=303)
    return plantillas.TemplateResponse(request, "crm_login.html", {
        "google": acceso_google.configurado(),
        "error": request.query_params.get("error") or None,
    })


@app.get("/crm/auth/google")
def crm_google_entrar(request: Request):
    if not acceso_google.configurado():
        return RedirectResponse("/crm/login", status_code=303)
    estado = secrets.token_urlsafe(24)
    destino = _base_crm(request) + "/crm/auth/google/callback"
    respuesta = RedirectResponse(
        acceso_google.url_entrada(destino, estado), status_code=303)
    respuesta.set_cookie("oauth_estado", estado, max_age=600,
                         httponly=True, samesite="lax", secure=_cookie_segura())
    return respuesta


@app.get("/crm/auth/google/callback")
def crm_google_callback(request: Request, code: str = "", state: str = ""):
    if (not acceso_google.configurado() or not code or not state
            or state != request.cookies.get("oauth_estado")):
        return RedirectResponse(
            "/crm/login?error=" + quote("La entrada con Google no se pudo "
                                        "completar. Prueba de nuevo."),
            status_code=303)
    try:
        cuenta = acceso_google.canjear_codigo(
            code, _base_crm(request) + "/crm/auth/google/callback")
    except acceso_google.FalloGoogle:
        return RedirectResponse(
            "/crm/login?error=" + quote("No se pudo verificar la cuenta con "
                                        "Google. Prueba de nuevo."),
            status_code=303)
    empleada = seguridad.entrar_con_google(
        cuenta["email"], cuenta["nombre"], es_admin=cuenta["email"] in _admins(),
        token=request.cookies.get("invitacion"))
    if empleada is None:
        return RedirectResponse(
            "/crm/login?error=" + quote(f"{cuenta['email']} no tiene "
                                        "invitación. Pide una al encargado."),
            status_code=303)
    respuesta = RedirectResponse("/crm/calendario", status_code=303)
    respuesta.set_cookie(
        "sesion", seguridad.crear_sesion(empleada["id"]),
        max_age=seguridad.DIAS_SESION * 24 * 3600,
        httponly=True, samesite="lax", secure=_cookie_segura(),
    )
    respuesta.delete_cookie("oauth_estado")
    respuesta.delete_cookie("invitacion")
    return respuesta


def _estado_crm(request):
    q = request.query_params
    dia = q.get("dia", "")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", dia):
        dia = calendario.hoy().isoformat()
    vista = q.get("vista", "semana")
    if vista not in VISTAS_CRM:
        vista = "semana"
    return {"dia": dia, "vista": vista, "hechas": q.get("hechas", "1") != "0"}


def _liga_crm(estado, **cambios):
    datos = {"dia": estado["dia"], "vista": estado["vista"],
             "hechas": "1" if estado["hechas"] else "0"}
    datos.update(cambios)
    partes = [f"{k}={quote(str(v))}" for k, v in datos.items() if v not in ("", None)]
    return "/crm/calendario?" + "&".join(partes)


def _iniciales(texto):
    partes = [p for p in (texto or "").split() if p]
    return "".join(p[0] for p in partes[:2]).upper() or "·"


@app.get("/crm/calendario")
def crm_calendario_pantalla(request: Request):
    empleada = request.state.empleada
    estado = _estado_crm(request)
    q = request.query_params
    dia_hoy = calendario.hoy().isoformat()
    ancla = datetime.strptime(estado["dia"], "%Y-%m-%d").date()
    error = q.get("error") or None
    aviso = q.get("aviso") or None

    try:
        todas = _actividades_para(estado)
    except calendario.ErrorCalendario as fallo:
        todas, error = [], error or str(fallo)

    # Mismo alcance que el calendario de inventario: el empleado entra
    # viendo lo suyo y el dueño el equipo; lo decide el servidor.
    yo = _yo_en_el_calendario(empleada)
    solo_mio = not yo["admin"] and bool(yo["id"])
    visibles = calendario.filtrar(todas, ver_hechas=estado["hechas"],
                                  solo_de=yo["id"] if solo_mio else "")
    dias = calendario.dias_de(estado["vista"], ancla)
    puede_escribir = calendario.escritura_activa() or not calendario.configurado()

    carril = mes = grupos = None
    if estado["vista"] in ("semana", "dia"):
        columnas = dias if estado["vista"] == "semana" else [estado["dia"]]
        carril = crm_twenty.repintar(
            crm_twenty.carril_dias(visibles, columnas, dia_hoy))
        for col in carril["columnas"]:
            # Tocar una hora vacía abre el formulario con fecha y hora puestas.
            col["celdas"] = [
                {"top": f["top"], "alto": f["alto"],
                 "liga": _liga_crm(estado, nueva="1", fecha=col["iso"],
                                   hora=f"{f['h']:02d}:00")}
                for f in carril["horas"]] if puede_escribir else []
        for bloque in carril["bloques"]:
            bloque["liga"] = _liga_crm(estado, act=bloque["a"]["id"])
            bloque["rango"] = calendario._rango_bonito(bloque["a"])
    elif estado["vista"] == "mes":
        mes = crm_twenty.repintar(
            calendario.rejilla_mes(visibles, ancla, estado["dia"], dia_hoy))
        for fila_mes in mes:
            for celda in fila_mes:
                celda["liga_dia"] = _liga_crm(estado, vista="dia", dia=celda["iso"])
                for barra in celda["barras"]:
                    barra["liga"] = _liga_crm(estado, act=barra["a"]["id"])
    else:
        grupos = [{
            "titulo": g["titulo"], "es_atraso": g["es_atraso"],
            "filas": [{
                "a": a, "rango": calendario._rango_bonito(a),
                "color": crm_twenty.color_crm(a["tipo"]),
                "tipo_nombre": calendario.nombre_de_tipo(a["tipo"]),
                "liga": _liga_crm(estado, act=a["id"]),
            } for a in g["actividades"]],
        } for g in calendario.grupos_lista(visibles, dias, dia_hoy)]

    leads = [dict(lead, liga=_liga_crm(estado, lead=lead["ref"]),
                  color=crm_twenty.color_etiqueta(lead["etiqueta"]))
             for lead in calendario.leads_de_servicio()]

    # La ficha abierta (actividad, lead o el formulario) la decide la
    # dirección: la página entera se arma aquí, el navegador no calcula nada.
    abierta = lead_abierto = nueva = None
    id_abierta = q.get("act", "")
    if id_abierta:
        a = next((x for x in todas if x["id"] == id_abierta), None)
        if a:
            try:
                notas = calendario.comentarios(a["id"])
            except calendario.ErrorCalendario:
                notas = []
            cuando = calendario.dmy(a["fecha"])
            if a["fecha"]:
                fecha_a = datetime.strptime(a["fecha"], "%Y-%m-%d").date()
                cuando = f"{calendario.DOW_LARGO[fecha_a.weekday()]} {cuando}"
            abierta = {
                "a": a,
                "color": crm_twenty.color_crm(a["tipo"]),
                "tipo_nombre": calendario.nombre_de_tipo(a["tipo"]),
                "estado_nombre": calendario.nombre_de_estado(a["estado"]),
                "cuando": f"{cuando} · {calendario._rango_bonito(a)}",
                "iniciales": _iniciales(a["cliente"]),
                "notas": notas,
                "accion_estado": (f"/crm/calendario/actividad/{a['id']}/estado"
                                  f"?volver={quote(_liga_crm(estado))}"),
            }
    ref_lead = q.get("lead", "")
    if ref_lead:
        fila_lead = next((l for l in leads if l["ref"] == ref_lead), None)
        if fila_lead:
            ficha = crm_twenty.ficha_de_lead(fila_lead) or {}
            lead_abierto = {
                "l": fila_lead,
                "iniciales": _iniciales(fila_lead["nombre"]),
                "pp": ficha.get("pp") or fila_lead.get("pp") or "",
                "telefono": ficha.get("telefono") or "",
                "wa": ficha.get("wa") or "",
                "llego": ficha.get("llego") or "",
                "mensajes": ficha.get("mensajes") or [],
                "twenty_url": ficha.get("twenty_url") or crm_twenty.twenty_publico(),
                "liga_agendar": _liga_crm(estado, nueva="1", fecha=estado["dia"],
                                          tipo=fila_lead["tipo"],
                                          cliente=fila_lead["nombre"]),
            }
    if q.get("nueva") == "1":
        nueva = {
            "fecha": (q.get("fecha") if re.fullmatch(r"\d{4}-\d{2}-\d{2}", q.get("fecha", ""))
                      else estado["dia"]),
            "hora": q.get("hora") or calendario.HORA_POR_DEFECTO,
            "dur": q.get("dur") or str(calendario.DURACION_POR_DEFECTO),
            "tipo": (q.get("tipo") if q.get("tipo") in calendario.POR_CLAVE else "entrega"),
            "cliente": q.get("cliente", ""),
            "lugar": q.get("lugar", ""),
            "nota": q.get("nota", ""),
            "accion": f"/crm/calendario/actividad?volver={quote(_liga_crm(estado))}",
        }

    ligas = {
        "vistas": {v: _liga_crm(estado, vista=v) for v in VISTAS_CRM},
        "ant": _liga_crm(estado, dia=calendario.paso_de_vista(
            estado["vista"], ancla, -1).isoformat()),
        "sig": _liga_crm(estado, dia=calendario.paso_de_vista(
            estado["vista"], ancla, 1).isoformat()),
        "hoy": _liga_crm(estado, dia=dia_hoy),
        "hechas": _liga_crm(estado, hechas="0" if estado["hechas"] else "1"),
        "nueva": _liga_crm(estado, nueva="1", fecha=estado["dia"]),
        "cerrar": _liga_crm(estado),
    }

    return plantillas.TemplateResponse(request, "crm_calendario.html", {
        "titulo_rango": calendario.titulo_de(estado["vista"], ancla),
        "cuenta": len(visibles),
        "vista": estado["vista"], "hechas": estado["hechas"],
        "ligas": ligas, "carril": carril, "mes": mes, "grupos": grupos,
        "leads": leads, "abierta": abierta, "lead_abierto": lead_abierto,
        "nueva": nueva, "tipos": calendario.TIPOS,
        "error": error, "aviso": aviso, "puede_escribir": puede_escribir,
    })


def _volver_crm(request, **extras):
    destino = request.query_params.get("volver") or "/crm/calendario"
    if not destino.startswith("/crm/calendario"):
        destino = "/crm/calendario"
    if extras:
        destino += ("&" if "?" in destino else "?") + "&".join(
            f"{clave}={quote(str(valor))}" for clave, valor in extras.items()
            if valor not in ("", None))
    return RedirectResponse(destino, status_code=303)


@app.post("/crm/calendario/actividad")
async def crm_calendario_crear(request: Request):
    form = await request.form()
    yo = _yo_en_el_calendario(request.state.empleada)
    try:
        bloqueo = _puede_tocar(None, yo)
        if bloqueo:
            raise calendario.ErrorCalendario(bloqueo)
        creada = calendario.crear(
            tipo=form.get("tipo", "otro"), cliente=form.get("cliente", ""),
            fecha=form.get("fecha", ""),
            hora=form.get("hora") or calendario.HORA_POR_DEFECTO,
            dur=form.get("dur") or calendario.DURACION_POR_DEFECTO,
            lugar=form.get("lugar", ""), resp_id=yo["id"],
            nota=form.get("nota", ""))
    except calendario.ErrorCalendario as fallo:
        # Se vuelve al formulario abierto y CON lo escrito.
        return _volver_crm(request, error=str(fallo), nueva="1",
                           fecha=form.get("fecha", ""), hora=form.get("hora", ""),
                           dur=form.get("dur", ""), tipo=form.get("tipo", ""),
                           cliente=form.get("cliente", ""),
                           lugar=form.get("lugar", ""), nota=form.get("nota", ""))
    calendario_google.sincronizar_en_fondo()
    return _volver_crm(request, aviso=f"Creada {creada['ref']}.")


@app.post("/crm/calendario/actividad/{id_actividad}/estado")
async def crm_calendario_estado(request: Request, id_actividad: str):
    form = await request.form()
    yo = _yo_en_el_calendario(request.state.empleada)
    try:
        bloqueo = _puede_tocar(None, yo)
        if bloqueo:
            raise calendario.ErrorCalendario(bloqueo)
        calendario.cambiar_estado(id_actividad, form.get("estado", ""))
    except calendario.ErrorCalendario as fallo:
        return _volver_crm(request, error=str(fallo))
    calendario_google.sincronizar_en_fondo()
    return _volver_crm(
        request, aviso=calendario.nombre_de_estado(form.get("estado", "")) + ".")


@app.get("/calendario.ics")
def calendario_feed(request: Request):
    """La suscripción del teléfono (Apple/Google), autorizada por token.

    Con el usuario de Linear enlazado (email verificado de Google) el feed
    trae lo de esa empleada; una admin, o una cuenta sin enlazar, recibe el
    equipo completo. Solo lectura: el teléfono no escribe en Linear.
    """
    empleada = calendario_ics.empleada_del_token(request.query_params.get("t", ""))
    if empleada is None:
        return PlainTextResponse("No existe.", status_code=404)
    yo = _yo_en_el_calendario(empleada)
    desde, hasta = calendario_ics.rango()
    try:
        actividades = calendario.listar(desde, hasta)
    except calendario.ErrorCalendario:
        # Antes que romperle la suscripción al teléfono, un calendario vacío;
        # el cliente reintenta solo en el próximo refresco.
        return Response(calendario_ics.feed([]), media_type="text/calendar; charset=utf-8")
    nombre = "Calendario Rose"
    if yo["id"] and not yo["admin"]:
        actividades = [a for a in actividades if a["resp_id"] == yo["id"]]
        nombre = "Calendario Rose · " + yo["nombre"]
    return Response(
        calendario_ics.feed(actividades, nombre),
        media_type="text/calendar; charset=utf-8",
        headers={"Cache-Control": "private, max-age=300"})


@app.get("/calendario/google/conectar")
def calendario_google_conectar(request: Request):
    """Manda a Google a pedir el permiso de calendario (scope de eventos),
    con el mismo state anti-CSRF del login."""
    if not calendario_google.configurado():
        return RedirectResponse("/?tab=ajustes", status_code=303)
    estado = secrets.token_urlsafe(24)
    destino = _base_publica(request) + "/calendario/google/callback"
    respuesta = RedirectResponse(
        calendario_google.url_conectar(destino, estado), status_code=303)
    respuesta.set_cookie("gcal_estado", estado, max_age=600,
                         httponly=True, samesite="lax", secure=_cookie_segura())
    return respuesta


@app.get("/calendario/google/callback")
def calendario_google_callback(request: Request, code: str = "", state: str = ""):
    if (not calendario_google.configurado() or not code or not state
            or state != request.cookies.get("gcal_estado")):
        return RedirectResponse("/?tab=ajustes&aviso=google-error", status_code=303)
    try:
        cuenta = calendario_google.canjear(
            code, _base_publica(request) + "/calendario/google/callback")
    except acceso_google.FalloGoogle:
        return RedirectResponse("/?tab=ajustes&aviso=google-error", status_code=303)
    calendario_google.conectar(
        request.state.empleada["id"], cuenta["email"], cuenta["refresh_token"])
    # La primera pasada, ya: que el calendario aparezca lleno de una vez.
    calendario_google.sincronizar_en_fondo()
    respuesta = RedirectResponse("/?tab=ajustes&aviso=google-conectado", status_code=303)
    respuesta.delete_cookie("gcal_estado")
    return respuesta


@app.post("/calendario/google/desconectar")
def calendario_google_desconectar(request: Request):
    calendario_google.desconectar(request.state.empleada["id"])
    return RedirectResponse("/?tab=ajustes&aviso=google-fuera", status_code=303)


@app.post("/calendario/suscripcion/regenerar")
def calendario_regenerar_enlace(request: Request):
    """Enlace nuevo para la empleada de la sesión; el viejo muere ya."""
    calendario_ics.regenerar(request.state.empleada["id"])
    return RedirectResponse("/?tab=ajustes&aviso=enlace-nuevo", status_code=303)


# ---------------------------------------------------------------------------
# Compras/Gastos: la plata que sale
# ---------------------------------------------------------------------------
# La pantalla agrupa por a quien se le carga la compra (Abraham, 18/09/2026):
# Proyectos, Ventas y Del vivero. Las compras viven en Odoo; lo unico que se
# decide aqui es que es valido y si hay que subir stock, y eso ultimo pasa
# siempre por el order-api.

def _pagina_compras(request, vista="proyectos", q="", error=None):
    contexto = {
        "request": request,
        "compras_activo": compras.activo(),
        "vista": vista,
        "vistas": compras.VISTAS,
        "q": q,
        "error": error,
        "resumen": {"total": 0.0, "cantidad": 0, "proyectos": 0.0, "ventas": 0.0,
                    "vivero": 0.0, "categorias": [], "mes": ""},
        "tarjetas": [],
        "compras": [],
    }
    if compras.activo():
        try:
            contexto["resumen"] = compras.resumen_del_mes()
            if vista == "proyectos":
                contexto["tarjetas"] = compras.por_proyecto()
            elif vista == "ventas":
                contexto["tarjetas"] = compras.por_venta()
            else:
                contexto["compras"] = compras.listar("vivero", q)
        except Exception as falla:  # Odoo caido o credenciales malas
            contexto["error"] = ventas._mensaje_de_error(falla)
    return plantillas.TemplateResponse(request, "compras.html", contexto)


@app.get("/compras")
def compras_lista(request: Request, vista: str = "proyectos", q: str = "",
                  error: str = ""):
    if vista not in dict(compras.VISTAS):
        vista = "proyectos"
    return _pagina_compras(request, vista, q, error or None)


def _contexto_compra(request, compra=None, error=None, vista="proyectos",
                     destino="vivero", proyecto_id=None, orden_id=None,
                     orden_nombre=""):
    return {
        "request": request,
        "compra": compra,
        "error": error,
        "vista": vista,
        "accion": f"/compras/{compra['n']}" if compra else "/compras/nueva",
        "categorias": compras.CATEGORIAS,
        "formas_pago": compras.FORMAS_PAGO,
        "destinos": (("vivero", "Del vivero"), ("proyecto", "Un proyecto"),
                     ("venta", "Una venta")),
        "destino": destino,
        "proyectos": compras.proyectos_para_elegir() if compras.activo() else [],
        "proyecto_id": proyecto_id,
        "orden_id": orden_id,
        "orden_nombre": orden_nombre,
        "hoy": datetime.now(datos.ZONA_PANAMA).date().isoformat(),
    }


@app.get("/compras/nueva")
def compra_nueva(request: Request, proyecto: int = 0, orden: int = 0,
                 error: str = ""):
    """El formulario en blanco. Si se entra desde la tarjeta de un proyecto o
    de una venta, ese destino llega puesto y no hay que elegirlo."""
    if not compras.activo():
        return RedirectResponse("/compras", status_code=303)
    destino = "proyecto" if proyecto else ("venta" if orden else "vivero")
    orden_nombre = ""
    if orden:
        filas = ventas._ejecutar("sale.order", "read", [[int(orden)]],
                                 {"fields": ["name", "partner_id"]})
        if filas:
            orden_nombre = f"{filas[0]['name']} · {(filas[0].get('partner_id') or [0, ''])[1]}"
    contexto = _contexto_compra(
        request, error=error or None, destino=destino,
        proyecto_id=proyecto or None, orden_id=orden or None,
        orden_nombre=orden_nombre)
    return plantillas.TemplateResponse(request, "compra_form.html", contexto)


async def _recibo_del_form(form):
    """(bytes, nombre) del archivo subido, o (None, '') si no mandaron uno."""
    archivo = form.get("recibo")
    if not archivo or not getattr(archivo, "filename", ""):
        return None, ""
    contenido = await archivo.read()
    if not contenido:
        return None, ""
    if len(contenido) > 15 * 1024 * 1024:
        raise ValueError("El recibo pesa más de 15 MB. Toma la foto otra vez.")
    return contenido, archivo.filename


@app.post("/compras/nueva")
async def compra_crear(request: Request):
    if not compras.activo():
        return RedirectResponse("/compras", status_code=303)
    form = await request.form()
    destino = form.get("destino") or "vivero"
    try:
        recibo, recibo_nombre = await _recibo_del_form(form)
        lineas = compras.lineas_del_form(form) if destino != "proyecto" else []
        n = compras.crear(request.state.empleada, form, lineas,
                          recibo, recibo_nombre)
    except ValueError as falla:
        contexto = _contexto_compra(
            request, error=str(falla), destino=destino,
            proyecto_id=compras._entero(form.get("proyecto_id")),
            orden_id=compras._entero(form.get("orden_id")))
        return plantillas.TemplateResponse(request, "compra_form.html", contexto)
    except Exception as falla:
        contexto = _contexto_compra(
            request, error=ventas._mensaje_de_error(falla), destino=destino)
        return plantillas.TemplateResponse(request, "compra_form.html", contexto)

    # El stock se mueve DESPUES de que la compra quedo guardada: si el
    # order-api falla, la compra ya esta anotada y solo falta el inventario.
    aviso = ""
    if lineas:
        try:
            compras.subir_al_inventario(n, compras.lineas_de(n),
                                        request.state.empleada["id"])
            datos.reiniciar_cache_proxy()
        except datos.SinConexion as falla:
            aviso = (f"La compra quedó guardada, pero el stock no subió: "
                     f"{falla}. Ajústalo desde Stock.")
    vista = "proyectos" if destino == "proyecto" else (
        "ventas" if destino == "venta" else "vivero")
    destino_url = f"/compras?vista={vista}"
    if aviso:
        destino_url += "&error=" + quote(aviso)
    return RedirectResponse(destino_url, status_code=303)


@app.get("/compras/productos")
def compras_productos(request: Request, q: str = ""):
    """Plantas e insumos para la lista de lo que entra al inventario."""
    if not compras.activo():
        return {"productos": []}
    return {"productos": compras.buscar_productos(q)}


@app.get("/compras/ventas")
def compras_ventas(request: Request, q: str = ""):
    """Ventas y cotizaciones a las que colgarle la compra."""
    if not compras.activo():
        return {"ventas": []}
    return {"ventas": compras.buscar_ventas(q)}


@app.get("/compras/{n}")
def compra_ficha(request: Request, n: int, error: str = ""):
    if not compras.activo():
        return RedirectResponse("/compras", status_code=303)
    compra = compras.obtener(n)
    if not compra:
        return RedirectResponse("/compras", status_code=303)
    contexto = _contexto_compra(
        request, compra=compra, error=error or None,
        destino=compra["destino"] if compra["destino"] != "proyecto" else "proyecto",
        proyecto_id=compra["proyecto_id"], orden_id=compra["orden_id"],
        orden_nombre=compra["orden_nombre"])
    return plantillas.TemplateResponse(request, "compra_form.html", contexto)


@app.post("/compras/{n}")
async def compra_guardar(request: Request, n: int):
    if not compras.activo():
        return RedirectResponse("/compras", status_code=303)
    form = await request.form()
    try:
        recibo, recibo_nombre = await _recibo_del_form(form)
        compras.editar(n, form, recibo, recibo_nombre)
    except ValueError as falla:
        compra = compras.obtener(n)
        contexto = _contexto_compra(
            request, compra=compra, error=str(falla),
            destino=form.get("destino") or "vivero",
            proyecto_id=compras._entero(form.get("proyecto_id")),
            orden_id=compras._entero(form.get("orden_id")))
        return plantillas.TemplateResponse(request, "compra_form.html", contexto)
    vista = {"proyecto": "proyectos", "venta": "ventas"}.get(
        form.get("destino") or "vivero", "vivero")
    return RedirectResponse(f"/compras?vista={vista}", status_code=303)


@app.post("/compras/{n}/borrar")
def compra_borrar(request: Request, n: int):
    if not compras.activo():
        return RedirectResponse("/compras", status_code=303)
    compras.borrar(n)
    return RedirectResponse("/compras", status_code=303)


@app.get("/compras/{n}/recibo")
def compra_recibo(request: Request, n: int):
    contenido, nombre = compras.recibo_de(n)
    if not contenido:
        return RedirectResponse(f"/compras/{n}", status_code=303)
    tipo = "application/pdf" if nombre.lower().endswith(".pdf") else "image/jpeg"
    return Response(contenido, media_type=tipo,
                    headers={"Content-Disposition": f'inline; filename="{nombre}"'})

