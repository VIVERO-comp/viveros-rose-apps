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
from datetime import datetime
from urllib.parse import quote

from fastapi import FastAPI, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import acceso_google, calculos, conteos, datos, fichas, fotos, seguridad, ventas

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
    if (ruta == "/login" or ruta.startswith("/static") or ruta.startswith("/f/")
            or ruta.startswith("/auth/google") or ruta.startswith("/invitacion/")):
        return await call_next(request)
    empleada = seguridad.empleada_de_sesion(request.cookies.get("sesion"))
    if empleada is None:
        return RedirectResponse("/login", status_code=303)
    request.state.empleada = empleada
    return await call_next(request)


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
            **_fotos_de(p),
        }
        for p in inventario
    ]
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
            # Sin credenciales de Cloudinary el pincel del modal de foto no
            # se ofrece (el zoom y la descarga siguen funcionando).
            "puedeSubir": fotos.subida_configurada(),
            "puedeFichas": puede_fichas,
            "fichas": fichas.todas(),
            "referencias": fichas.referencias(),
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
def venta(request: Request, error: str = ""):
    # La pestaña: el botón grande "+ Nueva venta" y el historial local.
    en_curso = 0
    if ventas.configurado():
        try:
            en_curso = len(ventas.carrito_de(request.state.empleada["id"])[0])
        except Exception:
            pass
    return plantillas.TemplateResponse(request, "venta.html", {
        # El menu de abajo muestra Fichas con la misma regla del principal.
        "puede_fichas": fichas.es_editora(request.state.empleada["id"]),
        "ventas_activo": ventas.configurado(),
        "error_venta": error or None,
        "en_curso": en_curso,
        "ventas": [{**v, "fecha_texto": _fecha_venta(v["creado_en"]),
                    "etiqueta_estado": ventas.ETIQUETAS_ESTADO[v["estado"]],
                    "whatsapp": _enlace_whatsapp(request, v)}
                   for v in ventas.ventas_todas()],
    })


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
    # venta.js guarda nombre/celular mientras se escriben, para que
    # sobrevivan a los reloads de agregar/quitar plantas.
    form = await request.form()
    ventas.guardar_borrador(request.state.empleada["id"],
                            (form.get("cliente") or "").strip()[:120],
                            (form.get("celular") or "").strip()[:30])
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
    return RedirectResponse("/venta/nueva" + (f"?q={quote(q)}" if q else ""), status_code=303)


@app.post("/venta/carrito/cantidad")
async def venta_cantidad(request: Request):
    form = await request.form()
    try:
        ventas.cambiar_cantidad(request.state.empleada["id"],
                                int(form.get("producto_id", "")),
                                int(form.get("cantidad", "")))
    except (TypeError, ValueError):
        pass
    return RedirectResponse("/venta/nueva", status_code=303)


@app.post("/venta/carrito/quitar")
async def venta_quitar(request: Request):
    form = await request.form()
    try:
        ventas.quitar_del_carrito(request.state.empleada["id"],
                                  int(form.get("producto_id", "")))
    except (TypeError, ValueError):
        pass
    return RedirectResponse("/venta/nueva", status_code=303)


@app.post("/venta/cotizar")
async def venta_cotizar(request: Request):
    form = await request.form()
    try:
        registro = ventas.crear_cotizacion(request.state.empleada,
                                           form.get("cliente", ""), form.get("celular", ""))
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


@app.post("/venta/pagar")
async def venta_pagar(request: Request):
    # El botón grande "PAGADO Y CONFIRMAR PEDIDO": crea la orden desde el
    # carrito y pasa a elegir el método de pago (el cobro corre después).
    form = await request.form()
    try:
        registro = ventas.crear_cotizacion(request.state.empleada,
                                           form.get("cliente", ""), form.get("celular", ""))
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
    """Guardar de la pestaña Fichas: descripción y guía de cuidado curadas.

    Escribe en la tabla fichas_producto de la base tienda (el sitio las toma
    de ahí cuando el dueño regenera el catálogo). No toca Odoo ni cambia el
    sitio al instante."""
    def error(codigo, clave, mensaje):
        return Response(json.dumps({"error": clave, "mensaje": mensaje}),
                        status_code=codigo, media_type="application/json")

    if not fichas.es_editora(request.state.empleada["id"]):
        return error(403, "sin_permiso", "Tu usuario no puede editar fichas.")
    if not re.fullmatch(r"[A-Za-z0-9-]{1,80}", sku):
        return error(400, "sku_invalido", "SKU inválido.")
    campos = fichas.limpiar(await request.json())
    mensaje = fichas.validar(campos)
    if mensaje:
        return error(400, "ficha_invalida", mensaje)
    try:
        fichas.guardar(sku, campos, request.state.empleada["id"])
    except Exception as excepcion:
        # A la pantalla va un mensaje simple; el detalle queda en el log.
        print(f"fichas: error guardando {sku}: {excepcion!r}", flush=True)
        return error(502, "sin_guardar",
                      "No se pudo guardar en la base. Intenta de nuevo.")
    return {"resultado": "guardada", "ficha": fichas.todas().get(sku)}


@app.get("/venta/foto/{producto_id}")
def venta_foto(request: Request, producto_id: int):
    foto = ventas.foto_producto(producto_id)
    if foto is None:
        return Response(status_code=404)
    contenido, tipo = foto
    return Response(contenido, media_type=tipo,
                    headers={"Cache-Control": "private, max-age=86400"})
