"""Settings del Item 1 de Jay (5/10/2026): roles, marcas, tipos de venta,
llegadas y términos por defecto. SOLO datos locales y su pantalla.

El modelo, del decision record (docs/vivero-scope-return-to-abraham-2026-10-04
§Roles, §Two fields, §Terms) y su reply del 5/10 (§Roles reminder):

- Un ROL es un espacio con nombre donde vive trabajo. Se RENOMBRA, se
  DUPLICA y puede tener VARIAS personas (rol_persona). No es un organigrama:
  sin diseñador de permisos, sin anidación, sin pantalla por rol — esas
  operaciones SON la flexibilidad.
- Los 3 DEBERES (system_manager · operations · owner_view) son roles
  marcados con la columna `deber`: el deber se queda aunque cambie la
  persona. Un deber vive en UN rol a la vez, y nunca queda vacío en
  silencio: deberes_estado() lo dice y la pantalla lo pinta.
- MARCAS, TIPOS_VENTA (con su término por defecto y la bandera de override
  visible) y LLEGADAS son catálogos editables: agregar, renombrar,
  DESACTIVAR (nada se borra; una fila inactiva no sale en los selectores).
- Los NOMBRES DE PERSONAS jamás van en código: solo en la SEMILLA (dato),
  que se inserta únicamente cuando la tabla nace vacía. La semilla de
  rol_persona se resuelve contra las empleadas reales del login
  (seguridad.listar()): la pista que no calza con nadie no inserta nada y
  la pantalla avisa el hueco.

El CONSUMO de estos catálogos (el lead a mano con llegada/marca/tipo, los
términos en la cotización) llega con los items 3 y 5: ellos leen
listar_roles(), marcas_activas(), tipos_venta_activos(), llegadas_activas()
y quien_ocupa(deber) de aquí, sin re-trabajo.
"""

import time
import unicodedata

from . import seguridad
from .datos import _db, ahora_iso

# Los 3 deberes, con el texto que ve la pantalla. La CLAVE es estable (va
# en la columna roles.deber); el texto es presentación.
DEBERES = {
    "system_manager": {
        "titulo": "System manager",
        "que_hace": "marca entregado y ganado, y trabaja la cola de pagos por confirmar",
    },
    "operations": {
        "titulo": "Operaciones y banco",
        "que_hace": "reporta lo que entró al banco y ve la operación y el top line",
    },
    "owner_view": {
        # El TÍTULO no se toca: «Owner view» y «System manager» son los
        # nombres de los deberes del decision record de Jay, y además son
        # el nombre del rol sembrado en la base. Renombrarlos es decisión
        # suya (queda listado como dudoso en el BLOQUE 53 · A10).
        "titulo": "Owner view",
        # Lo que SÍ se cambió: «capas» y «pipeline» eran palabras de
        # programador; este deber lo que hace es VER todo.
        "que_hace": "ve todo: las respuestas, el trato y el embudo "
                    "completo",
    },
}

LARGO_NOMBRE = 60
LARGO_TERMINO = 120

# Los roles con comportamiento en código (BLOQUE 13 + BLOQUE 20 punto 1)
# se identifican por SLUG INMUTABLE, nunca por el nombre visible: renombrar
# la fila desde la pantalla no suelta ni un candado (la trampa es_maceta,
# que compara por nombre, ya mordió una vez). El slug solo lo escribe la
# migración de iniciar_tablas; ninguna ruta lo toca.
SLUG_DIRECTOR = "director"
SLUG_OPERACIONES = "operaciones"
SLUG_ATENCION = "atencion"
SLUG_INVENTARIO = "inventario"
SLUG_FINANZAS = "finanzas"

# Las 5 filas que la migración asegura: (slug, nombre visible de la
# semilla). El nombre se puede renombrar por pantalla; el slug jamás.
# Los roles nuevos nacen SIN personas (la asignación es dato, por la
# pantalla de Ajustes); nombres de personas JAMÁS en código.
ROLES_CON_SLUG = (
    (SLUG_DIRECTOR, "Director General"),
    (SLUG_OPERACIONES, "Gerente de Operaciones"),
    (SLUG_ATENCION, "Atención al Cliente"),
    (SLUG_INVENTARIO, "Inventario"),
    (SLUG_FINANZAS, "Finanzas"),
)

# ---------------------------------------------------------------------------
# El menú y el alcance por rol (punto 1 del plan de roles, precisión 4 del
# review: menú y autorización salen de la MISMA fuente). Esto es CÓDIGO,
# no dato editable por pantalla (precisión 6).
#
# PESTANAS es la fuente única: cada pestaña trae su enlace del nav Y los
# prefijos de ruta que esa pantalla usa. MENU_DE_ROL dice qué pestañas
# lleva cada slug (en su orden); ALCANCE_DE_ROL deriva los prefijos
# permitidos de esas MISMAS pestañas. Un prefijo "/x" permite "/x" y
# "/x/...", nunca "/xy".
# ---------------------------------------------------------------------------

PESTANAS = {
    "calendario": {"titulo": "Calendario", "href": "/calendario",
                   # /crm es el calendario dentro de Twenty (otro dominio,
                   # mismo trabajo); /calendario/* incluye agendar, mover,
                   # Google Calendar y la suscripción.
                   "prefijos": ("/calendario", "/crm")},
    "stock": {"titulo": "Stock", "href": "/?tab=stock",
              # El tablero del stock vive en "/" (?tab=stock); el resto es
              # su utilería: ajustar, crear/publicar productos, fotos,
              # fichas, conteos y alertas. "/" es EXACTO (no un comodín).
              "prefijos": ("/", "/stock", "/ajustar", "/productos", "/fotos",
                           "/fichas", "/conteos", "/revisiones",
                           "/plantilla.xlsx", "/alertas", "/umbral")},
    # UNA sola entrada «CRM» para todos (BLOQUE 35: «Control» desaparece
    # como nombre del menú). La ruta /control se queda; /mi-crm es el CRM
    # chico. El href por defecto es /control —el CRM de todos, el lienzo
    # de la Dirección— y acceso_de() se lo cambia a /mi-crm a quien
    # `crm_chico` diga (Operaciones y Atención, cuyos lienzos son «Mi
    # CRM»): de ahí el CRM completo se abre con «Ver todos», que para
    # Atención además es de solo lectura (BLOQUE 39.2).
    "crm": {"titulo": "CRM", "href": "/control",
            "prefijos": ("/control", "/mi-crm")},
    # Contactos (BLOQUE 33): la construye otro worker — aquí viven solo el
    # enlace del menú y la puerta que la deja pasar.
    "contactos": {"titulo": "Contactos", "href": "/contactos",
                  "prefijos": ("/contactos",)},
    "pedidos": {"titulo": "Pedidos", "href": "/pedidos",
                "prefijos": ("/pedidos",)},
    "vender": {"titulo": "Vender", "href": "/venta", "prefijos": ("/venta",)},
    "compras": {"titulo": "Compras", "href": "/compras",
                "prefijos": ("/compras",)},
    # «Conversaciones» es LA entrada del menú (BLOQUE 36.1): «Respuestas»
    # ya no es pestaña — es la sub-pestaña de adentro (el conmutador
    # Respuestas / Todos los chats de la cabecera). El prefijo cubre
    # también /conversaciones/respuestas.
    "conversaciones": {"titulo": "Conversaciones", "href": "/conversaciones",
                       "prefijos": ("/conversaciones",)},
    "finanzas": {"titulo": "Finanzas", "href": "/finanzas",
                 "prefijos": ("/finanzas",)},
    "ajustes": {"titulo": "Ajustes", "href": "/?tab=ajustes",
                "prefijos": ("/", "/ajustes", "/avisos", "/equipo",
                             "/resumen")},
}

# El menú completo (director, admin sin rol o empleada sin rol), en el
# ORDEN del diseño (BLOQUE 35.3 + BLOQUE 37): Calendario · Stock · CRM ·
# Contactos · Pedidos · Vender · Compras · Conversaciones · Finanzas ·
# Ajustes.
MENU_COMPLETO = ("calendario", "stock", "crm", "contactos", "pedidos",
                 "vender", "compras", "conversaciones", "finanzas", "ajustes")

# El interruptor del recorte por rol (BLOQUE 39.1): True = cada rol ve SU
# menú (MENU_DE_ROL); False = TODOS ven MENU_COMPLETO pintado y la puerta
# del servidor rebota lo ajeno con su aviso. Apagarlo o prenderlo es ESTA
# línea y nada más — el alcance (la puerta) NO depende de esta constante:
# esconder o mostrar una pestaña jamás cambia un permiso.
MENU_RECORTADO = True

MENU_DE_ROL = {
    SLUG_DIRECTOR: MENU_COMPLETO,
    SLUG_OPERACIONES: ("calendario", "stock", "crm", "contactos", "pedidos",
                       "vender", "compras"),
    SLUG_ATENCION: ("calendario", "crm", "contactos", "pedidos", "vender"),
    # Inventario: SOLO Stock (BLOQUE 39.1) — acceso_de() le apunta la
    # entrada a su vista plana /stock, no a /?tab=stock.
    SLUG_INVENTARIO: ("stock",),
    # Finanzas según el diseño: sus dos pantallas propias. El resto lo
    # abre por ver_todo (candado, no menú); SIN Ajustes.
    SLUG_FINANZAS: ("finanzas", "conversaciones"),
}

# ---------------------------------------------------------------------------
# V2 de permisos (BLOQUE 29 aprobado + ANALISIS-rol-manda-sobre-admin):
# el rol manda aunque seas admin. La excepción del admin ya NO es global
# (en la puerta no existe un «admin pasa siempre»): es POR RUTA — estas
# son las rutas de «Ajustes y sistema», siempre accesibles para un admin
# con cualquier rol, para que nadie quede sin el timón. "/" es exacto
# (la pestaña Ajustes vive en /?tab=ajustes). Lista en CÓDIGO, no dato.
# ---------------------------------------------------------------------------
RUTAS_SISTEMA = ("/", "/ajustes", "/avisos", "/equipo", "/resumen")

# El rótulo humano de cada rol con slug, para los avisos del rebote (los
# nombres de las filas se pueden renombrar por pantalla; el aviso usa
# estos, estables). Y el rótulo del pie del costado sí usa el nombre
# visible de la fila (rotulo_de).
ROTULO_ROL = {
    SLUG_DIRECTOR: "Dirección",
    SLUG_OPERACIONES: "Operaciones",
    SLUG_ATENCION: "Atención al Cliente",
    SLUG_INVENTARIO: "Inventario",
    SLUG_FINANZAS: "Finanzas",
}


def _prefijos_de_menu(claves):
    """Los prefijos de ruta de un juego de pestañas, aplanados y sin
    repetir — la derivación que hace de PESTANAS la fuente única."""
    vistos = []
    for clave in claves:
        for prefijo in PESTANAS[clave]["prefijos"]:
            if prefijo not in vistos:
                vistos.append(prefijo)
    return tuple(vistos)


# slug -> None (sin puerta: el director pasa todo, como hoy) o
# {"casa", "prefijos", "ver_todo"}. `ver_todo` (solo finanzas) deja pasar
# TODOS los GET/HEAD — modo ver de verdad: abre fichas — mientras
# `prefijos` vacío vuelve 403 TODA escritura (sin lista blanca de POST
# hasta que Jay dé el sí del botón de confirmar; prenderlo será agregar
# UNA ruta aquí, con test). El alcance se deriva de MENU_DE_ROL (la misma
# fuente), NUNCA de MENU_RECORTADO: el recorte es presentación.
ALCANCE_DE_ROL = {
    SLUG_DIRECTOR: None,
    # Operaciones y Atención: la casa es SU Mi CRM, la misma puerta a la
    # que apunta su entrada «CRM» del menú (crm_chico) — una casa que no
    # fuera la del menú mandaría el rebote a otra pantalla.
    SLUG_OPERACIONES: {"casa": "/mi-crm",
                       "prefijos": _prefijos_de_menu(MENU_DE_ROL[SLUG_OPERACIONES]),
                       "ver_todo": False},
    # Atención (BLOQUE 39.2): /control le queda en el alcance como la
    # vista «Ver todos» — GET completo, y sus POST del tablero pasan la
    # puerta pero el candado por lead (_control_permiso) le devuelve 403
    # duro sobre lo ajeno.
    SLUG_ATENCION: {"casa": "/mi-crm",
                    "prefijos": _prefijos_de_menu(MENU_DE_ROL[SLUG_ATENCION]),
                    "ver_todo": False},
    # Idéntico al comportamiento del BLOQUE 13: solo /stock (y /stock/*),
    # casa /stock. Sus tests siguen verdes sin tocarse.
    SLUG_INVENTARIO: {"casa": "/stock", "prefijos": ("/stock",),
                      "ver_todo": False},
    # Finanzas: su casa es SU pantalla (la primera de su menú). Con
    # ver_todo la puerta no rebota ningún GET, así que la casa solo se
    # usa cuando una pantalla la manda de vuelta (el ?tab=ajustes que no
    # le toca): mandarla a /control sería mandarla a una ajena.
    SLUG_FINANZAS: {"casa": "/finanzas", "prefijos": (), "ver_todo": True},
}

# Con varios roles, la casa es la del primero de ESTA lista que la persona
# tenga (prioridad explícita y estable, precisión 4). El director primero
# (su casa es /control y además no tiene puerta); inventario al final: su
# /stock es la casa solo cuando es el único rol.
PRIORIDAD_CASA = (SLUG_DIRECTOR, SLUG_OPERACIONES, SLUG_ATENCION,
                  SLUG_FINANZAS, SLUG_INVENTARIO)

# ---------------------------------------------------------------------------
# Semillas (DATO, no código). Se insertan solo si la tabla nace vacía; de
# ahí en adelante mandan los datos de la base. Las "pistas" de persona se
# casan contra usuario, email o nombre de las empleadas activas del login:
# la que no calce con nadie simplemente no se siembra (y la pantalla avisa).
# ---------------------------------------------------------------------------

SEMILLA_ROLES = (
    # (nombre, deber, pistas de las personas de hoy)
    ("Eventos", None, ("mary",)),
    ("PH y proyectos grandes", None, ("ruben",)),
    ("Ventas Plantas Panamá / Vivero Rose", None, ("abraham",)),
    ("System manager", "system_manager", ("abraham",)),
    ("Operaciones y banco", "operations", ("salomon", "info@")),
    ("Owner view", "owner_view", ("jordan", "jay")),
)

SEMILLA_MARCAS = ("Plantas Panamá", "Vivero Rose")

SEMILLA_TIPOS = (
    # (nombre, término por defecto, override visible). El vocabulario de los
    # tipos es el del decision record, tal cual; renombrar es de la pantalla.
    ("plant retail", "100% antes de proceder", 0),
    ("garden", "A medida", 1),
    ("maintenance", "A medida", 1),
    ("PH", "A medida", 1),
    ("commercial project", "A medida", 1),
    ("rental event", "50% depósito, 50% al cumplir", 1),
    ("other", "A medida", 1),
)

SEMILLA_LLEGADAS = ("WhatsApp", "Teléfono", "Referido",
                    "Prospección fría", "Email", "Otro")

# Los catálogos que la pantalla edita por nombre de tabla. La ruta valida
# contra ESTA lista: nunca se interpola un nombre de tabla que venga del
# navegador sin pasar por aquí.
CATALOGOS = ("marcas", "tipos_venta", "llegadas")


def _plano(texto):
    """minúsculas y sin acentos, para comparar nombres sin sorpresas."""
    limpio = unicodedata.normalize("NFD", (texto or "").strip().lower())
    return "".join(c for c in limpio if unicodedata.category(c) != "Mn")


def iniciar_tablas():
    with _db() as con:
        con.executescript("""
        -- Un rol: espacio con nombre donde vive trabajo. `deber` marca los
        -- 3 fijos (system_manager | operations | owner_view), NULL el resto.
        -- `slug` identifica a los roles con comportamiento en código (hoy
        -- solo 'inventario'): inmutable, NULL para los roles normales.
        CREATE TABLE IF NOT EXISTS roles (
            n INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT NOT NULL,
            deber TEXT,
            slug TEXT,
            activo INTEGER NOT NULL DEFAULT 1,
            creado_en TEXT NOT NULL
        );
        -- Qué empleadas (usuario del login, tabla empleadas) ocupan cada
        -- rol. Varias personas por rol y varios roles por persona.
        CREATE TABLE IF NOT EXISTS rol_persona (
            rol INTEGER NOT NULL,
            usuario TEXT NOT NULL,
            puesto_por TEXT NOT NULL DEFAULT '',
            puesto_en TEXT NOT NULL,
            PRIMARY KEY (rol, usuario)
        );
        CREATE TABLE IF NOT EXISTS marcas (
            n INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT NOT NULL,
            activo INTEGER NOT NULL DEFAULT 1,
            creado_en TEXT NOT NULL
        );
        -- termino_default: texto corto que la cotización mostrará (item 5).
        -- override_visible: si al cotizar se ofrece cambiar el término a la
        -- vista (plant retail arranca en 0: su default es pagar completo).
        CREATE TABLE IF NOT EXISTS tipos_venta (
            n INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT NOT NULL,
            termino_default TEXT NOT NULL DEFAULT '',
            override_visible INTEGER NOT NULL DEFAULT 1,
            activo INTEGER NOT NULL DEFAULT 1,
            creado_en TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS llegadas (
            n INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT NOT NULL,
            activo INTEGER NOT NULL DEFAULT 1,
            creado_en TEXT NOT NULL
        );
        -- Bitácora de asignaciones de rol (precisión 7 del review de
        -- roles): INSERT-only — nunca se actualiza ni se borra una fila.
        -- `epoch` es UTC en segundos (regla de la casa: nada de texto de
        -- hora entre máquinas); `accion` es 'alta' o 'baja'.
        CREATE TABLE IF NOT EXISTS rol_persona_bitacora (
            n INTEGER PRIMARY KEY AUTOINCREMENT,
            rol INTEGER NOT NULL,
            usuario TEXT NOT NULL,
            accion TEXT NOT NULL,
            por TEXT NOT NULL DEFAULT '',
            epoch INTEGER NOT NULL
        );
        """)
        _asegurar_columna_slug(con)
        _sembrar(con)
        _asegurar_roles_con_slug(con)


def _asegurar_columna_slug(con):
    """Migración al vuelo para las bases que nacieron antes del slug.
    Idempotente, como el resto del esquema."""
    columnas = {f["name"] for f in con.execute("PRAGMA table_info(roles)")}
    if "slug" not in columnas:
        con.execute("ALTER TABLE roles ADD COLUMN slug TEXT")


def _asegurar_roles_con_slug(con):
    """Los 5 roles del plan (ROLES_CON_SLUG) existen siempre, SIN persona
    nueva (la asignación es dato: la hace Abraham por la pantalla o por
    BD). Si alguien ya había creado a mano un rol con ese nombre (o con
    el slug como nombre), se adopta ESE (se le estampa el slug) en vez de
    nacer un tocayo; si no, se inserta. Generaliza la migración del rol
    Inventario (BLOQUE 13) sin cambiarle el comportamiento."""
    for slug, nombre in ROLES_CON_SLUG:
        if con.execute("SELECT 1 FROM roles WHERE slug=?",
                       (slug,)).fetchone():
            continue
        adoptado = False
        for fila in con.execute("SELECT n, nombre FROM roles WHERE slug IS NULL"):
            if _plano(fila["nombre"]) in (slug, _plano(nombre)):
                con.execute("UPDATE roles SET slug=? WHERE n=?",
                            (slug, fila["n"]))
                adoptado = True
                break
        if not adoptado:
            con.execute(
                "INSERT INTO roles (nombre, deber, slug, activo, creado_en) "
                "VALUES (?,NULL,?,1,?)", (nombre, slug, ahora_iso()))


def _sembrar(con):
    """Semillas solo en tablas vacías: una base ya poblada no se toca (lo
    renombrado o desactivado por pantalla gana para siempre)."""
    ahora = ahora_iso()
    if con.execute("SELECT 1 FROM roles LIMIT 1").fetchone() is None:
        empleadas = _empleadas_activas(con)
        for nombre, deber, pistas in SEMILLA_ROLES:
            fila = con.execute(
                "INSERT INTO roles (nombre, deber, activo, creado_en) "
                "VALUES (?,?,1,?)", (nombre, deber, ahora))
            usuario = _casar_pista(pistas, empleadas)
            if usuario:
                con.execute(
                    "INSERT OR IGNORE INTO rol_persona "
                    "(rol, usuario, puesto_por, puesto_en) VALUES (?,?,?,?)",
                    (fila.lastrowid, usuario, "semilla", ahora))
                _anotar_bitacora(con, fila.lastrowid, usuario, "alta", "semilla")
    if con.execute("SELECT 1 FROM marcas LIMIT 1").fetchone() is None:
        for nombre in SEMILLA_MARCAS:
            con.execute("INSERT INTO marcas (nombre, activo, creado_en) "
                        "VALUES (?,1,?)", (nombre, ahora))
    if con.execute("SELECT 1 FROM tipos_venta LIMIT 1").fetchone() is None:
        for nombre, termino, override in SEMILLA_TIPOS:
            con.execute(
                "INSERT INTO tipos_venta (nombre, termino_default, "
                "override_visible, activo, creado_en) VALUES (?,?,?,1,?)",
                (nombre, termino, override, ahora))
    if con.execute("SELECT 1 FROM llegadas LIMIT 1").fetchone() is None:
        for nombre in SEMILLA_LLEGADAS:
            con.execute("INSERT INTO llegadas (nombre, activo, creado_en) "
                        "VALUES (?,1,?)", (nombre, ahora))


def _empleadas_activas(con):
    return [dict(f) for f in con.execute(
        "SELECT usuario, nombre, email FROM empleadas WHERE activa=1")]


def _casar_pista(pistas, empleadas):
    """El usuario de la primera empleada que calce con alguna pista, o None.

    Calza si la pista aparece dentro del usuario o del email, o si alguna
    palabra del nombre empieza con ella (sin acentos ni mayúsculas). Si
    nadie calza no se inventa nada: el rol queda sin persona y la pantalla
    lo avisa.
    """
    for pista in pistas:
        plana = _plano(pista)
        for e in empleadas:
            if plana in _plano(e["usuario"]) or plana in _plano(e["email"] or ""):
                return e["usuario"]
            if any(p.startswith(plana) for p in _plano(e["nombre"]).split()):
                return e["usuario"]
    return None


# ---------------------------------------------------------------------------
# Lecturas: lo que consumen la pantalla y los items 3 y 5.
# ---------------------------------------------------------------------------

def listar_roles(solo_activos=True):
    """[{n, nombre, deber, activo, personas: [{usuario, nombre, activa}]}].

    Las personas salen con su nombre del login; una empleada desactivada
    después de ocupar el rol sigue visible (activa=0) para que la pantalla
    la muestre apagada en vez de hacerla desaparecer en silencio.
    """
    with _db() as con:
        roles = [dict(f) for f in con.execute(
            "SELECT n, nombre, deber, activo FROM roles "
            + ("WHERE activo=1 " if solo_activos else "") + "ORDER BY n")]
        personas = con.execute(
            "SELECT rp.rol, rp.usuario, "
            "       COALESCE(e.nombre, rp.usuario) AS nombre, "
            "       COALESCE(e.activa, 0) AS activa "
            "FROM rol_persona rp LEFT JOIN empleadas e ON e.usuario = rp.usuario "
            "ORDER BY nombre").fetchall()
    por_rol = {}
    for p in personas:
        por_rol.setdefault(p["rol"], []).append(
            {"usuario": p["usuario"], "nombre": p["nombre"],
             "activa": bool(p["activa"])})
    for rol in roles:
        rol["personas"] = por_rol.get(rol["n"], [])
    return roles


def _rol(con, n):
    fila = con.execute("SELECT n, nombre, deber, activo FROM roles WHERE n=?",
                       (n,)).fetchone()
    return dict(fila) if fila else None


def quien_ocupa(deber):
    """Quién carga un deber hoy: {"rol": {...}, "personas": [...]} o None si
    ningún rol activo lo tiene. Para los consumidores (items 3+): 'quién
    marca entregado', 'a quién le toca el banco'."""
    roles = [r for r in listar_roles(solo_activos=True) if r["deber"] == deber]
    if not roles:
        return None
    rol = roles[0]
    return {"rol": rol,
            "personas": [p for p in rol["personas"] if p["activa"]]}


def deberes_estado():
    """Los 3 deberes para la pantalla, SIN huecos silenciosos: cada uno trae
    su rol y sus personas, o el aviso de qué le falta ('sin_rol' no debería
    pasar — la semilla los crea y desactivar un rol con deber se rechaza —
    pero si pasa, se dice)."""
    estado = []
    for clave, textos in DEBERES.items():
        ocupante = quien_ocupa(clave)
        estado.append({
            "clave": clave,
            "titulo": textos["titulo"],
            "que_hace": textos["que_hace"],
            "rol": ocupante["rol"] if ocupante else None,
            "personas": ocupante["personas"] if ocupante else [],
            "aviso": (None if ocupante and ocupante["personas"]
                      else ("sin_persona" if ocupante else "sin_rol")),
        })
    return estado


def roles_activos_de(usuario):
    """Los roles ACTIVOS que ocupa una empleada del login:
    [{n, nombre, slug}]. Un rol desactivado no cuenta."""
    with _db() as con:
        return [dict(f) for f in con.execute(
            "SELECT r.n, r.nombre, r.slug FROM roles r "
            "JOIN rol_persona rp ON rp.rol = r.n "
            "WHERE rp.usuario=? AND r.activo=1 ORDER BY r.n", (usuario,))]


def solo_inventario(empleada):
    """EL predicado del rol Inventario — el único lugar donde se decide.

    True para una empleada cuyo ÚNICO rol activo es el del slug
    'inventario'. De aquí cuelgan las tres cosas, siempre juntas: el
    menú (=[Stock]), el redirect global a /stock y los candados de los
    POST. La matriz: solo-inventario → True · inventario+otro rol →
    False · sin roles → False · **admin+inventario → True** (V2 del
    BLOQUE 29: el rol manda aunque seas admin — la excepción del admin
    ya no vive en el predicado sino POR RUTA, en RUTAS_SISTEMA: un admin
    con este rol conserva Ajustes y nada más). Se compara por SLUG,
    jamás por el nombre: la fila se puede renombrar sin soltar un solo
    candado.

    `empleada` es el dict de la sesión (request.state.empleada)."""
    mios = roles_activos_de(empleada["id"])
    return len(mios) == 1 and mios[0]["slug"] == SLUG_INVENTARIO


def crm_chico(slugs):
    """¿La entrada «CRM» de esta persona es su Mi CRM (/mi-crm) y no el
    tablero completo? Sí para Operaciones y Atención —los dos lienzos del
    diseño son «Mi CRM» (ruben-gerente-de-operaciones-mi-crm y
    mary-atencion-al-cliente-mi-crm)— y no para la Dirección, cuyo lienzo
    es el CRM de todos. UNA sola fuente: la usan el menú (acceso_de), la
    casa del rol y el enlace «Ver todos» de Mi CRM.

    `slugs` es cualquier iterable de slugs de rol."""
    slugs = set(slugs)
    return bool(slugs & {SLUG_OPERACIONES, SLUG_ATENCION}) and (
        SLUG_DIRECTOR not in slugs)


def acceso_de(empleada):
    """El menú y el alcance de UNA empleada, desde la misma fuente
    (PESTANAS / MENU_DE_ROL / ALCANCE_DE_ROL — precisión 4 del review).

    Devuelve {"menu": [...], "alcance": None | {...}}:

    - menu: [{clave, titulo, href}] ya decidido en Python para _nav.html
      y _lado.html (regla 10: cero lógica en la plantilla). Con el
      recorte prendido (MENU_RECORTADO) y varios roles es la UNIÓN en el
      ORDEN de MENU_COMPLETO; sin ningún rol con menú propio, o con el
      recorte apagado, el menú completo. El rol Atención lleva su CRM a
      /mi-crm (BLOQUE 39.2) y el solo-inventario su Stock a /stock (la
      vista plana). Un admin SIEMPRE lleva Ajustes en el menú — es el
      espejo visible de RUTAS_SISTEMA.
    - alcance: None = sin puerta (como hoy). Es None cuando la persona no
      tiene roles, o cuando ALGUNO de sus roles no acota (director, o un
      rol sin slug como los pods: Eventos, PH…) — ese FAIL-OPEN de
      transición es decisión explícita (precisión 8), fijada con test; la
      excepción del ADMIN ya no es global (V2): vive POR RUTA en
      RUTAS_SISTEMA, aplicada en main._puerta_por_rol.
      Si todos sus roles acotan: {"prefijos": unión, "casa": la del
      primer slug en PRIORIDAD_CASA, "ver_todo": True si algún rol lo es
      (finanzas), "slugs": set} — la puerta del middleware lo aplica.
    """
    roles = roles_activos_de(empleada["id"])
    slugs = [r["slug"] for r in roles if r["slug"] in ALCANCE_DE_ROL]
    orden = [s for s in PRIORIDAD_CASA if s in slugs]

    claves = []
    if MENU_RECORTADO:
        con_menu = [s for s in orden if MENU_DE_ROL.get(s)]
        if len(con_menu) == 1:
            # Un solo rol: SU orden, el del lienzo. Finanzas abre con
            # Finanzas (jordan-finanzas), no con Conversaciones.
            claves = list(MENU_DE_ROL[con_menu[0]])
        else:
            # Varios roles: la unión sale en el ORDEN del diseño
            # (MENU_COMPLETO), no en el de los roles — si no, el menú
            # dependería de en qué orden le pusieron los roles.
            en_union = set()
            for slug in con_menu:
                en_union.update(MENU_DE_ROL[slug])
            claves = [c for c in MENU_COMPLETO if c in en_union]
    if not claves:
        claves = list(MENU_COMPLETO)
    menu = [{"clave": c, "titulo": PESTANAS[c]["titulo"],
             "href": PESTANAS[c]["href"]} for c in claves]
    if MENU_RECORTADO:
        if set(orden) == {SLUG_INVENTARIO}:
            # La única entrada del solo-inventario es SU pantalla.
            menu = [{"clave": "stock", "titulo": "Stock", "href": "/stock"}]
        elif crm_chico(orden):
            for p in menu:
                if p["clave"] == "crm":
                    p["href"] = "/mi-crm"
    if seguridad.es_admin(empleada) and all(p["clave"] != "ajustes"
                                            for p in menu):
        # El timón nunca se esconde: un admin con rol restrictivo ve
        # Ajustes en el menú porque RUTAS_SISTEMA se lo deja pasar.
        menu.append({"clave": "ajustes", "titulo": "Ajustes",
                     "href": "/?tab=ajustes"})

    abierto = (not roles) or any(
        r["slug"] not in ALCANCE_DE_ROL or ALCANCE_DE_ROL[r["slug"]] is None
        for r in roles)
    if abierto:
        return {"menu": menu, "alcance": None}
    prefijos = []
    ver_todo = False
    for slug in orden:
        ver_todo = ver_todo or ALCANCE_DE_ROL[slug]["ver_todo"]
        for p in ALCANCE_DE_ROL[slug]["prefijos"]:
            if p not in prefijos:
                prefijos.append(p)
    return {"menu": menu, "alcance": {
        "prefijos": tuple(prefijos),
        "casa": ALCANCE_DE_ROL[orden[0]]["casa"],
        "ver_todo": ver_todo,
        "slugs": frozenset(orden),
    }}


def rotulo_de(empleada):
    """El pie del costado (diseño Roles v3: nombre y rol del usuario):
    los nombres VISIBLES de sus roles activos, tal como están en la
    base. Sin roles: «Admin» para un admin, «Equipo» para el resto —
    nunca se inventa un rol."""
    nombres = [r["nombre"] for r in roles_activos_de(empleada["id"])]
    if nombres:
        return " · ".join(nombres[:3])
    return "Admin" if seguridad.es_admin(empleada) else "Equipo"


def texto_pestana_ajena(ruta, tab=""):
    """El aviso del rebote honesto (BLOQUE 39.3): a quién pertenece la
    pestaña que se intentó abrir. Se calcula de la MISMA fuente del menú
    (MENU_DE_ROL); los nombres son los rótulos estables de ROTULO_ROL,
    no los renombrables de la base.

    `tab` es el `?tab=` de la raíz: en "/" conviven Stock y Ajustes y el
    camino no alcanza para nombrar cuál se pidió — sin él, «/?tab=stock»
    saldría con el aviso genérico."""
    clave, largo = None, 0
    if ruta == "/" and tab in PESTANAS:
        clave = tab
    if clave is None:
        for c, p in PESTANAS.items():
            for pre in p["prefijos"]:
                if pre == "/":
                    continue
                if (ruta == pre
                        or ruta.startswith(pre + "/")) and len(pre) > largo:
                    clave, largo = c, len(pre)
    if clave is None:
        return "Esa pantalla no es de tu rol; pedísela al encargado."
    if clave == "ajustes":
        quienes = "los administradores y la Dirección"
    else:
        duenos = [ROTULO_ROL[s] for s in PRIORIDAD_CASA
                  if clave in MENU_DE_ROL.get(s, ())]
        quienes = " y ".join([", ".join(duenos[:-1]), duenos[-1]]
                             if len(duenos) > 1 else duenos)
    titulo = PESTANAS[clave]["titulo"]
    return (f"La pestaña {titulo} es de {quienes}; tu rol no la usa.")


def _catalogo(tabla, solo_activos):
    if tabla not in CATALOGOS:
        raise ValueError(f"catálogo desconocido: {tabla}")
    extra = ", termino_default, override_visible" if tabla == "tipos_venta" else ""
    with _db() as con:
        return [dict(f) for f in con.execute(
            f"SELECT n, nombre, activo{extra} FROM {tabla} "
            + ("WHERE activo=1 " if solo_activos else "") + "ORDER BY n")]


def marcas_activas():
    return _catalogo("marcas", True)


def tipos_venta_activos():
    """Los tipos vivos, cada uno con termino_default y override_visible:
    lo que el selector del lead (item 3) y los términos de la cotización
    (item 5) necesitan, ya listo."""
    return _catalogo("tipos_venta", True)


def llegadas_activas():
    return _catalogo("llegadas", True)


def catalogo_completo(tabla):
    """Activos e inactivos, para la pantalla (los inactivos salen apagados
    con su botón de reactivar; en los selectores de consumo no existen)."""
    return _catalogo(tabla, False)


# ---------------------------------------------------------------------------
# Escrituras de roles. Devuelven None si quedó, o un código de error que la
# ruta traduce a su aviso. Nada se borra nunca.
# ---------------------------------------------------------------------------

def _anotar_bitacora(con, rol_n, usuario, accion, por):
    """UNA fila nueva en rol_persona_bitacora (precisión 7: asignar o
    quitar un rol queda auditado — quién, cuándo, qué rol, a quién; fila
    nueva, nunca cambio silencioso). Solo la llaman las escrituras de
    rol_persona que de verdad cambiaron algo."""
    con.execute(
        "INSERT INTO rol_persona_bitacora (rol, usuario, accion, por, epoch) "
        "VALUES (?,?,?,?,?)", (rol_n, usuario, accion, por, int(time.time())))


def _nombre_valido(nombre):
    nombre = (nombre or "").strip()
    if not nombre or len(nombre) > LARGO_NOMBRE:
        return None
    return nombre


def _nombre_repetido(con, tabla, nombre, menos_n=None):
    """¿Ya existe ese nombre (sin acentos ni mayúsculas) en la tabla?
    Cuenta también los inactivos: reactivar no debe chocar con un tocayo."""
    for fila in con.execute(f"SELECT n, nombre FROM {tabla}"):
        if fila["n"] != menos_n and _plano(fila["nombre"]) == _plano(nombre):
            return True
    return False


def renombrar_rol(n, nombre):
    nombre = _nombre_valido(nombre)
    if not nombre:
        return "vacio"
    with _db() as con:
        if _rol(con, n) is None:
            return "no_existe"
        if _nombre_repetido(con, "roles", nombre, menos_n=n):
            return "repetido"
        con.execute("UPDATE roles SET nombre=? WHERE n=?", (nombre, n))
    return None


def duplicar_rol(n, nombre=None, por=""):
    """La copia para los pods: mismo equipo de personas, SIN el deber (un
    deber vive en un solo rol). Devuelve (error, n del nuevo)."""
    with _db() as con:
        original = _rol(con, n)
        if original is None or not original["activo"]:
            return "no_existe", None
        nombre = _nombre_valido(nombre) or f"Copia de {original['nombre']}"[:LARGO_NOMBRE]
        if _nombre_repetido(con, "roles", nombre):
            return "repetido", None
        ahora = ahora_iso()
        nuevo = con.execute(
            "INSERT INTO roles (nombre, deber, activo, creado_en) "
            "VALUES (?,NULL,1,?)", (nombre, ahora))
        con.execute(
            "INSERT INTO rol_persona (rol, usuario, puesto_por, puesto_en) "
            "SELECT ?, usuario, puesto_por, ? FROM rol_persona WHERE rol=?",
            (nuevo.lastrowid, ahora, n))
        # Cada persona copiada es una asignación nueva: a la bitácora
        # (precisión 7), con quién hizo la copia.
        for fila in con.execute("SELECT usuario FROM rol_persona WHERE rol=?",
                                (nuevo.lastrowid,)):
            _anotar_bitacora(con, nuevo.lastrowid, fila["usuario"], "alta", por)
        return None, nuevo.lastrowid


def poner_persona(rol_n, usuario, por):
    """Agrega una empleada ACTIVA del login al rol. Idempotente (ponerla
    dos veces no duplica ni truena)."""
    with _db() as con:
        rol = _rol(con, rol_n)
        if rol is None or not rol["activo"]:
            return "no_existe"
        empleada = con.execute(
            "SELECT 1 FROM empleadas WHERE usuario=? AND activa=1",
            (usuario,)).fetchone()
        if empleada is None:
            return "empleada_invalida"
        puesto = con.execute(
            "INSERT OR IGNORE INTO rol_persona (rol, usuario, puesto_por, "
            "puesto_en) VALUES (?,?,?,?)", (rol_n, usuario, por, ahora_iso()))
        if puesto.rowcount:
            # Solo si de verdad entró (re-ponerla es idempotente y no
            # ensucia la bitácora con altas repetidas).
            _anotar_bitacora(con, rol_n, usuario, "alta", por)
    return None


def quitar_persona(rol_n, usuario, por=""):
    """Quita a la persona del rol. Si el rol carga un deber y se queda sin
    nadie, el quite SE APLICA pero se devuelve 'deber_sin_persona': la ruta
    lo convierte en el aviso — reasignable, nunca silenciosamente vacío."""
    with _db() as con:
        rol = _rol(con, rol_n)
        if rol is None:
            return "no_existe"
        quitado = con.execute(
            "DELETE FROM rol_persona WHERE rol=? AND usuario=?",
            (rol_n, usuario))
        if quitado.rowcount:
            _anotar_bitacora(con, rol_n, usuario, "baja", por)
        if rol["deber"]:
            queda = con.execute(
                "SELECT 1 FROM rol_persona WHERE rol=? LIMIT 1",
                (rol_n,)).fetchone()
            if queda is None:
                return "deber_sin_persona"
    return None


def asignar_deber(deber, rol_n):
    """Mueve un deber a otro rol activo (el rol que lo tenía queda como rol
    normal, con sus personas). Un rol carga UN deber como mucho; si el
    destino se queda sin personas, se aplica igual y se avisa."""
    if deber not in DEBERES:
        return "deber_invalido"
    with _db() as con:
        rol = _rol(con, rol_n)
        if rol is None or not rol["activo"]:
            return "no_existe"
        if rol["deber"] and rol["deber"] != deber:
            return "rol_con_otro_deber"
        con.execute("UPDATE roles SET deber=NULL WHERE deber=?", (deber,))
        con.execute("UPDATE roles SET deber=? WHERE n=?", (deber, rol_n))
        sin_gente = con.execute(
            "SELECT 1 FROM rol_persona WHERE rol=? LIMIT 1",
            (rol_n,)).fetchone() is None
    return "deber_sin_persona" if sin_gente else None


# ---------------------------------------------------------------------------
# Escrituras de catálogos (marcas · tipos_venta · llegadas).
# ---------------------------------------------------------------------------

def catalogo_agregar(tabla, nombre, termino="", override_visible=1):
    if tabla not in CATALOGOS:
        return "catalogo_invalido"
    nombre = _nombre_valido(nombre)
    if not nombre:
        return "vacio"
    with _db() as con:
        if _nombre_repetido(con, tabla, nombre):
            return "repetido"
        if tabla == "tipos_venta":
            con.execute(
                "INSERT INTO tipos_venta (nombre, termino_default, "
                "override_visible, activo, creado_en) VALUES (?,?,?,1,?)",
                (nombre, (termino or "").strip()[:LARGO_TERMINO],
                 1 if override_visible else 0, ahora_iso()))
        else:
            con.execute(
                f"INSERT INTO {tabla} (nombre, activo, creado_en) "
                "VALUES (?,1,?)", (nombre, ahora_iso()))
    return None


def catalogo_renombrar(tabla, n, nombre):
    if tabla not in CATALOGOS:
        return "catalogo_invalido"
    nombre = _nombre_valido(nombre)
    if not nombre:
        return "vacio"
    with _db() as con:
        if con.execute(f"SELECT 1 FROM {tabla} WHERE n=?", (n,)).fetchone() is None:
            return "no_existe"
        if _nombre_repetido(con, tabla, nombre, menos_n=n):
            return "repetido"
        con.execute(f"UPDATE {tabla} SET nombre=? WHERE n=?", (nombre, n))
    return None


def catalogo_activar(tabla, n, activo):
    """Desactivar (nunca borrar) o reactivar una fila. La inactiva se queda
    con su historia y desaparece de los selectores de consumo."""
    if tabla not in CATALOGOS:
        return "catalogo_invalido"
    with _db() as con:
        if con.execute(f"SELECT 1 FROM {tabla} WHERE n=?", (n,)).fetchone() is None:
            return "no_existe"
        con.execute(f"UPDATE {tabla} SET activo=? WHERE n=?",
                    (1 if activo else 0, n))
    return None


def fijar_termino(n, termino, override_visible):
    """El término por defecto y la bandera de override de UN tipo de venta.
    El registro del override ya aplicado en una cotización es del item 5."""
    with _db() as con:
        if con.execute("SELECT 1 FROM tipos_venta WHERE n=?", (n,)).fetchone() is None:
            return "no_existe"
        con.execute(
            "UPDATE tipos_venta SET termino_default=?, override_visible=? "
            "WHERE n=?",
            ((termino or "").strip()[:LARGO_TERMINO],
             1 if override_visible else 0, n))
    return None
