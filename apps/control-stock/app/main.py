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
import sys
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
               calendario_ics, conteos, control, conversaciones, cot_lead,
               cotizaciones, coworkers, crm_twenty, datos, datos_roles,
               entregas, fichas, fotos, linear_leads, mantenimiento,
               pagos_confirmar, pedidos, proveedores, resumen, seguridad,
               stock_escritura, vehiculos, venta_estado, ventas,
               wa_autor)

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


# El filtro `dinero` de las plantillas ES el formateador único de la casa
# (`calculos.dinero`): coma de miles y dos decimales, igual en Jinja que en
# Python. Hasta el 7/10/2026 esta función tenía su propio `:.2f` sin coma,
# y por eso la misma pantalla mezclaba «$50,403.00» con «$1522.50».
plantillas.env.filters["dinero"] = calculos.dinero

# El Inicio pinta el calendario con el color y el nombre que decide
# app/calendario.py; la plantilla no conoce los tipos.
plantillas.env.globals["cal_color"] = calendario.color_de
plantillas.env.globals["colores"] = colores  # la paleta unica en las plantillas
plantillas.env.globals["cal_tipo"] = calendario.nombre_de_tipo
plantillas.env.filters["fecha_dmy"] = calendario.dmy

# Cómo se ESCRIBE el nombre de una persona en pantalla (BLOQUE 53 · A15):
# «Mary» se lee «Mari». Es un filtro de formato, como `dinero` — la tabla
# y la decisión viven en Python (linear_leads.NOMBRE_VISIBLE); el dato de
# Linear, su usuario y su correo no se tocan. Se usa SOLO en lo que se
# pinta: el `value` de un campo y la clave de un filtro siguen llevando
# el nombre crudo, que es con el que casa la etiqueta `Resp:`.
plantillas.env.filters["nombre_visible"] = linear_leads.nombre_visible

# ---------------------------------------------------------------------------
# El logger de la app, enganchado a stdout (Nº17 del lote, 2/10/2026).
#
# "control_stock" no tenía NI UN handler: sus .info() —las líneas del
# calentamiento de arranque— no llegaban al stdout de Docker (el root sin
# handlers solo saca WARNING+ por el lastResort de logging, a stderr). Un
# fallo del arranque caliente era funcional pero MUDO. Acá se le da un
# handler propio a stdout, una sola vez (idempotente: reimportar o los
# tests no agregan un segundo), y `propagate` se queda en True a
# propósito: el root no tiene handlers en este deploy (uvicorn solo
# configura los suyos), así que no hay línea doble, y el caplog de pytest
# —que escucha en el root— sigue viendo todo.
# ---------------------------------------------------------------------------

def _enganchar_registro():
    registro = logging.getLogger("control_stock")
    registro.setLevel(logging.INFO)
    if not registro.handlers:
        a_stdout = logging.StreamHandler(sys.stdout)
        a_stdout.setFormatter(logging.Formatter(
            "%(levelname)s:     control-stock: %(message)s"))
        registro.addHandler(a_stdout)
    return registro


_enganchar_registro()


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
# Item 1 de Jay (5/10/2026): roles, marcas, tipos de venta, llegadas y
# términos por defecto. Mismo patrón: crear al importar es idempotente, y
# la semilla solo entra si la tabla nace vacía.
datos_roles.iniciar_tablas()
# Items 5-6-7 de Jay (5/10/2026): los 3 estados con sus dos hechos, y la
# entrega como obligación nombrada (dirección + asignado).
venta_estado.iniciar_tablas()
entregas.iniciar_tablas()
pagos_confirmar.iniciar_tablas()
# La bitácora de cambios de stock (rol Inventario, 5/10/2026): el punto
# único de escritura (stock_escritura.escribir_stock) la necesita desde
# la primera petición.
stock_escritura.iniciar_tablas()
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
    """Los emails fijados en el servidor (AJUSTES_ADMINS). Solo para el
    login con Google (entran sin invitación); la decisión de quién es
    admin vive en seguridad.es_admin()."""
    return seguridad.admins_fijados()


def _es_admin(empleada):
    """LA puerta de admin de todas las pantallas. Desde el 5/10/2026 la
    regla vive en seguridad.es_admin(): fijado en AJUSTES_ADMINS (email
    solo verificado) o hecho admin desde la pantalla de Ajustes."""
    return seguridad.es_admin(empleada)


def _ruta_en_alcance(ruta, prefijos):
    """¿La ruta cae dentro de alguno de los prefijos del alcance?
    Un prefijo "/x" cubre "/x" exacto y "/x/...", nunca "/xy"; el
    prefijo "/" cubre SOLO la raíz (el tablero de ?tab=…), jamás es un
    comodín."""
    return any(ruta == p or (p != "/" and ruta.startswith(p + "/"))
               for p in prefijos)


def _texto_403_rol(slugs):
    """El rechazo de una escritura fuera de alcance, con texto claro.
    El del rol Inventario es EXACTAMENTE el del BLOQUE 13 (sus tests lo
    citan); los demás nombran el modo, nunca a una persona."""
    if slugs == {datos_roles.SLUG_INVENTARIO}:
        return ("Tu usuario es solo de inventario: esta acción no está "
                "permitida. Pídesela al encargado.")
    if slugs == {datos_roles.SLUG_FINANZAS}:
        return ("Tu rol de Finanzas es de solo ver: esta acción no está "
                "permitida. Pídesela al encargado.")
    return ("Tu rol no permite esta acción aquí. Pídesela al encargado.")


def _puerta_por_rol(request, empleada, alcance):
    """LA puerta global por rol (BLOQUE 20 punto 1), en su V2 (BLOQUE 29
    aprobado: «el rol manda aunque seas admin»). Recibe `alcance` ya
    calculado por datos_roles.acceso_de (la MISMA fuente del menú,
    precisión 4). Con alcance None (director, sin rol, o un rol sin
    slug) no corta nada: el fail-open de transición es decisión explícita
    (precisión 8), fijada con test.

    - La EXCEPCIÓN DEL ADMIN ya NO es global: es POR RUTA
      (datos_roles.RUTAS_SISTEMA — Ajustes y sistema): un admin con rol
      restrictivo conserva el timón de Ajustes y nada más. Ver
      docs/ANALISIS-rol-manda-sobre-admin.md del repo plantaspanama.
    - /logout pasa siempre (precisión 9); /login y los estáticos ya
      pasaron ANTES de la sesión, en la lista exenta del middleware.
    - Ruta dentro del alcance → pasa (adentro, cada pantalla conserva sus
      candados propios: /stock/cambios con admin-o-director, etc.).
    - Otro GET/HEAD → pasa si el rol es de ver-todo (finanzas: modo ver
      de verdad, abre fichas), si no 303 a su casa CON el aviso honesto
      («La pestaña X es de <rol>; tu rol no la usa», BLOQUE 39.3) — la
      casa lo pinta, nunca un 403 pelado en un clic del menú.
    - Cualquier otra escritura (POST/PUT/PATCH/DELETE) → 403: un endpoint
      nuevo nace cerrado para estos roles sin acordarse de nada, y
      finanzas no tiene NINGUNA escritura (sin lista blanca de POST hasta
      el sí de Jay — BLOQUE 22.1).

    Devuelve la respuesta que corta, o None si puede seguir."""
    if alcance is None:
        return None
    ruta = request.url.path
    if ruta == "/logout" or _ruta_en_alcance(ruta, alcance["prefijos"]):
        return None
    if (seguridad.es_admin(empleada)
            and _ruta_en_alcance(ruta, datos_roles.RUTAS_SISTEMA)):
        return None
    if request.method in ("GET", "HEAD"):
        if alcance["ver_todo"]:
            return None
        aviso = quote(datos_roles.texto_pestana_ajena(
            ruta, request.query_params.get("tab", "")))
        # `rebote` y no `aviso`: la casa ya usa `aviso` para SUS mensajes
        # de éxito, y un rebote no es un éxito — se pinta distinto.
        return RedirectResponse(f"{alcance['casa']}?rebote={aviso}",
                                status_code=303)
    return PlainTextResponse(_texto_403_rol(set(alcance["slugs"])),
                             status_code=403)


def _entrar_con(request, empleada):
    """Deja la sesión, el menú por rol y el rótulo del pie en
    request.state y aplica la puerta. Todo viaja ya decidido en Python
    (regla 10): _nav.html y _lado.html solo lo recorren. Devuelve la
    respuesta que corta, o None."""
    request.state.empleada = empleada
    acceso = datos_roles.acceso_de(empleada)
    request.state.menu_nav = acceso["menu"]
    request.state.alcance_rol = acceso["alcance"]
    request.state.rol_rotulo = datos_roles.rotulo_de(empleada)
    return _puerta_por_rol(request, empleada, acceso["alcance"])


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
            if (corte := _entrar_con(request, empleada)) is not None:
                return corte
            return await call_next(request)
    empleada = seguridad.empleada_de_sesion(request.cookies.get("sesion"))
    if empleada is None:
        # La cara del CRM (bajo crm.plantaspanama.com) tiene su propio login:
        # el de siempre vive en otro dominio y su cookie no sirve aquí.
        destino = "/crm/login" if ruta.startswith("/crm/") else "/login"
        return RedirectResponse(destino, status_code=303)
    # La puerta por rol corta aquí, ANTES de cualquier handler (ver
    # _puerta_por_rol): el rol Inventario vive en su vista plana, los
    # roles del BLOQUE 20 en sus pestañas, finanzas en modo ver.
    if (corte := _entrar_con(request, empleada)) is not None:
        return corte
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


# EL TÍTULO DEL ENCABEZADO DE ESTA PANTALLA (BLOQUE 54, G1 — defecto
# encontrado el 7/10/2026 sobre la captura de 1440px).
#
# `/` es UNA sola página con cuatro secciones que se encienden con JS, así
# que su encabezado nacía con el literal «Inicio» y lo corregía el
# navegador. Con eso, ENTRAR POR URL —/?tab=ajustes, un favorito, el guion
# de capturas— dejaba «Inicio» arriba y el contenido de Ajustes debajo:
# el encabezado le mentía a quien mira.
#
# Ahora lo decide Python y llega pintado (regla 10: la decisión se calcula
# en el servidor y llega lista a la plantilla). El JS solo lo refresca al
# cambiar de pestaña sin recargar. De paso el defecto queda MEDIBLE desde
# el servidor, que es lo que lo había dejado pasar: la prueba del marco
# miraba el HTML, y en el HTML el título lo ponía el navegador.
TITULO_PESTANA = {
    "home": "Inicio", "stock": "Stock", "inv": "Inventario",
    "ajustes": "Ajustes",
}


def _titulo_pestana(parametros) -> str:
    """El título que corresponde a la pestaña con la que se ENTRA."""
    pedida = parametros.get("tab") or ""
    if pedida in TITULO_PESTANA:
        return TITULO_PESTANA[pedida]
    # /?producto=SKU abre la ficha de una planta, que vive DENTRO de Stock
    # (la recarga tras "Guardar en Odoo" viaja así, sin tab).
    if parametros.get("producto"):
        return TITULO_PESTANA["stock"]
    return TITULO_PESTANA["home"]


@app.get("/")
def inicio(request: Request, refrescar: int = 0, crear: str = "",
           volver: str = ""):
    # Sin pestaña pedida, la app ABRE en el Calendario (dueño, 22/09/2026:
    # "quita inicio y pon calendario de primero" y, al ver que la raíz
    # seguía mostrando el tablero, "todavía inicio está"). El tablero del
    # home queda solo como el lienzo de /?tab=stock y /?tab=ajustes.
    #
    # ?producto=SKU también es la pestaña Stock (el bug del lugar, spec de
    # Omar 5/10/2026): la recarga tras "Guardar en Odoo" desde el detalle
    # viaja como /?producto=SKU SIN tab (parametrosDeEstado en app.js), y
    # este redirect la mandaba al Calendario — guardar una cantidad te
    # sacaba del producto. Con el producto pedido se pinta el tablero y el
    # JS de arranque reabre ese detalle: la pantalla se queda donde estaba.
    if "tab" not in request.query_params and "producto" not in request.query_params:
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
    # La pestaña Ajustes en la V2 (BLOQUE 29): el admin ve TODOS los
    # paneles; el Director sin admin, los de NEGOCIO (roles y catálogos);
    # el fail-open de transición (sin rol que acote) la sigue viendo como
    # hoy; el resto NO la ve — pedirla por URL rebota a su casa con el
    # aviso honesto. Los paneles los decide Python, no la URL.
    es_admin = _es_admin(request.state.empleada)
    es_director = _es_director(request.state.empleada)
    puede_negocio = es_admin or es_director
    ve_ajustes = puede_negocio or request.state.alcance_rol is None
    if request.query_params.get("tab") == "ajustes" and not ve_ajustes:
        aviso = quote(datos_roles.texto_pestana_ajena("/ajustes"))
        return RedirectResponse(
            f"{request.state.alcance_rol['casa']}?rebote={aviso}",
            status_code=303)
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
        # El título del encabezado, decidido por la pestaña con la que se
        # entra (ver _titulo_pestana): entrar por URL ya llega bien.
        "titulo_pestana": _titulo_pestana(request.query_params),
        "empleada": request.state.empleada,
        "puede_fichas": puede_fichas,
        "es_admin": es_admin,
        "puede_negocio": puede_negocio,
        "ve_ajustes": ve_ajustes,
        "empleadas": (_empleadas_para_ajustes(request.state.empleada)
                      if es_admin else []),
        "invitaciones": seguridad.invitaciones_pendientes() if es_admin else [],
        "coworkers": lista_coworkers,
        "coworkers_error": coworkers_error,
        "dispositivos": dispositivos,
        "dispositivos_armados": wa_autor.configurado(),
        "responsables_wa": linear_leads.responsables() if es_admin else [],
        # Item 1 de Jay (5/10/2026): roles y catálogos de venta, solo para
        # admins. Todo llega decidido desde Python: la lista completa de
        # roles (inactivos incluidos, apagados), los 3 deberes con su
        # ocupante o su aviso, los catálogos enteros y las empleadas
        # activas del login para el selector de "poner persona".
        "roles_ajustes": datos_roles.listar_roles(solo_activos=False) if puede_negocio else [],
        "deberes_ajustes": datos_roles.deberes_estado() if puede_negocio else [],
        "marcas_ajustes": datos_roles.catalogo_completo("marcas") if puede_negocio else [],
        "tipos_ajustes": datos_roles.catalogo_completo("tipos_venta") if puede_negocio else [],
        "llegadas_ajustes": datos_roles.catalogo_completo("llegadas") if puede_negocio else [],
        "personas_roles": ([{"usuario": e["usuario"], "nombre": e["nombre"]}
                            for e in seguridad.listar() if e["activa"]]
                           if puede_negocio else []),
        "aviso_ajustes": request.query_params.get("aviso"),
        # Precios de envío rebotados (regla 5, Nº7 del lote): el campo que
        # falló, el mensaje que va debajo de él y lo que se había tecleado
        # en los 4 campos — todo viajó en el redirect del POST, para que
        # el rechazo no borre lo escrito ni mande el aviso al tope.
        "campo_error_envio": (
            (request.query_params.get("campo") or "").strip()[:40]
            if request.query_params.get("aviso") == "envio-invalido" else ""),
        "error_envio": ("Este precio no se entiende: escribe un número "
                        "mayor que cero, como 12.50 o 12,50. No se guardó "
                        "ninguno."
                        if request.query_params.get("aviso") == "envio-invalido"
                        else ""),
        "envio_tecleado": {
            opcion["clave"]: request.query_params.get("v_" + opcion["clave"])
            for opcion in ventas.OPCIONES_ENVIO
            if request.query_params.get("v_" + opcion["clave"]) is not None},
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
    """El ajuste rápido del modal y el − cantidad + de la lista (A3/A4 del
    BLOQUE 53). El guardado real pasa por el punto único de escritura
    (stock_escritura.escribir_stock → order-api), que compara `esperada`
    contra Odoo: si alguien movió el stock en el medio, vuelve `conflicto`
    con el valor fresco y nada se escribe.

    Dos formas de mandar la cantidad, y las dos las juzga PYTHON:

    - `cantidad`: un entero ≥ 0, el contrato de siempre del modal. Lo que
      no sea exactamente eso es `peticion_invalida` (400).
    - `cantidadTexto`: lo TECLEADO tal cual en el campo de la lista, sin
      tocar. Lo valida `stock_escritura.cantidad_contada()` —el mismo
      juez y el mismo texto que la vista plana del rol Inventario— y lo
      ilegible vuelve como {"error": "cantidad", "mensaje": …} para que
      la pantalla lo pinte DEBAJO del campo, conservando lo tecleado
      (regla del lote de formularios: nunca se vuelve 0 en silencio).

    El navegador no decide nada: manda el texto y pinta la respuesta."""
    cuerpo = await request.json()
    sku = cuerpo.get("sku")
    cantidad = cuerpo.get("cantidad")
    esperada = cuerpo.get("esperada")
    if "cantidadTexto" in cuerpo:
        cantidad, mensaje = stock_escritura.cantidad_contada(
            cuerpo.get("cantidadTexto"))
        if mensaje:
            return Response(
                json.dumps({"error": "cantidad", "mensaje": mensaje}),
                status_code=400, media_type="application/json")
    if (not isinstance(sku, str) or not isinstance(cantidad, int) or cantidad < 0
            or not isinstance(esperada, int)):
        return Response(json.dumps({"error": "peticion_invalida"}), status_code=400,
                        media_type="application/json")
    try:
        respuesta = stock_escritura.escribir_stock(
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
    if respuesta["registro_fallo"]:
        # El error ruidoso del review: Odoo quedó escrito pero la bitácora
        # no. El texto lo decide Python; app.js solo lo muestra.
        resultado = {**resultado, "aviso": stock_escritura.AVISO_REGISTRO}
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
    registro_aviso = ""
    if cantidad > 0:
        try:
            respuesta = stock_escritura.escribir_stock(
                [{"sku": sku, "cantidad": cantidad, "esperada": 0}],
                request.state.empleada["id"], "alta_de_planta",
            )
            stock = respuesta["resultados"][0]["resultado"]
            if respuesta["registro_fallo"]:
                registro_aviso = stock_escritura.AVISO_REGISTRO
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
            "cantidad": cantidad, "stock": stock, "agregadaA": agregada_a,
            # El error ruidoso de la bitácora (stock_cambio): vacío casi
            # siempre; con texto, el JS lo muestra tal cual.
            "registroAviso": registro_aviso}


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
    """403 si quien llama no es admin; None si puede seguir. Es el
    candado de lo TÉCNICO de Ajustes (quién entra / cómo corre el
    sistema): invitar, cancelar invitación, revocar, admin, coworkers,
    envío y dispositivos — la partición de la V2 (BLOQUE 28/29)."""
    if not _es_admin(request.state.empleada):
        return Response("Solo para administradores.", status_code=403)
    return None


def _slugs_de(empleada):
    """Los slugs de los roles activos de la sesión (solo los 5 del plan)."""
    return {r["slug"] for r in datos_roles.roles_activos_de(empleada["id"])
            if r["slug"]}


def _es_director(empleada):
    return datos_roles.SLUG_DIRECTOR in _slugs_de(empleada)


def _admin_o_director(request):
    """El candado de lo de NEGOCIO de Ajustes (V2, BLOQUE 28: el Director
    sin ser admin reparte roles y edita los catálogos del negocio) y de
    /stock/cambios (la auditoría que el Director necesita). 403 con texto
    claro si no es ni admin ni Director; None si puede seguir."""
    empleada = request.state.empleada
    if _es_admin(empleada) or _es_director(empleada):
        return None
    return Response("Solo para administradores o el rol Director "
                    "(Ajustes → Roles).", status_code=403)


def _puede_supervisar(empleada):
    """La supervisión de negocio (/conversaciones, /revisar y su nota):
    en la V2 es de los ROLES Director y Finanzas, ya no del admin pelado.
    EXCEPCIÓN DE TRANSICIÓN, parte del fail-open sin-rol (precisión 8):
    un admin cuyo alcance es None (sin rol, o solo pods sin slug — hoy
    korto-qa) la sigue viendo; cuando el fail-open muera, esta línea
    muere con él. Un admin con rol restrictivo NO llega aquí de todos
    modos: la puerta global ya lo rebotó."""
    if _slugs_de(empleada) & {datos_roles.SLUG_DIRECTOR,
                              datos_roles.SLUG_FINANZAS}:
        return True
    return (_es_admin(empleada)
            and datos_roles.acceso_de(empleada)["alcance"] is None)


def _crm_lectura(empleada):
    """BLOQUE 39.2: el rol Atención abre el CRM completo (/control) en
    SOLO LECTURA — ve el tablero entero («Ver todos» desde su Mi CRM),
    pero lo ajeno no se toca: 403 duro en los POST (no el redirect con
    error de siempre) y el chat de una ficha ajena no se muestra. No
    aplica si además es Director u Operaciones, ni a un admin."""
    slugs = _slugs_de(empleada)
    return (datos_roles.SLUG_ATENCION in slugs
            and not slugs & {datos_roles.SLUG_DIRECTOR,
                             datos_roles.SLUG_OPERACIONES}
            and not _es_admin(empleada))


def _solo_supervision(request):
    """403 si quien llama no supervisa (ver _puede_supervisar); None si
    puede seguir."""
    if _puede_supervisar(request.state.empleada):
        return None
    return Response("Esta pantalla es de los roles Director y Finanzas "
                    "(Ajustes → Roles).", status_code=403)


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
    tecleado = {opcion["clave"]: str(form.get(opcion["clave"]) or "").strip()
                for opcion in ventas.OPCIONES_ENVIO}
    for opcion in ventas.OPCIONES_ENVIO:
        crudo = tecleado[opcion["clave"]].replace(",", ".")
        try:
            precio = float(crudo)
        except ValueError:
            precio = 0.0
        if precio <= 0:
            # Regla 5 (Nº7 del lote): el rechazo viaja con el campo que
            # falló Y los 4 montos tecleados, para que la pantalla los
            # conserve y pinte el error debajo del campo — antes el
            # redirect pelado los borraba y el aviso genérico salía arriba.
            vuelta = ("/?tab=ajustes&aviso=envio-invalido"
                      + f"&campo={quote(opcion['clave'])}"
                      + "".join(f"&v_{clave}={quote(valor)}"
                                for clave, valor in tecleado.items()))
            return RedirectResponse(vuelta, status_code=303)
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


def _empleadas_para_ajustes(yo):
    """Las filas de «Con acceso» con todo ya decidido en Python: si es
    admin, de dónde le viene (servidor o pantalla), qué botón va en su
    fila y el rastro del último cambio. La plantilla solo pinta.

    Reglas de los botones (5/10/2026): solo un admin llega aquí; nadie se
    quita el admin a sí mismo (botón deshabilitado, y el POST lo rechaza
    igual); a quien está fijado en AJUSTES_ADMINS no se le puede quitar
    por pantalla — la variable queda como semilla y respaldo."""
    filas = seguridad.listar()
    for e in filas:
        fijado = seguridad.fijado_en_servidor(
            e["usuario"], e.get("email"), e.get("email_verificado"))
        e["admin"] = fijado or bool(e.get("es_admin"))
        e["origen_admin"] = ("servidor" if fijado else "pantalla") if e["admin"] else ""
        if not e["admin"]:
            e["boton_admin"], e["nota_admin"] = "hacer", ""
        elif fijado:
            e["boton_admin"], e["nota_admin"] = "", "fijado en el servidor"
        elif e["usuario"] == yo["id"]:
            e["boton_admin"], e["nota_admin"] = "", "nadie se quita el admin a sí mismo"
        else:
            e["boton_admin"], e["nota_admin"] = "quitar", ""
        # El rastro discreto, solo del admin dado por pantalla (el fijado
        # no tiene quién/cuándo: viene del .env).
        e["rastro_admin"] = (
            bool(e.get("es_admin")) and not fijado and bool(e.get("admin_cambiado_en")))
    return filas


@app.post("/ajustes/admin")
async def ajustes_admin(request: Request):
    """Hacer o quitar admin desde la pantalla, sin tocar el .env ni
    redesplegar. Solo un admin llama esto; los rechazos repiten en el
    servidor lo que la pantalla ya deshabilita (que no te quedes afuera)."""
    if (rechazo := _solo_admin(request)) is not None:
        return rechazo
    form = await request.form()
    usuario = (form.get("usuario") or "").strip()
    dar = form.get("dar") == "1"
    if not usuario:
        return RedirectResponse("/?tab=ajustes", status_code=303)
    yo = request.state.empleada
    if not dar:
        # Nadie se quita el admin a sí mismo: siempre queda un admin adentro.
        if usuario == yo["id"]:
            return RedirectResponse("/?tab=ajustes&aviso=admin-propio",
                                    status_code=303)
        # Los de AJUSTES_ADMINS no se quitan por pantalla: son la semilla.
        fila = next((e for e in seguridad.listar() if e["usuario"] == usuario), None)
        if fila is not None and seguridad.fijado_en_servidor(
                fila["usuario"], fila.get("email"), fila.get("email_verificado")):
            return RedirectResponse("/?tab=ajustes&aviso=admin-fijado",
                                    status_code=303)
    quien = yo.get("nombre") or yo["id"]
    if seguridad.fijar_admin(usuario, dar, quien) == "no_existe":
        return RedirectResponse("/?tab=ajustes&aviso=admin-no-existe",
                                status_code=303)
    aviso = "admin-dado" if dar else "admin-quitado"
    return RedirectResponse(f"/?tab=ajustes&aviso={aviso}", status_code=303)


# ---------------------------------------------------------------------------
# Ajustes: roles y catálogos de venta (Item 1 de Jay, 5/10/2026).
# Son el lado de NEGOCIO de Ajustes (V2, BLOQUE 28/29): candado
# _admin_o_director — el Director reparte roles y edita los catálogos
# sin ser admin; lo TÉCNICO (invitar, revocar, admin, coworkers, envío,
# dispositivos) sigue _solo_admin. Solo tocan las tablas locales de
# datos_roles: ni Linear, ni Twenty, ni Odoo. Cada POST redirige a la
# pestaña con su aviso.
# ---------------------------------------------------------------------------

def _vuelta_ajustes(aviso):
    return RedirectResponse(f"/?tab=ajustes&aviso={aviso}", status_code=303)


# Códigos de datos_roles -> aviso de la pestaña. Lo que no esté aquí sale
# con su propio nombre (los códigos y los avisos comparten vocabulario).
_AVISOS_ROLES = {
    "vacio": "nombre-vacio",
    "repetido": "nombre-repetido",
    "no_existe": "fila-no-existe",
    "empleada_invalida": "rol-empleada-invalida",
    "deber_invalido": "deber-invalido",
    "rol_con_otro_deber": "deber-rol-ocupado",
    "deber_sin_persona": "deber-sin-persona",
    "catalogo_invalido": "catalogo-invalido",
}


def _n_entero(valor):
    try:
        return int(valor)
    except (TypeError, ValueError):
        return 0


@app.post("/ajustes/roles/renombrar")
async def ajustes_rol_renombrar(request: Request):
    if (rechazo := _admin_o_director(request)) is not None:
        return rechazo
    form = await request.form()
    error = datos_roles.renombrar_rol(_n_entero(form.get("rol")),
                                      form.get("nombre"))
    return _vuelta_ajustes(_AVISOS_ROLES.get(error, "rol-renombrado"))


@app.post("/ajustes/roles/duplicar")
async def ajustes_rol_duplicar(request: Request):
    """La copia para los pods: mismas personas, sin el deber. El nombre es
    opcional (sin él sale "Copia de <rol>")."""
    if (rechazo := _admin_o_director(request)) is not None:
        return rechazo
    form = await request.form()
    yo = request.state.empleada
    error, _ = datos_roles.duplicar_rol(_n_entero(form.get("rol")),
                                        form.get("nombre"),
                                        por=yo.get("nombre") or yo["id"])
    return _vuelta_ajustes(_AVISOS_ROLES.get(error, "rol-duplicado"))


@app.post("/ajustes/roles/persona/poner")
async def ajustes_rol_persona_poner(request: Request):
    if (rechazo := _admin_o_director(request)) is not None:
        return rechazo
    form = await request.form()
    yo = request.state.empleada
    error = datos_roles.poner_persona(_n_entero(form.get("rol")),
                                      (form.get("usuario") or "").strip(),
                                      yo.get("nombre") or yo["id"])
    return _vuelta_ajustes(_AVISOS_ROLES.get(error, "persona-puesta"))


@app.post("/ajustes/roles/persona/quitar")
async def ajustes_rol_persona_quitar(request: Request):
    """Quitar a alguien de un rol. Si el rol carga un deber y queda sin
    nadie, el quite se aplica y el aviso lo DICE (deber-sin-persona): un
    deber se reasigna, nunca se vacía en silencio."""
    if (rechazo := _admin_o_director(request)) is not None:
        return rechazo
    form = await request.form()
    yo = request.state.empleada
    error = datos_roles.quitar_persona(_n_entero(form.get("rol")),
                                       (form.get("usuario") or "").strip(),
                                       por=yo.get("nombre") or yo["id"])
    return _vuelta_ajustes(_AVISOS_ROLES.get(error, "persona-quitada"))


@app.post("/ajustes/deberes")
async def ajustes_deber_asignar(request: Request):
    if (rechazo := _admin_o_director(request)) is not None:
        return rechazo
    form = await request.form()
    error = datos_roles.asignar_deber((form.get("deber") or "").strip(),
                                      _n_entero(form.get("rol")))
    return _vuelta_ajustes(_AVISOS_ROLES.get(error, "deber-asignado"))


@app.post("/ajustes/catalogo/agregar")
async def ajustes_catalogo_agregar(request: Request):
    if (rechazo := _admin_o_director(request)) is not None:
        return rechazo
    form = await request.form()
    error = datos_roles.catalogo_agregar(
        (form.get("tabla") or "").strip(), form.get("nombre"),
        termino=form.get("termino") or "",
        override_visible=form.get("override") == "1")
    return _vuelta_ajustes(_AVISOS_ROLES.get(error, "catalogo-agregado"))


@app.post("/ajustes/catalogo/renombrar")
async def ajustes_catalogo_renombrar(request: Request):
    if (rechazo := _admin_o_director(request)) is not None:
        return rechazo
    form = await request.form()
    error = datos_roles.catalogo_renombrar(
        (form.get("tabla") or "").strip(), _n_entero(form.get("n")),
        form.get("nombre"))
    return _vuelta_ajustes(_AVISOS_ROLES.get(error, "catalogo-renombrado"))


@app.post("/ajustes/catalogo/activar")
async def ajustes_catalogo_activar(request: Request):
    """Desactivar o reactivar una fila de catálogo. Nada se borra: la
    inactiva se queda con su historia y sale de los selectores futuros."""
    if (rechazo := _admin_o_director(request)) is not None:
        return rechazo
    form = await request.form()
    prender = form.get("activo") == "1"
    error = datos_roles.catalogo_activar(
        (form.get("tabla") or "").strip(), _n_entero(form.get("n")), prender)
    return _vuelta_ajustes(_AVISOS_ROLES.get(
        error, "catalogo-prendido" if prender else "catalogo-apagado"))


@app.post("/ajustes/tipos/termino")
async def ajustes_tipo_termino(request: Request):
    if (rechazo := _admin_o_director(request)) is not None:
        return rechazo
    form = await request.form()
    error = datos_roles.fijar_termino(
        _n_entero(form.get("n")), form.get("termino"),
        form.get("override") == "1")
    return _vuelta_ajustes(_AVISOS_ROLES.get(error, "termino-guardado"))


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
        respuesta = stock_escritura.escribir_stock(
            ajustes, request.state.empleada["id"], "conteo_quincenal")
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
    if respuesta["registro_fallo"]:
        # Odoo quedó ajustado y el conteo confirmado, pero la bitácora
        # local no se pudo escribir para estos SKUs: se dice en la misma
        # pantalla de revisión (error ruidoso del review), nunca silencio.
        conteo = datos.conteo(n)
        return plantillas.TemplateResponse(request, "revisar.html", {
            "empleada": request.state.empleada, "conteo": conteo,
            "errores": [stock_escritura.AVISO_REGISTRO + " Productos: "
                        + ", ".join(respuesta["registro_fallo"]) + "."],
            "diferencias": conteo["datos"]["diferencias"],
            "sin_contar": conteo["datos"].get("sin_contar", 0),
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
def venta(request: Request, error: str = "", abrir: str = "", vista: str = "",
          ver: str = ""):
    # La pestaña: el botón grande "+ Venta" (arriba de los servicios,
    # dueño 28/09/2026) y el historial local.
    #
    # Este GET ya NO recibe ?lead= (auditoría de GETs que mutan,
    # precisión 2 del review de roles): dejar el lead pendiente ESCRIBE
    # en la base, así que ese camino ahora es POST /venta/lead. Un enlace
    # viejo con ?lead= simplemente pinta la pestaña, sin tocar nada.
    usuario = request.state.empleada["id"]
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
    # La vista del CUERPO CELULAR (BLOQUE 37, pantalla 25): píldoras
    # Pendientes/Pagadas que navegan por GET — la decisión de qué vale
    # es de Python (regla 10); una vista manoseada cae en la de siempre.
    if vista not in VISTAS_VENDER_MOVIL:
        vista = "pendientes"
    # Las DOS vistas de la pestaña (punto 8 del BLOQUE 56): el tablero de
    # siempre y la tabla. Cuál se mira lo decide PYTHON (regla 10) — la
    # plantilla pinta una o la otra, no alterna nada con JS —, y una vista
    # manoseada cae en el tablero, igual que `vista`.
    if ver not in VISTAS_VENDER:
        ver = "tablero"
    filas, aviso_lista = _lista_vender(request, vista, ver)
    columnas = _vender_columnas(filas)
    # Muta las columnas con su cara de sección (la necesita el tablero del
    # teléfono) aunque las píldoras no se pinten en la vista de tabla.
    pildoras = _vender_movil(columnas, vista)
    return plantillas.TemplateResponse(request, "venta.html", {
        # La cola de pagos (item 6) es de los tres deberes: el enlace
        # solo existe para quien la puede abrir.
        "cola_pagos_visible": pagos_confirmar.puede_ver(usuario),
        "lead_pendiente": ventas.lead_pendiente(usuario),
        # El menu de abajo muestra Fichas con la misma regla del principal.
        "puede_fichas": fichas.es_editora(request.state.empleada["id"]),
        "ventas_activo": ventas.configurado(),
        "error_venta": error or None,
        "en_curso": en_curso,
        "tipos_servicio": [(t, cotizaciones.etiqueta_para_cotizar(t))
                          for t in cotizaciones.ORDEN_TIPOS],
        "vender_lista": filas,
        "aviso_lista": aviso_lista,
        # Diseño Orquesta (2/10/2026): la misma lista única, agrupada en
        # columnas por su estado de HOY — la agrupación es presentación,
        # cada tarjeta conserva sus mismas acciones y rutas.
        "vender_columnas": columnas,
        # Las dos vistas y el segmento que las cambia (punto 8 del BLOQUE
        # 56): el control es el MISMO de la casa (_vistas_compras.html /
        # Pedidos) y los dos enlaces los arma Python.
        "vender_vista": ver,
        "vender_vistas": _vender_vistas(ver, vista),
        # Las filas de la vista de tabla: contacto · fecha · interés ·
        # etapa · monto, todas de lo que la lista única YA trae — ni un
        # viaje nuevo a Odoo.
        "vender_tabla": _vender_tabla(filas) if ver == "tabla" else [],
        # La cara celular del cuerpo (pantalla 25): las píldoras con sus
        # conteos reales y las columnas vestidas de secciones (_vender_movil
        # les cuelga su título y su orden del lienzo); el aviso rojo de
        # atoradas solo si el dato REAL existe (_vender_alerta, nunca un
        # número inventado); y la URL de volver que no pierde la vista.
        #
        # Las píldoras son del TABLERO del teléfono: lo que esconden son
        # sus secciones. En la tabla no esconden nada, así que no se
        # pintan — un control que no hace nada es peor que no tenerlo (la
        # misma razón por la que las tres rayas no salen en 768-899).
        "vender_pildoras": pildoras if ver == "tablero" else [],
        "vender_alerta": _vender_alerta(filas),
        "volver_url": _volver_vender(vista, ver),
        # La tarjeta abierta (?abrir=v3 | ?abrir=s3): panel a la derecha en
        # computadora, pantalla completa en el teléfono. Mismo patrón
        # servidor-y-enlaces que «Ventas a revisar» (revisar_ventas.html).
        "abierta": _vender_abierta(filas, abrir),
    })


@app.get("/venta/panel")
def venta_panel(request: Request, abrir: str = "", vista: str = "",
                ver: str = ""):
    """El PEDAZO del panel de una tarjeta de Vender, ya armado (A5).

    Lo pide panel.js al tocar una tarjeta, con la MISMA query del enlace
    de siempre (`?vista=…&abrir=v3`), y lo mete en su caja sin recargar el
    tablero — así no se pierde el lugar en la lista. Sin `abrir`, o con
    uno que no apunta a nada, la plantilla sale vacía: eso es exactamente
    lo que pide el enlace de CERRAR.

    Mismo armado que la pantalla entera (_lista_vender + _vender_abierta):
    acá no se decide nada nuevo, y la puerta por rol es la misma (el
    prefijo «/venta» del alcance cubre esta ruta).
    """
    if vista not in VISTAS_VENDER_MOVIL:
        vista = "pendientes"
    if ver not in VISTAS_VENDER:
        ver = "tablero"
    filas, _aviso = _lista_vender(request, vista, ver)
    return plantillas.TemplateResponse(request, "_panel_venta.html", {
        "abierta": _vender_abierta(filas, abrir),
        "volver_url": _volver_vender(vista, ver),
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


def _query_vender(vista, ver="tablero"):
    """Los parámetros que una URL de Vender tiene que ARRASTRAR para no
    devolverte a otra vista de la que estabas: la del celular
    (?vista=pagadas) y la de la pestaña (?ver=tabla).

    Las dos por omisión no se escriben: en la vista de siempre las URLs
    son las de toda la vida (`/venta?abrir=v3`), y eso está clavado con
    pruebas."""
    partes = []
    if vista != "pendientes":
        partes.append(f"vista={vista}")
    if ver != "tablero":
        partes.append(f"ver={ver}")
    return "&".join(partes)


def _base_abrir(vista, ver="tablero"):
    """El comienzo de la URL que abre el panel de una tarjeta. En la
    vista de siempre es el /venta? de toda la vida (las URLs no cambian);
    en otra vista —del celular (?vista=pagadas) o de la pestaña
    (?ver=tabla)— la arrastra, para que abrir y cerrar un panel no te
    devuelva a la vista equivocada (en esta casa nunca se pierde el lugar
    en una lista)."""
    query = _query_vender(vista, ver)
    return f"/venta?{query}&" if query else "/venta?"


def _volver_vender(vista, ver="tablero"):
    """La URL de CERRAR el panel: la misma pantalla, sin `?abrir=`, en la
    misma vista. Es la pareja de `_base_abrir`."""
    query = _query_vender(vista, ver)
    return f"/venta?{query}" if query else "/venta"


def _fila_venta(request, v, vista="pendientes", ver="tablero"):
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
        # Diseño Orquesta (2/10/2026): color según el interés — una venta
        # de plantas es retail (familia green de la paleta única).
        "acento": colores.acento_servicio("retail"),
        "chip_estilo": colores.chip_estilo("green"),
        "chip_texto": "Plantas",
        "contacto": _contacto_de(v.get("celular")),
        # El ancla de la tarjeta (no perder el lugar en la lista) y la URL
        # que abre su panel (?abrir=, mismo patrón que Ventas a revisar).
        "ancla": f"v-{v['n']}",
        "abrir_url": f"{_base_abrir(vista, ver)}abrir=v{v['n']}#v-{v['n']}",
    }


def _lista_vender(request, vista="pendientes", ver="tablero"):
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
        [_fila_venta(request, v, vista, ver) for v in ventas.ventas_todas()
         if v["estado"] != "cancelada"]
        + [_fila_servicio(c, vista, ver) for c in servicios
           if not c["cancelada"]]
    )
    filas.sort(key=lambda f: (_numero_de_orden(f["orden"]) is not None,
                              _numero_de_orden(f["orden"]) or 0),
              reverse=True)
    # El chip de los 3 estados (items 5-7): una consulta para toda la
    # lista; una venta sin fila está en 1 (Acordada).
    estados3 = venta_estado.estados_de()
    for fila in filas:
        origen = "venta" if fila["tipo"] == "venta" else "servicio"
        numero = (estados3.get((origen, fila["n"])) or {}).get("estado", 1)
        fila["estado3"] = {
            "n": numero,
            "texto": venta_estado.ETIQUETA_CORTA[numero],
            "css": "b-ok" if numero >= 2 else "b-bajo",
            "href": f"/venta/estado/{origen}/{fila['n']}",
        }
        # La tarjeta simple (BLOQUE 32, pág. 09): UN dato contextual,
        # decidido aquí — la plantilla solo lo pinta.
        fila["linea"], fila["linea_alerta"] = _linea_tarjeta(fila)
        fila["pagada"] = (fila["tipo"] == "venta"
                          and fila["estado"] == "pagado")
        # El arrastre como gesto de NAVEGACIÓN (BLOQUE 40): a qué
        # columnas se puede soltar esta tarjeta y a qué URL navega el
        # drop. Decidido acá (regla 10); soltar nunca escribe.
        fila["arrastre"] = _arrastre_vender(fila)
    return filas, aviso


_NOMBRES_METODO = {"yappy": "Yappy", "efectivo": "Efectivo"}


def _linea_tarjeta(f):
    """(texto, alerta): el ÚNICO dato contextual de la tarjeta simple del
    tablero (pág. 09 del diseño) — todo lo demás vive en el panel que la
    tarjeta abre. La decisión es de Python (regla 10): qué merece el
    renglón lo dice el estado de HOY, nunca la plantilla.

    - pagada: cómo pagó + cuándo (como el «Yappy · entrega mié 14» del
      diseño); - cotización: cuándo se cotizó; - vendida/facturado de
      servicio: dónde vive el cobro; - atorada a medio pipeline: el paso
      pendiente, en alerta si el último intento falló."""
    if f["tipo"] == "servicio":
        if f["facturada"]:
            return "Facturado · cobro en Odoo", False
        return f"Cotizada · {f['fecha_texto']}", False
    if f["estado"] == "pagado":
        metodo = _NOMBRES_METODO.get(f.get("metodo"))
        texto = f"{metodo} · {f['fecha_texto']}" if metodo else f["fecha_texto"]
        return texto, False
    if f["estado"] == "cotizacion":
        return f"Cotizada · {f['fecha_texto']}", False
    # vendida o atorada a medio pipeline: la etiqueta ya dice el paso
    # («Venta confirmada · cobro en Odoo», «Facturada · pago pendiente»).
    return f["etiqueta_estado"], bool(f.get("ultimo_error"))


def _fila_servicio(c, vista="pendientes", ver="tablero"):
    """Una cotización de servicio, vestida para la lista única. "tipo"
    pasa a ser el discriminador de la plantilla ("servicio") y el tipo de
    NEGOCIO (renta, boda, …) sobrevive en "tipo_servicio" — antes se
    pisaba y el color del interés no tenía de dónde salir."""
    return {
        **c, "tipo": "servicio", "tipo_servicio": c["tipo"],
        "acento": colores.acento_servicio(c["tipo"]),
        "chip_estilo": colores.chip_servicio(c["tipo"]),
        "chip_texto": c["etiqueta_tipo"],
        "contacto": _contacto_de(c.get("celular")),
        "ancla": f"cot-{c['n']}",
        "abrir_url": f"{_base_abrir(vista, ver)}abrir=s{c['n']}#cot-{c['n']}",
    }


def _contacto_de(celular):
    """Los enlaces de contacto del panel (llamar y abrir el chat de
    WhatsApp) a partir del celular guardado; None si no hay. Un celular
    local de 8 dígitos se completa con 507, igual que _enlace_whatsapp."""
    if not celular:
        return None
    digitos = "".join(c for c in celular if c.isdigit())
    if not digitos:
        return None
    if len(digitos) == 8:
        digitos = "507" + digitos
    return {"tel": f"tel:+{digitos}", "wa": f"https://wa.me/{digitos}"}


def _cid_de_contacto(f):
    """El id del contacto de ESTA venta o cotización en la pestaña
    Contactos (A12 del BLOQUE 53).

    La clave es la de `contactos._unir()`, que casa EN LECTURA: con
    teléfono, «t<normalizado>»; sin teléfono, su propia aparición local
    («lv<n>» una venta, «ls<n>» una cotización). Siempre resuelve — la
    venta misma es una de las apariciones que esa pantalla une —, así que
    la fila «Contacto» del panel abre SU PÁGINA y no un buscador.

    La normalización es la de `contactos.normalizar_telefono`, nunca una
    copia: lo que decide quién es la misma persona vive allá. Y el enlace
    está pineado con prueba (test_paneles_b53): si Contactos cambiara su
    clave, la suite se pone roja en vez de dejar un enlace muerto en
    silencio."""
    tel = contactos.normalizar_telefono(f.get("celular"))
    if tel:
        return "t" + tel
    return ("lv" if f["tipo"] == "venta" else "ls") + str(f["n"])


def _quien_vender(f):
    """Las DOS filas «Contacto» y «Lead» del panel de Vender (A12): de
    quién es esta venta o cotización, y de qué lead salió.

    Las dos se pintan SIEMPRE. Cuando el dato no existe la fila lo DICE en
    su lugar, sin enlace, en vez de desaparecer: un hueco callado deja al
    vendedor sin saber si esta venta no tiene lead o si la pantalla se lo
    comió. Y nada se adivina — el lead es el `lead_issue` (LEAD-NN) que el
    espejo del CRM grabó al crear la venta, no una búsqueda por nombre."""
    que = "venta" if f["tipo"] == "venta" else "cotización"
    issue = (f.get("lead_issue") or "").strip()
    pp = (f.get("lead_ref") or "").strip()
    contacto = {
        "etiqueta": "Contacto",
        "valor": (f.get("cliente") or "").strip() or "Sin nombre guardado",
        "href": "/contactos/" + quote(_cid_de_contacto(f)),
        "nota": ("" if (f.get("celular") or "").strip() else
                 f"Sin teléfono: su página junta solo esta {que}"),
    }
    if issue:
        lead = {"etiqueta": "Lead", "valor": issue,
                "href": "/control?abrir=" + quote(issue),
                "nota": pp}
    elif pp:
        lead = {"etiqueta": "Lead", "valor": pp, "href": "",
                "nota": ("Sin su número LEAD-NN guardado: no hay por dónde "
                         "abrirlo en el CRM")}
    else:
        lead = {"etiqueta": "Lead", "valor": "Sin lead", "href": "",
                "nota": f"Esta {que} no salió de un lead del CRM"}
    return [contacto, lead]


# Las tres columnas del tablero de Vender (diseño Orquesta). Son los
# estados que EXISTEN hoy — no las columnas soñadas del lienzo (Abonado,
# Pagado esta semana), que piden datos de pagos que esta lista no lee.
COLUMNAS_VENDER = (
    ("cotizado", "Cotizado",
     "No aparta plantas. El pedido nace cuando el cliente paga o abona."),
    ("confirmado", "Confirmado · falta cobrar",
     "El cobro se registra en Odoo."),
    ("pagado", "Pagado", "Cobradas por completo."),
)


def _columna_vender(f):
    """En qué columna cae una fila de la lista única, según su estado de
    HOY: cotización (planta o servicio sin facturar) · confirmado (vendida,
    facturada o a medio pipeline) · pagado."""
    if f["tipo"] == "servicio":
        return "confirmado" if f["facturada"] else "cotizado"
    if f["estado"] == "cotizacion":
        return "cotizado"
    if f["estado"] == "pagado":
        return "pagado"
    return "confirmado"


_TITULOS_VENDER = {clave: titulo for clave, titulo, _pista in COLUMNAS_VENDER}


def _arrastre_vender(f):
    """El arrastre de una tarjeta de Vender como GESTO DE NAVEGACIÓN
    (BLOQUE 40): {url, destinos} — a qué columnas se puede SOLTAR y a
    qué URL NAVEGA el drop —, o None si la tarjeta no se arrastra.

    LA REGLA DE ORO: SOLTAR NUNCA ESCRIBE. El drop solo abre el panel
    de cobro EXISTENTE (/venta/pago/<n>): método, monto y botón negro
    ya viven allá, y cerrarlo sin pagar deja todo como estaba — no hay
    nada que revertir, porque nada se escribió. Por eso:

    - una cotización va hacia «Confirmado · falta cobrar» o «Pagado»
      (los dos gestos terminan en el mismo panel de cobro);
    - una confirmada (vendida, facturada o atorada a medio pipeline)
      solo hacia «Pagado» — su paso pendiente es el mismo panel;
    - una pagada no se arrastra: no tiene paso siguiente;
    - un servicio no se arrastra: su cobro vive en el kanban de Odoo,
      no hay panel propio al que navegar.

    HACIA ATRÁS no hay destino (el JS pinta no-drop): el candado real
    del retroceso sigue siendo el del motor (venta_estado.bloqueo_manual
    tras POST /venta/estado — solo el system manager baja un estado);
    esto es cortesía, como el draggable de las tarjetas de Control."""
    if f["tipo"] != "venta":
        return None
    columna = _columna_vender(f)
    if columna == "cotizado":
        destinos = ["confirmado", "pagado"]
    elif columna == "confirmado":
        destinos = ["pagado"]
    else:
        return None
    return {"url": f"/venta/pago/{f['n']}", "destinos": destinos}


def _mover_a_vender(f, panel):
    """El bloque «Mover a» del panel (BLOQUE 40): el MISMO gesto del
    arrastre para el celular (≤899px, sin drag), como enlaces {texto,
    href} ya decididos acá (regla 10). Cada enlace navega al panel de
    cobro existente — tocar nunca escribe, igual que soltar.

    NO se repite la puerta que el panel YA tiene. Si el botón negro
    lleva al mismo lugar que el gesto, «Mover a» queda vacío: en una
    cotización ese botón ES «Facturar / Pagado» → /venta/pago/<n>, el
    mismo destino del arrastre y con mejor nombre, así que ofrecerlo de
    nuevo serían tres puertas al mismo cuarto (y rompería la regla de
    siempre: un solo botón negro, las acciones una sola vez).

    Donde SÍ aporta es en una confirmada: ahí el botón negro es el PDF
    y el panel no tiene ninguna puerta al cobro, así que en el celular
    —sin arrastre— «Mover a → Pagado» es el ÚNICO camino."""
    gesto = f.get("arrastre")
    if not gesto:
        return []
    if gesto["url"] == ((panel.get("boton") or {}).get("href") or ""):
        return []
    return [{"texto": _TITULOS_VENDER[destino], "href": gesto["url"]}
            for destino in gesto["destinos"]]


def _vender_columnas(filas):
    """Las columnas del tablero, armadas en Python (regla 10): la lista
    única de siempre repartida por estado, con cuenta y total por
    columna. El ORDEN dentro de cada columna es el de la lista (número de
    orden descendente): agrupar es presentación, no otro orden."""
    columnas = []
    for clave, titulo, pista in COLUMNAS_VENDER:
        items = [f for f in filas if _columna_vender(f) == clave]
        columnas.append({
            "clave": clave, "titulo": titulo, "pista": pista,
            "items": items, "cuenta": len(items),
            "total": sum((f["total"] or 0) for f in items),
        })
    return columnas


# ---------------------------------------------------------------------------
# LAS DOS VISTAS DE VENDER (punto 8 del BLOQUE 56, 7/10/2026)
#
# Abraham pidió dos cosas en el mismo gesto: «quitá el resumen de la
# derecha, hacé una vista». El `<aside class="vd-res">` —Cotizado,
# Confirmado, Pagado y «Cómo pagaron»— se fue entero, con su
# `_vender_pagos` y su CSS; «Cómo pagaron» ya vive en Finanzas
# (pagos_confirmar.confirmado_por_metodo, BLOQUE 59.4), así que acá solo
# se SACA, no se muda.
#
# Lo que NO se fue, porque está compartido y medido con la regla 11:
# `_NOMBRES_METODO` (lo usa `_linea_tarjeta` para el «Yappy · vie 3» de
# cada tarjeta pagada), `col.total` y `col.cuenta` (los encabezados de
# columna y las pastillas del teléfono, los dos bajo prueba) y
# `_columna_vender` / `COLUMNAS_VENDER` (el arrastre y `_mover_a_vender`).
#
# Y sobre el ancho que el panel liberó van las dos vistas: el tablero de
# siempre y la tabla. El segmento que las cambia es el de la casa
# (_vistas_vender.html), y cuál está activa lo decide ESTA función.
# ---------------------------------------------------------------------------

VISTAS_VENDER = ("tablero", "tabla")

_TEXTOS_VISTA_VENDER = {"tablero": "Tablero", "tabla": "Tabla"}


def _vender_vistas(ver, vista="pendientes"):
    """El segmento Tablero · Tabla, ya decidido acá (regla 10): qué dice
    cada cara, a dónde va y cuál está puesta.

    El enlace arrastra la vista del celular (?vista=pagadas) para no
    cambiar DOS cosas de un toque: cambiar de cara no puede devolverte a
    las pendientes si estabas mirando las pagadas."""
    salida = []
    for clave in VISTAS_VENDER:
        query = _query_vender(vista, clave)
        salida.append({
            "clave": clave,
            "texto": _TEXTOS_VISTA_VENDER[clave],
            "href": f"/venta?{query}" if query else "/venta",
            "activa": clave == ver,
        })
    return salida


def _vender_tabla(filas):
    """Las filas de la VISTA DE TABLA: contacto · fecha · interés · etapa ·
    monto cobrado o por cobrar. Todo decidido acá (regla 10) — la
    plantilla solo pinta celdas.

    NI UN VIAJE NUEVO A ODOO: las cinco columnas salen de lo que la lista
    única ya trae. `cliente` y `fecha_texto` son de la tabla local;
    `chip_texto`/`chip_estilo` son el interés que ya decide `colores` para
    la tarjeta; la etapa es LA MISMA `_columna_vender` que reparte el
    kanban (una fila y una tarjeta no pueden decir cosas distintas de la
    misma venta); y el monto es el `total` de siempre.

    LO QUE EL MONTO ES Y LO QUE NO ES, dicho en voz alta: es el TOTAL de
    la venta, no un residual de Odoo — eso no viaja en esta lista (ni
    `ventas_locales` ni `cotizaciones_servicio` guardan lo pagado, y
    `cotizaciones.estados_en_odoo` solo trae state/invoice_ids). Así que
    cada fila dice CUÁL de las dos cosas es su total: en «Pagado» está
    cobrado, en las otras dos está por cobrar. Es exactamente la verdad
    que el tablero ya imprime en sus encabezados, y es el rótulo que le
    faltaba al resumen que se fue (prometía un cobrable y entregaba una
    facturación — medido en docs/MEDICION-linear-prueba-y-vender-kanban).
    """
    salida = []
    for f in filas:
        clave = _columna_vender(f)
        cobrado = clave == "pagado"
        salida.append({
            # El mismo nombre que la tarjeta, y el mismo relleno honesto
            # que usa el panel cuando la venta no guardó ninguno.
            "cliente": (f.get("cliente") or "").strip() or "Sin nombre guardado",
            "abrir_url": f["abrir_url"],
            "ancla": f["ancla"],
            "fecha": f["fecha_texto"],
            "interes": f["chip_texto"],
            "chip_estilo": f["chip_estilo"],
            "etapa": _TITULOS_VENDER[clave],
            "total": f["total"] or 0,
            "cobrado": cobrado,
            "monto_rotulo": "cobrado" if cobrado else "por cobrar",
        })
    return salida


# ---------------------------------------------------------------------------
# El CUERPO CELULAR de Vender (BLOQUE 37, pantalla 25 del lienzo): dos
# píldoras arriba (Pendientes / Pagadas) que navegan por GET (?vista=) y
# las mismas columnas del tablero vestidas de SECCIONES apiladas. Todo
# se decide aquí (regla 10): la plantilla solo pinta lo que llega, y la
# computadora no cambia (estas caras solo existen ≤899px, en el CSS).
# ---------------------------------------------------------------------------

VISTAS_VENDER_MOVIL = ("pendientes", "pagadas")

# columna -> (vista que la muestra, título de sección del lienzo, orden
# en pantalla). En el lienzo «Falta cobrar» va ANTES que «Cotizado, sin
# pagar», aunque el tablero de computadora las pinte al revés — el orden
# es presentación (CSS order), el HTML no se duplica (las anclas de las
# tarjetas tienen que seguir siendo únicas).
_SECCION_MOVIL = {
    "confirmado": ("pendientes", "Falta cobrar", 1),
    "cotizado": ("pendientes", "Cotizado, sin pagar", 2),
    "pagado": ("pagadas", "Pagadas", 1),
}


def _vender_movil(columnas, vista):
    """Las píldoras Pendientes/Pagadas del teléfono, con sus conteos
    REALES (la suma de las columnas de cada vista), y de paso le cuelga
    a cada columna su cara de sección (título del lienzo, orden, si la
    vista activa la muestra y si sus tarjetas llevan el Cobrar apagado).
    MUTA las columnas a propósito: son la misma estructura que pinta el
    tablero de computadora, solo que vestida dos veces."""
    cuentas = {v: 0 for v in VISTAS_VENDER_MOVIL}
    for col in columnas:
        v, titulo, orden = _SECCION_MOVIL[col["clave"]]
        col["movil"] = {
            "titulo": titulo, "orden": orden, "visible": v == vista,
            # El «Cobrar» por tarjeta del lienzo va APAGADO (pide el
            # flujo de abonos, que no existe): solo en las pendientes —
            # a una pagada no hay nada que cobrarle.
            "cobrar_apagado": v == "pendientes",
        }
        cuentas[v] += col["cuenta"]
    return [
        {"clave": v, "texto": texto, "cuenta": cuentas[v],
         "href": f"/venta?vista={v}", "activa": v == vista}
        for v, texto in (("pendientes", "Pendientes"),
                         ("pagadas", "Pagadas"))
    ]


def _vender_alerta(filas):
    """El aviso rojo de arriba del cuerpo celular. El lienzo dice «4
    ventas por revisar»; el dato REAL que existe hoy son las ventas
    ATORADAS a medio pipeline con su último intento fallido
    (linea_alerta, o sea ultimo_error) — ese es el conteo que se pinta,
    y el botón abre el panel de la primera (donde vive Reintentar).
    Sin atoradas no hay aviso: nada se inventa."""
    atoradas = [f for f in filas if f.get("linea_alerta")]
    if not atoradas:
        return None
    n = len(atoradas)
    return {
        "cuenta": n,
        "texto": ("1 venta atorada por revisar" if n == 1
                  else f"{n} ventas atoradas por revisar"),
        "href": atoradas[0]["abrir_url"],
    }


def _vender_abierta(filas, abrir):
    """La fila que pide ?abrir= (v3 = venta n.º 3, s3 = cotización de
    servicio n.º 3), vestida con su panel (_panel_vender); None si el
    parámetro no apunta a nada — una URL vieja o manoseada no rompe la
    pantalla, solo no abre panel."""
    if len(abrir or "") < 2 or abrir[0] not in "vs" or not abrir[1:].isdigit():
        return None
    tipo = "venta" if abrir[0] == "v" else "servicio"
    n = int(abrir[1:])
    for f in filas:
        if f["tipo"] == tipo and f["n"] == n:
            f = dict(f)
            f["panel"] = _panel_vender(f)
            # «Mover a» (BLOQUE 40): el gesto del arrastre, en enlaces,
            # para el celular. Lo decide Python, no la plantilla — y
            # mirando el panel, para no repetir el botón negro.
            f["panel"]["mover_a"] = _mover_a_vender(f, f["panel"])
            # A12 del BLOQUE 53: de quién es y de qué lead salió.
            f["quien"] = _quien_vender(f)
            return f
    return None


def _panel_vender(f):
    """Todo lo que el panel de la tarjeta abierta muestra de acciones:
    el ÚNICO botón negro (el paso que toca HOY) y las acciones
    secundarias. La decisión de CUÁL botón es de Python (regla 10) — la
    plantilla solo recorre esta estructura.

    - boton: {texto, href, pdf?, descarga?} — pdf=True baja y abre en
      pestaña nueva con data-pdf (regla del 28/09).
    - acciones: enlaces {texto, href, ...} o el POST de cancelar
      ({cancelar: True, action, confirm, boton}, _vender_cancelar.html).
    - chip: el chip de estado que la tarjeta vieja mostraba (Cotización ·
      Venta · Facturado · el paso pendiente), ahora en el panel."""
    if f["tipo"] == "venta":
        pdf_cotizacion = {
            "texto": "Descargar / Compartir PDF",
            "href": f"/venta/{f['n']}/cotizacion.pdf",
            "pdf": True, "descarga": f["nombre_cotizacion_pdf"],
        }
        if f["estado"] == "cotizacion":
            return {
                "chip": {"texto": "Cotización", "css": ""},
                "boton": {"texto": "Facturar / Pagado",
                          "href": f"/venta/pago/{f['n']}"},
                "acciones": [
                    dict(pdf_cotizacion, ancha=True),
                    {"cancelar": True,
                     "action": f"/venta/cancelar/{f['n']}",
                     "confirm": ("Cancela la cotización "
                                 + (f["orden"] or "sin número")
                                 + " también en Odoo y no tiene marcha"
                                   " atrás desde la app: para revivirla"
                                   " habría que crear la venta de nuevo."
                                   " ¿Cancelar?"),
                     "boton": "Cancelar"},
                ],
            }
        if f["estado"] == "vendida":
            return {"chip": {"texto": "Venta", "css": "vd-ok"},
                    "boton": pdf_cotizacion, "acciones": []}
        if f["estado"] == "pagado":
            acciones = []
            if f.get("whatsapp"):
                acciones.append({"texto": "Mandar factura por WhatsApp",
                                 "href": f["whatsapp"], "externo": True,
                                 "ancha": True})
            return {
                "chip": None,  # «Pagado» ya lo dicen el ✓ y el estado
                "boton": {"texto": "Descargar / Compartir factura",
                          "href": f"/venta/{f['n']}/factura.pdf",
                          "pdf": True, "descarga": f["nombre_factura_pdf"]},
                "acciones": acciones,
            }
        # Atorada a medio pipeline: el paso que toca es reintentar.
        return {"chip": {"texto": f["etiqueta_estado"], "css": "vd-debe"},
                "boton": {"texto": "Reintentar",
                          "href": f"/venta/pago/{f['n']}"},
                "acciones": []}
    # Cotización de servicio: el PDF es el botón (su cobro vive en Odoo);
    # Editar y Quitar solo mientras Odoo diga que se puede.
    acciones = []
    if f["editable"]:
        acciones = [
            {"texto": "Editar", "href": f"/venta/servicio/{f['n']}/editar"},
            {"cancelar": True,
             "action": f"/venta/servicio/{f['n']}/cancelar",
             "confirm": ("Quita la cotización " + f["orden"]
                         + " también en Odoo y no tiene marcha atrás"
                           " desde la app: para recuperarla habría que"
                           " crearla de nuevo. ¿Quitar?"),
             "boton": "Quitar"},
        ]
    return {
        "chip": ({"texto": "Facturado", "css": "vd-ok"} if f["facturada"]
                 else {"texto": "Cotización", "css": ""}),
        "boton": {"texto": "Descargar / Compartir PDF",
                  "href": f"/venta/servicio/{f['n']}/propuesta.pdf",
                  "pdf": True, "descarga": f["nombre_pdf"]},
        "acciones": acciones,
    }


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


@app.post("/venta/lead")
async def venta_lead_poner(request: Request):
    """El «Cotizar» de la ficha de Control: deja anotado el lead y la
    próxima cotización/venta de esta empleada nace vinculada a él, con el
    nombre y el celular ya puestos en el borrador (dueño, 23/09/2026).
    Era GET /venta?lead=… y MUTABA: ahora es POST (auditoría de la
    precisión 2 del review de roles — un GET no escribe)."""
    form = await request.form()
    lead = (form.get("lead") or "").strip()[:80]
    if not lead:
        return RedirectResponse("/venta", status_code=303)
    usuario = request.state.empleada["id"]
    cliente = (form.get("cliente") or "").strip()
    ventas.poner_lead_pendiente(usuario, lead, cliente)
    ventas.guardar_borrador(usuario, cliente[:120],
                            (form.get("cel") or "").strip()[:30])
    return RedirectResponse("/venta/nueva", status_code=303)


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
        "itbms_carrito": 0.0,
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
                # F2 (6/10/2026): Vender busca plantas, macetas e insumos
                # — el producto real en la línea es lo que hace que Odoo
                # cobre el ITBMS del 7% que el comodín exento no cobraba.
                contexto["resultados"] = _resultados_con_stock(
                    ventas.buscar_productos(contexto["q"],
                                            prefijos=ventas.PREFIJOS_VENDER))
            contexto["carrito"], contexto["total_carrito"] = ventas.carrito_de(usuario)
            contexto["itbms_carrito"] = ventas.itbms_del_carrito(contexto["carrito"])
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
    # Los términos de pago (item 5): la venta de plantas es plant retail.
    contexto["terminos"] = _contexto_terminos(venta_estado.TIPO_PLANTAS,
                                              contexto["borrador"])
    contexto["total_con_cargos"] = (
        contexto["total_carrito"] + contexto["itbms_carrito"]
        + contexto["total_renglones_planta"]
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
def venta_buscar(request: Request, q: str = "", solo_plantas: str = ""):
    # Alimenta el buscador en vivo (venta.js): mismo resultado que la
    # búsqueda server-rendered, en JSON, con el precio ya formateado y el
    # stock (28/09/2026) para que la búsqueda en vivo y la de recarga de
    # página digan lo mismo. F2 (6/10/2026): Vender busca PL-, MC- e IN-
    # (el ITBMS lo pone Odoo desde el producto real); la pantalla de
    # EDITAR manda solo_plantas=1 y sigue como estaba — su cuenta en vivo
    # se arma en el navegador y no sabe de impuestos todavía.
    prefijos = ((ventas.PREFIJO_PLANTA,) if solo_plantas
                else ventas.PREFIJOS_VENDER)
    try:
        resultados = _resultados_con_stock(
            ventas.buscar_productos(q, prefijos=prefijos))
    except Exception:
        return {"error": "Sin conexión con Odoo en este momento."}
    # "precio_num" (30/09/2026): el buscador de la pantalla de editar arma
    # la fila de la planta en el navegador (no hay a dónde hacer un POST
    # con carrito, esa pantalla edita una orden ya existente) y necesita el
    # número crudo, no el "$3.50" ya formateado para mostrar.
    return {"resultados": [{**p, "precio": calculos.dinero(p["precio"]),
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


def _post_al_borrador(request, form):
    """Nº10 del lote (solución que pidió Abraham): el nombre y el celular
    del cliente viajan EN EL POST (los inputs llevan name= y form=) y acá
    MANDAN sobre el borrador que guarda el beacon de venta.js — con su
    debounce de 400 ms, un envío rápido podía re-renderizar con el nombre
    viejo o vacío. El POST es la fuente primaria y el borrador el
    respaldo, nunca al revés: se llama al entrar a TODOS los POST de
    Vender que crean algo, antes de validar, para que cualquier re-render
    con error pinte lo que de verdad viajó."""
    if "cliente" not in form and "celular" not in form:
        return
    ventas.guardar_borrador(request.state.empleada["id"],
                            (form.get("cliente") or "").strip()[:120],
                            (form.get("celular") or "").strip()[:30],
                            _servicios_del_form(form),
                            _datos_cliente_del_form(form),
                            _renglones_del_form(form))


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
        # La vista previa no resuelve clientes (5/10/2026, bug Nº2):
        # trabaja sobre el comodín de la empleada, así que no pasa
        # decisiones de cliente ni puede levantar ClienteAjeno /
        # ConfirmarDatoFiscal — esas preguntas viven al concretar.
        pdf = ventas.pdf_vista_previa(
            empleada, form.get("cliente", ""), form.get("celular", ""),
            datos_cliente, _cargos_del_form(form),
            banderas=ventas.banderas_de(form, False))
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
    _post_al_borrador(request, form)
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
    _sellar_estado_y_termino("venta", registro["n"], venta_estado.TIPO_PLANTAS,
                             form, request.state.empleada)
    return plantillas.TemplateResponse(request, "venta_exito.html", {
        "titulo": "Cotización creada",
        "sub": f"{registro['orden']} · {registro['cliente']}",
        # El aviso honesto del amarre perdido (Nº5, 2/10/2026): la venta
        # salió igual, pero si venía de una ficha y Linear no contestó, acá
        # se dice — antes nadie se enteraba.
        "aviso": registro.get("aviso_lead") or "",
        "filas": [("Total", calculos.dinero(registro["total"]), None),
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
    _post_al_borrador(request, form)
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
    _sellar_estado_y_termino("venta", registro["n"], venta_estado.TIPO_PLANTAS,
                             form, request.state.empleada)
    return plantillas.TemplateResponse(request, "venta_exito.html", {
        "titulo": "Venta confirmada",
        "sub": f"{registro['orden']} · {registro['cliente']}",
        "aviso": registro.get("aviso_lead") or "",
        "filas": [("Total", calculos.dinero(registro["total"]), None),
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

def _contexto_terminos(tipo_venta, borrador):
    """Los términos de pago del formulario (item 5 de Jay, 5/10/2026): el
    default del TIPO DE VENTA (Settings · datos_roles), lo que el
    borrador traiga escrito, y si el cambio se ofrece a la vista
    (override_visible — plantas lo lleva plegado: su default es pagar
    completo y el override es raro, pero nunca imposible).

    `tipo` sale en palabras de la casa (BLOQUE 53 · A10): el catálogo
    guarda «plant retail» y la pantalla decía «Default de este tipo
    (plant retail)». El dato no se toca; la traducción la hace
    colores.nombre_tipo_venta."""
    info = venta_estado.tipo_info(tipo_venta) or {}
    default = info.get("termino_default") or ""
    valor = (borrador.get("termino") or "").strip() or default
    crudo = info.get("nombre") or tipo_venta
    return {"tipo": colores.nombre_tipo_venta(crudo), "default": default,
            "valor": valor, "editable": bool(info.get("override_visible", 1))}


def _sellar_estado_y_termino(origen, n, tipo_venta, form, empleada):
    """Tras crear la venta/cotización: la fila de los 3 estados (nace en
    1, con su tipo — la conversión por tipo se decide con esto) y el
    término guardado con la venta (override registrado si difiere del
    default). Best-effort: la orden ya está creada en Odoo y esto no la
    tumba — pero el fallo queda en el log, nunca mudo del todo."""
    por = empleada.get("nombre") or empleada["id"]
    try:
        venta_estado.abrir(origen, n, tipo_venta, por)
        venta_estado.guardar_termino(origen, n, tipo_venta,
                                     form.get("termino") or "", por)
    except Exception as error:
        logging.getLogger("control_stock").warning(
            f"venta_estado: el estado/término de {origen} {n} no quedó "
            f"anotado: {error!r}")


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
        "itbms_carrito": 0.0,
        "borrador": borrador, "servicios": servicios or [{"texto": "", "monto": "", "descripcion": ""}],
        "terminos": _contexto_terminos(
            venta_estado.TIPO_DE_SERVICIO.get(tipo, "other"), borrador),
        "error_venta": error or None,
    }
    if contexto["ventas_activo"]:
        try:
            if contexto["q"]:
                contexto["resultados"] = ventas.buscar_productos(contexto["q"])
            contexto["carrito"], contexto["total_carrito"] = ventas.carrito_de(
                usuario, base_cero=por_planta)
            # Una maceta o un insumo agregados desde Vender pueden seguir
            # en el carrito al llegar aquí: su ITBMS se muestra igual,
            # porque Odoo lo va a cobrar igual (va por producto).
            contexto["itbms_carrito"] = ventas.itbms_del_carrito(contexto["carrito"])
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
    _post_al_borrador(request, form)
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
    _sellar_estado_y_termino("servicio", registro["n"],
                             venta_estado.TIPO_DE_SERVICIO.get(tipo, "other"),
                             form, request.state.empleada)
    filas = [("Tipo", cotizaciones.etiqueta_de(tipo), None),
             ("Total", calculos.dinero(registro["total"]), None),
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
        "itbms_carrito": 0.0,
        "borrador": borrador, "error_venta": error or None,
        # La casilla del PDF (garantía): en el personalizado nace MARCADA.
        "casillas": ventas.banderas_de(borrador, True),
        # Términos (item 5): el personalizado no tiene tipo propio — cae
        # en "other" (a medida, override a la vista).
        "terminos": _contexto_terminos("other", borrador),
        "renglones": renglones or [{"texto": "", "cantidad": "", "precio": "", "descripcion": ""}],
        "servicios": servicios or [{"texto": "", "monto": "", "descripcion": ""}],
    }
    if contexto["ventas_activo"]:
        try:
            if contexto["q"]:
                contexto["resultados"] = ventas.buscar_productos(contexto["q"])
            contexto["carrito"], contexto["total_carrito"] = ventas.carrito_de(usuario)
            contexto["itbms_carrito"] = ventas.itbms_del_carrito(contexto["carrito"])
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
    _post_al_borrador(request, form)
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
    except ventas.ErrorDeCampo as error:
        # Regla 5: el error debajo del campo que falló, con lo escrito en
        # pantalla (los mismos renglones y servicios del POST).
        contexto = _contexto_personalizada(request, error=str(error),
                                           renglones=renglones,
                                           servicios=servicios)
        contexto["campo_error"] = error.campo
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
    _sellar_estado_y_termino("servicio", registro["n"], "other",
                             form, request.state.empleada)
    return plantillas.TemplateResponse(request, "venta_exito.html", {
        "titulo": "Cotización creada",
        "sub": f"{registro['orden']} · {registro['cliente']}",
        "filas": [("Tipo", "Personalizado", None),
                  ("Total", calculos.dinero(registro["total"]), None),
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
    except ventas.ErrorDeCampo as error:
        # Regla 5: lo mismo que el ValueError de abajo, pero el mensaje
        # sale debajo del campo que falló y la pantalla aterriza ahí.
        return _editar_rebotado(request, n, form, servicios, plantas,
                                renglones, str(error), campo=error.campo)
    except ValueError as error:
        # El formulario vuelve con lo escrito, como al crear: un redirect
        # perdería lo que la empleada ya corrigió.
        return _editar_rebotado(request, n, form, servicios, plantas,
                                renglones, str(error))
    except Exception as error:
        return _redirigir_venta(
            f"No se pudo guardar: {ventas._mensaje_de_error(error)}")
    # De vuelta a la lista, ANCLADO en la tarjeta que se editó: guardar no
    # debe mandar a la empleada al tope de la lista.
    return RedirectResponse(f"/venta#cot-{n}", status_code=303)


def _editar_rebotado(request, n, form, servicios, plantas, renglones,
                     error, campo=""):
    """El re-render de Editar cotización cuando el guardado rebotó: el
    formulario vuelve con lo escrito (un redirect lo perdería) y, si el
    error es de un campo, con el mensaje debajo de ese campo (regla 5)."""
    datos_edicion = cotizaciones.cargar_para_editar(n)
    if datos_edicion is None or not datos_edicion["editable"]:
        return _redirigir_venta(error)
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
    contexto = _contexto_editar(request, datos_edicion, error=error)
    contexto["campo_error"] = campo
    return plantillas.TemplateResponse(
        request, "venta_servicio_editar.html", contexto, status_code=200)


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
    _sellar_estado_y_termino("venta", registro["n"], venta_estado.TIPO_PLANTAS,
                             form, request.state.empleada)
    return RedirectResponse(f"/venta/pago/{registro['n']}", status_code=303)


# El botón único que facturaba, pagaba Y entregaba de un golpe era el
# defecto central (reply de Jay del 5/10, §3): pagar y entregar son
# hechos distintos. La ruta vieja /venta/cobrar NO desaparece: cae en el
# flujo nuevo — el GET redirige a la pantalla de pago, y el POST registra
# el pago (sin entregar), igual que /venta/pago.

@app.get("/venta/cobrar/{n}")
def venta_cobrar(request: Request, n: int):
    return RedirectResponse(f"/venta/pago/{n}", status_code=303)


@app.get("/venta/pago/{n}")
def venta_pago(request: Request, n: int):
    registro = ventas.obtener_venta(n)
    if registro is None or registro["estado"] == "pagado":
        return RedirectResponse("/venta", status_code=303)
    return plantillas.TemplateResponse(request, "venta_pago.html", {
        "v": {**registro, "fecha_texto": _fecha_venta(registro["creado_en"]),
              "etiqueta_estado": ventas.ETIQUETAS_ESTADO[registro["estado"]]},
    })


async def _registrar_pago_venta(request: Request, n: int):
    form = await request.form()
    metodo = form.get("metodo", "")
    if metodo not in ("yappy", "efectivo"):
        return RedirectResponse(f"/venta/pago/{n}", status_code=303)
    registro = ventas.registrar_pago(
        n, metodo, por=request.state.empleada.get("nombre")
        or request.state.empleada["id"])
    if registro is None:
        return RedirectResponse("/venta", status_code=303)
    if registro["estado"] != "pagado":
        # Quedó a medias: la pantalla de pago muestra el estado real y el
        # error, y el mismo botón reintenta desde el paso que faltó.
        return RedirectResponse(f"/venta/pago/{n}", status_code=303)
    metodo_texto = "Yappy" if metodo == "yappy" else "Efectivo"
    return plantillas.TemplateResponse(request, "venta_exito.html", {
        "titulo": "Pago registrado",
        "sub": f"{registro['orden']} · {registro['cliente']}",
        # El pago NO entrega (items 5-7, 5/10/2026): el aviso lo dice,
        # para que la plata sola no se celebre como trabajo terminado.
        "aviso": "La entrega queda pendiente: la marca quien carga el "
                 "deber de system manager, desde «Estado / Entrega» de "
                 "la venta.",
        "filas": [("Factura", registro["factura"], None),
                  ("Total", calculos.dinero(registro["total"]), "ok"),
                  ("Método", metodo_texto, None),
                  ("Entrega", "Pendiente de marcar", "dorado")],
        "pdf_href": f"/venta/{n}/factura.pdf",
        "pdf_texto": "Descargar / Compartir factura",
        "pdf_nombre": ventas.nombre_de_pdf(
            (registro["factura"] or str(n)).replace("/", "-"), registro["cliente"]),
    })


@app.post("/venta/pago/{n}")
async def venta_pago_confirmar(request: Request, n: int):
    return await _registrar_pago_venta(request, n)


@app.post("/venta/cobrar/{n}")
async def venta_cobrar_confirmar(request: Request, n: int):
    # La URL vieja sigue aceptando el POST (nada desaparece), pero hace
    # lo NUEVO: registrar el pago sin entregar.
    return await _registrar_pago_venta(request, n)


# ---------------------------------------------------------------------------
# La ficha «Estado / Entrega» de una venta (items 5-6-7 de Jay): los 3
# estados con sus candados, los dos hechos, el término del trato y la
# entrega como obligación nombrada (dirección + asignado, con historial).
# Todo lo que la plantilla pinta se decide acá, en Python (regla 10).
# ---------------------------------------------------------------------------

def _registro_local(origen, n):
    """La venta local detrás de la ficha: ventas_locales o
    cotizaciones_servicio, con las llaves que la ficha necesita."""
    if origen == "venta":
        registro = ventas.obtener_venta(n)
    else:
        registro = cotizaciones.obtener(n)
    return registro


def _quien_es(request):
    empleada = request.state.empleada
    return empleada["id"], (empleada.get("nombre") or empleada["id"])


def _url_estado(origen, n, error="", aviso=""):
    url = f"/venta/estado/{origen}/{n}"
    partes = []
    if error:
        partes.append("error=" + quote(error))
    if aviso:
        partes.append("aviso=" + quote(aviso))
    return url + ("?" + "&".join(partes) if partes else "")


# Los avisos de la ficha, en palabras de la casa (BLOQUE 53 · A10): los
# números de estado («pasar a 2», «El 3 exige») son de adentro y no le
# dicen nada a quien trabaja. Cada texto dice QUÉ FALTA.
_TEXTO_ERROR_ESTADO = {
    "falta_pago": "Falta registrar el pago: el chip del estado no "
                  "registra plata.",
    "faltan_hechos": "Para cerrar la venta hacen falta las DOS cosas: el "
                     "pago confirmado Y la entrega marcada — nunca una "
                     "sola.",
    "falta_saldo": "Para cerrar la venta falta cobrar el saldo (un abono "
                   "no la cierra).",
    "solo_system_manager": "Eso lo hace quien carga el deber de system "
                           "manager (Ajustes → Roles).",
    "falta_asignado": "Ponle un asignado a la entrega antes de marcarla: "
                      "la entrega es una obligación con nombre.",
    "cerrada": "La venta ya cerró: la entrega queda como historia y no "
               "se edita.",
    "estado_invalido": "Ese estado no existe.",
}


def _contexto_ficha_estado(request, origen, n, error="", aviso=""):
    """El contexto completo de la ficha Estado/Entrega, o None si la venta
    no existe. Lo usan el GET y el re-pintado del POST que rechaza una
    fecha programada mal escrita (regla forms-lote: mismo contexto, con
    lo tecleado encima)."""
    registro = _registro_local(origen, n)
    if registro is None:
        return None
    usuario, _nombre = _quien_es(request)
    hechos = venta_estado.estado_de(origen, n)
    # Los botones del chip a mano: cada estado con su candado ya decidido
    # (la plantilla no sabe de reglas, solo pinta).
    botones = []
    for numero, texto in venta_estado.ESTADOS.items():
        bloqueo = venta_estado.bloqueo_manual(hechos, numero, usuario)
        botones.append({
            "n": numero, "texto": texto,
            "actual": numero == hechos["estado"],
            "permitido": bloqueo is None and numero != hechos["estado"],
            "motivo": venta_estado.MOTIVO_BLOQUEO.get(bloqueo, ""),
        })
    manager = datos_roles.quien_ocupa("system_manager")
    termino = venta_estado.termino_de(origen, n)
    return {
        "origen": origen, "n": n,
        "registro": registro,
        "titulo_doc": f"{registro.get('orden') or ''} · {registro.get('cliente') or ''}",
        "hechos": hechos,
        # Los instantes van en EPOCH (UTC); el texto de pantalla se arma
        # acá — la zona la decide quien muestra, nunca quien guarda.
        "pago_en_texto": venta_estado.texto_de_epoch(hechos.get("pago_en")),
        "entrega_en_texto": venta_estado.texto_de_epoch(
            hechos.get("entrega_en")),
        "estado_texto": venta_estado.ESTADOS[hechos["estado"]],
        "botones": botones,
        "soy_manager": venta_estado.es_system_manager(usuario),
        "manager_nombres": ", ".join(
            p["nombre"] for p in (manager or {}).get("personas", [])) or "—",
        "termino": termino,
        # El tipo del trato en palabras de la casa (BLOQUE 53 · A10): la
        # ficha decía «· tipo plant retail». El dato guardado no cambia.
        "termino_tipo": colores.nombre_tipo_venta(
            (termino or {}).get("tipo_venta") or ""),
        "overrides": venta_estado.overrides_de(origen, n),
        "obligacion": entregas.obligacion_de(origen, n),
        "historial_entrega": entregas.historial_de(origen, n),
        "historial_estado": [
            {**c, "en_texto": venta_estado.texto_de_epoch(c["puesto_en"])}
            for c in venta_estado.historial_de(origen, n)],
        "empleadas": [e for e in seguridad.listar() if e["activa"]],
        "hoy": datetime.now(datos.ZONA_PANAMA).date().isoformat(),
        # El acceso directo a Registrar pago, solo para la venta de
        # plantas que todavía no pasó por él.
        "puede_registrar_pago": (origen == "venta"
                                 and registro.get("estado")
                                 in ("cotizacion", "confirmada", "entregada",
                                     "facturada")),
        "error_texto": _TEXTO_ERROR_ESTADO.get(error, error or None),
        "aviso": aviso or None,
        # La regla forms-lote: cuando un campo falla, el POST re-pinta
        # con estos dos puestos (acá nacen vacíos).
        "campo_error": "",
        "error_campo_texto": "",
    }


@app.get("/venta/estado/{origen}/{n}")
def venta_estado_ficha(request: Request, origen: str, n: int,
                       error: str = "", aviso: str = ""):
    if origen not in venta_estado.ORIGENES:
        return RedirectResponse("/venta", status_code=303)
    contexto = _contexto_ficha_estado(request, origen, n, error, aviso)
    if contexto is None:
        return RedirectResponse("/venta", status_code=303)
    return plantillas.TemplateResponse(request, "venta_trato.html", contexto)


@app.post("/venta/estado/{origen}/{n}")
async def venta_estado_manual(request: Request, origen: str, n: int):
    if origen not in venta_estado.ORIGENES:
        return RedirectResponse("/venta", status_code=303)
    form = await request.form()
    usuario, nombre = _quien_es(request)
    try:
        estado = int(form.get("estado") or 0)
    except ValueError:
        estado = 0
    error = venta_estado.poner_estado_manual(origen, n, estado,
                                             usuario, nombre)
    aviso = "" if error else f"Estado puesto en {estado}."
    return RedirectResponse(_url_estado(origen, n, error or "", aviso),
                            status_code=303)


@app.post("/venta/estado/{origen}/{n}/entrega")
async def venta_estado_obligacion(request: Request, origen: str, n: int):
    """Guardar la obligación nombrada: dirección + asignado (empleada
    activa del selector, o el texto libre si se escribió — mensajero,
    tercero, contratista) + la fecha programada de entrega (el plan,
    AAAA-MM-DD — la que decide «Programado» en la pestaña Pedidos).
    Editable hasta cerrar.

    Una fecha mal escrita NO redirige: re-pinta la ficha con el error
    DEBAJO del campo y lo tecleado conservado (regla forms-lote) — el
    motor no escribió nada."""
    if origen not in venta_estado.ORIGENES:
        return RedirectResponse("/venta", status_code=303)
    form = await request.form()
    _usuario, nombre = _quien_es(request)
    libre = (form.get("asignado_libre") or "").strip()
    asignado = libre or (form.get("asignado_sel") or "").strip()
    fecha_programada = (form.get("fecha_programada") or "").strip()
    error = entregas.guardar(origen, n, form.get("direccion"),
                             asignado, nombre,
                             fecha_programada=fecha_programada)
    if error == "fecha_programada_invalida":
        contexto = _contexto_ficha_estado(request, origen, n)
        if contexto is None:
            return RedirectResponse("/venta", status_code=303)
        # Lo tecleado se conserva tal cual encima de lo guardado — nada
        # se escribió en el motor.
        contexto["obligacion"] = {
            **contexto["obligacion"],
            "direccion": (form.get("direccion") or "").strip(),
            "asignado": asignado,
            "fecha_programada": fecha_programada,
        }
        contexto["campo_error"] = "fecha_programada"
        contexto["error_campo_texto"] = (
            "La fecha no se entiende: va AAAA-MM-DD (ej. 2026-10-15).")
        return plantillas.TemplateResponse(request, "venta_trato.html",
                                           contexto)
    aviso = "" if error else "Entrega guardada."
    return RedirectResponse(_url_estado(origen, n, error or "", aviso),
                            status_code=303)


# ---------------------------------------------------------------------------
# La pestaña PEDIDOS (punto 4 del BLOQUE 12, diseño con ACK del
# Arquitecto 6/10): una VISTA sobre el motor de los 3 estados — el lado
# de la ENTREGA del trabajo pagado. NO es la pestaña Pedidos descartada
# del 30/09 (aquella listaba tiquetes de otra época): esta nace del
# modelo de Jay — el pedido nace cuando el cliente paga o abona. Todo lo
# que se pinta lo decide app/pedidos.py (regla 10); la tarjeta abre la
# ficha Estado/Entrega EXISTENTE. La ven todos MENOS el rol Inventario:
# su puerta global (_puerta_por_rol, en el middleware) ya corta
# cualquier ruta nueva — GET → 303 a /stock, POST → 403 — sin acordarse
# de nada; hay prueba por request directa (el patrón de los 27+).
# ---------------------------------------------------------------------------

@app.get("/pedidos")
def pedidos_tablero(request: Request, tipo: str = ""):
    return plantillas.TemplateResponse(request, "pedidos.html", {
        "empleada": request.state.empleada,
        "tablero": pedidos.tablero(tipo=(tipo or "").strip() or None),
    })


# ---------------------------------------------------------------------------
# «Pagos por confirmar» (item 6 de Jay): la cola del dinero que nadie
# confirmó que llegó, sobre el MISMO motor de /revisar (solo lectura
# hacia Odoo/Linear). La ven los tres deberes; confirma el system
# manager. /revisar queda exactamente como está.
# ---------------------------------------------------------------------------

@app.get("/pagos-por-confirmar")
def pagos_por_confirmar(request: Request, error: str = "", aviso: str = ""):
    usuario = request.state.empleada["id"]
    if not pagos_confirmar.puede_ver(usuario):
        return Response("Esta pantalla es de los deberes system manager, "
                        "operaciones y owner view (Ajustes → Roles).",
                        status_code=403)
    pendientes, huecos = pagos_confirmar.cola()
    return plantillas.TemplateResponse(request, "pagos_confirmar.html", {
        "empleada": request.state.empleada,
        "pendientes": pendientes,
        "huecos": huecos,
        "historial": pagos_confirmar.historial(),
        "evidencias": pagos_confirmar.EVIDENCIAS,
        "puedo_confirmar": pagos_confirmar.puede_confirmar(usuario),
        "error_aviso": error or None,
        "aviso": aviso or None,
    })


@app.post("/pagos-por-confirmar/confirmar")
async def pagos_por_confirmar_confirmar(request: Request):
    usuario, nombre = _quien_es(request)
    if not pagos_confirmar.puede_ver(usuario):
        return Response("Solo los tres deberes.", status_code=403)
    form = await request.form()
    orden_id = (form.get("orden_id") or "").strip()
    # La fila se relee de la COLA REAL: el monto y el «saldo en 0» salen
    # del informe, nunca de campos del navegador — un POST armado a mano
    # no puede convertir un depósito en pago completo.
    pendientes, _huecos = pagos_confirmar.cola()
    fila = next((p for p in pendientes
                 if str(p["orden_id"]) == orden_id), None)
    if fila is None:
        return RedirectResponse(
            "/pagos-por-confirmar?error="
            + quote("Esa venta ya no está en la cola (recarga)."),
            status_code=303)
    codigo = pagos_confirmar.confirmar(
        fila["orden_id"], fila["orden"], fila["cliente"],
        fila["monto_nuevo"],  # la plata de ESTE hecho, no el total
        form.get("evidencia") or "", form.get("nota") or "",
        usuario, nombre, completo=fila["completo"],
        pagado_total=fila["pagado"])
    if codigo == "ya_confirmado":
        # El doble clic / doble POST del MISMO pago: no-op con aviso.
        return RedirectResponse(
            "/pagos-por-confirmar?aviso="
            + quote(f"El pago de {fila['orden']} ya estaba confirmado."),
            status_code=303)
    if codigo:
        textos = {
            "solo_system_manager": "Confirmar es del system manager: "
                                   "operaciones reporta, él marca.",
            "evidencia_invalida": "Elegí qué evidencia viste (tarjeta, "
                                  "Yappy, transferencia o el reporte).",
        }
        return RedirectResponse(
            "/pagos-por-confirmar?error=" + quote(textos.get(codigo, codigo)),
            status_code=303)
    return RedirectResponse(
        "/pagos-por-confirmar?aviso="
        + quote(f"Pago de {fila['orden']} confirmado."), status_code=303)


@app.post("/venta/estado/{origen}/{n}/entregada")
async def venta_estado_entregada(request: Request, origen: str, n: int):
    """«Marcar entregada»: solo el system manager; exige asignado (con
    aviso visible); valida la salida en Odoo AHÍ y fija la fecha REAL de
    entrega — la que usará delivered revenue."""
    if origen not in venta_estado.ORIGENES:
        return RedirectResponse("/venta", status_code=303)
    form = await request.form()
    usuario, nombre = _quien_es(request)
    if venta_estado.estado_de(origen, n)["entrega_marcada"]:
        # Ya estaba marcada: este POST es la CORRECCIÓN de la fecha (la
        # entrega real fue otro día) — solo el system manager, con su
        # fila en el historial; el instante del acto no se reescribe.
        error = venta_estado.corregir_fecha_entrega(
            origen, n, form.get("fecha"), usuario, nombre)
        aviso = "" if error else "Fecha de entrega corregida."
        if error == "fecha_invalida":
            error = "La fecha no se entiende (AAAA-MM-DD)."
        return RedirectResponse(_url_estado(origen, n, error or "", aviso),
                                status_code=303)
    error, resultado = entregas.marcar_entregada(
        origen, n, usuario, nombre, fecha=(form.get("fecha") or "").strip())
    if error == "odoo":
        texto = ("Odoo no aceptó la salida: "
                 f"{resultado.get('detalle') or ''} — nada quedó marcado.")
        return RedirectResponse(_url_estado(origen, n, error=texto),
                                status_code=303)
    aviso = "" if error else "Entrega marcada."
    return RedirectResponse(_url_estado(origen, n, error or "", aviso),
                            status_code=303)


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
        # El detalle sale SIEMPRE de la orden (qué compró el cliente).
        # Desde los items 5-7 (5/10/2026) la factura del pago puede ser un
        # anticipo del 100% —cuando se cobró antes de entregar— y su única
        # línea («Anticipo») no le dice nada al cliente; las líneas de la
        # orden son idénticas a las de la factura en el camino viejo.
        lineas = ventas.lineas_de_cotizacion(registro)
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


# ---------------------------------------------------------------------------
# La vista plana de Stock del rol Inventario (BLOQUE 13, 5/10/2026).
# PROPUESTA: la captura la toma la coordinadora antes de darla por buena.
# Lista única sin pestañas de categoría, buscador arriba, orden por nombre;
# columnas nombre · cantidad (el FÍSICO, que es lo que se cuenta en el
# vivero) · tamaño (alto de/a si existe) · precio de venta (costos NO);
# la foto se abre al tocar, solo ver. Todo decidido en Python (regla 10).
# ---------------------------------------------------------------------------

def _productos_planos(q=""):
    """(productos ya listos para la plantilla, error del proxy o None).
    Filtra por nombre o SKU sin acentos ni mayúsculas y ordena por
    nombre; sin costos de compra en ningún campo."""
    try:
        inventario, _ = datos.obtener_inventario()
        error = None
    except datos.SinConexion as fallo:
        return [], str(fallo)
    plana = datos_roles._plano
    buscado = plana(q)
    subidas = datos.fotos_subidas()
    productos = []
    for p in sorted(inventario, key=lambda p: plana(p["nombre"])):
        if buscado and (buscado not in plana(p["nombre"])
                        and buscado not in plana(p["sku"])):
            continue
        info = fotos.info_foto(p["sku"], subidas.get(p["sku"]))
        foto = (info["grande"] if info
                else f"/stock/foto/{quote(p['sku'])}" if ventas.configurado()
                else None)
        hmin, hmax = p.get("altura_min", 0), p.get("altura_max", 0)
        productos.append({
            "sku": p["sku"], "nombre": p["nombre"],
            "fisico": p["fisico"],
            "tamano": (f"{hmin}–{hmax} cm" if hmin and hmax
                       else f"{hmin or hmax} cm" if (hmin or hmax) else ""),
            "precio": calculos.precio_online(p.get("precio_centavos", 0)),
            "foto": foto,
        })
    return productos, error


def _pantalla_stock_plano(request, q="", aviso="", aviso_sku="", error="",
                          campo_error="", error_campo_texto="",
                          valores=None, estado=200):
    """La pantalla, compartida por el GET y los rebotes del POST (regla 5:
    el error va DEBAJO del campo que falló y lo tecleado no se borra)."""
    productos, sin_proxy = _productos_planos(q)
    respuesta = plantillas.TemplateResponse(request, "stock_plano.html", {
        "empleada": request.state.empleada,
        "productos": productos,
        "q": q,
        "sin_proxy": sin_proxy,
        "aviso": aviso,
        "aviso_sku": aviso_sku,
        "error": error,
        "campo_error": campo_error,
        "error_campo_texto": error_campo_texto,
        "valores": valores or {},
        # El rebote honesto (BLOQUE 39.3): /stock es la casa del rol
        # Inventario, así que los avisos de la puerta aterrizan acá.
        "rebote": request.query_params.get("rebote"),
    })
    respuesta.status_code = estado
    return respuesta


@app.get("/stock")
def stock_plano(request: Request, q: str = "", aviso: str = "",
                sku: str = ""):
    """La vista plana para quien SOLO tiene el rol Inventario (el
    predicado único decide). Para cualquier otro perfil /stock es la
    pestaña de siempre — así el redirect global del rol no hace loop y
    nadie más pierde su pantalla."""
    if not datos_roles.solo_inventario(request.state.empleada):
        return RedirectResponse("/?tab=stock", status_code=303)
    return _pantalla_stock_plano(request, q=q.strip()[:80],
                                 aviso=aviso, aviso_sku=sku)


@app.post("/stock/cantidad")
async def stock_cantidad(request: Request):
    """La cantidad ABSOLUTA contada (el conteo del rol Inventario).

    Único camino de escritura: stock_escritura.escribir_stock (candado →
    el «antes» lo lee Odoo al escribir → bitácora, con error ruidoso).
    `esperada` es el candado optimista de la casa: si el stock se movió
    en el medio, Odoo devuelve conflicto con el valor fresco y nada se
    escribe. Texto, negativo o decimales se rechazan con el aviso bajo
    el campo, conservando lo tecleado — jamás se vuelven 0 (regla del
    lote de formularios). El éxito vuelve con el ancla #p-<sku>: la
    pantalla se queda en el MISMO producto."""
    form = await request.form()
    sku = (form.get("sku") or "").strip()
    q = (form.get("q") or "").strip()[:80]
    crudo = form.get("cantidad")
    valores = {f"cantidad-{sku}": str(crudo or "")}

    def rebote(mensaje, estado=400):
        return _pantalla_stock_plano(
            request, q=q, campo_error=f"cantidad-{sku}",
            error_campo_texto=mensaje, valores=valores, estado=estado)

    if not re.fullmatch(r"[A-Za-z0-9-]{1,80}", sku):
        return _pantalla_stock_plano(request, q=q, estado=400,
                                     error="La petición no se entiende.")
    cantidad, mensaje = stock_escritura.cantidad_contada(crudo)
    if mensaje:
        return rebote(mensaje)
    try:
        esperada = int(str(form.get("esperada") or "").strip())
    except ValueError:
        # El hidden no vino o vino roto: se repinta con el valor fresco.
        return rebote("La pantalla quedó vieja: revisa la cantidad actual "
                      "y guarda de nuevo.")
    try:
        respuesta = stock_escritura.escribir_stock(
            [{"sku": sku, "cantidad": cantidad, "esperada": esperada}],
            request.state.empleada["id"], "conteo_inventario")
    except datos.SinConexion as fallo:
        return rebote(f"No se pudo guardar: {fallo}. Intenta de nuevo.",
                      estado=502)
    resultado = respuesta["resultados"][0]
    if resultado["resultado"] == "conflicto":
        return rebote(f"El stock cambió en Odoo: ahora hay "
                      f"{resultado['anterior']} físicas. Revisa la cantidad "
                      "y guarda de nuevo.", estado=409)
    if resultado["resultado"] == "no_existe":
        return rebote("Este producto ya no existe en Odoo. Actualiza la "
                      "lista.", estado=404)
    if resultado["resultado"] not in ("aplicado", "sin_cambio"):
        return rebote("Odoo rechazó el ajuste"
                      + (f": {resultado['detalle']}" if resultado.get("detalle")
                         else ". Intenta de nuevo o avisa al encargado."),
                      estado=502)
    if resultado["resultado"] == "aplicado":
        datos.atender_alerta(sku, request.state.empleada["id"])
    if respuesta["registro_fallo"]:
        # Odoo quedó escrito y la bitácora no: error RUIDOSO en pantalla
        # (banner), sin fingir que el guardado falló.
        return _pantalla_stock_plano(request, q=q,
                                     error=stock_escritura.AVISO_REGISTRO,
                                     estado=200)
    # De vuelta al MISMO producto (ancla de la casa), con la búsqueda viva.
    destino = f"/stock?aviso=guardado&sku={quote(sku)}"
    if q:
        destino += f"&q={quote(q)}"
    return RedirectResponse(destino + f"#p-{quote(sku)}", status_code=303)


@app.get("/stock/cambios")
def stock_cambios(request: Request, sku: str = ""):
    """La bitácora de stock_cambio para admins: quién, cuándo, cuánto
    había y cuánto quedó. La puerta vive en el enlace «Bitácora» de la
    pestaña Stock; la fecha se pinta en hora de Panamá (en_epoch es
    epoch UTC: quien muestra decide la zona)."""
    if (rechazo := _admin_o_director(request)) is not None:
        return rechazo
    sku = sku.strip()[:80]
    cambios = [
        {**c, "fecha": datetime.fromtimestamp(c["en_epoch"], tz=datos.ZONA_PANAMA)
                               .strftime("%d/%m/%Y %I:%M %p").lower()}
        for c in stock_escritura.cambios_recientes(sku)
    ]
    return plantillas.TemplateResponse(request, "stock_cambios.html", {
        "empleada": request.state.empleada,
        "cambios": cambios,
        "sku": sku,
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


# Los campos del formulario «Actividad nueva» que viajan en la dirección:
# el rebote de un error ya los usaba (ver `calendario_crear`), y el
# buscador de personas los usa para volver al MISMO formulario con lo que
# ya estaba escrito. `resp` es el assignee de Linear y `resp_nombre` la
# etiqueta `Resp:` del trabajo: son dos cosas distintas y viajan las dos.
_CAMPOS_ACTIVIDAD_NUEVA = ("tipo", "cliente", "lead", "fecha", "hora", "dur",
                           "prioridad", "lugar", "recogida", "nota", "resp",
                           "resp_nombre")


def _pre_actividad(request, empleada, estado):
    """Lo que el formulario «Actividad nueva» trae puesto al abrirse.

    Vive aparte desde el BLOQUE 59 porque lo necesitan DOS caminos: la
    pantalla entera y el pedazo del buscador (`/calendario/buscar`), que
    tiene que devolver enlaces capaces de volver a este mismo formulario
    sin perder nada de lo ya escrito.
    """
    q = request.query_params
    return {
        "fecha": q.get("fecha") or estado["dia"],
        "hora": q.get("hora") or calendario.HORA_POR_DEFECTO,
        "resp": q.get("resp") or estado["yo"]["id"],
        # El responsable del select (29/09/2026): por defecto, quien está
        # en la sesión SI su nombre es una etiqueta Resp: real (misma
        # sugerencia que al agendar un lead); si no, queda "" = Sin
        # asignar. En el rebote vuelve lo elegido.
        "resp_nombre": (q.get("resp_nombre")
                        or agenda.responsable_de_empleada(empleada)),
        # El lead a conectar: lo trae el enlace del log de «Leads de
        # servicio», el buscador de personas, o el rebote de un error.
        "lead": q.get("lead", ""),
        # Si el crear falló, el formulario vuelve CON lo escrito: estos
        # llegan en la dirección del rebote (ver calendario_crear).
        "tipo": q.get("tipo", ""),
        "cliente": q.get("cliente", ""),
        "lugar": q.get("lugar", ""),
        "nota": q.get("nota", ""),
        "dur": q.get("dur", ""),
        "prioridad": q.get("prioridad", ""),
        "recogida": q.get("recogida", ""),
    }


def _buscador_persona_contexto(request, empleada):
    """EL BUSCADOR ÚNICO de «Actividad nueva», armado en UN solo lugar.

    Lo llaman los DOS caminos que lo pintan (el mismo patrón que
    `_panel_lead_contexto` para el panel del lead): la pantalla entera del
    calendario, que lo incluye dentro del formulario, y `/calendario/
    buscar`, que devuelve ESE MISMO pedazo para que el navegador lo cambie
    sin recargar. Una sola función, para que no haya una segunda puerta
    que se olvide de tapar algo.

    Todo lo que la plantilla necesita llega decidido (regla 10): las filas
    con su enlace ya armado, la pastilla de quién está elegido, los campos
    escondidos del form GET y los avisos de cada fuente ausente. La
    plantilla no calcula direcciones ni el navegador arma estado.
    """
    estado = _estado_calendario(request, empleada)
    pre = _pre_actividad(request, empleada, estado)
    # `qp` («q de persona») y no `q`: `q` es el buscador de ACTIVIDADES de
    # la barra de arriba, y pisarlo filtraría el calendario entero.
    qp = (request.query_params.get("qp") or "").strip()

    bus = agenda.buscar_personas(qp)
    # Lo que el formulario conserva al ELEGIR: su propio estado menos las
    # dos cosas que la fila elegida decide (`lead` y `cliente`).
    puestos = {campo: pre[campo] for campo in _CAMPOS_ACTIVIDAD_NUEVA
               if campo not in ("lead", "cliente") and pre.get(campo)}
    for fila in bus["filas"]:
        # Elegir una fila es volver a ESTE formulario con los dos campos
        # que el POST ya leía: `lead` (amarra) y `cliente` (el nombre).
        fila["liga"] = _liga(estado, nueva="1", qp=qp, lead=fila["lead"],
                             cliente=fila["nombre"], **puestos)

    return {
        "bus": bus,
        "bus_elegido": agenda.elegido_en_el_buscador(
            pre["lead"], pre["cliente"], tipo=pre["tipo"]),
        # Quitar al elegido: el mismo formulario sin esos dos campos.
        "bus_liga_quitar": _liga(estado, nueva="1", qp=qp, **puestos),
        # El form GET del buscador manda a /calendario (sin JS, una
        # recarga) y los campos escondidos son los que lo devuelven igual.
        # Acá SÍ van `lead` y `cliente`: buscar otra vez no tiene por qué
        # des-elegir a quien ya se eligió.
        "bus_ocultos": _ocultos_del_buscador(
            estado, {campo: pre[campo] for campo in _CAMPOS_ACTIVIDAD_NUEVA
                     if pre.get(campo)}),
        # De dónde pide el navegador el MISMO pedazo, sin recargar.
        "bus_fuente": "/calendario/buscar",
    }


def _ocultos_del_buscador(estado, puestos):
    """[{nombre, valor}] — los `<input type=hidden>` del form GET.

    Son los mismos datos que `_liga` mete en un enlace, puestos como
    campos: un form GET solo manda lo que lleva adentro, así que sin esto
    buscar a alguien perdería la fecha, el tipo y la nota ya escritos.

    `lead` y `cliente` se mandan SIEMPRE, aunque vayan vacíos, y los demás
    solo si traen valor (igual que `_liga`, para no ensuciar la
    dirección). La razón es que esos dos son los campos donde el buscador
    escribe a quién se eligió: si el vacío no existiera como campo, no
    habría dónde ponerlo.
    """
    campos = {
        "nueva": "1", "dia": estado["dia"], "vista": estado["vista"],
        "mio": "1" if estado["solo_mio"] else "0",
        "apagados": ",".join(sorted(estado["apagados"])),
        "quien": estado["quien"], "q": estado["q"],
        "hechas": "1" if estado["hechas"] else "0", "mes": estado["mes"],
    }
    campos.update(puestos)
    for campo in ("lead", "cliente"):
        campos.setdefault(campo, "")
    return [{"nombre": nombre, "valor": valor}
            for nombre, valor in campos.items()
            if nombre in ("lead", "cliente") or valor not in ("", None)]


@app.get("/calendario/buscar")
def calendario_buscar(request: Request):
    """El PEDAZO del buscador de personas, ya armado por el servidor.

    Lo pide `calendario.js` al buscar, con los MISMOS campos del
    formulario, para cambiarlo en su caja sin recargar la página. No
    decide nada que la pantalla entera no decida igual: el contexto sale
    de `_buscador_persona_contexto`, la única fuente de los dos caminos.

    Buscar es LECTURA: esta ruta existe también donde el calendario es de
    solo lectura (el 8095), porque encontrar a una persona no escribe
    nada. Lo que allá queda apagado es el botón de guardar.
    """
    return plantillas.TemplateResponse(
        request, "_buscador_persona.html",
        _buscador_persona_contexto(request, request.state.empleada))


@app.get("/calendario")
def calendario_pantalla(request: Request):
    empleada = request.state.empleada
    estado = _estado_calendario(request, empleada)
    # Lo que el formulario trae puesto: la MISMA función que usa el pedazo
    # del buscador, para que los enlaces de elegir vuelvan a un formulario
    # idéntico al que se estaba llenando.
    pre = _pre_actividad(request, empleada, estado)
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
        # Regla 5: el campo que falló (viajó en el redirect del POST).
        "campo_error": ((request.query_params.get("campo") or "").strip()[:60]
                        if error else ""),
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
        # Las sugerencias del campo Cliente (29/09/2026): armadas EN
        # PYTHON y renderizadas — nada consulta al vuelo. Solo cuando el
        # formulario está abierto. El selector de 32 leads de al lado lo
        # reemplazó el buscador único (BLOQUE 59), que trae leads Y
        # contactos; este datalist se queda porque cubre una cuarta
        # fuente que el buscador no mira: los clientes que solo existen
        # como texto en una actividad del calendario.
        "clientes_sugeridos": (
            agenda.clientes_para_sugerir(todas)
            if request.query_params.get("nueva") == "1" else []),
        "pre": pre,
        # EL BUSCADOR ÚNICO (BLOQUE 59), por la MISMA función que sirve el
        # pedazo de /calendario/buscar. Solo con el formulario abierto:
        # una pintada normal del calendario no busca a nadie.
        **(_buscador_persona_contexto(request, empleada)
           if request.query_params.get("nueva") == "1"
           else {"bus": None, "bus_elegido": None, "bus_ocultos": [],
                 "bus_liga_quitar": "", "bus_fuente": "/calendario/buscar"}),
        "modo": calendario.modo(),
        "puede_escribir": calendario.escritura_activa() or not calendario.configurado(),
        # El botón de guardar, APAGADO con su motivo donde no se escribe
        # (el 8095: `CALENDARIO_ESCRITURA=0`, `modo()` = lectura). Se
        # pinta igual, nunca escondido: esconderlo hace que alguien lo
        # busque. La REGLA no vive acá —el POST ya rechaza en lectura—,
        # esto es decirlo antes de que alguien llene el formulario.
        "guardar_motivo": ("En pruebas no se guarda"
                           if calendario.modo() == "lectura" else ""),
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


def _panel_lead_contexto(request, empleada, alc, vista, ver_query):
    """TODO lo que el panel del lead necesita, decidido en UN solo lugar.

    Lo llaman los DOS caminos que pintan ese panel (A5 del BLOQUE 53): la
    pantalla entera (`/control`, que lo incluye) y el pedazo que abre una
    tarjeta sin recargar (`/control/panel`, que renderiza la misma
    plantilla `_panel_lead.html` y nada más). Vive acá, y no copiado en
    cada ruta, porque es donde viven los CANDADOS DE LECTURA: la plata por
    lead (`control.puede_ver_plata`) y el chat ajeno del modo lectura de
    Atención. Una sola función = el panel pedido es, por construcción, el
    mismo panel de la página — no hay una segunda puerta que se olvide de
    tapar algo.
    """
    # El ref del ?abrir= se resuelve una vez y se le dice a la ficha si
    # esta sesión ve la plata de ESE lead (el candado de lectura vive en
    # control.puede_ver_plata, no en la plantilla).
    ref_abierta = request.query_params.get("abrir", "")
    lead_abierto = linear_leads.uno(ref_abierta) if ref_abierta else None
    abierta = control.ficha(
        ref_abierta, request.query_params.get("buscar", ""), vista=vista,
        ve_plata=control.puede_ver_plata(lead_abierto, alc))
    # BLOQUE 39.2: en el modo lectura de Atención la ficha ajena se abre
    # (ver todos es ver), pero su CHAT no se muestra — es conversación de
    # otro responsable. Va DESPUÉS de armar la ficha: el candado de la
    # PLATA (ve_plata) y el del CHAT son dos cosas distintas y cada una
    # tapa lo suyo. Decidido aquí, en Python; la plantilla solo pinta
    # hilo_error como siempre.
    if abierta and _crm_lectura(empleada) and not control.puede_tocar(abierta, alc):
        abierta["hilo"] = []
        abierta["hilo_error"] = ("El chat de este lead es de otro "
                                 "responsable: en tu vista de solo "
                                 "lectura no se muestra.")
    puede_escribir = (linear_leads.escritura_activa()
                      or not linear_leads.configurado())
    return {
        "empleada": empleada,
        "modo": linear_leads.modo(),
        "alc": alc,
        "vista": vista,
        "ver_query": ver_query,
        # El «Ver más» del panel (BLOQUE 53 · A16): una capa GET, como el
        # resto de esta pantalla. Vive ACÁ y no en la ruta grande para que
        # el pedazo de /control/panel abra igual que la página entera: las
        # tres filas que apuntan adentro viajan con ?ver_mas=1, y si esto
        # se decidiera solo arriba, tocarlas sin recargar devolvería el
        # panel plegado con el resultado escondido dentro.
        "ver_mas": request.query_params.get(control.CLAVE_VER_MAS) == "1",
        "abierta": abierta,
        # Quién ve la plata, escrito UNA vez (control.LEYENDA_SIN_PLATA):
        # la misma frase en la fila «Cotización» y donde iría el detalle
        # de la orden real.
        "leyenda_sin_plata": control.LEYENDA_SIN_PLATA,
        "estados": linear_leads.ESTADOS,
        "responsables": linear_leads.responsables(),
        "puede_mover": puede_escribir,
        "puede_tocar_abierta": (
            puede_escribir and control.puede_tocar(abierta, alc)
            if abierta else False),
    }


@app.get("/control/panel")
def control_panel(request: Request):
    """El PEDAZO del panel de un lead, ya armado por el servidor (A5).

    Lo pide panel.js al tocar una tarjeta, con la MISMA query del enlace
    de siempre (`?vista=…&ver=…&abrir=LEAD-NN`), para meterlo en su caja
    sin recargar el tablero. Sin `abrir` la plantilla sale vacía, y eso es
    justo lo que necesita el enlace de CERRAR: abrir y cerrar son la misma
    operación por el mismo camino.

    No hay nada nuevo que decidir acá: el contexto es el de la pantalla
    entera (_panel_lead_contexto), candados incluidos, y la puerta por rol
    es la misma (el prefijo «/control» del alcance cubre esta ruta).
    """
    empleada = request.state.empleada
    alc = control.alcance(empleada, _es_admin(empleada))
    vista = control.vista_pedida(request.query_params.get("vista", ""), alc)
    ver = request.query_params.get("ver", "")
    return plantillas.TemplateResponse(
        request, "_panel_lead.html",
        _panel_lead_contexto(request, empleada, alc, vista,
                             ("&ver=" + quote(ver)) if ver else ""))


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

    # «Ver a:» (BLOQUE 43, lienzo de Roles): una vista, no un permiso —
    # filtra lo que se pinta y nada más. Los avisos y la caché de espera
    # siguen mirando el tablero COMPLETO, abajo.
    ver = request.query_params.get("ver", "")
    filtros_ver = control.filtros_ver(leads, ver, vista)
    vistos = control.filtrar_por_ver(leads, ver)

    # El alcance viaja al tablero (BLOQUE 43): decide de qué tarjetas se
    # lee el monto de la orden real — la plata de un lead la ven quien lo
    # atiende, el Director y Finanzas, y de nadie más se pide a Odoo.
    if vista == "empleado":
        columnas = control.tablero_por_empleado(vistos, alc)
    else:
        columnas = control.tablero_por_estado(vistos, alc)

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

    # El panel del lead, armado por la MISMA función que usa el pedazo de
    # /control/panel (A5 del BLOQUE 53): candados de la plata y del chat
    # ajeno incluidos, para que el panel pedido sin recargar sea idéntico
    # al de la página.
    lectura_crm = _crm_lectura(empleada)
    panel = _panel_lead_contexto(request, empleada, alc, vista,
                                 ("&ver=" + quote(ver)) if ver else "")
    abierta = panel["abierta"]
    # El cuadro de asignar/reasignar (BLOQUE 43): una capa más sobre el
    # mismo panel, abierta por enlace GET (?asignar=1) como el modal del
    # motivo. Repartir sigue siendo cosa del dueño.
    asignando = None
    if abierta and request.query_params.get("asignar") == "1" and alc["admin"]:
        asignando = control.cuadro_asignar(abierta, leads)
    # El modal de la corrección manual: a un estado nuevo no se llega sin
    # motivo, así que el drag (y el botón) pasan por aquí.
    moviendo = None
    ref_mover = request.query_params.get("mover", "")
    destino = linear_leads.POR_CLAVE.get(request.query_params.get("a", ""))
    if ref_mover and destino:
        lead = control.ficha(ref_mover)
        if lead and control.puede_tocar(lead, alc):
            moviendo = {"lead": lead, "destino": destino}

    return plantillas.TemplateResponse(request, "control.html", {
        # Todo lo del panel (abierta, los candados, puede_tocar_abierta,
        # los estados, la leyenda de la plata, vista y ver_query) llega de
        # _panel_lead_contexto: UNA fuente para la página y para el pedazo.
        **panel,
        # La tira «Ver a:» (el pedacito de query que la mantiene puesta al
        # abrir o cerrar el panel ya viene en `panel`, igual que el
        # «Ver más» de A16: los dos salen de _panel_lead_contexto.
        "filtros_ver": filtros_ver,
        "columnas": columnas,
        "asignando": asignando,
        "moviendo": moviendo,
        "motivos": linear_leads.MOTIVOS_PERDIDA,
        "aviso": request.query_params.get("aviso"),
        "error": request.query_params.get("error"),
        # El rebote honesto (BLOQUE 39.3): cuando esta pantalla es la casa
        # del rol, el aviso de la puerta llega acá y se pinta.
        "rebote": request.query_params.get("rebote"),
        # BLOQUE 35 + 39.2: el título es «CRM» para todos; la marca de al
        # lado la decide Python según quién mira. Para Atención la vista
        # es el «Ver todos» de solo lectura y se dice.
        "crm_marca": ("Todos · solo lectura de lo ajeno" if lectura_crm
                      else "Todos" if alc["admin"] or _es_director(empleada)
                      else "Todos · movés solo lo tuyo"),
        # El enlace de vuelta a SU Mi CRM: solo para quien entró acá por
        # el «Ver todos» de Atención.
        "volver_mi_crm": lectura_crm,
    })


def _control_vuelve(vista, aviso="", error="", abrir=""):
    if isinstance(error, Response):
        # El 403 duro del modo lectura (BLOQUE 39.2) ya viene armado:
        # no se disfraza de redirect.
        return error
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
        quien = lead.get('resp') or 'nadie'
        if _crm_lectura(request.state.empleada):
            # BLOQUE 39.2: el CRM completo de Atención es solo lectura
            # sobre lo ajeno — escritura fuera de alcance = 403 claro.
            return alc, vista, PlainTextResponse(
                f"Ese lead es de {quien}: tu vista del CRM completo es de "
                "solo lectura y lo ajeno no se mueve desde tu rol.",
                status_code=403)
        return alc, vista, f"Ese lead es de {quien}: no lo movés vos."
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
        # Regla 5: el campo que falló (viajó en el redirect); la plantilla
        # pinta el error debajo de él y lo enfoca.
        "campo_error": ((request.query_params.get("campo") or "").strip()[:60]
                        if request.query_params.get("error") else ""),
        # Lo tecleado en el campo que falló (viajó en el redirect): la
        # lista lo repinta tal cual en vez del valor guardado.
        "valor_error": (request.query_params.get("v") or "")[:40],
    })


def _compras_vuelve(aviso="", error="", ancla="", nueva=False, abrir="",
                    bajos=False, q="", campo="", valor=""):
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
        if campo:
            # Regla 5: el campo que falló viaja con el error, para que la
            # pantalla pinte el mensaje debajo de él y lo enfoque; `valor`
            # es lo que se tecleó ahí, para repintarlo tal cual.
            partes.append("campo=" + quote(campo))
            if valor:
                partes.append("v=" + quote(valor))
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
    # Lo legible queda guardado; lo ilegible NO pisa nada y vuelve a la
    # pantalla con su aviso debajo del campo (regla 5, Nº8 del lote).
    problema = compras.guardar_borrador(usuario,
                                        datos=_campos_del_borrador(form),
                                        cantidades=cantidades, costos=costos)
    problema = problema or {}
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
        return _compras_vuelve(aviso=aviso,
                               error=error or problema.get("error", ""),
                               campo="" if error else problema.get("campo", ""),
                               valor=problema.get("valor", ""),
                               nueva=True, bajos=bajos,
                               ancla=compras.ANCLA_LINEAS)
    if form.get("quitar") is not None:
        aviso, error = compras.quitar_del_borrador(usuario, form.get("quitar"))
        return _compras_vuelve(aviso=aviso,
                               error=error or problema.get("error", ""),
                               campo="" if error else problema.get("campo", ""),
                               valor=problema.get("valor", ""),
                               nueva=True, bajos=bajos,
                               ancla=compras.ANCLA_LINEAS)
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
        error=problema.get("error", ""), campo=problema.get("campo", ""),
        valor=problema.get("valor", ""),
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
    problema = compras.guardar_borrador(usuario,
                                        datos=_campos_del_borrador(form),
                                        cantidades=cantidades, costos=costos)
    if problema:
        # Una cantidad o un costo ilegibles NO anotan la compra: el
        # borrador conserva lo demás y el aviso sale debajo del campo que
        # falló, con lo tecleado tal cual (regla 5, Nº8 del lote). Antes
        # la cantidad caía al valor previo y el costo a «no se sabe», los
        # dos en silencio.
        return _compras_vuelve(error=problema["error"],
                               campo=problema["campo"],
                               valor=problema["valor"], nueva=True,
                               ancla=compras.ANCLA_LINEAS)
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
        # El borrador sigue en pie: el formulario se reabre con todo, y si
        # el error es de un campo, el mensaje sale debajo de él (regla 5).
        return _compras_vuelve(error=str(fallo), nueva=True,
                               campo=getattr(fallo, "campo", "") or "",
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
        if getattr(fallo, "campo", ""):
            # Regla 5: el campo que falló viaja con el error — el GET pinta
            # el mensaje debajo de ese campo y lo enfoca.
            campos["campo"] = fallo.campo
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
                   avisos=(), estado=200, volver="", nombre="", campo=""):
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
        # Regla 5: el campo que falló; la plantilla pinta el error debajo
        # de él y lo enfoca, y el banner queda para errores sin campo.
        "campo_error": campo if error else "",
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
    limpio, error, campo = altas.revisar(tipo, crudo)
    if error:
        return _pantalla_alta(request, tipo if tipo in altas.CATEGORIA_DE else "",
                              previo=crudo, error=error, estado=400,
                              volver=volver, campo=campo)

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
    if (rechazo := _solo_supervision(request)) is not None:
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
    if (rechazo := _solo_supervision(request)) is not None:
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


# ---------------------------------------------------------------------------
# Conversaciones de WhatsApp (ITEM 11 del plan de Jay, 5/10/2026): TODAS
# las conversaciones por chat, casadas o no con un lead — la puerta para
# supervisar el tono. SOLO LECTURA (esta pantalla no tiene ni un POST:
# jamás escribe en Twenty, Linear ni Odoo) y solo admin, el mismo candado
# de /revisar. Los datos los arma `app/conversaciones.py`.
# ---------------------------------------------------------------------------

@app.get("/conversaciones")
def conversaciones_whatsapp(request: Request):
    """La lista agrupada por chat y el hilo de una (`?abrir=<chatId>`) en
    el panel lateral — el mismo mecanismo de /revisar, sin JS. Twenty
    caído o sin key: la pantalla carga igual y lo dice."""
    if (rechazo := _solo_supervision(request)) is not None:
        return rechazo
    try:
        n = int(request.query_params.get("n") or conversaciones.LIMITE_BASE)
    except ValueError:
        n = conversaciones.LIMITE_BASE
    n = max(60, min(n, conversaciones.LIMITE_TOPE))
    lista = conversaciones.listar(n)
    abrir = (request.query_params.get("abrir") or "").strip()
    abierta = conversaciones.abrir(abrir) if abrir else None
    # El «Ver más» solo si de verdad hay más mensajes atrás y el tope
    # todavía lo permite — honesto, nunca un botón de mentira.
    ver_mas = (min(n * 2, conversaciones.LIMITE_TOPE)
               if lista["ok"] and lista["hay_mas"]
               and n < conversaciones.LIMITE_TOPE else None)
    return plantillas.TemplateResponse(request, "conversaciones.html", {
        "empleada": request.state.empleada,
        "lista": lista,
        "abierta": abierta,
        "n": n,
        "ver_mas": ver_mas,
    })


# ===========================================================================
# ESQUELETO DE ROLES (BLOQUES 20-25 y 29, 6/10/2026): pantallas NAVEGABLES
# en solo lectura, cada una con su módulo y su plantilla propios. Regla
# dura de Abraham: nada que parezca funcionar y no guarde — lo que no
# funciona es un <button disabled> con «Todavía no». NINGUNO de estos
# módulos registra rutas POST (hay prueba que recorre app.routes). Hasta
# la fusión con la rama roles-menu estas rutas no están en _nav: se llega
# por URL directa. Los imports van aquí adentro a propósito: el bloque es
# autocontenido y no toca el import de arriba (otra sesión edita main.py
# en paralelo).
# ===========================================================================

# --- esqueleto roles: mi_crm (GET /mi-crm) ---
from . import mi_crm  # noqa: E402

# El panel «Hoy» de /calendario pinta sus cajas-hueco con esta función
# (global de plantilla, mismo patrón que envio_precios): así el route del
# calendario no se toca y la fusión con roles-menu no choca.
plantillas.env.globals["esq_panel_hoy"] = mi_crm.panel_hoy


@app.get("/mi-crm")
def mi_crm_pantalla(request: Request, pestana: str = "", etapa: str = ""):
    """El CRM chico: SOLO lo del responsable en sesión (la etiqueta
    `Resp:` que casa con su usuario — el mismo casamiento de siempre).
    La puerta es la sesión: cada quien ve únicamente lo suyo, y el rol
    Inventario ya quedó afuera por su puerta global del middleware."""
    empleada = request.state.empleada
    return plantillas.TemplateResponse(request, "mi_crm.html", {
        "empleada": empleada,
        "v": mi_crm.vista(empleada, _es_admin(empleada),
                          pestana=pestana, etapa=etapa),
        # El rebote honesto de la puerta (BLOQUE 39.3) llega aquí cuando
        # esta es la casa del rol; la pantalla lo pinta y listo.
        "rebote": request.query_params.get("rebote"),
        # El «Ver todos» (BLOQUE 39.2): el tablero completo desde el CRM
        # chico. Lo lleva quien tiene a /mi-crm como su entrada «CRM»
        # (Operaciones y Atención); para Atención además es de SOLO
        # LECTURA y el enlace lo dice.
        "ver_todos": datos_roles.crm_chico(_slugs_de(empleada)),
        "ver_todos_lectura": _crm_lectura(empleada),
    })


# --- esqueleto roles: finanzas (GET /finanzas) ---
from . import finanzas  # noqa: E402


@app.get("/finanzas")
def finanzas_pantalla(request: Request):
    """La pantalla de Finanzas, SOLO LECTURA. Nace cerrada (BLOQUE
    22.7): la abren los roles Finanzas y Director (BLOQUE 29), los
    admins y los tres deberes de la cola de pagos — la misma puerta
    del dinero que /pagos-por-confirmar."""
    empleada = request.state.empleada
    slugs = {r["slug"]
             for r in datos_roles.roles_activos_de(empleada["id"])}
    if not (slugs & {datos_roles.SLUG_FINANZAS, datos_roles.SLUG_DIRECTOR}
            or _es_admin(empleada)
            or pagos_confirmar.puede_ver(empleada["id"])):
        return Response(
            "La pantalla Finanzas es de los roles Finanzas y Director, "
            "los administradores y los deberes de la cola de pagos "
            "(Ajustes → Roles).",
            status_code=403)
    return plantillas.TemplateResponse(request, "finanzas.html", {
        "empleada": empleada,
        "f": finanzas.resumen(),
        "rebote": request.query_params.get("rebote"),
    })


# --- esqueleto roles: respuestas (GET /conversaciones/respuestas) ---
from . import respuestas  # noqa: E402


@app.get("/conversaciones/respuestas")
def conversaciones_respuestas(request: Request):
    """La sub-pestaña «Respuestas» DENTRO de Conversaciones (BLOQUES 21
    y 36.1), SOLO LECTURA. Mismo candado V2 que /conversaciones: los
    roles Director y Finanzas (y el admin-sin-rol, mientras viva el
    fail-open de transición)."""
    if (rechazo := _solo_supervision(request)) is not None:
        return rechazo
    return plantillas.TemplateResponse(request, "respuestas.html", {
        "empleada": request.state.empleada,
        "r": respuestas.vista(),
    })


# --- p37: contactos ---
# La pantalla CONTACTOS (BLOQUE 37, item 2 de Jay; diseño corto
# docs/DISENO-ITEM2-contactos.md) y, desde el BLOQUE 43, sus TRES
# pantallas del lienzo: la lista a todo el ancho (abraham-contactos),
# la página del contacto abierto (abraham-contacto-abierto) y su pestaña
# de chat (contacto-whatsapp). SOLO LECTURA: el casamiento por teléfono
# normalizado se calcula EN LECTURA al armar la vista (app/contactos.py)
# y nada se escribe en Odoo, Twenty ni Linear — este bloque no registra
# ni una ruta POST (hay prueba que recorre app.routes). La puerta de
# entrada es la sesión del middleware (Inventario ya quedó afuera por su
# puerta global); el candado FINO del dinero y del chat lo decide
# contactos._permiso() con la sesión que se arma aquí, en el servidor.
# Bloque autocontenido a propósito: otro worker edita main.py en otras
# zonas.
from . import contactos  # noqa: E402

# La única tabla propia de Contactos: las EXCEPCIONES a la unión de
# repetidos (BLOQUE 56). La pantalla sigue siendo de solo lectura —esta
# tabla se LEE al armar la lista—; quien la escribe será el «Deshacer»
# del punto B2, cuando se apruebe.
contactos.iniciar_tablas()


def _sesion_contactos(request):
    """Quién mira, para el candado del dinero y del chat. Se arma una vez
    por request y viaja al módulo: la pantalla nunca decide esto."""
    empleada = request.state.empleada
    return contactos.sesion_de(empleada, _es_admin(empleada))


@app.get("/contactos")
def contactos_lista(request: Request, q: str = "", f: str = "",
                    error: str = ""):
    """La lista del lienzo, a TODO EL ANCHO: buscador server-rendered
    (?q=) y filtros como enlaces GET (?f=); tocar un contacto abre su
    página. Todo lo que se pinta lo decide contactos.lista() (regla 10),
    el candado del dinero incluido."""
    return plantillas.TemplateResponse(request, "contactos.html", {
        "empleada": request.state.empleada,
        "v": contactos.lista(q=q, filtro=f, sesion=_sesion_contactos(request)),
        "error_aviso": error,
    })


@app.get("/contactos/{cid}")
def contactos_ficha_pantalla(request: Request, cid: str, panel: str = ""):
    """La página del contacto abierto. Las dos pestañas del panel derecho
    son enlaces GET (?panel=leads|whatsapp) que resuelve Python. Un id que
    ya no existe no es un 500: es el mismo «ya no está» de Compras,
    Control y Proveedores."""
    v = contactos.ficha(cid, sesion=_sesion_contactos(request), panel=panel)
    if v is None:
        return RedirectResponse(
            "/contactos?error="
            + quote("Ese contacto ya no está en la lista."),
            status_code=303)
    return plantillas.TemplateResponse(request, "contactos_ficha.html", {
        "empleada": request.state.empleada,
        "v": v,
    })
