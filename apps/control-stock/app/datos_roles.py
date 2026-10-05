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
        "titulo": "Owner view",
        "que_hace": "ve todas las capas: respuestas, tono, pipeline",
    },
}

LARGO_NOMBRE = 60
LARGO_TERMINO = 120

# El rol Inventario (BLOQUE 13, 5/10/2026) se identifica por este SLUG
# INMUTABLE, nunca por el nombre visible: renombrar la fila desde la
# pantalla no suelta ni un candado (la trampa es_maceta, que compara por
# nombre, ya mordió una vez). El slug solo lo escribe la migración de
# iniciar_tablas; ninguna ruta lo toca.
SLUG_INVENTARIO = "inventario"

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
        """)
        _asegurar_columna_slug(con)
        _sembrar(con)
        _asegurar_rol_inventario(con)


def _asegurar_columna_slug(con):
    """Migración al vuelo para las bases que nacieron antes del slug.
    Idempotente, como el resto del esquema."""
    columnas = {f["name"] for f in con.execute("PRAGMA table_info(roles)")}
    if "slug" not in columnas:
        con.execute("ALTER TABLE roles ADD COLUMN slug TEXT")


def _asegurar_rol_inventario(con):
    """El rol Inventario existe siempre, SIN persona (a Omar lo invita
    Korto y le pone el rol por la pantalla del item 1). Si alguien ya
    había creado a mano un rol llamado «Inventario», se adopta ESE (se le
    estampa el slug) en vez de nacer un tocayo; si no, se inserta."""
    if con.execute("SELECT 1 FROM roles WHERE slug=?",
                   (SLUG_INVENTARIO,)).fetchone():
        return
    for fila in con.execute("SELECT n, nombre FROM roles"):
        if _plano(fila["nombre"]) == SLUG_INVENTARIO:
            con.execute("UPDATE roles SET slug=? WHERE n=?",
                        (SLUG_INVENTARIO, fila["n"]))
            return
    con.execute(
        "INSERT INTO roles (nombre, deber, slug, activo, creado_en) "
        "VALUES (?,NULL,?,1,?)", ("Inventario", SLUG_INVENTARIO, ahora_iso()))


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

    True solo para una empleada NO admin cuyo ÚNICO rol activo es el del
    slug 'inventario'. De aquí cuelgan las tres cosas, siempre juntas: el
    menú (=[Stock]), el redirect global a /stock y los candados de los
    POST (la puerta vive en main._puerta_rol_inventario). La matriz:
    admin → False · solo-inventario → True · inventario+otro rol → False
    · sin roles → False. Se compara por SLUG, jamás por el nombre: la
    fila se puede renombrar sin soltar un solo candado.

    `empleada` es el dict de la sesión (request.state.empleada)."""
    mios = roles_activos_de(empleada["id"])
    if len(mios) != 1 or mios[0]["slug"] != SLUG_INVENTARIO:
        return False
    # El admin nunca queda preso en la vista plana, tenga el rol que
    # tenga. Se pregunta al final: es la consulta más cara de las dos.
    return not seguridad.es_admin(empleada)


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


def duplicar_rol(n, nombre=None):
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
        con.execute(
            "INSERT OR IGNORE INTO rol_persona (rol, usuario, puesto_por, "
            "puesto_en) VALUES (?,?,?,?)", (rol_n, usuario, por, ahora_iso()))
    return None


def quitar_persona(rol_n, usuario):
    """Quita a la persona del rol. Si el rol carga un deber y se queda sin
    nadie, el quite SE APLICA pero se devuelve 'deber_sin_persona': la ruta
    lo convierte en el aviso — reasignable, nunca silenciosamente vacío."""
    with _db() as con:
        rol = _rol(con, rol_n)
        if rol is None:
            return "no_existe"
        con.execute("DELETE FROM rol_persona WHERE rol=? AND usuario=?",
                    (rol_n, usuario))
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
