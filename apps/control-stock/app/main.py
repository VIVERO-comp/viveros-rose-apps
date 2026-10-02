"""Rutas de la app de control de stock (server-rendered con Jinja2).

Una sola pantalla con pestañas (Inicio, Stock y, para los editores, Fichas;
Inventario sigue vivo en /?tab=inv pero fuera del menú), como el prototipo
aprobado: el servidor arma los datos y las pestañas se mueven con el JS del
prototipo. Las acciones (ajustar stock, atender alertas, conteos, fichas)
son POSTs de vuelta a este mismo servidor; la app nunca toca Odoo directo.
"""

import base64
import json
import logging
import os
import re
import secrets
import time
from datetime import datetime, timedelta
from urllib.parse import quote

from fastapi import FastAPI, Request, UploadFile
from fastapi.responses import (FileResponse, JSONResponse, PlainTextResponse,
                               RedirectResponse, Response)
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import (acceso_google, agenda, altas, avisos, calculos, calendario,
               calendario_google, colores, compra_odoo, compras,
               calendario_ics, conteos, control, cot_lead, cotizaciones,
               coworkers, crm_twenty, datos, fichas, fotos,
               linear_leads, mantenimiento, proveedores, resumen, seguridad,
               vehiculos, ventas, wa_autor)

app = FastAPI(title="Control Viverorose")

RUTA_APP = os.path.dirname(__file__)
app.mount("/static", StaticFiles(directory=os.path.join(RUTA_APP, "static")), name="static")

# Velocidad (23/09/2026): las respuestas viajan comprimidas y los estáticos
# versionados (?v=mtime) se cachean un año — el v_estaticos ya cambia solo
# en cada deploy, así que el navegador nunca ve una versión vieja. Los que
# van sin versión (logo) se cachean una hora.
app.add_middleware(GZipMiddleware, minimum_size=500)


@app.middleware("http")
async def cachear_estaticos(request, siguiente):
    respuesta = await siguiente(request)
    if request.url.path.startswith("/static/"):
        if "v=" in (request.url.query or ""):
            respuesta.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        else:
            respuesta.headers["Cache-Control"] = "public, max-age=3600"
    return respuesta

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
# El catálogo de cargos de servicio (envío, instalación, mantenimiento) lo
# pinta el parcial _cargos.html en las cuatro pantallas de venta: va como
# global para no pasarlo por contexto en cada una.
plantillas.env.globals["cargos_catalogo"] = ventas.CARGOS
# Las opciones de envío de Vender (28/09/2026): el parcial las pinta como
# radios con su precio de Ajustes, y el desglose rotula la línea con la
# opción elegida. Globals por la misma razón que cargos_catalogo;
# precios_envio va como función porque los precios cambian sin reiniciar.
plantillas.env.globals["envio_opciones"] = ventas.OPCIONES_ENVIO
plantillas.env.globals["envio_precios"] = ventas.precios_envio
plantillas.env.globals["nombre_linea_envio"] = ventas.nombre_linea_envio
# Retail y CRM se BORRARON en la Fase 5 (24/09/2026): no quedan pantallas
# ni banderas que las escondan. `RETAIL_EN_MENU` y `CRM_EN_MENU` ya no
# hacen nada y se pueden sacar del .env del droplet.


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
plantillas.env.globals["colores"] = colores  # la paleta unica en las plantillas
plantillas.env.globals["cal_tipo"] = calendario.nombre_de_tipo
plantillas.env.filters["fecha_dmy"] = calendario.dmy

# ---------------------------------------------------------------------------
# Calentamiento de arranque (29/09/2026): la PRIMERA petición tras levantar
# el proceso pagaba TODAS las consultas en línea con las cachés vacías —
# hasta 21 segundos medidos en producción — y eso pasa en cada deploy y
# cada mañana. Al arrancar, un hilo de fondo adelanta esas consultas: si
# una petición llega antes de que el calentamiento termine, paga la suya
# como siempre — esto solo ADELANTA trabajo, no cambia ningún camino.
# ---------------------------------------------------------------------------

def _calentar_publicados():
    """`obtener_publicados` no lanza (devuelve el motivo): acá se convierte
    en excepción para que el log del calentamiento diga la verdad."""
    _skus, error = datos.obtener_publicados()
    if error:
        raise RuntimeError(error)


def _piezas_de_arranque():
    """[(nombre, tarea)] a calentar, SOLO de los servicios configurados.

    Sin token no hay nada que calentar: en la suite y en desarrollo local
    la lista queda vacía y no se dispara ni un hilo ni una consulta. El
    calendario no está aquí porque ya se calienta solo desde el 22/09
    (`calendario.calentar_en_fondo`, su propio hilo).
    """
    piezas = []
    if linear_leads.configurado():
        # El catálogo primero: es chico y las vistas de Control lo piden
        # junto con la lista (columnas, responsables, etiquetas).
        piezas.append(("catálogo del equipo LEAD",
                       lambda: linear_leads.catalogo()))
        piezas.append(("leads de Linear",
                       lambda: linear_leads.listar(refrescar=True)))
        if crm_twenty.twenty_configurado():
            # La espera (el último mensaje del cliente, para el orden de
            # las columnas) vive en SQLite y nunca bloquea una pintada;
            # se pide su refresco con el mecanismo propio de Control, que
            # corre en SU hilo — por eso el log no mide cuánto tardó.
            piezas.append(("espera de Control (sigue por su cuenta)",
                           lambda: control.refrescar_espera_en_fondo(
                               linear_leads.listar())))
    if datos.proxy_configurado():
        piezas.append(("inventario del stock-proxy",
                       lambda: datos.obtener_inventario()))
        # El catálogo publicado del sitio (pestaña Stock online) va con la
        # misma llave del proxy: donde hay stock real hay sitio real, y
        # así la suite —que no configura el proxy— no sale nunca a la red.
        piezas.append(("catálogo publicado del sitio", _calentar_publicados))
    return piezas


def _calentar_piezas(piezas):
    """Corre las piezas UNA tras otra, con una línea de log por pieza
    (cuánto tardó, sin secretos). Un fallo no frena a las siguientes ni
    tumba el proceso: el calentamiento era un adelanto, y la petición que
    llegue pagará su consulta como siempre."""
    registro = logging.getLogger("control_stock")
    for nombre, tarea in piezas:
        arranco = time.time()
        try:
            tarea()
            registro.info("Calentamiento de arranque: %s en %.1f s",
                          nombre, time.time() - arranco)
        except Exception as fallo:
            registro.warning("Calentamiento de arranque: %s falló a los "
                             "%.1f s (%s)", nombre, time.time() - arranco, fallo)


def calentar_arranque_en_fondo():
    """Dispara el calentamiento en un hilo aparte, sin bloquear nada.

    UNA corrida por arranque: se llama una vez al importar el módulo, y la
    clave de `calendario._en_fondo` no admite dos corridas a la vez."""
    piezas = _piezas_de_arranque()
    if not piezas:
        return
    calendario._en_fondo("arranque", lambda: _calentar_piezas(piezas))


# Las tablas se crean al importar: es idempotente y así el proceso (o los
# tests) nunca corren contra una base sin esquema.
datos.iniciar_db()
ventas.iniciar_tablas()
cotizaciones.iniciar_tablas()
control.iniciar_tablas()
compras.iniciar_tablas()
compra_odoo.iniciar_tablas()
proveedores.iniciar_tablas()
mantenimiento.iniciar_tablas()
avisos.iniciar_tablas()
calendario_ics.iniciar_tablas()
calendario_google.iniciar_tablas()
calendario_google.arrancar_hilo()
# El calendario arranca calentándose en fondo (catálogo + mes en curso):
# ni la primera visita del día espera a Linear (velocidad, 22/09/2026).
calendario.calentar_en_fondo()
# Y el resto de las cachés (29/09/2026): los leads de Linear para Control,
# la espera de Twenty y el inventario del stock-proxy para Stock y Vender.
calentar_arranque_en_fondo()


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
    # /sw-avisos.js y /manifest.webmanifest los pide el navegador por su
    # cuenta (también cuando la sesión venció): si contestaran con el
    # redirect al login, los avisos del celular se caerían en silencio.
    # /avisos/resumen lo llama el cron del droplet, no una persona: su
    # candado es RESUMEN_SECRETO (lo verifica la ruta), no la cookie.
    # /wa/autor lo llama WAHA desde el droplet del CRM: su candado es la
    # firma HMAC del cuerpo crudo (la verifica la ruta), no la cookie.
    # /entregas-pendientes/revisar lo llama el cron de las 7 a.m., mismo
    # trato que /avisos/resumen: el candado es ENTREGAS_PENDIENTES_SECRETO.
    if (ruta == "/login" or ruta == "/calendario.ics" or ruta == "/crm/login"
            or ruta == "/avisos/resumen" or ruta == "/wa/autor"
            or ruta == "/entregas-pendientes/revisar"
            or ruta == "/sw-avisos.js" or ruta == "/manifest.webmanifest"
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
def inicio(request: Request, refrescar: int = 0, crear: str = "",
           volver: str = ""):
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
    # En qué vehículos puede viajar cada planta (Odoo, 29/09/2026): la ficha
    # pinta la sección "Entrega en línea" solo para lo publicado y solo si
    # el Odoo consultado ya tiene los campos (None = no prometer nada).
    mapa_vehiculos = vehiculos.leer()

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
        # nombreCompartir es el nombre con el que "Compartir" (28/09/2026)
        # arma el archivo: el slug de la planta con ".jpg" — Cloudinary
        # entrega la foto en su formato original sin decir cuál es en la
        # URL, así que ".jpg" es el valor sano por defecto (casi todo lo
        # que se sube es jpg); el share sheet del teléfono de todos modos
        # decide por el `type` real del archivo, no por esta extensión.
        info = fotos.info_foto(p["sku"], subidas.get(p["sku"]))
        nombre_compartir = f"{ventas.slug(p['nombre']) or p['sku'].lower()}.jpg"
        if info:
            return {"img": info["img"], "imgG": info["grande"], "imgD": info["descarga"],
                    "nombreCompartir": nombre_compartir}
        respaldo = f"/stock/foto/{quote(p['sku'])}" if ventas.configurado() else None
        return {"img": respaldo, "imgG": respaldo, "imgD": respaldo,
                "nombreCompartir": nombre_compartir}

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
            # veh: {moto, carro, pickup} desde Odoo, o null. Null = la ficha
            # no pinta la sección (sin publicar, o el dato no se pudo leer).
            "veh": vehiculos.para_planta(p["sku"], p.get("publicado", True),
                                         mapa_vehiculos),
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
    # Números de coworkers (chats internos que no se vuelven leads): la lista
    # vive en la base `tienda` del droplet; si no responde, Ajustes lo dice
    # sin tumbar el resto de la pestaña.
    lista_coworkers, coworkers_error = [], None
    # Dispositivos de WhatsApp (Fase A, 25/09/2026): quién escribió cada
    # respuesta del 6099. La tabla es local y nunca falla, pero se arma solo
    # para el dueño: es el mapa de quién es quién en el equipo.
    dispositivos = []
    if es_admin:
        try:
            lista_coworkers = coworkers.listar()
        except Exception:
            coworkers_error = True
        dispositivos = wa_autor.vistos()
    return plantillas.TemplateResponse(request, "app.html", {
        "empleada": request.state.empleada,
        "puede_fichas": puede_fichas,
        "es_admin": es_admin,
        "empleadas": seguridad.listar() if es_admin else [],
        "invitaciones": seguridad.invitaciones_pendientes() if es_admin else [],
        "coworkers": lista_coworkers,
        "coworkers_error": coworkers_error,
        "dispositivos": dispositivos,
        "dispositivos_armados": wa_autor.configurado(),
        "responsables_wa": linear_leads.responsables() if es_admin else [],
        "aviso_ajustes": request.query_params.get("aviso"),
        "inv_nueva": request.query_params.get("inv") if es_admin else None,
        # Para armar los links /invitacion/{token} que se comparten.
        "base_publica": (os.environ.get("PUBLIC_BASE_URL")
                         or str(request.base_url)).rstrip("/"),
        # Avisos Web Push (23/09/2026): cada quien activa SU celular; los
        # de "Esperando respuesta" le llegan al encargado de los chats.
        "avisos_clave": avisos.clave_publica(),
        "avisos_celulares": avisos.cuantos(request.state.empleada["id"]),
        "avisos_encargado": avisos.USUARIO_CHATS,
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
        # Las categorías del alta de plantas las pinta Jinja (antes las
        # armaba el JS): así la pantalla puede llegar con el formulario ya
        # abierto desde "Crear producto" sin una línea de JavaScript.
        "categorias_planta": datos.CATEGORIAS_PLANTA,
        # /?tab=stock&crear=planta —el enlace "Planta" de Crear producto—
        # abre el formulario de siempre ya desplegado.
        "abrir_crear_planta": crear == "planta",
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
            # A dónde va la pantalla cuando se acaba de crear una planta, y
            # a dónde hay que devolver la planta nueva. Los DOS los calcula
            # Python y el JS solo los lee: `app.js` tenía el destino
            # escrito a mano, que es justo lo que la regla del proyecto no
            # permite (la decisión en Python, el navegador recibiendo el
            # resultado). Siempre vienen con valor —el de siempre cuando
            # nadie está esperando la planta— para que el JS no tenga que
            # elegir nada.
            "destinoTrasCrear": _destino_tras_crear_planta(volver),
            "volverTrasCrear": (volver if _vuelta_del_alta(volver) else ""),
        }, ensure_ascii=False),
    })


# A dónde vuelve la pantalla de Stock después de crear una planta. Sin nadie
# esperándola es la recarga de siempre (la planta nueva tiene que entrar a
# la lista con su stock); con un `volver` pendiente, a la pantalla que la
# pidió — hoy el formulario de compra nueva, con la planta ya agregada.
DESTINO_TRAS_CREAR_PLANTA = "/?refrescar=1&tab=stock&vista=global"


def _destino_tras_crear_planta(volver=""):
    if _vuelta_del_alta(volver) and volver == "compra":
        return "/compras?nueva=1" + compras.ANCLA_LINEAS
    return DESTINO_TRAS_CREAR_PLANTA


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

    `volver` dice quién está esperando esta planta (hoy el formulario de
    compra nueva, que mandó a crearla porque no apareció en su buscador):
    con él, la planta recién creada se agrega sola como línea de esa compra.
    El valor sale de la pantalla, que lo recibió de Python — no es una
    decisión del navegador.
    """
    cuerpo = await request.json()
    volver = (cuerpo.get("volver") or "").strip()
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
    # La planta ya está en Odoo. Si alguien la estaba esperando, se le
    # agrega ahí mismo: así el empleado vuelve y la encuentra puesta, sin
    # tener que buscarla otra vez. Va DESPUÉS del alta y del stock, y sin
    # poder tumbar la respuesta: la planta quedó creada y decir lo contrario
    # sería mentir.
    agregada_a = ""
    if volver == "compra" and _vuelta_del_alta(volver):
        try:
            _agregar_planta_a_la_compra(request, sku, nombre)
            agregada_a = "compra"
        except Exception as fallo:
            compras.registro_aviso(
                f"La planta {sku} se creó pero no se pudo agregar a la "
                f"compra en curso: {fallo!r}")
    return {"ok": True, "sku": sku, "nombre": nombre, "id": creada.get("id"),
            "cantidad": cantidad, "stock": stock, "agregadaA": agregada_a}


def _agregar_planta_a_la_compra(request, sku, nombre):
    """La planta recién creada, como línea de la compra que la pidió.

    El id de `product.product` se pide aparte (el alta por el order-api
    devuelve el del `product.template`, que no es el mismo) y si Odoo no
    contesta esa consulta **la línea se agrega igual** con su SKU y su
    nombre, que es lo durable.
    """
    producto = compras.producto_por_sku(sku)
    compras.agregar_al_borrador(
        request.state.empleada["id"],
        producto_id=(producto or {}).get("id"),
        sku=sku, nombre=(producto or {}).get("nombre") or nombre or sku)


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


@app.post("/ajustes/coworkers/agregar")
async def ajustes_coworker_agregar(request: Request):
    """Números de coworkers: chats internos (el jefe, el equipo) que el
    receptor de WhatsApp descarta para que no nazcan como leads. La lista
    vive en la base `tienda` del droplet (migración 018 del order-api)."""
    if (rechazo := _solo_admin(request)) is not None:
        return rechazo
    form = await request.form()
    numero = coworkers.normalizar(form.get("numero"))
    if not numero:
        return RedirectResponse("/?tab=ajustes&aviso=coworker-invalido",
                                status_code=303)
    try:
        coworkers.agregar(numero, (form.get("nota") or "").strip()[:60],
                          request.state.empleada["id"])
    except Exception:
        return RedirectResponse("/?tab=ajustes&aviso=coworker-error",
                                status_code=303)
    return RedirectResponse("/?tab=ajustes&aviso=coworker-agregado",
                            status_code=303)


@app.post("/ajustes/coworkers/quitar")
async def ajustes_coworker_quitar(request: Request):
    if (rechazo := _solo_admin(request)) is not None:
        return rechazo
    form = await request.form()
    numero = coworkers.normalizar(form.get("numero"))
    if numero:
        try:
            coworkers.quitar(numero)
        except Exception:
            return RedirectResponse("/?tab=ajustes&aviso=coworker-error",
                                    status_code=303)
    return RedirectResponse("/?tab=ajustes&aviso=coworker-quitado",
                            status_code=303)


@app.post("/ajustes/envio")
async def ajustes_envio(request: Request):
    """Los 4 precios de las opciones fijas de envío de Vender. Viven en la
    tabla config (claves envio_precio_*) para cambiarlos sin desplegar;
    Personalizado no tiene precio que guardar."""
    if (rechazo := _solo_admin(request)) is not None:
        return rechazo
    form = await request.form()
    nuevos = {}
    for opcion in ventas.OPCIONES_ENVIO:
        crudo = str(form.get(opcion["clave"]) or "").strip().replace(",", ".")
        try:
            precio = float(crudo)
        except ValueError:
            precio = 0.0
        if precio <= 0:
            return RedirectResponse("/?tab=ajustes&aviso=envio-invalido",
                                    status_code=303)
        nuevos[opcion["clave"]] = precio
    # Se guarda todo o nada: un formulario con un precio ilegible no deja
    # los otros tres a medias.
    for clave, precio in nuevos.items():
        datos.fijar_config(ventas.PREFIJO_PRECIO_ENVIO + clave, f"{precio:.2f}")
    return RedirectResponse("/?tab=ajustes&aviso=envio-guardado",
                            status_code=303)


@app.post("/ajustes/dispositivos/nombrar")
async def ajustes_dispositivo_nombrar(request: Request):
    """Le pone nombre al dispositivo que escribió por el WhatsApp del negocio.

    Es el mapeo entero: cada empleado manda UN mensaje desde su equipo, el
    dispositivo aparece aquí, y el dueño le escribe el nombre. Reasignable
    siempre — cuando alguien vuelve a vincular su computadora, WhatsApp le
    da un número nuevo y esto se arregla escribiendo, no desplegando.

    Un nombre vacío lo devuelve a «Equipo · dispositivo N»: nunca se inventa
    quién escribió.
    """
    if (rechazo := _solo_admin(request)) is not None:
        return rechazo
    form = await request.form()
    dispositivo = (form.get("dispositivo") or "").strip()
    if not dispositivo.isdigit():
        return RedirectResponse("/?tab=ajustes&aviso=dispositivo-invalido",
                                status_code=303)
    wa_autor.nombrar(dispositivo, form.get("nombre") or "")
    return RedirectResponse("/?tab=ajustes&aviso=dispositivo-guardado",
                            status_code=303)


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


def _redirigir_venta(error=None, nueva=False, conflicto=None, fiscal=None,
                     campo=""):
    destino = ("/venta/nueva" if nueva else "/venta") + \
        (f"?error={quote(error)}" if error else "")
    if error and campo:
        # Regla 5 (2/10/2026): el campo que falló viaja con el error, para
        # que el GET pinte el mensaje debajo de ese campo y aterrice ahí
        # (autofocus) en vez del banner en el tope.
        destino += f"&campo={quote(campo)}"
    if conflicto:
        # El aviso de cliente ajeno (B.2, por teléfono o por nombre): el
        # id, el nombre, el teléfono y el motivo viajan en la URL para que
        # /venta/nueva pinte las dos opciones (usar ese cliente o crear
        # uno nuevo). El id solo alimenta el value de usar-<id>; lo que la
        # empleada lee es el nombre con el teléfono (2/10/2026).
        destino += (f"&conflicto={conflicto['id']}"
                    f"&conflicto_nombre={quote(conflicto['nombre'])}"
                    f"&conflicto_telefono={quote(conflicto.get('telefono') or '')}"
                    f"&conflicto_motivo={conflicto['motivo']}")
    if fiscal:
        # La confirmación R2 (guardar un dato fiscal en un cliente
        # existente elegido a sabiendas): qué dato y en qué cliente.
        destino += (f"&fiscal={fiscal['id']}"
                    f"&fiscal_nombre={quote(fiscal['nombre'])}"
                    f"&fiscal_detalle={quote(fiscal['detalle'])}")
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
def venta(request: Request, error: str = "", lead: str = "",
          cliente: str = "", cel: str = ""):
    # La pestaña: el botón grande "+ Venta" (arriba de los servicios,
    # dueño 28/09/2026) y el historial local.
    usuario = request.state.empleada["id"]
    if lead:
        # "Cotizar en Vender" desde la ficha de Retail: queda anotado el
        # lead y la próxima cotización/venta de esta empleada nace
        # vinculada a él. Directo al formulario de Nueva venta con el
        # nombre y el celular del lead ya puestos (dueño, 23/09/2026:
        # "debería abrir automáticamente venta de lo que es y el form con
        # el nombre y número ya puestos"); los leads de Retail son ventas
        # de plantas, así que "lo que es" siempre es Nueva venta.
        ventas.poner_lead_pendiente(usuario, lead, cliente)
        ventas.guardar_borrador(usuario, cliente.strip()[:120],
                                cel.strip()[:30])
        return RedirectResponse("/venta/nueva", status_code=303)
    en_curso = 0
    if ventas.configurado():
        try:
            # Cuenta las plantas del catálogo Y los renglones libres de
            # "planta personalizada" (28/09/2026): las dos son "algo en
            # curso" de la misma venta.
            en_curso = (len(ventas.carrito_de(usuario)[0])
                       + len(ventas.renglones_planta_de(usuario)))
        except Exception:
            pass
    lista, aviso_lista = _lista_vender(request)
    return plantillas.TemplateResponse(request, "venta.html", {
        "lead_pendiente": ventas.lead_pendiente(usuario),
        # El menu de abajo muestra Fichas con la misma regla del principal.
        "puede_fichas": fichas.es_editora(request.state.empleada["id"]),
        "ventas_activo": ventas.configurado(),
        "error_venta": error or None,
        "en_curso": en_curso,
        "tipos_servicio": [(t, cotizaciones.etiqueta_para_cotizar(t))
                          for t in cotizaciones.ORDEN_TIPOS],
        "vender_lista": lista,
        "aviso_lista": aviso_lista,
    })


# ---------------------------------------------------------------------------
# La lista única de "Vender": ventas de plantas y cotizaciones de
# servicio mezcladas (dueño, 30/09/2026: "ponlo en orden de número, y no,
# si es de servicio o planta no importa, pon todo en una fila"). La
# decisión de QUÉ se pinta y en qué ORDEN vive aquí, en Python — la
# plantilla solo recorre esta lista ya armada (regla del proyecto).
# ---------------------------------------------------------------------------

_RE_NUMERO_ORDEN = re.compile(r"(\d+)\s*$")


def _numero_de_orden(orden):
    """El número dentro de "S00099" -> 99, para ordenar de mayor a menor.
    None si la orden no tiene ni un dígito al final (un renglón que
    todavía no llegó a Odoo, ej. un borrador local sin confirmar) — el
    llamador lo manda al fondo, nunca intercalado entre los que sí tienen
    número."""
    coincidencia = _RE_NUMERO_ORDEN.search(orden or "")
    return int(coincidencia.group(1)) if coincidencia else None


def _fila_venta(request, v):
    """Una venta de plantas, con "tipo" para que la plantilla sepa qué
    tarjeta pintar en la lista única."""
    return {
        **v, "tipo": "venta", "fecha_texto": _fecha_venta(v["creado_en"]),
        "etiqueta_estado": ventas.ETIQUETAS_ESTADO[v["estado"]],
        "whatsapp": _enlace_whatsapp(request, v),
        # (v["orden"] or ""): un renglón que todavía no llegó a Odoo (sin
        # número, ver _lista_vender) no puede tronar aquí con un
        # AttributeError sobre None.
        "nombre_cotizacion_pdf": ventas.nombre_de_pdf(
            (v["orden"] or "").replace("/", "-"), v["cliente"]),
        "nombre_factura_pdf": ventas.nombre_de_pdf(
            (v["factura"] or str(v["n"])).replace("/", "-"), v["cliente"]),
    }


def _lista_vender(request):
    """(filas, aviso): ventas locales + cotizaciones de servicio, en UNA
    sola lista, ordenada por número de orden de mayor a menor (la más
    nueva arriba) — y el aviso honesto si Odoo no contestó al armarla.

    Una CANCELADA no se pinta (dueño, 30/09/2026): el dato se queda
    intacto en Odoo (y en la tabla local, para una venta), solo deja de
    aparecer aquí. Un renglón sin número (todavía no llegó a Odoo) cae al
    fondo por construcción: `_numero_de_orden` devuelve None y la clave de
    orden lo trata como el más chico de todos, nunca intercalado."""
    servicios, aviso = _cotizaciones_con_estado()
    filas = (
        [_fila_venta(request, v) for v in ventas.ventas_todas()
         if v["estado"] != "cancelada"]
        + [{**c, "tipo": "servicio"} for c in servicios
           if not c["cancelada"]]
    )
    filas.sort(key=lambda f: (_numero_de_orden(f["orden"]) is not None,
                              _numero_de_orden(f["orden"]) or 0),
              reverse=True)
    return filas, aviso


def _cotizaciones_con_estado():
    """(filas, aviso): las cotizaciones locales con su estado REAL en Odoo
    (una sola consulta para todas): facturada, cancelada o todavía
    cotización, y de ahí si se puede editar. Si Odoo no contesta, la lista
    sale como siempre, sin botón Editar (mejor sin botón que un botón que
    rompe) — pero CON el aviso que lo dice: antes el `except` mudo dejaba
    todas las cotizaciones «raras», sin Editar ni Quitar y sin una palabra
    de por qué (2/10/2026)."""
    filas = cotizaciones.cotizaciones_todas()
    estados = {}
    aviso = ""
    try:
        estados = cotizaciones.estados_en_odoo([c["orden_id"] for c in filas])
    except Exception:
        if any(c.get("orden_id") for c in filas):
            aviso = ("Odoo no contesta en este momento: Editar y Quitar de "
                     "las cotizaciones no están disponibles por ahora. "
                     "Recarga en un rato.")
    resultado = []
    for c in filas:
        estado = estados.get(c["orden_id"])
        resultado.append({
            **c, "fecha_texto": _fecha_venta(c["creado_en"]),
            "etiqueta_tipo": cotizaciones.etiqueta_de(c["tipo"]),
            "facturada": bool(estado and estado["facturada"]),
            "cancelada": bool(estado and estado["cancelada"]),
            "editable": bool(estado and estado["editable"]),
            "nombre_pdf": ventas.nombre_de_pdf(c["orden"].replace("/", "-"), c["cliente"]),
        })
    return resultado, aviso


@app.post("/venta/lead/quitar")
async def venta_lead_quitar(request: Request):
    # "No es para este lead": la cotización que viene se crea suelta. Se
    # vuelve a la pantalla donde estaba el aviso (Vender o Nueva venta).
    ventas.quitar_lead_pendiente(request.state.empleada["id"])
    form = await request.form()
    destino = "/venta/nueva" if form.get("volver") == "nueva" else "/venta"
    return RedirectResponse(destino, status_code=303)


@app.post("/venta/cancelar/{n}")
def venta_cancelar(request: Request, n: int):
    try:
        ventas.cancelar(n)
    except Exception as error:
        return RedirectResponse(
            "/venta?error=" + quote(f"No se pudo cancelar: {error}"),
            status_code=303)
    return RedirectResponse("/venta", status_code=303)


@app.post("/venta/servicio/{n}/cancelar")
def venta_servicio_cancelar(request: Request, n: int):
    # "Quitar" en una cotización de servicio (dueño, 30/09/2026): mismo
    # efecto que Cancelar en una venta de planta, reusando el mismo
    # camino a Odoo (cotizaciones.cancelar -> ventas._cancelar_en_odoo).
    try:
        cotizaciones.cancelar(n)
    except Exception as error:
        return RedirectResponse(
            "/venta?error=" + quote(f"No se pudo quitar: {error}"),
            status_code=303)
    return RedirectResponse("/venta", status_code=303)


def _resultados_con_stock(resultados):
    """El stock de cada resultado del buscador de plantas, leído de donde
    ya lo lee Stock (`datos.obtener_inventario`, mismo caché y TTL — no se
    inventa otra fuente): así la empleada ve si alcanza antes de vender.
    `disponible` sale en None por producto si el inventario no contestó
    ahora — "no hay" nunca se confunde con "no sé" (regla del proyecto)."""
    if not resultados:
        return resultados
    try:
        inventario, _leido_en = datos.obtener_inventario()
    except Exception:
        return [{**r, "disponible": None} for r in resultados]
    stock = {p["sku"]: p["disponible"] for p in inventario}
    return [{**r, "disponible": stock.get(r["sku"])} for r in resultados]


@app.get("/venta/nueva")
def venta_nueva(request: Request, q: str = "", error: str = "", campo: str = "",
                conflicto: str = "", conflicto_nombre: str = "",
                conflicto_telefono: str = "",
                conflicto_motivo: str = "", fiscal: str = "",
                fiscal_nombre: str = "", fiscal_detalle: str = ""):
    # El formulario de la venta: cliente (nombre y celular), buscador en
    # vivo para añadir plantas, la lista con cantidades y el total.
    usuario = request.state.empleada["id"]
    contexto = {
        "ventas_activo": ventas.configurado(), "q": q.strip(),
        "resultados": None, "carrito": [], "total_carrito": 0.0,
        "renglones_planta": [], "total_renglones_planta": 0.0,
        # None = todavía no se sabe (Odoo no contestó): la plantilla no
        # debe leerlo como "no disponible" y apagar el formulario por las
        # puras. Ver `personalizada_activa` en venta_nueva.html.
        "personalizada_activa": None,
        "borrador": ventas.borrador_de(usuario),
        # El aviso "quedará amarrada al lead X" también se ve aquí: llegar
        # desde Retail aterriza directo en este formulario (23/09/2026).
        "lead_pendiente": ventas.lead_pendiente(usuario),
        "error_venta": error or None,
        # Regla 5: el campo que falló (viajó en el redirect del POST); la
        # plantilla pinta el error debajo de él y lo enfoca.
        "campo_error": (campo or "").strip()[:60] if error else "",
        # El aviso B.2 tras el redirect de un ClienteAjeno: con esto el
        # bloque Cliente pinta las dos opciones (usar ese cliente o crear
        # uno nuevo) y el POST siguiente viaja con cliente_decision.
        "conflicto_cliente": ({"id": int(conflicto),
                               "nombre": conflicto_nombre.strip()[:120],
                               "telefono": conflicto_telefono.strip()[:30],
                               "motivo": ("nombre" if conflicto_motivo == "nombre"
                                          else "telefono")}
                              if conflicto.isdigit() else None),
        # La confirmación R2 tras el redirect de un ConfirmarDatoFiscal.
        "confirmar_fiscal": ({"id": int(fiscal),
                              "nombre": fiscal_nombre.strip()[:120],
                              "detalle": fiscal_detalle.strip()[:400]}
                             if fiscal.isdigit() else None),
    }
    if contexto["ventas_activo"]:
        try:
            if contexto["q"]:
                contexto["resultados"] = _resultados_con_stock(
                    ventas.buscar_productos(contexto["q"]))
            contexto["carrito"], contexto["total_carrito"] = ventas.carrito_de(usuario)
            contexto["renglones_planta"] = ventas.renglones_planta_de(usuario)
            contexto["total_renglones_planta"] = round(
                sum(r["importe"] for r in contexto["renglones_planta"]), 2)
            contexto["personalizada_activa"] = (
                ventas.id_producto_personalizada_planta() is not None)
        except Exception:
            contexto["error_venta"] = ("Sin conexión con Odoo en este momento. "
                                       "Vuelve a intentar en un rato.")
    # El desglose del total (plantas + renglones libres + envío +
    # instalación) sale pintado del servidor con lo que diga el borrador;
    # venta.js solo lo refresca mientras se escribe. El total real lo
    # confirma Odoo al crear.
    contexto["leads_crm"] = _leads_para_elegir()
    contexto["cargos_montos"] = _cargos_del_form(contexto["borrador"],
                                                 avisar=False)
    # La casilla del PDF (garantía): en venta normal nace DESMARCADA.
    contexto["casillas"] = ventas.banderas_de(contexto["borrador"], False)
    contexto["total_con_cargos"] = (
        contexto["total_carrito"] + contexto["total_renglones_planta"]
        + sum(v for v in contexto["cargos_montos"].values()
              if isinstance(v, (int, float))))
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
    # búsqueda server-rendered, en JSON, con el precio ya formateado y el
    # stock (28/09/2026) para que la búsqueda en vivo y la de recarga de
    # página digan lo mismo.
    try:
        resultados = _resultados_con_stock(ventas.buscar_productos(q))
    except Exception:
        return {"error": "Sin conexión con Odoo en este momento."}
    # "precio_num" (30/09/2026): el buscador de la pantalla de editar arma
    # la fila de la planta en el navegador (no hay a dónde hacer un POST
    # con carrito, esa pantalla edita una orden ya existente) y necesita el
    # número crudo, no el "$3.50" ya formateado para mostrar.
    return {"resultados": [{**p, "precio": dinero_venta(p["precio"]),
                            "precio_num": p["precio"]} for p in resultados]}


def _datos_cliente_del_form(form):
    """Los datos opcionales del formulario —los del cliente (RUC, cédula,
    correo, dirección) y los cargos (envío, instalación)— si el formulario
    los trae; None si no, para no borrar lo ya guardado en el borrador."""
    if not any(campo in form for campo in ventas.CAMPOS_EXTRA):
        return None
    return {campo: (form.get(campo) or "").strip()[:120]
            for campo in ventas.CAMPOS_EXTRA}


def _decision_cliente_del_form(form):
    """La elección de la empleada tras el aviso de «ese teléfono ya es de
    otro cliente» (B.2): "usar-<id>" o "nuevo". Cualquier otra cosa cuenta
    como sin decidir — el aviso vuelve a salir, nunca se adivina."""
    decision = (form.get("cliente_decision") or "").strip()
    return decision if re.fullmatch(r"nuevo|usar-\d+", decision) else None


def _confirmar_fiscal_del_form(form):
    """La confirmación R2 («sí, guarda ese dato en ese cliente»): "si" o
    "no". Cualquier otra cosa cuenta como sin confirmar — el aviso vuelve
    a salir, nunca se asume."""
    valor = (form.get("confirmar_fiscal") or "").strip()
    return valor if valor in ("si", "no") else None


def _conflicto_de(error):
    """El dict que pinta el aviso con las dos opciones en _cliente.html.
    El teléfono acompaña al nombre en el texto; el id solo arma el value
    de usar-<id> (2/10/2026: el empleado no lee ids)."""
    return {"id": error.partner_id, "nombre": error.nombre_existente,
            "telefono": error.telefono, "motivo": error.motivo}


def _fiscal_de(error):
    """El dict que pinta la confirmación R2 en _cliente.html. El detalle
    ya trae nombre y teléfono (lo arma ConfirmarDatoFiscal)."""
    return {"id": error.partner_id, "nombre": error.nombre_existente,
            "telefono": error.telefono, "detalle": error.detalle}


def _leads_para_elegir():
    """Los leads del embudo para el selector de Nueva venta (dueño,
    23/09/2026: "pon la opción de elegir una tarjeta").

    Salen del equipo LEAD de Linear —la única fuente del estado— y no del
    kanban Retail, que murió en la Fase 5. Cambio de alcance a favor: antes
    solo ofrecía los leads retail/mayorista; ahora ofrece todos los que
    siguen en juego, así que una venta de un lead de servicio también se
    puede amarrar. Los cerrados (Ganado, Perdido) no se ofrecen: a un lead
    cerrado no se le hace una venta nueva.

    Vacío si Linear no responde — el selector no ofrece nada, y la pantalla
    nunca espera (regla de pestañas rápidas, 22/09/2026).
    """
    try:
        leads = linear_leads.listar()
    except linear_leads.ErrorLeads:
        return []
    filas = []
    for lead in leads:
        if lead["estado"] in linear_leads.CERRADOS:
            continue
        filas.append({"ref": lead["ref"], "nombre": lead["nombre"],
                      "cel": lead.get("celular") or "",
                      "etapa": lead["estado_nombre"]})
    return filas


def _amarrar_lead_del_form(request, form):
    """La tarjeta de Retail elegida en el formulario manda: se vuelve el
    lead pendiente (la venta que viene nace amarrada a ella). "Ninguna"
    suelta el amarre que hubiera. Formularios sin el selector no tocan
    nada."""
    if "lead_ref" not in form:
        return
    usuario = request.state.empleada["id"]
    lead_ref = (form.get("lead_ref") or "").strip()[:40]
    if lead_ref:
        ventas.poner_lead_pendiente(usuario, lead_ref,
                                    (form.get("cliente") or "").strip())
    else:
        ventas.quitar_lead_pendiente(usuario)


def _cargos_del_form(form, avisar=True):
    """Los cargos opcionales (envío, instalación, mantenimiento): el monto
    de cada uno y, en "<clave>_desc", el párrafo que se imprime debajo.
    Vacío cuenta como 0: son opcionales, no motivo de error. Pero un
    TEXTO o un NEGATIVO se rechazan con ValueError, que las rutas POST ya
    muestran en pantalla — antes caían a $0 en silencio y la cotización
    salía sin el cargo que se quiso cobrar (2/10/2026). `avisar=False` es
    solo para PINTAR una pantalla desde el borrador guardado (ahí no se
    guarda nada y reventar el render dejaría al empleado sin formulario
    que corregir): lo inválido se muestra como 0 y el rechazo de verdad
    llega al dar el botón. Un párrafo vacío NO se guarda: así el renglón
    sale con el de fábrica. El envío ya no es un monto suelto: es la
    opción elegida (radios) y ventas.resolver_envio decide el monto, la
    opción y la nota."""
    cargos = {}
    for cargo in ventas.CARGOS:
        if cargo["clave"] == "envio":
            try:
                cargos.update(ventas.resolver_envio(form))
            except ValueError:
                if avisar:
                    raise
                cargos["envio"] = 0.0
        else:
            crudo = str(form.get(cargo["clave"]) or "").strip().replace(",", ".")
            if not crudo:
                valor = 0.0
            else:
                try:
                    valor = float(crudo)
                except ValueError:
                    valor = None
                if valor is not None and valor < 0:
                    valor = None
                if valor is None:
                    if avisar:
                        raise ventas.ErrorDeCampo(
                            f"El monto de «{cargo['nombre']}» no se "
                            "entiende: escribe un número, como 12.50 o "
                            "12,50 (o déjalo vacío para no cobrarlo).",
                            cargo["clave"])
                    valor = 0.0
            cargos[cargo["clave"]] = valor
        parrafo = str(form.get(cargo["clave"] + "_desc") or "").strip()[:600]
        if parrafo:
            cargos[cargo["clave"] + "_desc"] = parrafo
    return cargos


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

    La búsqueda ya NO viaja (pedido del dueño, 22/09/2026):
    al elegir una planta el buscador queda limpio. Y la vuelta lleva el
    ancla #plantas, para quedar en la lista de plantas en vez de saltar al
    tope de la página."""
    destino = (form.get("volver") or "").strip()
    if destino != "/venta/servicio-personalizada" \
            and not destino.startswith("/venta/servicio/"):
        destino = "/venta/nueva"
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


@app.post("/venta/carrito/precio")
async def venta_precio(request: Request):
    """El precio unitario escrito a mano en una línea del carrito (pedido
    del dueño, 23/09/2026: "por si acaso le vendo más caro"). Vacío —o el
    botón "precio de Odoo"— devuelve la línea a la lista de precios."""
    form = await request.form()
    precio = "" if form.get("odoo") else form.get("precio", "")
    try:
        ventas.cambiar_precio(request.state.empleada["id"],
                              int(form.get("producto_id", "")), precio[:20])
    except (TypeError, ValueError):
        pass
    return RedirectResponse(_volver_del_carrito(form), status_code=303)


@app.post("/venta/cobro")
async def venta_cobro(request: Request):
    """El selector "Cómo se cobra" del Alquiler (23/09/2026). Se guarda en
    el borrador del servidor: agregar una planta recarga la pantalla y el
    modo tiene que seguir donde estaba."""
    form = await request.form()
    ventas.guardar_cobro(request.state.empleada["id"], form.get("cobro", ""))
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


# Los renglones libres de "planta personalizada" (dueño, 28/09/2026): un
# renglón por vez, como el carrito de plantas del catálogo, pero sin
# producto_id — solo vive en Nueva Venta, así que el destino es siempre
# esa pantalla (a diferencia de _volver_del_carrito, que reparte entre
# varias). agregar_renglon_planta valida y avisa con un ValueError claro
# (nombre sin letras, cantidad o precio ilegibles); nunca revienta.

@app.post("/venta/renglon-planta/agregar")
async def venta_renglon_planta_agregar(request: Request):
    form = await request.form()
    try:
        ventas.agregar_renglon_planta(
            request.state.empleada["id"], form.get("texto", ""),
            form.get("cantidad", ""), form.get("precio", ""))
    except ValueError as error:
        return _redirigir_venta(str(error), nueva=True)
    return RedirectResponse("/venta/nueva#personalizada", status_code=303)


@app.post("/venta/renglon-planta/quitar")
async def venta_renglon_planta_quitar(request: Request):
    form = await request.form()
    try:
        ventas.quitar_renglon_planta(request.state.empleada["id"],
                                     int(form.get("n", "")))
    except (TypeError, ValueError):
        pass
    return RedirectResponse("/venta/nueva#personalizada", status_code=303)


def _ruta_vista_previa(usuario):
    """El PDF del vistazo, uno por empleada. El usuario es su nombre de
    acceso: se limpia antes de usarlo como nombre de archivo."""
    seguro = re.sub(r"[^A-Za-z0-9_-]", "", str(usuario)) or "empleada"
    return os.path.join(datos.ruta_archivos(), f"vista-previa-{seguro}.pdf")


@app.post("/venta/vista-previa")
async def venta_vista_previa(request: Request):
    """Ver el PDF ANTES de generar la cotización (dueño, 23/09/2026). No
    crea la venta: arma el documento con lo que hay en pantalla, lo deja
    en la carpeta de archivos y lo muestra con un botón para salir."""
    form = await request.form()
    empleada = request.state.empleada
    datos_cliente = _datos_cliente_del_form(form)
    # Lo escrito se guarda antes de irse a Odoo: salir de la vista previa
    # tiene que devolver el formulario tal cual estaba.
    ventas.guardar_borrador(empleada["id"],
                            (form.get("cliente") or "").strip()[:120],
                            (form.get("celular") or "").strip()[:30],
                            None, datos_cliente, None)
    _amarrar_lead_del_form(request, form)
    try:
        pdf = ventas.pdf_vista_previa(
            empleada, form.get("cliente", ""), form.get("celular", ""),
            datos_cliente, _cargos_del_form(form),
            banderas=ventas.banderas_de(form, False),
            decision_cliente=_decision_cliente_del_form(form),
            confirmar_fiscal=_confirmar_fiscal_del_form(form))
    except ventas.ClienteAjeno as error:
        return _redirigir_venta(str(error), nueva=True,
                                conflicto=_conflicto_de(error))
    except ventas.ConfirmarDatoFiscal as error:
        return _redirigir_venta(str(error), nueva=True,
                                fiscal=_fiscal_de(error))
    except ValueError as error:
        return _redirigir_venta(str(error), nueva=True)
    except Exception as error:
        return _redirigir_venta(
            f"No se pudo armar la vista previa: {ventas._mensaje_de_error(error)}",
            nueva=True)
    with open(_ruta_vista_previa(empleada["id"]), "wb") as archivo:
        archivo.write(pdf)
    return plantillas.TemplateResponse(request, "venta_vista_previa.html", {
        "empleada": empleada,
        "borrador": ventas.borrador_de(empleada["id"]),
        "lead_pendiente": ventas.lead_pendiente(empleada["id"]),
        "campos_extra": ventas.CAMPOS_EXTRA,
    })


@app.get("/venta/vista-previa.pdf")
def venta_vista_previa_pdf(request: Request):
    """El PDF de la vista previa, ADENTRO de la pantalla (inline, no
    descarga): en el celular una ventana nueva es justo lo que deja al
    empleado sin poder salir."""
    ruta = _ruta_vista_previa(request.state.empleada["id"])
    if not os.path.exists(ruta):
        return RedirectResponse("/venta/nueva", status_code=303)
    with open(ruta, "rb") as archivo:
        return Response(archivo.read(), media_type="application/pdf", headers={
            "Content-Disposition": 'inline; filename="vista-previa.pdf"',
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        })


@app.post("/venta/cotizar")
async def venta_cotizar(request: Request):
    form = await request.form()
    _amarrar_lead_del_form(request, form)
    try:
        registro = ventas.crear_cotizacion(
            request.state.empleada, form.get("cliente", ""), form.get("celular", ""),
            _datos_cliente_del_form(form), _cargos_del_form(form),
            banderas=ventas.banderas_de(form, False),
            decision_cliente=_decision_cliente_del_form(form),
            confirmar_fiscal=_confirmar_fiscal_del_form(form))
    except ventas.ClienteAjeno as error:
        return _redirigir_venta(str(error), nueva=True,
                                conflicto=_conflicto_de(error))
    except ventas.ConfirmarDatoFiscal as error:
        return _redirigir_venta(str(error), nueva=True,
                                fiscal=_fiscal_de(error))
    except ventas.ErrorDeCampo as error:
        return _redirigir_venta(str(error), nueva=True, campo=error.campo)
    except ValueError as error:
        return _redirigir_venta(str(error), nueva=True)
    except Exception as error:
        return _redirigir_venta(f"Odoo no aceptó la cotización: {ventas._mensaje_de_error(error)}",
                                nueva=True)
    return plantillas.TemplateResponse(request, "venta_exito.html", {
        "titulo": "Cotización creada",
        "sub": f"{registro['orden']} · {registro['cliente']}",
        # El aviso honesto del amarre perdido (Nº5, 2/10/2026): la venta
        # salió igual, pero si venía de una ficha y Linear no contestó, acá
        # se dice — antes nadie se enteraba.
        "aviso": registro.get("aviso_lead") or "",
        "filas": [("Total", dinero_venta(registro["total"]), None),
                  ("Estado", "Cotización (borrador en Odoo)", "dorado")],
        "pdf_href": f"/venta/{registro['n']}/cotizacion.pdf",
        "pdf_texto": "Descargar / Compartir (PDF)",
        "pdf_nombre": ventas.nombre_de_pdf(registro["orden"].replace("/", "-"),
                                           registro["cliente"]),
    })


@app.post("/venta/vender")
async def venta_vender(request: Request):
    """«Guardar venta» (dueño, 28/09/2026): la MISMA orden que la
    cotización, con action_confirm encima. El pago no lo cobra la app: de
    aquí la orden entra sola al kanban de cobro de Odoo (etapa_cobro),
    igual que las cotizaciones de servicio — no hay pantalla de cobro
    nueva ni se toca la de Facturar/Reintentar de las cotizaciones."""
    form = await request.form()
    _amarrar_lead_del_form(request, form)
    try:
        registro = ventas.crear_cotizacion(
            request.state.empleada, form.get("cliente", ""), form.get("celular", ""),
            _datos_cliente_del_form(form), _cargos_del_form(form), confirmar=True,
            banderas=ventas.banderas_de(form, False),
            decision_cliente=_decision_cliente_del_form(form),
            confirmar_fiscal=_confirmar_fiscal_del_form(form))
    except ventas.ClienteAjeno as error:
        return _redirigir_venta(str(error), nueva=True,
                                conflicto=_conflicto_de(error))
    except ventas.ConfirmarDatoFiscal as error:
        return _redirigir_venta(str(error), nueva=True,
                                fiscal=_fiscal_de(error))
    except ventas.ErrorDeCampo as error:
        return _redirigir_venta(str(error), nueva=True, campo=error.campo)
    except ValueError as error:
        return _redirigir_venta(str(error), nueva=True)
    except Exception as error:
        return _redirigir_venta(f"Odoo no aceptó la venta: {ventas._mensaje_de_error(error)}",
                                nueva=True)
    return plantillas.TemplateResponse(request, "venta_exito.html", {
        "titulo": "Venta confirmada",
        "sub": f"{registro['orden']} · {registro['cliente']}",
        "aviso": registro.get("aviso_lead") or "",
        "filas": [("Total", dinero_venta(registro["total"]), None),
                  ("Estado", "Confirmada · el cobro se registra en Odoo", "dorado")],
        "pdf_href": f"/venta/{registro['n']}/cotizacion.pdf",
        "pdf_texto": "Descargar / Compartir (PDF)",
        "pdf_nombre": ventas.nombre_de_pdf(registro["orden"].replace("/", "-"),
                                           registro["cliente"]),
    })


# ---------------------------------------------------------------------------
# Cotizaciones de servicio (Alquiler, Boda, Mantenimiento…): botones por
# tipo junto a "+ Venta", cada uno con su mini-formulario. Reusan el mismo
# carrito de plantas de Nueva Venta (app/ventas.py) para los tipos que
# llevan catálogo — es el mismo carrito por empleada, así que solo debe
# haber un formulario en curso a la vez (igual que hoy con Nueva Venta).
# ---------------------------------------------------------------------------

def _contexto_servicio(request, tipo, q="", error=None, servicios=None):
    """El contexto del mini-formulario de un tipo. Lo comparten el GET y el
    POST que no pudo crear la cotización: así un error no borra los
    párrafos de servicio que la empleada ya escribió."""
    usuario = request.state.empleada["id"]
    borrador = ventas.borrador_de(usuario)
    if servicios is None:
        servicios = borrador["servicios"]
    # El Alquiler elige cómo se cobra (total del evento o precio de
    # alquiler por planta); los demás tipos no muestran el selector.
    cobro = borrador["cobro"]
    por_planta = cotizaciones.cobra_por_planta(tipo, cobro)
    contexto = {
        "ventas_activo": ventas.configurado(), "tipo": tipo,
        "meta": cotizaciones.TIPOS[tipo], "q": (q or "").strip(),
        "cobro": cobro, "por_planta": por_planta,
        "resultados": None, "carrito": [], "total_carrito": 0.0,
        "borrador": borrador, "servicios": servicios or [{"texto": "", "monto": "", "descripcion": ""}],
        "error_venta": error or None,
    }
    if contexto["ventas_activo"]:
        try:
            if contexto["q"]:
                contexto["resultados"] = ventas.buscar_productos(contexto["q"])
            contexto["carrito"], contexto["total_carrito"] = ventas.carrito_de(
                usuario, base_cero=por_planta)
        except Exception:
            contexto["error_venta"] = ("Sin conexión con Odoo en este momento. "
                                       "Vuelve a intentar en un rato.")
    return contexto


@app.get("/venta/servicio/{tipo}")
def venta_servicio(request: Request, tipo: str, q: str = "", error: str = ""):
    if tipo not in cotizaciones.TIPOS:
        return RedirectResponse("/venta", status_code=303)
    if cotizaciones.TIPOS[tipo].get("retirado"):
        # Boda y evento se absorbieron en «Alquiler / Eventos»: un enlace
        # viejo aterriza en el formulario unificado, no en un 404.
        return RedirectResponse("/venta/servicio/renta", status_code=303)
    return plantillas.TemplateResponse(
        request, "venta_servicio.html",
        _contexto_servicio(request, tipo, q, error))


@app.post("/venta/servicio/{tipo}")
async def venta_servicio_crear(request: Request, tipo: str):
    if tipo not in cotizaciones.TIPOS             or cotizaciones.TIPOS[tipo].get("retirado"):
        return RedirectResponse("/venta", status_code=303)
    form = await request.form()
    usuario = request.state.empleada["id"]
    servicios = cotizaciones.servicios_del_formulario(
        form.getlist("servicio_texto"), form.getlist("servicio_monto"),
        form.getlist("servicio_descripcion"))
    datos_cliente = _datos_cliente_del_form(form)
    cobro = ventas.borrador_de(usuario)["cobro"]
    por_planta = cotizaciones.cobra_por_planta(tipo, cobro)
    carrito, _total = ventas.carrito_de(usuario, base_cero=por_planta)
    # "precio" solo cuando se escribió a mano: si no, lo pone Odoo. En un
    # alquiler por planta viaja siempre, incluso el $0 de la que no lleva
    # precio: ahí no hay precio de lista que valga.
    lineas_catalogo = [{"producto_id": l["producto_id"], "cantidad": l["cantidad"],
                        "precio": l["precio"] if (l["precio_editado"] or por_planta)
                        else None}
                       for l in carrito]
    try:
        registro = cotizaciones.crear_cotizacion(
            request.state.empleada, tipo, form.get("cliente", ""),
            form.get("celular", ""), servicios, lineas_catalogo, datos_cliente,
            cargos=_cargos_del_form(form), cobro=cobro,
            decision_cliente=_decision_cliente_del_form(form),
            confirmar_fiscal=_confirmar_fiscal_del_form(form))
    except ventas.ClienteAjeno as error:
        contexto = _contexto_servicio(request, tipo, error=str(error),
                                      servicios=servicios)
        contexto["conflicto_cliente"] = _conflicto_de(error)
        return plantillas.TemplateResponse(
            request, "venta_servicio.html", contexto, status_code=200)
    except ventas.ConfirmarDatoFiscal as error:
        contexto = _contexto_servicio(request, tipo, error=str(error),
                                      servicios=servicios)
        contexto["confirmar_fiscal"] = _fiscal_de(error)
        return plantillas.TemplateResponse(
            request, "venta_servicio.html", contexto, status_code=200)
    except ventas.ErrorDeCampo as error:
        # Regla 5: el mensaje sale DEBAJO del campo que falló y la
        # pantalla aterriza ahí (autofocus en la plantilla); lo escrito
        # se conserva porque el contexto repinta los mismos servicios.
        contexto = _contexto_servicio(request, tipo, error=str(error),
                                      servicios=servicios)
        contexto["campo_error"] = error.campo
        return plantillas.TemplateResponse(
            request, "venta_servicio.html", contexto, status_code=200)
    except ValueError as error:
        return plantillas.TemplateResponse(
            request, "venta_servicio.html",
            _contexto_servicio(request, tipo, error=str(error),
                               servicios=servicios),
            status_code=200)
    except Exception as error:
        return plantillas.TemplateResponse(
            request, "venta_servicio.html",
            _contexto_servicio(
                request, tipo,
                error=f"Odoo no aceptó la cotización: {ventas._mensaje_de_error(error)}",
                servicios=servicios),
            status_code=200)
    ventas.vaciar_carrito(usuario)
    ventas._limpiar_borrador(usuario)
    filas = [("Tipo", cotizaciones.etiqueta_de(tipo), None),
             ("Total", dinero_venta(registro["total"]), None),
             ("Estado", "Cotización (borrador en Odoo)", "dorado")]
    return plantillas.TemplateResponse(request, "venta_exito.html", {
        "titulo": "Cotización de servicio creada",
        "sub": f"{registro['orden']} · {registro['cliente']}",
        "filas": filas,
        "pdf_href": f"/venta/servicio/{registro['n']}/propuesta.pdf",
        "pdf_texto": "Descargar / Compartir (PDF)",
        "pdf_nombre": ventas.nombre_de_pdf(registro["orden"].replace("/", "-"),
                                           registro["cliente"]),
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
        # La casilla del PDF (garantía): en el personalizado nace MARCADA.
        "casillas": ventas.banderas_de(borrador, True),
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
    # "precio" solo cuando se escribió a mano: si no, lo pone Odoo.
    lineas_catalogo = [{"producto_id": l["producto_id"], "cantidad": l["cantidad"],
                        "precio": l["precio"] if l["precio_editado"] else None}
                       for l in carrito]
    try:
        registro = cotizaciones.crear_personalizada(
            request.state.empleada, form.get("cliente", ""),
            form.get("celular", ""), lineas_catalogo, renglones,
            _datos_cliente_del_form(form), servicios,
            cargos=_cargos_del_form(form),
            banderas=ventas.banderas_de(form, True),
            decision_cliente=_decision_cliente_del_form(form),
            confirmar_fiscal=_confirmar_fiscal_del_form(form))
    except ventas.ClienteAjeno as error:
        contexto = _contexto_personalizada(request, error=str(error),
                                           renglones=renglones,
                                           servicios=servicios)
        contexto["conflicto_cliente"] = _conflicto_de(error)
        return plantillas.TemplateResponse(
            request, "venta_personalizada.html", contexto)
    except ventas.ConfirmarDatoFiscal as error:
        contexto = _contexto_personalizada(request, error=str(error),
                                           renglones=renglones,
                                           servicios=servicios)
        contexto["confirmar_fiscal"] = _fiscal_de(error)
        return plantillas.TemplateResponse(
            request, "venta_personalizada.html", contexto)
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
        "pdf_texto": "Descargar / Compartir (PDF)",
        "pdf_nombre": ventas.nombre_de_pdf(registro["orden"].replace("/", "-"),
                                           registro["cliente"]),
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


def _flot(valor):
    """Un campo del formulario o de cargar_para_editar (string, puede venir
    vacío) a float; nunca revienta, nunca None — para sumar subtotales."""
    try:
        texto = str(valor or "").strip().replace(",", ".")
        return float(texto) if texto else 0.0
    except (TypeError, ValueError):
        return 0.0


def _contexto_editar(request, datos_edicion, error=None):
    """El contexto de la pantalla de editar (30/09/2026, rediseño de dos
    columnas): además de los datos ya recuperados de Odoo, calcula en
    Python —nunca en la plantilla ni en JS— los subtotales y el total con
    los que arranca pintada la cuenta de la derecha, y si cada plegable
    (Envío, Instalación y mantenimiento, El PDF) nace abierto porque ya
    trae algo distinto del silencio/default. El total EN VIVO, mientras la
    empleada edita, lo recalcula venta.js con estos mismos números como
    punto de partida."""
    registro = datos_edicion["registro"]
    servicios = datos_edicion["servicios"]
    renglones = datos_edicion["renglones"]
    cargos = datos_edicion.get("cargos") or {}
    es_personalizada = registro["tipo"] not in cotizaciones.TIPOS
    casillas = (datos_edicion.get("banderas") or {"con_garantia": True})
    precio_editable = datos_edicion.get("precio_editable", True)

    # El subtotal de cada planta se calcula acá, no en la plantilla (nada
    # de aritmética en Jinja): sin precio editable (cobro por total) Odoo
    # la deja en $0 igual, así que el subtotal también es $0.
    plantas = [{**p, "subtotal": (_flot(p.get("cantidad")) * _flot(p.get("precio"))
                                  if precio_editable else 0.0)}
              for p in datos_edicion["plantas"]]

    subtotal_plantas = sum(p["subtotal"] for p in plantas)
    subtotal_servicios = sum(_flot(s.get("monto")) for s in servicios)
    subtotal_renglones = (sum(_flot(r.get("cantidad")) * _flot(r.get("precio"))
                              for r in renglones) if es_personalizada else 0.0)
    monto_envio = _flot(cargos.get("envio"))
    monto_instalacion = _flot(cargos.get("instalacion"))
    monto_mantenimiento = _flot(cargos.get("mantenimiento"))
    total_inicial = (subtotal_plantas + subtotal_servicios + subtotal_renglones
                     + monto_envio + monto_instalacion + monto_mantenimiento)

    opcion = ventas.opcion_envio(cargos.get("envio_opcion") or "")
    if opcion:
        resumen_envio = f"{opcion['vehiculo_pantalla']} · {opcion['zona']}"
    elif (cargos.get("envio_opcion") or "") == "personalizado":
        resumen_envio = "Personalizado"
    else:
        resumen_envio = "Sin envío"

    partes_cargos = []
    if monto_instalacion > 0:
        partes_cargos.append("instalación")
    if monto_mantenimiento > 0:
        partes_cargos.append("mantenimiento")
    resumen_cargos = (" y ".join(partes_cargos).capitalize()
                      if partes_cargos else "sin cobrar")

    resumen_pdf = "con garantía" if casillas["con_garantia"] else "sin garantía"

    return {
        "puede_fichas": fichas.es_editora(request.state.empleada["id"]),
        "registro": registro,
        "es_personalizada": es_personalizada,
        "etiqueta_tipo": cotizaciones.etiqueta_de(registro["tipo"]),
        "servicios": servicios,
        "plantas": plantas,
        # Sin este dato la plantilla trata "precio_editable" como
        # indefinida (falsa en Jinja2) y la casilla del precio nunca se
        # dibuja: lo escrito a mano se pierde al guardar (30/09/2026).
        "precio_editable": datos_edicion.get("precio_editable", True),
        "plantas_permitidas": datos_edicion.get("plantas_permitidas", True),
        "renglones": renglones,
        "cargos": cargos,
        # Al editar, las casillas quedan como se guardaron en la orden.
        "casillas": casillas,
        "error_venta": error or None,
        # La cuenta de la derecha, calculada en Python para la carga
        # inicial (30/09/2026): venta.js la recalcula en vivo desde aquí.
        "subtotal_plantas": subtotal_plantas,
        "subtotal_servicios": subtotal_servicios,
        "subtotal_renglones": subtotal_renglones,
        "subtotal_cargos": monto_envio + monto_instalacion + monto_mantenimiento,
        "total_inicial": total_inicial,
        "resumen_envio": resumen_envio,
        "resumen_cargos": resumen_cargos,
        "resumen_pdf": resumen_pdf,
        "abrir_envio": bool((cargos.get("envio_opcion") or "").strip()) or monto_envio > 0,
        "abrir_cargos": monto_instalacion > 0 or monto_mantenimiento > 0,
        "abrir_pdf": not casillas["con_garantia"],
    }


@app.post("/venta/servicio/{n}/editar")
async def venta_servicio_editar_guardar(request: Request, n: int):
    form = await request.form()
    servicios = cotizaciones.servicios_del_formulario(
        [t[:2000] for t in form.getlist("servicio_texto")],
        [m[:20] for m in form.getlist("servicio_monto")],
        [d[:2000] for d in form.getlist("servicio_descripcion")])
    plantas = cotizaciones.plantas_del_formulario(
        form.getlist("planta_id"), form.getlist("planta_cantidad"),
        [p[:20] for p in form.getlist("planta_precio")])
    renglones = cotizaciones.renglones_del_formulario(
        [t[:2000] for t in form.getlist("renglon_texto")],
        [c[:20] for c in form.getlist("renglon_cantidad")],
        [p[:20] for p in form.getlist("renglon_precio")],
        [d[:2000] for d in form.getlist("renglon_descripcion")])
    try:
        cotizaciones.editar_cotizacion(
            n, servicios, plantas, renglones, cargos=_cargos_del_form(form),
            # Solo el formulario del personalizado pinta las casillas; los
            # demás tipos mandan None y la orden conserva lo que tenga.
            banderas=(ventas.banderas_de(form, True)
                      if str(form.get("casillas") or "") == "1" else None),
            # Quién lo editó, para el renglón del antes/después en el hilo
            # del lead — mismo patrón que conectar/desconectar cotización.
            autor=(request.state.empleada.get("nombre")
                  or request.state.empleada["id"]))
    except ValueError as error:
        # El formulario vuelve con lo escrito, como al crear: un redirect
        # perdería lo que la empleada ya corrigió.
        datos_edicion = cotizaciones.cargar_para_editar(n)
        if datos_edicion is None or not datos_edicion["editable"]:
            return _redirigir_venta(str(error))
        datos_edicion["servicios"] = servicios or datos_edicion["servicios"]
        nombres = {p["producto_id"]: p["nombre"] for p in datos_edicion["plantas"]}
        # El buscador (30/09/2026) puede haber sumado una planta que la
        # cotización todavía no tenía en Odoo: su nombre no está en
        # `nombres`. El formulario ya lo sabe (lo puso el buscador en un
        # campo oculto junto al id) y gana sobre el diccionario viejo.
        nombres.update({
            _entero_o_none(pid): nombre
            for pid, nombre in zip(form.getlist("planta_id"),
                                   form.getlist("planta_nombre")) if nombre})
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
    return _respuesta_pdf(
        "vivero_rose_pedidos.reporte_propuesta_venta", registro["orden_id"],
        ventas.nombre_de_pdf(registro["orden"].replace("/", "-"), registro["cliente"]))


@app.post("/venta/pagar")
async def venta_pagar(request: Request):
    # El botón grande "PAGADO Y CONFIRMAR PEDIDO": crea la orden desde el
    # carrito y pasa a elegir el método de pago (el cobro corre después).
    form = await request.form()
    _amarrar_lead_del_form(request, form)
    try:
        registro = ventas.crear_cotizacion(
            request.state.empleada, form.get("cliente", ""), form.get("celular", ""),
            _datos_cliente_del_form(form), _cargos_del_form(form),
            banderas=ventas.banderas_de(form, False),
            decision_cliente=_decision_cliente_del_form(form),
            confirmar_fiscal=_confirmar_fiscal_del_form(form))
    except ventas.ClienteAjeno as error:
        return _redirigir_venta(str(error), nueva=True,
                                conflicto=_conflicto_de(error))
    except ventas.ConfirmarDatoFiscal as error:
        return _redirigir_venta(str(error), nueva=True,
                                fiscal=_fiscal_de(error))
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
        "pdf_texto": "Descargar / Compartir factura",
        "pdf_nombre": ventas.nombre_de_pdf(
            (registro["factura"] or str(n)).replace("/", "-"), registro["cliente"]),
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
    return _respuesta_pdf(
        "sale.report_saleorder", registro["orden_id"],
        ventas.nombre_de_pdf(registro["orden"].replace("/", "-"), registro["cliente"]))


@app.get("/venta/{n}/factura.pdf")
def venta_pdf_factura(request: Request, n: int):
    registro = ventas.obtener_venta(n)
    if registro is None or not registro["factura_id"]:
        return RedirectResponse("/venta", status_code=303)
    # El nombre lleva el número de FACTURA (no el de la orden/cotización):
    # es el documento que el cliente reconoce.
    return _respuesta_pdf(
        "account.report_invoice", registro["factura_id"],
        ventas.nombre_de_pdf((registro["factura"] or str(n)).replace("/", "-"),
                             registro["cliente"]))


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
    # Entrega en línea (29/09/2026): None si el form vino sin la sección
    # (sin el marcador tiene_vehiculo, o producto sin publicar) — entonces
    # no se toca nada en Odoo; el porqué vive en vehiculos.decidir_guardado.
    veh_valores = vehiculos.decidir_guardado(sku, crudo)
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
    if veh_valores is not None:
        try:
            vehiculos.fijar_en_odoo(sku, veh_valores)
        except datos.SinConexion as fallo:
            return error(502, "sin_guardar", str(fallo))
        except Exception as excepcion:
            print(f"fichas: error guardando los vehículos de {sku}: {excepcion!r}",
                  flush=True)
            return error(502, "sin_guardar",
                         "No se pudieron guardar los vehículos en Odoo. "
                         "Intenta de nuevo.")
    try:
        fichas.guardar(sku, campos, request.state.empleada["id"])
    except Exception as excepcion:
        # A la pantalla va un mensaje simple; el detalle queda en el log.
        print(f"fichas: error guardando {sku}: {excepcion!r}", flush=True)
        return error(502, "sin_guardar",
                      "No se pudo guardar en la base. Intenta de nuevo.")
    return {"resultado": "guardada", "ficha": fichas.todas().get(sku),
            "altura_min": altura["altura_min"], "altura_max": altura["altura_max"],
            # Lo que quedó en Odoo, en la forma de la planta ({moto, carro,
            # pickup}), para que la pantalla no muestre el valor viejo; null
            # si este POST no tocó los vehículos.
            "vehiculos": (vehiculos.a_planta(veh_valores)
                          if veh_valores is not None else None)}


@app.get("/venta/foto/{producto_id}")
def venta_foto(request: Request, producto_id: int):
    foto = ventas.foto_producto(producto_id)
    if foto is None:
        return Response(status_code=404)
    contenido, tipo = foto
    return Response(contenido, media_type=tipo,
                    headers={"Cache-Control": "private, max-age=86400"})


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
    # abre "Nueva actividad" prellenada con el tipo, el nombre — y desde el
    # 29/09/2026 el LEAD, para que el selector salga puesto y la actividad
    # nazca amarrada en vez de suelta.
    leads_servicio = calendario.leads_de_servicio()
    for lead in leads_servicio:
        lead["liga"] = _liga(estado, nueva="1", tipo=lead["tipo"],
                             cliente=lead["nombre"], lead=lead["ref"])

    # Y debajo, el bloque "Por agendar" (Fase 4, 24/09/2026): los leads que
    # ya pagaron, con su etiqueta de pago y el saldo que trae Odoo. Antes
    # este bloque decía "Por entregar" y lo armaba la pestaña Retail; ahora
    # sale del embudo de Linear, que es donde vive el estado.
    bloque = agenda.por_agendar()
    for lead in bloque["leads"]:
        lead["liga"] = _liga(estado, agendar=lead["ref"])
    # Los Entregado que ya no deben nada pasan a Ganado, por detrás: el
    # pago que salda entra en Odoo y nadie más cierra ese círculo.
    agenda.cerrar_en_fondo()

    # El formulario de "Agendar" (fecha · tipo · responsable), si el
    # empleado tocó un lead del bloque.
    agendando = None
    ref_agendar = request.query_params.get("agendar", "")
    if ref_agendar:
        agendando = agenda.lead_con_saldo(ref_agendar)
        if agendando:
            # El responsable se SUGIERE del lead y queda editable (decisión
            # del 24/09/2026); sin Resp: en el lead, el de la sesión.
            agendando["resp_sugerido"] = (
                agendando.get("resp")
                or agenda.responsable_de_empleada(empleada))

    abierta = None
    id_abierta = request.query_params.get("abrir", "")
    if id_abierta:
        abierta = next((a for a in todas if a["id"] == id_abierta), None)

    return plantillas.TemplateResponse(request, "calendario.html", {
        "empleada": empleada,
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
        "por_agendar": bloque["leads"],
        "saldo_error": bloque["error"],
        "agendando": agendando,
        "tipos_agenda": agenda.TIPOS,
        "responsables_lead": linear_leads.responsables(),
        "agenda": agenda,
        # El saldo de la actividad abierta: va ARRIBA del botón "Hecha",
        # nunca escondido — quien entrega lo ve antes de marcarla.
        "lead_abierta": (agenda.lead_con_saldo(abierta["lead"])
                         if abierta and abierta.get("lead") else None),
        "cierra_entrega": (agenda.cierra_la_entrega(abierta["tipo"])
                           if abierta else False),
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
        # Las sugerencias del campo Cliente y el selector «Lead»
        # (29/09/2026): armados EN PYTHON y renderizados — nada consulta
        # al vuelo. Solo cuando el formulario está abierto: una pintada
        # normal del calendario no va a preguntarle los leads a Linear.
        "clientes_sugeridos": (
            agenda.clientes_para_sugerir(todas)
            if request.query_params.get("nueva") == "1" else []),
        "leads_para_conectar": (
            agenda.leads_para_conectar()
            if request.query_params.get("nueva") == "1" else []),
        "pre": {
            "fecha": request.query_params.get("fecha") or estado["dia"],
            "hora": request.query_params.get("hora") or calendario.HORA_POR_DEFECTO,
            "resp": request.query_params.get("resp") or yo["id"],
            # El responsable del select (29/09/2026): por defecto, quien
            # está en la sesión SI su nombre es una etiqueta Resp: real
            # (misma sugerencia que al agendar un lead); si no, queda "" =
            # Sin asignar. En el rebote vuelve lo elegido.
            "resp_nombre": (request.query_params.get("resp_nombre")
                            or agenda.responsable_de_empleada(empleada)),
            # El lead a conectar (29/09/2026): lo trae el enlace del log de
            # «Leads de servicio», o el rebote de un error.
            "lead": request.query_params.get("lead", ""),
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


@app.get("/control")
def control_pantalla(request: Request):
    """La pestaña Control (Fase 5, 24/09/2026): reparte el trabajo.

    Dos vistas sobre el MISMO tablero de Linear — «Por empleado» (reparto,
    solo el dueño) y «Por estado» (las 8 columnas del embudo, COMPLETO para
    todos desde el 28/09/2026: «que todos lo puedan ver», Abraham; mover
    sigue limitado a lo suyo por `puede_tocar()`). Control no guarda nada
    propio: todo se lee y se escribe en el issue.
    """
    empleada = request.state.empleada
    alc = control.alcance(empleada, _es_admin(empleada))
    vista = control.vista_pedida(request.query_params.get("vista", ""), alc)
    leads = linear_leads.listar(
        refrescar=request.query_params.get("refrescar") == "1")

    if vista == "empleado":
        columnas = control.tablero_por_empleado(leads)
    else:
        columnas = control.tablero_por_estado(leads)

    # El celular del encargado suena cuando un lead gana «Te toca»; una
    # sola vez por lead, y por detrás para que la pantalla no espere.
    control.avisar_en_fondo(leads)
    # Y de paso se reintentan los autores que quedaron esperando a que
    # OpenWA guardara su mensaje. También por detrás: esta pantalla no
    # espera a Twenty.
    wa_autor.aplicar_en_fondo()
    # El orden de las columnas (quién lleva más esperando) lee una caché
    # local que se refresca acá mismo, por detrás: la pintada de HOY usa lo
    # que ya estaba guardado.
    control.refrescar_espera_en_fondo(leads)

    abierta = control.ficha(request.query_params.get("abrir", ""),
                           request.query_params.get("buscar", ""))
    # El modal de la corrección manual: a un estado nuevo no se llega sin
    # motivo, así que el drag (y el botón) pasan por aquí.
    moviendo = None
    ref_mover = request.query_params.get("mover", "")
    destino = linear_leads.POR_CLAVE.get(request.query_params.get("a", ""))
    if ref_mover and destino:
        lead = control.ficha(ref_mover)
        if lead and control.puede_tocar(lead, alc):
            moviendo = {"lead": lead, "destino": destino}

    puede_escribir = linear_leads.escritura_activa() or not linear_leads.configurado()
    return plantillas.TemplateResponse(request, "control.html", {
        "empleada": empleada,
        "modo": linear_leads.modo(),
        "alc": alc,
        "vista": vista,
        "columnas": columnas,
        "abierta": abierta,
        "moviendo": moviendo,
        "estados": linear_leads.ESTADOS,
        "responsables": linear_leads.responsables(),
        "motivos": linear_leads.MOTIVOS_PERDIDA,
        "puede_mover": puede_escribir,
        "puede_tocar_abierta": (
            puede_escribir and control.puede_tocar(abierta, alc)
            if abierta else False),
        "aviso": request.query_params.get("aviso"),
        "error": request.query_params.get("error"),
    })


def _control_vuelve(vista, aviso="", error="", abrir=""):
    partes = [f"vista={quote(vista)}"]
    if abrir:
        partes.append("abrir=" + quote(abrir))
    if aviso:
        partes.append("aviso=" + quote(aviso))
    if error:
        partes.append("error=" + quote(error))
    return RedirectResponse("/control?" + "&".join(partes), status_code=303)


def _control_permiso(request, ref):
    """(alcance, vista, error) — el candado del servidor.

    Un empleado solo mueve lo suyo, y eso se verifica AQUÍ: que el
    navegador no muestre el botón no basta, porque un POST se puede mandar
    a mano.
    """
    empleada = request.state.empleada
    alc = control.alcance(empleada, _es_admin(empleada))
    vista = control.vista_pedida(request.query_params.get("vista", ""), alc)
    if not (linear_leads.escritura_activa() or not linear_leads.configurado()):
        return alc, vista, ("Esta instancia mira el tablero real pero no "
                            "escribe en Linear.")
    ref = (ref or "").strip()
    if not ref:
        # Un POST sin ref no es un lead borrado (29/09/2026: el navegador
        # arrastró el enlace de adentro de la tarjeta): se corta acá, sin
        # preguntarle nada a Linear.
        return alc, vista, linear_leads.mensaje_lead_ausente("")
    lead = linear_leads.uno(ref)
    if lead is None:
        return alc, vista, linear_leads.mensaje_lead_ausente(ref)
    if not control.puede_tocar(lead, alc):
        return alc, vista, f"Ese lead es de {lead.get('resp') or 'nadie'}: no lo movés vos."
    return alc, vista, ""


@app.post("/control/responsable")
async def control_responsable(request: Request):
    """Repartir: la etiqueta `Resp:` del issue cambia de nombre.

    Es lo que hace el arrastre de la vista «Por empleado». Nunca toca el
    `assignee` del issue.
    """
    form = await request.form()
    ref = form.get("ref", "")
    alc, vista, error = _control_permiso(request, ref)
    if error:
        return _control_vuelve(vista, error=error)
    if not alc["admin"]:
        return _control_vuelve(vista, error="Repartir es cosa del dueño.")
    autor = request.state.empleada.get("nombre") or request.state.empleada["id"]
    aviso, error = control.mover_a_empleado(ref, form.get("resp", ""), autor=autor)
    return _control_vuelve(vista, aviso=aviso, error=error)


@app.post("/control/estado")
async def control_estado(request: Request):
    """Corregir el estado a mano: exige motivo y queda firmado.

    Sin motivo (el caso del arrastre) redirige al modal que lo pregunta; el
    modal vuelve aquí con el motivo escrito.
    """
    form = await request.form()
    ref, estado = form.get("ref", ""), form.get("estado", "")
    alc, vista, error = _control_permiso(request, ref)
    if error:
        return _control_vuelve(vista, error=error)
    if estado not in linear_leads.POR_CLAVE:
        return _control_vuelve(vista, error="Ese estado no existe en el embudo.")
    if not (form.get("nota") or "").strip():
        return RedirectResponse(
            f"/control?vista={quote(vista)}&mover={quote(ref)}&a={quote(estado)}",
            status_code=303)
    autor = request.state.empleada.get("nombre") or request.state.empleada["id"]
    aviso, error = control.mover_a_estado(
        ref, estado, nota=form.get("nota", ""), motivo=form.get("motivo", ""),
        autor=autor)
    return _control_vuelve(vista, aviso=aviso, error=error)


@app.post("/control/ya-llego")
async def control_ya_llego(request: Request):
    """El botón «Ya llegó» de Recordatorio (29/09/2026): el producto que
    el cliente esperaba llegó — a Hablando por el camino manual, con
    «Te toca» puesto para escribirle hoy. La regla vive en
    `control.ya_llego`; aquí solo el candado de siempre."""
    form = await request.form()
    ref = form.get("ref", "")
    _alc, vista, error = _control_permiso(request, ref)
    if error:
        return _control_vuelve(vista, error=error)
    autor = request.state.empleada.get("nombre") or request.state.empleada["id"]
    aviso, error = control.ya_llego(ref, autor=autor)
    return _control_vuelve(vista, aviso=aviso, error=error)


@app.post("/control/nota")
async def control_nota(request: Request):
    """Una nota sobre el lead: un comentario en su issue de Linear."""
    form = await request.form()
    ref = form.get("ref", "")
    _alc, vista, error = _control_permiso(request, ref)
    if error:
        return _control_vuelve(vista, error=error, abrir=ref)
    autor = request.state.empleada.get("nombre") or request.state.empleada["id"]
    aviso, error = control.escribir_nota(ref, form.get("texto", ""), autor=autor)
    return _control_vuelve(vista, aviso=aviso, error=error, abrir=ref)


@app.post("/control/responder")
async def control_responder(request: Request):
    """El interruptor 🔴 Responder de la ficha: prende o apaga «Te toca» a
    mano, deja el comentario firmado y pide la sincronización a WhatsApp.

    `prender` viaja en el formulario con lo que el BOTÓN pintaba (nunca se
    recalcula acá): así la acción es idempotente aunque la lectura del
    servidor esté un poco vieja — el bug de "hay que apretar dos veces".
    """
    form = await request.form()
    ref = form.get("ref", "")
    _alc, vista, error = _control_permiso(request, ref)
    if error:
        return _control_vuelve(vista, error=error, abrir=ref)
    autor = request.state.empleada.get("nombre") or request.state.empleada["id"]
    aviso, error = control.alternar_responder(
        ref, form.get("prender") == "1", autor=autor)
    return _control_vuelve(vista, aviso=aviso, error=error, abrir=ref)


@app.post("/control/senal")
async def control_senal(request: Request):
    """Una señal suelta del panel (Seguimiento, Importante, Cliente
    potencial): el mismo interruptor que Responder, pero interna — sin
    comentario en el issue y sin tocar WhatsApp. Mismo `prender` explícito
    del formulario, por la misma razón."""
    form = await request.form()
    ref, nombre = form.get("ref", ""), form.get("nombre", "")
    _alc, vista, error = _control_permiso(request, ref)
    if error:
        return _control_vuelve(vista, error=error, abrir=ref)
    autor = request.state.empleada.get("nombre") or request.state.empleada["id"]
    aviso, error = control.alternar_senal(
        ref, nombre, form.get("prender") == "1", autor=autor)
    return _control_vuelve(vista, aviso=aviso, error=error, abrir=ref)


@app.post("/control/cotizacion/conectar")
async def control_cotizacion_conectar(request: Request):
    """Conecta una orden de Odoo (candidata o buscada por número) al lead:
    avanza el embudo según lo que sugiera esa orden, nunca hacia atrás."""
    form = await request.form()
    ref = form.get("ref", "")
    _alc, vista, error = _control_permiso(request, ref)
    if error:
        return _control_vuelve(vista, error=error, abrir=ref)
    autor = request.state.empleada.get("nombre") or request.state.empleada["id"]
    aviso, error = control.conectar_cotizacion(
        ref, form.get("orden_id", ""), autor=autor)
    return _control_vuelve(vista, aviso=aviso, error=error, abrir=ref)


@app.post("/control/cotizacion/desconectar")
async def control_cotizacion_desconectar(request: Request):
    """Quita una orden ya conectada. No toca el estado del embudo."""
    form = await request.form()
    ref = form.get("ref", "")
    _alc, vista, error = _control_permiso(request, ref)
    if error:
        return _control_vuelve(vista, error=error, abrir=ref)
    autor = request.state.empleada.get("nombre") or request.state.empleada["id"]
    aviso, error = control.desconectar_cotizacion(
        ref, form.get("orden_id", ""), autor=autor)
    return _control_vuelve(vista, aviso=aviso, error=error, abrir=ref)


@app.post("/control/cotizacion/marcar-real")
async def control_cotizacion_marcar_real(request: Request):
    """Marca una orden ya conectada como LA real del lead (se la quita a
    cualquier otra) y avanza el embudo con su plata, nunca hacia atrás."""
    form = await request.form()
    ref = form.get("ref", "")
    _alc, vista, error = _control_permiso(request, ref)
    if error:
        return _control_vuelve(vista, error=error, abrir=ref)
    autor = request.state.empleada.get("nombre") or request.state.empleada["id"]
    aviso, error = control.marcar_real_cotizacion(
        ref, form.get("orden_id", ""), autor=autor)
    return _control_vuelve(vista, aviso=aviso, error=error, abrir=ref)


@app.post("/control/cotizacion/quitar-real")
async def control_cotizacion_quitar_real(request: Request):
    """Le quita a una orden la marca de «la real»: el lead se queda sin
    ninguna (la tarjeta de plata desaparece) hasta que se marque otra."""
    form = await request.form()
    ref = form.get("ref", "")
    _alc, vista, error = _control_permiso(request, ref)
    if error:
        return _control_vuelve(vista, error=error, abrir=ref)
    autor = request.state.empleada.get("nombre") or request.state.empleada["id"]
    aviso, error = control.quitar_real_cotizacion(
        ref, form.get("orden_id", ""), autor=autor)
    return _control_vuelve(vista, aviso=aviso, error=error, abrir=ref)


@app.post("/control/mantenimiento/parar")
async def control_mantenimiento_parar(request: Request):
    """El botón «Parar mantenimiento» de la ficha (28/09/2026): apaga la
    serie mensual, cancela la cita futura pendiente y deja un comentario
    firmado en el issue."""
    form = await request.form()
    ref = form.get("ref", "")
    _alc, vista, error = _control_permiso(request, ref)
    if error:
        return _control_vuelve(vista, error=error, abrir=ref)
    autor = request.state.empleada.get("nombre") or request.state.empleada["id"]
    aviso, error = control.parar_mantenimiento(ref, autor=autor)
    return _control_vuelve(vista, aviso=aviso, error=error, abrir=ref)


@app.get("/control/cotizacion/{orden_id}.pdf")
def control_cotizacion_pdf(request: Request, orden_id: int, nombre: str = "",
                           cliente: str = ""):
    """El PDF nativo de Odoo de una orden conectada — mismo camino que
    `/venta/{n}/cotizacion.pdf`, pero con el id de la orden de Odoo
    directo (esto no vive en la tabla local de ventas). `cliente` es el
    nombre del lead (`abierta.nombre` en control.html): no hace falta
    ninguna consulta nueva a Odoo, ya viaja en la ficha."""
    try:
        contenido = cot_lead.pdf_de_orden(orden_id)
    except RuntimeError as error:
        return RedirectResponse(
            "/control?error=" + quote(str(error)), status_code=303)
    archivo = ventas.nombre_de_pdf((nombre or str(orden_id)).replace("/", "-"), cliente)
    return Response(contenido, media_type="application/pdf",
                    headers=cabeceras_descarga(archivo))


# ---------------------------------------------------------------------------
# La pestaña Compras, Fase 1 (30/09/2026): el tablero de lo que se le compra
# al proveedor. El estado vive en el proyecto COMPRAS del equipo VIV de
# Linear (`app/compras.py`, la única puerta) y el dinero en Odoo. Quién
# puede mover qué lo decide el MISMO mecanismo de Control
# (`control.alcance` / `control.puede_tocar`): todos ven el tablero
# completo y cada quien mueve lo suyo.
# ---------------------------------------------------------------------------

@app.get("/compras")
def compras_pantalla(request: Request):
    """El tablero de compras, con el formulario de «compra nueva» detrás
    de `?nueva=1`.

    Hoy el proyecto COMPRAS todavía no existe en Linear: las 7 columnas
    salen vacías con el renglón que lo explica (`compras.falta_en_linear`),
    nunca un 500.

    Tres queries más, todos de pantalla: `?abrir=VIV-NN` muestra los
    productos de esa compra, `?q=` es lo que se escribió en el buscador del
    formulario, y `?cerrar=1` tira el formulario a medio llenar (es a donde
    van el telón y «Cerrar»: un `<a>` no puede hacer POST).
    """
    empleada = request.state.empleada
    usuario = empleada["id"]
    if request.query_params.get("cerrar"):
        compras.descartar_borrador(usuario)
        return RedirectResponse("/compras", status_code=303)
    alc = control.alcance(empleada, _es_admin(empleada))
    # `listar_o_vacio` y no `listar`: si Linear tiene un mal rato, la
    # pestaña sale con sus 7 columnas vacías y el renglón que lo explica,
    # nunca un 500.
    lista = compras.listar_o_vacio(
        refrescar=request.query_params.get("refrescar") == "1")
    columnas = compras.tablero(lista)
    puede_escribir = compras.escritura_activa() or not compras.configurado()

    # El formulario se abre con `?nueva=1` y TAMBIÉN solo, cuando hay una
    # compra a medio llenar: quien volvió de crear un producto —o de
    # cualquier otra pestaña— tiene que encontrar su trabajo donde lo dejó,
    # no el tablero. Lo descarta «Mejor no».
    #
    # Lo único que le gana a esa apertura sola es un `?abrir=` explícito:
    # quien tocó «ver los productos» de una tarjeta pidió ESO, y el
    # borrador sigue guardado para cuando vuelva.
    abrir = (request.query_params.get("abrir") or "").strip()
    nueva = bool(request.query_params.get("nueva")) and puede_escribir
    borrador = compras.borrador_de(usuario)
    if (puede_escribir and not nueva and not abrir
            and compras.borrador_con_algo(usuario)):
        nueva = True

    # Los datos del formulario solo se arman cuando el formulario se abre:
    # los proveedores son una consulta a Odoo y el tablero no la paga.
    lista_prov, prov_error, leads = [], "", []
    # El proveedor escrito: ¿ya existe en Odoo, o hay que ofrecer crearlo?
    # Lo decide Python para que un error de Odoo (`ok: False`) NUNCA se lea
    # como «ese proveedor no existe» — la misma trampa que ya tenía resuelta
    # el buscador de productos con `sin_resultados`.
    prov_calza, prov_nuevo = None, False
    busqueda = {"ok": True, "error": "", "productos": []}
    # «Lo que está bajo»: el atajo para agregar sin escribir el nombre. Se
    # abre con `?bajos=1` y el formulario lo arrastra en un marcador
    # escondido, así que agregar varios seguidos no la cierra.
    bajos = {"ok": True, "error": "", "productos": [], "cuantos": 0,
             "umbral": None, "sobran": 0}
    ver_bajos = bool(request.query_params.get("bajos"))
    texto_buscado = (request.query_params.get("q") or "").strip()[:120]
    if nueva:
        resultado = compras.proveedores()
        lista_prov = resultado["proveedores"]
        prov_error = "" if resultado["ok"] else resultado["error"]
        if resultado["ok"] and borrador["proveedor"]:
            prov_calza = compras.proveedor_que_calza(borrador["proveedor"],
                                                     lista_prov)
            # El «crearlo» solo se ofrece si Odoo está conectado: sin
            # conexión el botón no podría crear nada, y un botón muerto es
            # peor que no tenerlo. Con Odoo conectado pero caído,
            # `resultado["ok"]` ya es False y acá no se entra — la pantalla
            # dice que no se pudo leer, que es la verdad.
            prov_nuevo = prov_calza is None and ventas.configurado()
        if ver_bajos:
            bajos = compras.bajos()
        try:
            leads = [l for l in linear_leads.listar()
                     if l["estado"] not in linear_leads.CERRADOS]
        except linear_leads.ErrorLeads:
            # El lead es OPCIONAL en el formulario: sin Linear, el selector
            # sale con su "es para el vivero" y nada más. Que no se pueda
            # amarrar a un cliente no puede impedir anotar la compra.
            leads = []
        if texto_buscado:
            busqueda = compras.buscar_productos(texto_buscado)

    return plantillas.TemplateResponse(request, "compras.html", {
        "empleada": empleada,
        "modo": compras.modo(),
        "alc": alc,
        "columnas": columnas,
        "falta": compras.falta_en_linear(),
        "puede_mover": puede_escribir,
        "nueva": nueva,
        "borrador": borrador,
        "q": texto_buscado,
        "resultados": busqueda["productos"],
        "buscador_error": "" if busqueda["ok"] else busqueda["error"],
        # Ya buscó y no hay nada: es el momento de ofrecer «Crear
        # producto». Lo decide Python y no la plantilla, para que un error
        # de Odoo (`ok: False`) NUNCA se lea como «no existe».
        "sin_resultados": bool(texto_buscado and busqueda["ok"]
                               and not busqueda["productos"]),
        # El panel de los productos de una compra ya anotada. Con el
        # formulario abierto NO se pinta: serían dos telones y dos hojas
        # encima del tablero.
        "abierta": (None if nueva
                    else compras.con_lineas(compras.uno(abrir, lista))),
        "proveedores": lista_prov,
        "proveedores_error": prov_error,
        # El proveedor escrito calza con este contacto de Odoo (y entonces
        # la compra guarda su id), o no calza con ninguno y hay que ofrecer
        # crearlo — explícito, nunca solo.
        "proveedor_calza": prov_calza,
        "proveedor_nuevo": prov_nuevo,
        # Lo que está bajo o en cero, para agregarlo sin escribir.
        "ver_bajos": ver_bajos,
        "bajos": bajos,
        # El vocabulario de «¿cómo llega?», que vive en compras.py: la
        # plantilla recorre las opciones, no las escribe.
        "formas_llegada": compras.FORMAS_LLEGADA,
        "leads": leads,
        "responsables": linear_leads.responsables(),
        "resp_sugerido": agenda.responsable_de_empleada(empleada),
        "aviso": request.query_params.get("aviso"),
        "error": request.query_params.get("error"),
    })


def _compras_vuelve(aviso="", error="", ancla="", nueva=False, abrir="",
                    bajos=False, q=""):
    """El 303 de vuelta a Compras, SIN tirar al empleado para arriba.

    La regla de siempre del proyecto («volver tiene que devolverte donde
    estabas») se cumple con el ancla en la URL del redirect: `#c-VIV-204`
    es la tarjeta que acaba de tocar, `#cp-lineas` la lista de productos
    del formulario. El navegador la trae a la vista y, como el tablero
    scrollea de lado, también corre la columna sola — sin una línea de JS.

    `bajos` y `q` son el ESTADO de la pantalla, no datos: la lista de lo
    que está bajo y lo que se buscó tienen que seguir ahí después de
    agregar un producto, o cada clic cerraría lo que el empleado abrió.
    """
    partes = []
    if nueva:
        partes.append("nueva=1")
    if bajos:
        partes.append("bajos=1")
    if q:
        partes.append("q=" + quote(q))
    if abrir:
        partes.append("abrir=" + quote(abrir))
    if aviso:
        partes.append("aviso=" + quote(aviso))
    if error:
        partes.append("error=" + quote(error))
    return RedirectResponse(
        "/compras" + ("?" + "&".join(partes) if partes else "")
        + (ancla or ""), status_code=303)


def _compras_permiso(request, ref):
    """(alcance, error) — el candado del servidor para mover una compra.

    Un empleado solo mueve lo suyo, y eso se verifica AQUÍ: que el
    navegador no muestre la tarjeta como arrastrable no basta, porque un
    POST se puede mandar a mano. Un `ref` VACÍO se corta acá mismo sin
    preguntarle nada a Linear — es el POST del enlace arrastrado, no una
    compra borrada (la lección del 29/09/2026 en Control).
    """
    empleada = request.state.empleada
    alc = control.alcance(empleada, _es_admin(empleada))
    if not (compras.escritura_activa() or not compras.configurado()):
        return alc, ("Esta instancia mira el tablero real pero no escribe "
                     "en Linear.")
    ref = (ref or "").strip()
    if not ref:
        return alc, compras.mensaje_compra_ausente("")
    compra = compras.uno(ref)
    if compra is None:
        return alc, compras.mensaje_compra_ausente(ref)
    if not control.puede_tocar(compra, alc):
        return alc, (f"Esa compra es de {compra.get('resp') or 'nadie'}: "
                     f"no la movés vos.")
    return alc, ""


@app.post("/compras/estado")
async def compras_estado(request: Request):
    """Corregir la columna de una compra: es lo que hace el arrastre.

    No pide motivo, a diferencia del embudo de los leads: nada mueve una
    compra sola, así que mover a mano es el camino normal y no una
    excepción. Igual queda el comentario firmado en el issue con quién la
    movió y de dónde a dónde.

    Dos columnas hacen algo más, y es la Fase 2 (01/10/2026):

    - **«Pedido a proveedor»** hace nacer la orden de compra en Odoo. Lo que
      le FALTE a la compra para poder tener orden —proveedor de Odoo,
      productos— se dice ANTES y la compra se queda donde está: un dato que
      falta se arregla, no se arrastra. En cambio un Odoo que no contesta no
      frena nada (Linear es el tablero y manda sobre el estado): la compra
      se mueve y queda la marca «falta la orden en Odoo» con su reintento.
    - **«Recibido»** lleva a la pantalla de recepción, que es donde se dice
      cuánto llegó y cuánto llegó dañado. El estado ya se movió: la
      pantalla es para que el stock suba, no para decidir la columna.
    """
    form = await request.form()
    ref = form.get("ref", "")
    ancla = compras.ancla_de_compra(ref)
    _alc, error = _compras_permiso(request, ref)
    if error:
        return _compras_vuelve(error=error, ancla=ancla)
    autor = request.state.empleada.get("nombre") or request.state.empleada["id"]
    destino = form.get("estado", "")
    if destino == compra_odoo.CLAVE_PEDIDO:
        falta = compra_odoo.falta_para_pedir(ref)
        if falta:
            return _compras_vuelve(error=falta, ancla=ancla)
    aviso, error = compras.mover(ref, destino, autor=autor)
    if error:
        return _compras_vuelve(aviso=aviso, error=error, ancla=ancla)
    if destino == compra_odoo.CLAVE_PEDIDO:
        aviso_odoo, error_odoo = compra_odoo.al_pedir(ref, autor=autor)
        return _compras_vuelve(
            aviso=" ".join(p for p in (aviso, aviso_odoo) if p),
            error=error_odoo, abrir=(ref if error_odoo else ""), ancla=ancla)
    if destino == compra_odoo.CLAVE_RECIBIDO:
        return _compras_recibir_vuelve(ref, aviso=aviso)
    return _compras_vuelve(aviso=aviso, error=error, ancla=ancla)


def _campos_del_borrador(form):
    """Los cuatro campos de texto del formulario, tal como llegaron."""
    return {campo: form.get(campo) for campo in compras.CAMPOS_BORRADOR}


def _cantidades_del_form(form):
    """({n: cantidad}, {n: costo}) de los renglones que el formulario trae.

    Viajan como `cant-7` / `costo-7` porque un formulario sin JavaScript no
    puede mandar una lista de objetos: el número del renglón va en el
    nombre del campo. Un `n` que no sea de esta compra lo descarta
    `compras.guardar_cantidades`.
    """
    cantidades, costos = {}, {}
    for clave in form.keys():
        if clave.startswith("cant-"):
            cantidades[clave[len("cant-"):]] = form.get(clave)
        elif clave.startswith("costo-"):
            costos[clave[len("costo-"):]] = form.get(clave)
    return cantidades, costos


@app.post("/compras/borrador")
async def compras_borrador(request: Request):
    """Todo lo que se hace DENTRO del formulario de compra nueva.

    Un solo endpoint con un `accion`, porque son todas la misma cosa:
    guardar lo escrito y volver al formulario. Los botones del formulario
    apuntan acá con `formaction`, así que cada viaje —buscar un producto,
    agregarlo, quitarlo, irse a crear uno— **guarda primero lo que había
    escrito**. Eso es lo que hace que irse a «Crear producto» y volver no
    pierda nada, sin una línea de JavaScript.

    El ancla del redirect lo elige la acción: quien agrega un producto
    vuelve a la lista, quien busca vuelve al buscador. Nunca al tope.
    """
    form = await request.form()
    usuario = request.state.empleada["id"]
    accion = (form.get("accion") or "guardar").strip()
    if accion == "descartar":
        compras.descartar_borrador(usuario)
        return _compras_vuelve(ancla=compras.ANCLA_TABLERO)
    # El candado también acá, aunque esta instancia no muestre el
    # formulario: un POST se puede mandar a mano, y si no se va a poder
    # anotar la compra tampoco tiene sentido dejar llenar el borrador.
    if not (compras.escritura_activa() or not compras.configurado()):
        return _compras_vuelve(error="Esta instancia mira el tablero real "
                                     "pero no escribe en Linear.",
                               ancla=compras.ANCLA_TABLERO)

    cantidades, costos = _cantidades_del_form(form)
    compras.guardar_borrador(usuario, datos=_campos_del_borrador(form),
                             cantidades=cantidades, costos=costos)
    texto = (form.get("q") or "").strip()[:120]
    # El marcador escondido del formulario: la lista de «lo que está bajo»
    # sigue abierta al volver. Mismo patrón que `casillas` en Vender.
    bajos = bool(form.get("bajos")) and accion != "ocultar_bajos"

    # Agregar y quitar viajan en el NOMBRE del botón, no en un `accion`:
    # un submit manda un solo par nombre/valor, y cada renglón necesita
    # decir CUÁL es. El botón de agregar lleva el SKU y el de quitar el
    # número del renglón; el nombre y el id del producto vienen de dos
    # campos escondidos del mismo renglón, así que agregar no le cuesta ni
    # una consulta más a Odoo (y funciona aunque Odoo se haya caído entre
    # la búsqueda y el clic).
    sku = (form.get("agregar") or "").strip()
    if sku:
        producto_id = _entero_o_nada(form.get("pid-" + sku))
        if producto_id is None:
            # Los renglones de «lo que está bajo» salen del inventario, que
            # trae el SKU y el nombre pero no el id de `product.product`:
            # se le pregunta a Odoo por ESE producto, una sola consulta y
            # solo en este clic. Si Odoo no contesta queda en None, que la
            # línea ya sabe aguantar (el SKU es lo durable).
            producto = compras.producto_por_sku(sku)
            if producto:
                producto_id = producto["id"]
        aviso, error = compras.agregar_al_borrador(
            usuario, producto_id=producto_id,
            sku=sku, nombre=form.get("nom-" + sku) or "")
        # Se vuelve a la LISTA, que es donde está lo nuevo, y el buscador
        # queda vacío para que el siguiente producto empiece de cero.
        return _compras_vuelve(aviso=aviso, error=error, nueva=True,
                               bajos=bajos, ancla=compras.ANCLA_LINEAS)
    if form.get("quitar") is not None:
        aviso, error = compras.quitar_del_borrador(usuario, form.get("quitar"))
        return _compras_vuelve(aviso=aviso, error=error, nueva=True,
                               bajos=bajos, ancla=compras.ANCLA_LINEAS)
    if accion == "crear_proveedor":
        # El proveedor nace en Odoo **solo acá**, con el clic explícito: ni
        # anotar la compra ni escribir el nombre lo crean. Y el borrador ya
        # quedó guardado arriba, así que el viaje no pierde nada.
        resultado = compras.crear_proveedor(form.get("proveedor") or "",
                                            form.get("proveedor_tel") or "")
        if not resultado["ok"]:
            return _compras_vuelve(
                error=f"El proveedor no se creó: {resultado['error']}",
                nueva=True, bajos=bajos, q=texto,
                ancla=compras.ANCLA_PROVEEDOR)
        proveedor = resultado["proveedor"]
        # El nombre del borrador se reemplaza por el de Odoo tal cual quedó
        # (o por el del que ya estaba): así la pantalla siguiente CALZA y
        # ofrece crearlo de nuevo nunca más.
        compras.guardar_borrador(
            usuario, datos={**_campos_del_borrador(form),
                            "proveedor": proveedor["nombre"]})
        aviso = (f"{proveedor['nombre']} ya estaba en Odoo: queda elegido."
                 if resultado["ya_estaba"]
                 else f"{proveedor['nombre']} creado como proveedor en Odoo.")
        return _compras_vuelve(aviso=aviso, nueva=True, bajos=bajos, q=texto,
                               ancla=compras.ANCLA_PROVEEDOR)
    if accion in ("bajos", "ocultar_bajos"):
        # Abrir o cerrar la lista de lo que está bajo. Son dos acciones y no
        # un interruptor porque sin JavaScript el formulario no sabe
        # alternar nada: cada botón dice qué quiere.
        return _compras_vuelve(nueva=True, bajos=(accion == "bajos"),
                               q=texto, ancla=compras.ANCLA_BAJOS)
    if accion == "crear_producto":
        # Se va a dar de alta el producto que no apareció, y vuelve acá: el
        # borrador ya quedó guardado arriba. Lo escrito en el buscador viaja
        # como nombre sugerido, que es casi siempre el nombre del producto.
        destino = "/productos/crear?volver=compra"
        if texto:
            destino += "&nombre=" + quote(texto)
        return RedirectResponse(destino, status_code=303)

    # "buscar" y "guardar": el texto buscado viaja en el query, así que
    # recargar la pantalla repite la búsqueda y nada más.
    return _compras_vuelve(
        nueva=True, bajos=bajos, q=texto,
        ancla=(compras.ANCLA_BUSCADOR if accion == "buscar"
               else compras.ANCLA_LINEAS))


def _entero_o_nada(valor):
    try:
        return int(valor)
    except (TypeError, ValueError):
        return None


@app.post("/compras/nueva")
async def compras_nueva(request: Request):
    """Una compra a mano, que nace en «Por pedir».

    El proveedor viaja como texto (hoy Odoo no tiene ninguno marcado y un
    selector vacío no dejaría anotar nada): si lo escrito calza EXACTO con
    un contacto de Odoo se guarda también su id, y si no queda el nombre
    libre. Nunca se crea un contacto en Odoo desde acá.

    Lo primero que hace es GUARDAR el borrador, antes de validar nada: así
    un error (un lead que no existe, un responsable que no está en el
    equipo) devuelve el formulario con todo lo que el empleado había
    escrito y sus productos, en vez de hacerle empezar de nuevo.
    """
    form = await request.form()
    usuario = request.state.empleada["id"]
    cantidades, costos = _cantidades_del_form(form)
    compras.guardar_borrador(usuario, datos=_campos_del_borrador(form),
                             cantidades=cantidades, costos=costos)
    if not (compras.escritura_activa() or not compras.configurado()):
        return _compras_vuelve(error="Esta instancia mira el tablero real "
                                     "pero no escribe en Linear.",
                               nueva=True, ancla=compras.ANCLA_LINEAS)
    nombre_prov = (form.get("proveedor") or "").strip()
    proveedor_id = None
    if nombre_prov:
        # El id sale del MISMO casamiento que usa la pantalla para decidir
        # si ofrece crearlo (`compras.proveedor_que_calza`): un solo lugar
        # define cuándo dos nombres son el mismo proveedor, así que lo que
        # la pantalla dio por existente es lo que se guarda con su id.
        calza = compras.proveedor_que_calza(nombre_prov)
        if calza is not None:
            proveedor_id = calza["id"]
    autor = request.state.empleada.get("nombre") or request.state.empleada["id"]
    try:
        nueva = compras.crear(
            form.get("que_compro", ""), proveedor_nombre=nombre_prov,
            proveedor_id=proveedor_id, resp=form.get("resp", ""),
            lead_ref=form.get("lead_ref", ""), autor=autor,
            como_llega=form.get("como_llega", ""),
            # Las líneas del borrador pasan a ser las de esta compra, y el
            # borrador se descarta — solo si Linear aceptó.
            usuario_borrador=usuario)
    except compras.ErrorCompras as fallo:
        # El borrador sigue en pie: el formulario se reabre con todo.
        return _compras_vuelve(error=str(fallo), nueva=True,
                               ancla=compras.ANCLA_LINEAS)
    cuantas = len(compras.lineas_de(nueva["ref"]))
    detalle = (f" con {cuantas} producto" + ("s" if cuantas != 1 else "")
               if cuantas else "")
    return _compras_vuelve(
        aviso=f"{nueva['ref']} anotada en «Por pedir»{detalle}.",
        ancla=compras.ancla_de_compra(nueva["ref"]))


# ---------------------------------------------------------------------------

@app.get("/sw-avisos.js")
def avisos_service_worker():
    """El service worker tiene que vivir en la RAÍZ: servido desde /static
    solo podría atender /static, y los avisos son de toda la app. Sin
    caché, para que un deploy lo renueve de una."""
    return FileResponse(
        os.path.join(RUTA_APP, "static", "sw-avisos.js"),
        media_type="application/javascript",
        headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"})


@app.get("/manifest.webmanifest")
def avisos_manifest():
    """El manifest de la app instalada. En iPhone los avisos SOLO llegan si
    la app está agregada a la pantalla de inicio, y para eso hace falta
    este archivo."""
    return Response(json.dumps({
        "name": "Control Viverorose",
        "short_name": "Control",
        "start_url": "/control",
        "display": "standalone",
        "background_color": "#ffffff",
        "theme_color": "#ffffff",
        "icons": [{"src": "/static/logo.jpg", "sizes": "512x512", "type": "image/jpeg"}],
    }), media_type="application/manifest+json")


@app.post("/avisos/suscribir")
async def avisos_suscribir(request: Request):
    """El celular entrega su suscripción; se guarda a nombre de quien tiene
    la sesión abierta en él."""
    try:
        suscripcion = await request.json()
    except Exception:
        suscripcion = None
    if not avisos.guardar(request.state.empleada["id"], suscripcion):
        return Response(status_code=400)
    return Response(status_code=204)


@app.post("/wa/autor")
async def wa_autor_webhook(request: Request):
    """WAHA avisa qué dispositivo escribió un saliente del 6099.

    No lo abre una persona: lo llama WAHA desde el droplet del CRM, así que
    el candado es la firma HMAC del cuerpo crudo y no la cookie. Entra por
    el HTTPS que ya existe (inventario.plantaspanama.com), sin puertos
    nuevos.

    Guarda el dispositivo y NADA más: ni el texto, ni el número, ni el
    chat. Quién es ese dispositivo lo dice la tabla de Ajustes, y ponerle el
    autor al mensaje es cosa de la Fase B, que solo escribe encima de un
    mensaje que Twenty ya tenía.

    Contesta 200 hasta cuando el evento no sirve: WAHA reintenta lo que no
    sea 2xx, y un evento que nunca nos va a interesar (un entrante, un
    grupo) no debe volver quince veces.
    """
    if not wa_autor.configurado():
        # Sin WAHA_WEBHOOK_SECRET no se acepta nada: un endpoint que
        # cualquiera puede llenar de dispositivos inventados ensucia la
        # tabla de Ajustes y le pone nombres falsos a los mensajes.
        return JSONResponse({"ok": False, "motivo": "falta WAHA_WEBHOOK_SECRET"},
                            status_code=503)
    crudo = await request.body()
    if len(crudo) > 2_000_000:
        return JSONResponse({"ok": False, "motivo": "muy grande"}, status_code=413)
    if not wa_autor.firma_valida(crudo, request.headers.get("x-webhook-hmac")):
        return JSONResponse({"ok": False}, status_code=401)
    try:
        evento = json.loads(crudo.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return JSONResponse({"ok": False, "motivo": "json invalido"}, status_code=400)

    leido = wa_autor.leer_evento(evento)
    if leido is None:
        return JSONResponse({"ok": True, "anotado": False, "motivo": "no aplica"})
    anotado = wa_autor.anotar(leido["wa_message_id"], leido["dispositivo"],
                              leido["source"])
    # Fase B: ponerle el autor al mensaje de Twenty. Por detrás y sin que
    # WAHA lo espere — si el mensaje todavía no llegó de OpenWA, el
    # pendiente se reintenta en la próxima pasada.
    if anotado:
        wa_autor.aplicar_en_fondo()
    wa_autor.limpiar_pendientes()
    return JSONResponse({"ok": True, "anotado": anotado,
                         "dispositivo": leido["dispositivo"]})


@app.post("/avisos/resumen")
async def avisos_resumen(request: Request):
    """El resumen del día al celular del dueño. Lo dispara el cron del
    droplet a las 19:00 de Panamá, con el secreto en el header.

    No lleva sesión a propósito: no lo abre una persona, lo llama una
    máquina. Por eso el candado es el secreto y no la cookie.
    """
    if not resumen.armado():
        # Sin RESUMEN_SECRETO no corre: un resumen que cualquiera puede
        # disparar es un resumen que cualquiera puede usar para sondear el
        # negocio. Mismo trato que el barrido del frontend.
        return JSONResponse(
            {"ok": False, "motivo": "falta RESUMEN_SECRETO"}, status_code=503)
    if not resumen.credencial_valida(request.headers.get("authorization")):
        return JSONResponse({"ok": False}, status_code=401)
    hecho = resumen.mandar()
    return JSONResponse({"ok": True, "mandado": hecho["mandado"],
                         "motivo": hecho["motivo"], "titular": hecho["titular"]})


@app.post("/entregas-pendientes/revisar")
async def entregas_pendientes_revisar(request: Request):
    """La pasada diaria de las 7 a.m. de Panamá: pone o quita «Entrega
    pendiente» en cada lead Agendado según si su actividad ya venció.

    El chequeo también corre al agendar/reprogramar/marcar Hecha (Fase 4),
    así que esto es la red de seguridad para los leads que nadie tocó
    hoy —una actividad que amaneció vencida sola, sin que nadie abriera el
    calendario—. Mismo patrón que `/avisos/resumen`: sin sesión a
    propósito (lo llama una máquina), el candado es el secreto.
    """
    if not agenda.entregas_pendientes_armado():
        return JSONResponse(
            {"ok": False, "motivo": "falta ENTREGAS_PENDIENTES_SECRETO"},
            status_code=503)
    if not agenda.entregas_pendientes_credencial_valida(
            request.headers.get("authorization")):
        return JSONResponse({"ok": False}, status_code=401)
    resultado = agenda.marcar_entregas_pendientes()
    return JSONResponse({"ok": True, **resultado})


@app.get("/resumen")
def resumen_pantalla(request: Request):
    """El detalle del resumen: a donde lleva el aviso del celular."""
    dia = request.query_params.get("dia", "")
    try:
        cuando = datetime.strptime(dia, "%Y-%m-%d").date() if dia else resumen.hoy()
    except ValueError:
        cuando = resumen.hoy()
    datos = resumen.del_dia(cuando)
    ayer = cuando - timedelta(days=1)
    return plantillas.TemplateResponse(request, "resumen.html", {
        "empleada": request.state.empleada,
        "r": datos,
        "titular": resumen.titular(datos),
        "plata": resumen._plata,
        "es_hoy": cuando == resumen.hoy(),
        "liga_ayer": "/resumen?dia=" + ayer.isoformat(),
        "liga_hoy": "/resumen",
        "dueno": resumen.usuario_dueno(),
        "celulares": avisos.cuantos(resumen.usuario_dueno()),
        "armado": resumen.armado(),
    })


@app.post("/avisos/prueba")
def avisos_prueba(request: Request):
    """'Mandarme uno de prueba': el mismo camino que el aviso de verdad."""
    empleada = request.state.empleada
    if not avisos.cuantos(empleada["id"]):
        return RedirectResponse("/?tab=ajustes&aviso=avisos-sin-celular",
                                status_code=303)
    avisos.avisar(empleada["id"], "Aviso de prueba",
                  "Si ves esto, los avisos de este celular funcionan.",
                  "/control")
    return RedirectResponse("/?tab=ajustes&aviso=avisos-prueba", status_code=303)


@app.post("/avisos/baja")
def avisos_baja(request: Request):
    """Apagar los avisos en todos los celulares de esta empleada."""
    for suscripcion in avisos.suscripciones(request.state.empleada["id"]):
        avisos.borrar(suscripcion["endpoint"])
    return RedirectResponse("/?tab=ajustes&aviso=avisos-apagados", status_code=303)


@app.get("/equipo")
def equipo_redirige():
    """La pestaña se renombró a Control (23/09/2026); el enlace viejo vive."""
    return RedirectResponse("/control", status_code=308)


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
        # El assignee de Linear sigue siendo quien lo crea (si su correo
        # está enlazado); el RESPONSABLE del trabajo va aparte, por nombre
        # en la marca de la actividad (`resp_lead`, 29/09/2026: volvió el
        # select al formulario) — el mismo camino que usa Agendar. Nunca
        # se crea ninguna etiqueta.
        resp = form.get("resp_id") or yo["id"]
        resp_nombre = (form.get("resp_nombre") or "").strip()

        # Conectar lead (29/09/2026, punto 5 de Abraham): con un lead
        # elegido la actividad NO nace suelta. Si el tipo es de los cinco
        # agendables, va por el MISMO camino de la Fase 4
        # (`agenda.agendar`): actividad amarrada, lead a Agendado, fecha
        # en Odoo, responsable al lead — idéntico a agendar desde «Por
        # agendar». Un tipo fuera de esos cinco (un Alquiler del log, una
        # reunión) nace AMARRADO por la misma marca `lead=` pero sin
        # tocar el estado: solo los tipos agendables mueven el embudo,
        # regla ya escrita. Sin lead, todo sigue como hoy.
        ref_lead = (form.get("lead") or "").strip()
        lead_vivo = None
        if ref_lead:
            lead_vivo = linear_leads.uno(ref_lead)
            if lead_vivo is None:
                raise calendario.ErrorCalendario(
                    linear_leads.mensaje_lead_ausente(ref_lead))
            if form.get("tipo") in agenda.POR_CLAVE:
                return agenda.agendar(
                    ref_lead, form.get("tipo"), form.get("fecha", ""),
                    hora=form.get("hora") or None, resp=resp_nombre,
                    dur=form.get("dur") or None, lugar=form.get("lugar", ""),
                    nota=form.get("nota", ""), autor=yo["nombre"])

        # Con lead y sin cliente escrito, el cliente es el del lead — lo
        # mismo que hace `agenda.agendar` (el relleno de calendario.js es
        # cortesía de pantalla, no el dato).
        cliente = (form.get("cliente") or "").strip() or (
            (lead_vivo or {}).get("nombre") or "")
        creada = calendario.crear(
            tipo=form.get("tipo", "otro"), cliente=cliente,
            fecha=form.get("fecha", ""), hora=form.get("hora") or calendario.HORA_POR_DEFECTO,
            dur=form.get("dur") or calendario.DURACION_POR_DEFECTO,
            lugar=form.get("lugar", ""), resp_id=resp,
            prioridad=form.get("prioridad") or 3, nota=form.get("nota", ""),
            lead=(lead_vivo or {}).get("ref", ""), resp_lead=resp_nombre)
        texto = f"Creada {creada['ref']}: {calendario.nombre_de_tipo(form.get('tipo', 'otro'))}"
        if lead_vivo:
            texto += f", amarrada a {lead_vivo['ref']}"
        # Un alquiler nace con su recogida: nunca se queda una planta
        # alquilada sin fecha de vuelta. Con cualquier OTRO tipo la
        # recogida se descarta AQUÍ, en Python — el campo escondido del
        # formulario es presentación; esta es la regla.
        recogida = form.get("recogida", "")
        if form.get("tipo") == "alquiler" and re.fullmatch(r"\d{4}-\d{2}-\d{2}", recogida):
            otra = calendario.crear(
                tipo="recogida", cliente=cliente, fecha=recogida,
                hora="09:00", dur=60, lugar=form.get("lugar", ""), resp_id=resp,
                prioridad=form.get("prioridad") or 3,
                nota=f"Recogida del alquiler {creada['ref']}.",
                lead=(lead_vivo or {}).get("ref", ""), resp_lead=resp_nombre)
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
                      "prioridad", "recogida", "nota", "resp_id", "resp_nombre",
                      "lead"):
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
    autor = (request.state.empleada.get("nombre")
             or request.state.empleada["id"])

    def hacer(_yo, actividad):
        nuevo = form.get("estado", "")
        calendario.cambiar_estado(id_actividad, nuevo)
        texto = calendario.nombre_de_estado(nuevo) + "."
        # Fase 4: marcar Hecha cierra el embudo — el lead pasa a Entregado
        # y, si el saldo quedó en cero, sigue solo a Ganado. Una Recogida
        # (el retiro del alquiler) no mueve nada: el lead ya se entregó.
        if nuevo == "hecha" and actividad:
            try:
                extra = agenda.al_marcar_hecha(actividad, autor=autor)
            except (calendario.ErrorCalendario, linear_leads.ErrorLeads) as fallo:
                # La actividad YA quedó hecha: el embudo es lo que falló, y
                # eso se dice en vez de tragárselo.
                extra = f"La actividad quedó hecha, pero el embudo no se movió: {fallo}"
            if extra:
                texto += " " + extra
        return texto

    return await _accion_calendario(request, id_actividad, hacer)


@app.post("/calendario/agendar")
async def calendario_agendar(request: Request):
    """Fase 4: la fecha de un lead «Por agendar» crea su actividad y lo
    manda a «Agendado». Es el único camino: poner la fecha ES agendar."""
    form = await request.form()
    ref = form.get("lead", "")
    autor = (request.state.empleada.get("nombre")
             or request.state.empleada["id"])
    try:
        if not (calendario.escritura_activa() or not calendario.configurado()):
            raise calendario.ErrorCalendario(
                "Esta instancia mira el calendario real pero no escribe en Linear.")
        aviso = agenda.agendar(
            ref_lead=ref, tipo=form.get("tipo", ""), fecha=form.get("fecha", ""),
            hora=form.get("hora") or None, resp=form.get("resp", ""),
            dur=form.get("dur") or None, lugar=form.get("lugar", ""),
            nota=form.get("nota", ""), autor=autor)
    except (calendario.ErrorCalendario, linear_leads.ErrorLeads) as fallo:
        # Se vuelve al formulario abierto, para no perder lo escrito.
        destino = request.query_params.get("volver") or "/calendario"
        if not destino.startswith("/calendario"):
            destino = "/calendario"
        destino += ("&" if "?" in destino else "?") + "&".join([
            f"agendar={quote(ref)}", "error=" + quote(str(fallo))])
        return RedirectResponse(destino, status_code=303)
    calendario_google.sincronizar_en_fondo()
    return _volver_a(request, aviso=aviso)


@app.post("/calendario/actividad/{id_actividad}/reprogramar")
async def calendario_reprogramar(request: Request, id_actividad: str):
    """Mover la fecha SIN tocar el estado del lead (regla del plan)."""
    form = await request.form()

    def hacer(_yo, _actividad):
        return agenda.reprogramar(
            id_actividad, form.get("fecha", ""), form.get("hora") or None)

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
# Twenty (dueño, 22/09/2026): los armadores reciben crm_twenty.color_crm.
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
        carril = crm_twenty.carril_dias(visibles, columnas, dia_hoy)
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
        mes = calendario.rejilla_mes(visibles, ancla, estado["dia"], dia_hoy,
                                     color_de_tipo=crm_twenty.color_crm)
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
# Crear producto: Planta · Maceta · Insumo (dueño, 30/09/2026)
# ---------------------------------------------------------------------------
# El botón negro de Stock dejó de ser "Crear planta". Primero se elige el
# tipo —un selector que arranca en "— elegir —" dentro de un form GET, sin
# JS— y después se llena su formulario. Los enlaces directos
# (?tipo=maceta) siguen valiendo: el selector llega por la misma puerta.
# La planta sigue por su camino de siempre (el modal de Stock y
# POST /productos/nuevo, que pasa por el order-api); maceta e insumo se
# crean acá, directo en Odoo, con las reglas de app/altas.py: los dos
# impuestos explícitamente vacíos, la categoría por NOMBRE y la maceta
# naciendo sin publicar.

# Lo más grande que acepta la foto de una maceta. Odoo la guarda en
# image_1920 y la reescala; 12 MB cubre cualquier foto de teléfono.
MAX_FOTO_PRODUCTO = 12 * 1024 * 1024


# A dónde vuelve «Crear producto» cuando se llegó desde otra pantalla. Es
# una lista de permitidos a propósito: el valor llega en el query y lo único
# que puede hacer es elegir uno de estos destinos — nunca una URL suelta.
VUELTAS_DEL_ALTA = {"compra": {"url": "/compras", "texto": "a la compra"}}


def _vuelta_del_alta(valor):
    """La ficha del destino de vuelta, o None si no hay ninguno."""
    return VUELTAS_DEL_ALTA.get((valor or "").strip())


def _pantalla_alta(request, tipo, previo=None, error=None, creado="",
                   avisos=(), estado=200, volver="", nombre=""):
    """La pantalla de Crear producto: el paso de elegir, o un formulario."""
    etiquetas = {"maceta": "Maceta", "insumo": "Insumo"}
    previo = dict(previo or {})
    # El nombre que viajó desde el buscador de la otra pantalla (lo que el
    # empleado escribió y no apareció) entra como sugerencia, sin pisar lo
    # que ya hubiera escrito en este formulario.
    if nombre and not (previo.get("nombre") or "").strip():
        previo["nombre"] = nombre
    return plantillas.TemplateResponse(request, "crear_producto.html", {
        "tipo": tipo,
        "tipos": altas.tipos_para_pantalla(),
        "materiales": altas.MATERIALES,
        "unidades": altas.UNIDADES,
        "prefijo": altas.PREFIJO_DE.get(tipo, ""),
        "previo": previo,
        "error": error,
        "creado": creado,
        "volver": (volver or "").strip() if _vuelta_del_alta(volver) else "",
        "volver_texto": (_vuelta_del_alta(volver) or {}).get("texto", ""),
        "volver_url": (_vuelta_del_alta(volver) or {}).get("url", ""),
        # "Maceta creada" / "Insumo creado": el género lo decide Python, no
        # la plantilla.
        "frase_creado": ("Maceta creada" if tipo == "maceta"
                         else "Insumo creado"),
        "etiqueta_tipo": etiquetas.get(tipo, "Producto"),
        "avisos": altas.texto_de_avisos(avisos),
    }, status_code=estado)


@app.get("/productos/crear")
def alta_producto(request: Request, tipo: str = "", creado: str = "",
                  volver: str = "", nombre: str = ""):
    """Elegir el tipo, o el formulario de maceta / insumo.

    El tipo planta no tiene formulario propio acá: manda al de siempre, que
    vive en la pantalla de Stock. Y es el MISMO camino para el selector y
    para un enlace directo `?tipo=planta`: el formulario de elegir no puede
    tener dos destinos sin JavaScript, así que la redirección vive acá y hay
    una sola regla.

    Un tipo raro, el selector mandado sin elegir, o un tipo cuya categoría no
    está en Odoo caen en el paso de elegir, diciendo qué pasó.

    `volver` y `nombre` son de quien llegó desde otra pantalla (hoy el
    formulario de compra nueva): el destino de vuelta y el nombre que
    escribió y no apareció. Los dos atraviesan los dos pasos como campos
    escondidos del formulario, así que elegir el tipo no los pierde.
    """
    if tipo == "planta":
        # El `volver` viaja con ella: el modal de Stock es el único alta que
        # no vive acá, y sin esto la planta nueva no sabría a qué compra
        # volver (era media solución — el borrador sobrevivía, pero la
        # planta había que buscarla de nuevo).
        destino = "/?tab=stock&crear=planta"
        if _vuelta_del_alta(volver):
            destino += "&volver=" + quote(volver)
        return RedirectResponse(destino, status_code=303)
    estados = {t["clave"]: t for t in altas.tipos_para_pantalla()}
    if tipo not in altas.CATEGORIA_DE:
        # `?tipo=` vacío es el selector mandado sin elegir (el navegador lo
        # frena con `required`, pero un enlace a mano llega igual).
        return _pantalla_alta(request, "", error=(
            "Elige qué vas a crear." if "tipo" in request.query_params
            else None), volver=volver, nombre=nombre)
    if not estados[tipo]["listo"]:
        # Ej.: ?tipo=maceta con la categoría «Macetas» ausente. Se dice QUÉ
        # falta, no un genérico.
        return _pantalla_alta(request, "", error=estados[tipo]["motivo"],
                              volver=volver, nombre=nombre)
    return _pantalla_alta(request, tipo, creado=creado,
                          avisos=request.query_params.getlist("aviso"),
                          volver=volver, nombre=nombre)


@app.post("/productos/crear")
async def alta_producto_guardar(request: Request):
    """Crea la maceta o el insumo en Odoo y vuelve con su referencia.

    Un error de validación NO redirige: se repinta el formulario con lo que
    el empleado escribió, para no hacerle escribir todo de nuevo. El éxito sí
    redirige (303) con la referencia y los avisos como códigos en la URL, así
    que recargar no crea un segundo producto.
    """
    form = await request.form()
    tipo = (form.get("tipo") or "").strip()
    volver = (form.get("volver") or "").strip()
    crudo = {campo: form.get(campo) for campo in
             ("nombre", "material", "diametro", "alto", "color", "precio",
              "costo", "unidad", "itbms")}
    limpio, error = altas.revisar(tipo, crudo)
    if error:
        return _pantalla_alta(request, tipo if tipo in altas.CATEGORIA_DE else "",
                              previo=crudo, error=error, estado=400,
                              volver=volver)

    foto = None
    subida = form.get("foto")
    if hasattr(subida, "read"):
        contenido = await subida.read()
        if contenido:
            if len(contenido) > MAX_FOTO_PRODUCTO:
                return _pantalla_alta(request, tipo, previo=crudo, estado=400,
                                      error="La foto pesa demasiado: manda "
                                            "una de menos de 12 MB.",
                                      volver=volver)
            foto = base64.b64encode(contenido).decode()

    try:
        hecho = altas.crear(limpio, foto=foto)
    except datos.SinConexion as fallo:
        return _pantalla_alta(request, tipo, previo=crudo, error=str(fallo),
                              estado=502, volver=volver)
    if _vuelta_del_alta(volver) and volver == "compra":
        return _compras_con_el_producto_nuevo(request, hecho)
    destino = f"/productos/crear?tipo={tipo}&creado={quote(hecho['sku'])}"
    for aviso in hecho["avisos"]:
        destino += f"&aviso={quote(aviso)}"
    return RedirectResponse(destino, status_code=303)


def _compras_con_el_producto_nuevo(request, hecho):
    """De vuelta al formulario de compra, con el producto recién creado YA
    agregado como línea.

    Es la mitad que faltaba de «sin sacarlo de la misma pestaña»: el
    borrador ya tenía todo lo escrito (se guardó antes del viaje) y acá se
    le suma el producto nuevo, para que el empleado no tenga que buscarlo.

    El id de `product.product` se pide aparte porque el alta devuelve el del
    `product.template`, que no es el mismo. Si Odoo no contesta esa segunda
    consulta **la línea se agrega igual** con su SKU y su nombre, que es lo
    durable: perder la línea sería mucho peor que quedarse sin el id.
    """
    usuario = request.state.empleada["id"]
    producto = compras.producto_por_sku(hecho["sku"])
    aviso, error = compras.agregar_al_borrador(
        usuario,
        producto_id=(producto or {}).get("id"),
        sku=hecho["sku"],
        nombre=(producto or {}).get("nombre") or hecho["sku"])
    if not error:
        aviso = f"{aviso} Creado en Odoo con la referencia {hecho['sku']}."
        # Lo que el alta no pudo guardar (el ITBMS que no se encontró, los
        # campos que este Odoo todavía no tiene) se dice acá: en el camino
        # normal lo diría su propia pantalla, y de vuelta en Compras esa
        # pantalla no se ve.
        for texto in altas.texto_de_avisos(hecho["avisos"]):
            aviso = f"{aviso} {texto}"
    return _compras_vuelve(aviso=aviso, error=error, nueva=True,
                           ancla=compras.ANCLA_LINEAS)


# ---------------------------------------------------------------------------
# La vista de PROVEEDORES, la segunda pantalla de Compras (30/09/2026):
# cuánto se le compró a cada uno, calculado de Odoo, con "Preferido" como
# única marca a mano. `app/proveedores.py` es la única puerta a este dato;
# esta ruta solo lo pinta. El enlace desde el tablero de Compras lo pone
# la otra tanda que trabaja esa pantalla — acá no se toca `compras.html`.
# ---------------------------------------------------------------------------

@app.get("/compras/proveedores")
def proveedores_pantalla(request: Request):
    """La lista de proveedores con lo que de verdad se les compró.

    Hoy (30/09/2026) no hay ni un proveedor marcado ni una orden de compra
    en Odoo: la pantalla nace vacía y lo dice en palabras simples
    (`proveedores.listar_o_vacio`), nunca un error ni un 500.

    `?abrir=<id>` trae la ficha de UN proveedor en el panel lateral: sus
    datos y, de `product.supplierinfo`, lo que le compramos con su precio
    — y desde el 01/10/2026, ya editable (ver `/compras/proveedores/producto`
    más abajo). `?buscar=<texto>` busca en el catálogo (PL-/MC-/IN-) para
    asignarle un producto nuevo: es un GET porque buscar no escribe nada,
    y así recargar o volver desde un error nunca pierde lo que se había
    escrito.
    """
    resultado = proveedores.listar_o_vacio()
    lista = resultado["proveedores"]
    abrir = (request.query_params.get("abrir") or "").strip()
    buscar = (request.query_params.get("buscar") or "").strip()[:120]
    abierto, productos_abierto, buscados = None, None, None
    error_aviso = request.query_params.get("error")
    if abrir and resultado["ok"]:
        abierto = proveedores.uno(abrir, lista)
        if abierto is not None:
            productos_abierto = proveedores.productos_de(abierto["id"])
            if buscar:
                buscados = proveedores.buscar_para_asignar(buscar)
        elif not error_aviso:
            # Un enlace viejo (o tocado a mano): no es un 500, es el mismo
            # caso de "ya no está" que ya existe en Compras y en Control.
            error_aviso = f"Ese proveedor ({abrir}) ya no está en la lista."
    return plantillas.TemplateResponse(request, "proveedores.html", {
        "empleada": request.state.empleada,
        "ok": resultado["ok"],
        "error": resultado["error"],
        "proveedores": lista,
        "resumen": proveedores.resumen(lista),
        "es_admin": _es_admin(request.state.empleada),
        "abierto": abierto,
        "productos_abierto": productos_abierto,
        "buscar": buscar,
        "buscados": buscados,
        "delay_comun": (proveedores.delay_comun(productos_abierto["productos"])
                        if productos_abierto and productos_abierto["ok"]
                        else None),
        "aviso": request.query_params.get("aviso"),
        "error_aviso": error_aviso,
    })


def _proveedores_vuelve(partner_id="", error=""):
    """El 303 de vuelta a Proveedores, a la tarjeta que se tocó (si llegó
    cuál era) y no al tope de la lista."""
    url = "/compras/proveedores"
    if error:
        url += "?error=" + quote(error)
    if partner_id:
        url += f"#pv-{partner_id}"
    return RedirectResponse(url, status_code=303)


@app.post("/compras/proveedores/preferido")
async def proveedores_preferido(request: Request):
    """Prende o apaga «Preferido». Solo lo puede tocar un admin: es la
    única decisión de esta pantalla que no se calcula, y es del dueño."""
    form = await request.form()
    partner_id = (form.get("partner_id") or "").strip()
    if not _es_admin(request.state.empleada):
        return _proveedores_vuelve(
            partner_id, "Solo un admin puede marcar «Preferido».")
    error = proveedores.marcar_preferido(
        partner_id, form.get("preferido") == "1",
        autor=request.state.empleada.get("nombre") or request.state.empleada["id"])
    return _proveedores_vuelve(partner_id, error)


# ---------------------------------------------------------------------------
# Compras · Fase 2 (01/10/2026): la orden de compra en Odoo y la entrada de
# stock al recibir. La lógica vive en `app/compra_odoo.py` (el único que le
# habla a `purchase.order` y a los `stock.picking` de una compra); acá solo
# están las pantallas y los redirects, con su ancla de siempre.
# ---------------------------------------------------------------------------

def _compras_recibir_vuelve(ref, aviso="", error=""):
    """El 303 a la pantalla de recibir, con su ancla.

    Es la pantalla a la que lleva arrastrar una compra a «Recibido» y a la
    que vuelve un error de la recepción: el empleado tiene que caer en la
    lista de renglones y no en el tope, igual que en todo el resto de
    Compras.
    """
    partes = ["ref=" + quote((ref or "").strip())]
    if aviso:
        partes.append("aviso=" + quote(aviso))
    if error:
        partes.append("error=" + quote(error))
    return RedirectResponse(
        "/compras/recibir?" + "&".join(partes) + compras.ANCLA_RECIBIR,
        status_code=303)


@app.post("/compras/orden")
async def compras_orden(request: Request):
    """Crear (o reintentar) la orden de compra de esta compra en Odoo.

    Es el botón del panel, y es el que resuelve la marca «falta la orden en
    Odoo»: `asegurar_orden` es idempotente en sus dos mitades —busca la
    orden por su `origin` antes de crear nada y confirma la que quedó en
    borrador—, así que apretarlo dos veces no crea dos órdenes.
    """
    form = await request.form()
    ref = (form.get("ref") or "").strip()
    ancla = compras.ancla_de_compra(ref)
    _alc, error = _compras_permiso(request, ref)
    if error:
        return _compras_vuelve(error=error, ancla=ancla)
    autor = request.state.empleada.get("nombre") or request.state.empleada["id"]
    resultado = compra_odoo.asegurar_orden(ref, autor=autor)
    if not resultado["ok"]:
        return _compras_vuelve(error=resultado["error"], abrir=ref, ancla=ancla)
    orden = resultado["orden"]
    aviso = (f"La orden {orden['nombre']} ya estaba en Odoo."
             if resultado["ya_estaba"]
             else f"Orden de compra {orden['nombre']} creada en Odoo.")
    return _compras_vuelve(aviso=aviso, abrir=ref, ancla=ancla)


@app.get("/compras/recibir")
def compras_recibir_pantalla(request: Request):
    """Lo que llegó: los renglones de la entrada de stock de Odoo.

    Cada renglón dice cuánto se pidió, cuánto se recibió ya, y pide cuánto
    llegó ahora y cuántas llegaron dañadas. Lo que se escribe en Odoo es lo
    que llegó BUENO (llegó − dañadas), así el stock sube por el camino de
    Odoo y no por uno propio.

    Sin orden de compra, o con Odoo caído, la pantalla lo dice en palabras
    simples y nunca es un 500 — la misma tolerancia del tablero.
    """
    ref = (request.query_params.get("ref") or "").strip()
    _alc, error = _compras_permiso(request, ref)
    if error:
        return _compras_vuelve(error=error,
                               ancla=compras.ancla_de_compra(ref))
    return _pantalla_recibir(request, ref,
                             aviso=request.query_params.get("aviso"),
                             error=request.query_params.get("error"))


def _pantalla_recibir(request, ref, aviso=None, error=None, campo_error="",
                      valores=None):
    """La pantalla de recibir, compartida por el GET y el POST que rebotó.

    `campo_error` es el campo que falló («llego-7»): la plantilla pinta el
    mensaje DEBAJO de él y lo enfoca (regla 5 de formularios). `valores` es
    lo que el empleado tecleó en los renglones, para que un rechazo no le
    borre nada."""
    return plantillas.TemplateResponse(request, "compras_recibir.html", {
        "empleada": request.state.empleada,
        "modo": compras.modo(),
        "compra": compras.con_lineas(compras.uno(ref)),
        "recepcion": compra_odoo.recepcion(ref),
        # El panel de lo dañado se pinta aunque Odoo no contestara: es una
        # tabla local y no tiene por qué desaparecer con Odoo.
        "danado": compra_odoo.danado_de(ref),
        "aviso": aviso,
        "error": error,
        "campo_error": campo_error,
        "valores": valores or {},
    })


@app.post("/compras/recibir")
async def compras_recibir(request: Request):
    """Registra lo que llegó: valida la entrada en Odoo y sube el stock.

    Lo dañado NO entra al stock y queda anotado en la app; lo que faltó
    queda pendiente en la orden de compra, que es como Odoo ya lo maneja —
    así la recepción se puede repetir cuando llegue el resto.
    """
    form = await request.form()
    ref = (form.get("ref") or "").strip()
    _alc, error = _compras_permiso(request, ref)
    if error:
        return _compras_vuelve(error=error,
                               ancla=compras.ancla_de_compra(ref))
    llegadas, danadas = _recepcion_del_form(form)
    autor = request.state.empleada.get("nombre") or request.state.empleada["id"]
    resultado = compra_odoo.recibir(ref, llegadas=llegadas, danadas=danadas,
                                    autor=autor)
    if not resultado["ok"]:
        if resultado.get("campo"):
            # El error de UN campo (regla 5): la misma pantalla, pintada
            # directo del POST, con lo tecleado en todos los renglones y el
            # mensaje debajo del campo que falló — el redirect de abajo
            # borraría lo escrito.
            return _pantalla_recibir(
                request, ref, error=resultado["error"],
                campo_error=resultado["campo"],
                valores={**{f"llego-{m}": str(v or "")
                            for m, v in llegadas.items()},
                         **{f"roto-{m}": str(v or "")
                            for m, v in danadas.items()}})
        # Se vuelve a la MISMA pantalla: lo que se escribió se puede
        # corregir ahí, y mandar al tablero obligaría a volver a entrar.
        return _compras_recibir_vuelve(ref, error=resultado["error"])
    return _compras_vuelve(aviso=resultado["aviso"], abrir=ref,
                           ancla=compras.ancla_de_compra(ref))


def _recepcion_del_form(form):
    """({movimiento: llegó}, {movimiento: dañadas}) de los renglones que el
    formulario trae.

    Viajan como `llego-7` / `roto-7` porque un formulario sin JavaScript no
    puede mandar una lista de objetos: el id del movimiento de Odoo va en el
    nombre del campo. Un id que no sea de esta entrada lo descarta
    `compra_odoo.recibir`, que recorre los renglones que Odoo dio y no los
    que el navegador mandó.
    """
    llegadas, danadas = {}, {}
    for clave in form.keys():
        if clave.startswith("llego-"):
            llegadas[clave[len("llego-"):]] = form.get(clave)
        elif clave.startswith("roto-"):
            danadas[clave[len("roto-"):]] = form.get(clave)
    return llegadas, danadas
# El catálogo de un proveedor, editable (01/10/2026): pedido literal del
# dueño, «quiero poder asignar plantas a cada proveedor, precio, etc.».
# `app/proveedores.py` tiene las cuatro escrituras; esta ruta solo lee el
# formulario y decide CUÁL de las cuatro corresponde — por el NOMBRE del
# botón que llegó, igual que `/compras/borrador` distingue agregar/quitar.
# Admin-only, como «Marcar Preferido»: es la misma pantalla y el mismo
# tipo de decisión (plata de un proveedor), no una del día a día de un
# empleado cualquiera.
# ---------------------------------------------------------------------------

def _proveedores_vuelve_a_la_ficha(partner_id, buscar="", aviso="", error=""):
    """El 303 de vuelta a la FICHA abierta (a diferencia de
    `_proveedores_vuelve`, que cierra el panel): toda escritura del
    catálogo pasa por acá, así que quien agrega tres productos seguidos
    no tiene que volver a abrir la ficha cada vez, y el buscador conserva
    lo que tenía escrito."""
    url = f"/compras/proveedores?abrir={quote(str(partner_id))}"
    if buscar:
        url += "&buscar=" + quote(buscar)
    if aviso:
        url += "&aviso=" + quote(aviso)
    if error:
        url += "&error=" + quote(error)
    return RedirectResponse(url, status_code=303)


@app.post("/compras/proveedores/producto")
async def proveedores_producto(request: Request):
    """Las cuatro formas de tocar el catálogo de UN proveedor: agregar,
    cambiar precio, quitar, y los días que tarda (que se escriben en
    TODAS sus líneas a la vez — es un dato del proveedor, no del
    producto). Cuál de las cuatro llegó se decide por el NOMBRE del botón
    que se apretó, nunca por adivinar: cada botón manda su propio campo.
    """
    form = await request.form()
    partner_id = (form.get("partner_id") or "").strip()
    buscar = (form.get("buscar") or "").strip()[:120]
    if not _es_admin(request.state.empleada):
        return _proveedores_vuelve_a_la_ficha(
            partner_id, buscar=buscar,
            error="Solo un admin puede editar el catálogo de un proveedor.")

    tmpl_agregar = (form.get("agregar") or "").strip()
    if tmpl_agregar:
        error = proveedores.agregar_producto(
            partner_id, tmpl_agregar,
            precio=form.get(f"precio-{tmpl_agregar}"),
            cantidad_minima=form.get(f"minimo-{tmpl_agregar}"),
            codigo_proveedor=form.get(f"codigo-{tmpl_agregar}"),
            nombre_proveedor=form.get(f"nombreprov-{tmpl_agregar}"))
        return _proveedores_vuelve_a_la_ficha(
            partner_id, buscar=buscar, error=error,
            aviso=("" if error else "Producto agregado al catálogo."))

    linea_guardar = (form.get("guardar") or "").strip()
    if linea_guardar:
        error = proveedores.actualizar_linea(
            partner_id, linea_guardar,
            precio=form.get(f"precio-{linea_guardar}"),
            cantidad_minima=form.get(f"minimo-{linea_guardar}"),
            codigo_proveedor=form.get(f"codigo-{linea_guardar}"),
            nombre_proveedor=form.get(f"nombreprov-{linea_guardar}"))
        return _proveedores_vuelve_a_la_ficha(
            partner_id, error=error,
            aviso=("" if error else "Precio actualizado."))

    linea_quitar = (form.get("quitar") or "").strip()
    if linea_quitar:
        error = proveedores.quitar_producto(partner_id, linea_quitar)
        return _proveedores_vuelve_a_la_ficha(
            partner_id, error=error,
            aviso=("" if error else "Producto quitado del proveedor."))

    if (form.get("accion") or "").strip() == "dias":
        error = proveedores.cambiar_dias_entrega(
            partner_id, form.get("dias_valor"))
        return _proveedores_vuelve_a_la_ficha(
            partner_id, error=error,
            aviso=("" if error else "Días que tarda actualizados en "
                                    "todas sus líneas."))

    return _proveedores_vuelve_a_la_ficha(
        partner_id, buscar=buscar,
        error="No llegó ninguna acción para hacer.")


# ---------------------------------------------------------------------------
# Ventas a revisar (Orquesta · M1, 01/10/2026): la foto de la reconciliación
# —lo que dice Odoo contra lo que dicen las otras fuentes— SOLO para admins.
# La pantalla es de lectura: lo único que escribe es una nota de texto
# (`revision_nota`). Registrar un pago o confirmar una entrega es la fase
# M2 y sus botones salen desactivados a propósito. Los datos los arma
# `app/reconciliacion.py` (informe_datos(), el contrato de M1); el apoyo
# de pantalla (palabras, chips, notas) vive en `app/revisar.py`.
# ---------------------------------------------------------------------------

def _revisar_apoyo():
    """El módulo de apoyo de la pantalla. Import local a propósito: el
    nombre `revisar` a nivel de módulo lo ocupa la ruta del conteo
    quincenal (`def revisar`, /conteos/{n}/revisar) — un import de arriba
    quedaría PISADO por esa función sin ningún aviso."""
    from . import revisar
    return revisar


@app.get("/revisar")
def ventas_a_revisar(request: Request):
    """La lista de ventas a revisar, con sus contadores y el detalle de
    una (`?abrir=<orden>`) en el panel lateral — el mismo mecanismo de
    Proveedores, sin JS. Nada se inventa: si el informe trae huecos, la
    pantalla los dice en un renglón visible, nunca un 0 fingido."""
    if (rechazo := _solo_admin(request)) is not None:
        return rechazo
    apoyo = _revisar_apoyo()
    informe = apoyo.informe()
    lista = apoyo.preparar(informe.get("ventas") or [])
    abrir = (request.query_params.get("abrir") or "").strip()
    abierta, notas = None, []
    error_aviso = request.query_params.get("error")
    if abrir:
        abierta = next(
            (v for v in lista if str(v.get("orden_id")) == abrir), None)
        if abierta is None:
            if not error_aviso:
                # Un enlace viejo: no es un 500, es el mismo "ya no está"
                # de Compras, Control y Proveedores.
                error_aviso = f"Esa venta ({abrir}) ya no está en la lista."
        else:
            notas = apoyo.notas_de(abrir)
    return plantillas.TemplateResponse(request, "revisar_ventas.html", {
        "empleada": request.state.empleada,
        "contadores": informe.get("contadores") or {},
        "ventas": lista,
        "fuera_texto": apoyo.fuera_texto(informe.get("fuera_de_alcance")),
        "huecos": informe.get("huecos") or [],
        "abierta": abierta,
        "notas": notas,
        "aviso": request.query_params.get("aviso"),
        "error_aviso": error_aviso,
    })


@app.post("/revisar/nota")
async def ventas_a_revisar_nota(request: Request):
    """Guarda una nota de revisión y vuelve a la MISMA tarjeta (ancla
    #orden-<id>): en esta casa nunca se pierde el lugar en una lista.
    La nota es texto y nada más — jamás escribe algo que signifique
    «pagado»."""
    if (rechazo := _solo_admin(request)) is not None:
        return rechazo
    apoyo = _revisar_apoyo()
    form = await request.form()
    orden_id = (form.get("orden_id") or "").strip()
    error = apoyo.guardar_nota(
        orden_id, form.get("nota"),
        quien=(request.state.empleada.get("nombre")
               or request.state.empleada["id"]))
    partes = []
    if orden_id:
        partes.append("abrir=" + quote(orden_id))
    if error:
        partes.append("error=" + quote(error))
    else:
        partes.append("aviso=" + quote("Nota guardada."))
    url = "/revisar?" + "&".join(partes)
    if orden_id:
        url += "#orden-" + quote(orden_id)
    return RedirectResponse(url, status_code=303)
