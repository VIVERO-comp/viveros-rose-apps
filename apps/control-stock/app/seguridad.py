"""Autenticación de la app: login con Google por invitación, y sesiones.

Desde el 16/09/2026 la entrada principal es Google (módulo acceso_google):
solo entra un email invitado desde la pestaña Ajustes, o ya registrado como
empleada activa. La primera entrada crea la empleada (usuario = email, sin
contraseña, hash marcador SIN_CONTRASENA). Revocar acceso = desactivar la
empleada (borra también sus sesiones).

El esquema viejo de usuario + contraseña sigue vivo como respaldo (por si
Google falla o alguien no tiene cuenta): PBKDF2-SHA256 (stdlib, 600k
iteraciones, sal por empleada), altas por consola:
    python -m app.usuarias crear <usuario> "<Nombre>"

Las sesiones son las mismas para ambos caminos: token aleatorio en cookie
HttpOnly, con expiración deslizante.
"""

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta

from . import datos

ITERACIONES = 600_000
DIAS_SESION = 30

# Hash marcador de las empleadas que entran con Google: no hay contraseña
# que verificar y el formulario de respaldo nunca las deja pasar.
SIN_CONTRASENA = "google"


def _derivar(contrasena, sal_hex, iteraciones=ITERACIONES):
    return hashlib.pbkdf2_hmac(
        "sha256", contrasena.encode(), bytes.fromhex(sal_hex), iteraciones
    ).hex()


def _empacar_hash(contrasena):
    sal = secrets.token_hex(16)
    return f"pbkdf2_sha256${ITERACIONES}${sal}${_derivar(contrasena, sal)}"


def crear_empleada(usuario, nombre, contrasena):
    with datos._db() as con:
        con.execute(
            "INSERT INTO empleadas (usuario, nombre, hash, activa) VALUES (?,?,?,1)",
            (usuario, nombre, _empacar_hash(contrasena)),
        )


def cambiar_contrasena(usuario, contrasena):
    with datos._db() as con:
        con.execute("UPDATE empleadas SET hash=? WHERE usuario=?",
                    (_empacar_hash(contrasena), usuario))
        # La contraseña cambió: las sesiones abiertas dejan de valer.
        con.execute("DELETE FROM sesiones WHERE usuario=?", (usuario,))


def desactivar(usuario):
    with datos._db() as con:
        con.execute("UPDATE empleadas SET activa=0 WHERE usuario=?", (usuario,))
        con.execute("DELETE FROM sesiones WHERE usuario=?", (usuario,))


def listar():
    with datos._db() as con:
        return [dict(f) for f in con.execute(
            "SELECT usuario, nombre, email, activa FROM empleadas ORDER BY nombre")]


def verificar(usuario, contrasena):
    """La empleada activa si usuario y contraseña coinciden; si no, None."""
    with datos._db() as con:
        fila = con.execute(
            "SELECT usuario, nombre, email, email_verificado, hash "
            "FROM empleadas WHERE usuario=? AND activa=1",
            (usuario,),
        ).fetchone()
    if fila is None or "$" not in fila["hash"]:
        # Mismo costo aunque el usuario no exista (o entre solo con Google):
        # no se filtra quién existe.
        _derivar(contrasena, "00" * 16)
        return None
    _, iteraciones, sal, esperado = fila["hash"].split("$")
    calculado = _derivar(contrasena, sal, int(iteraciones))
    if hmac.compare_digest(calculado, esperado):
        return {"id": fila["usuario"], "nombre": fila["nombre"],
                "email": fila["email"], "email_verificado": fila["email_verificado"]}
    return None


# ---------------------------------------------------------------------------
# Login con Google e invitaciones (pestaña Ajustes)
# ---------------------------------------------------------------------------

def entrar_con_google(email, nombre_google, es_admin=False, token=None):
    """La empleada para un login de Google, creándola si tiene invitación.

    La invitación puede llegar por dos caminos: el email coincide con una
    invitación pendiente, o la persona entró por el link compartible y trae
    su token (cookie 'invitacion'). Devuelve la empleada (dict) o None si
    no puede entrar: sin invitación, o con el acceso revocado (empleada
    inactiva). Los emails admin (AJUSTES_ADMINS) entran sin invitación: son
    quienes invitan al resto y alguien tiene que poder entrar primero.
    """
    email = email.strip().lower()
    with datos._db() as con:
        fila = con.execute(
            "SELECT usuario, nombre, email, activa FROM empleadas "
            "WHERE lower(email)=? OR usuario=?", (email, email)).fetchone()
        if fila is not None:
            if not fila["activa"]:
                return None
            # Google confirmó que la cuenta es suya: el email queda
            # verificado (el anotado a mano en Mi cuenta nace sin verificar).
            con.execute(
                "UPDATE empleadas SET email=?, email_verificado=1 WHERE usuario=?",
                (email, fila["usuario"]))
            return {"id": fila["usuario"], "nombre": fila["nombre"],
                    "email": email, "email_verificado": 1}
        invitacion = con.execute(
            "SELECT token, nombre FROM invitaciones WHERE aceptada_en IS NULL "
            "AND (lower(email)=? OR token=?)",
            (email, token or "")).fetchone()
        if invitacion is None and not es_admin:
            return None
        nombre = (invitacion["nombre"] if invitacion else "") or nombre_google or email
        con.execute(
            "INSERT INTO empleadas (usuario, nombre, hash, activa, email, email_verificado) "
            "VALUES (?,?,?,1,?,1)", (email, nombre, SIN_CONTRASENA, email))
        if invitacion is not None:
            # De un solo uso: quien la usó queda anotado.
            con.execute(
                "UPDATE invitaciones SET aceptada_en=?, aceptada_email=? WHERE token=?",
                (datos.ahora_iso(), email, invitacion["token"]))
    return {"id": email, "nombre": nombre, "email": email, "email_verificado": 1}


def fijar_email(usuario, email):
    """El email que la empleada anota en Mi cuenta (sin verificar, hasta que
    entre con Google). Vacío = quitarlo. 'ocupado' si es de otra cuenta."""
    email = (email or "").strip().lower() or None
    with datos._db() as con:
        if email:
            otra = con.execute(
                "SELECT 1 FROM empleadas WHERE lower(email)=? AND usuario<>?",
                (email, usuario)).fetchone()
            if otra:
                return "ocupado"
        actual = con.execute(
            "SELECT email, email_verificado FROM empleadas WHERE usuario=?",
            (usuario,)).fetchone()
        # Re-guardar el mismo email ya verificado no le quita lo verificado.
        verificado = 1 if (actual and email and (actual["email"] or "").lower() == email
                           and actual["email_verificado"]) else 0
        con.execute(
            "UPDATE empleadas SET email=?, email_verificado=? WHERE usuario=?",
            (email, verificado, usuario))
    return "guardado"


def invitar(email, nombre, invitada_por):
    """Crea la invitación y devuelve (estado, token del link compartible).

    email opcional (el link basta). 'ya_activa' si ese email ya es una
    empleada activa y no hace falta invitarlo; si ya tenía una invitación
    pendiente se refresca esa misma (no se acumulan links viejos).
    """
    email = (email or "").strip().lower() or None
    with datos._db() as con:
        if email:
            activa = con.execute(
                "SELECT 1 FROM empleadas WHERE (lower(email)=? OR usuario=?) AND activa=1",
                (email, email)).fetchone()
            if activa:
                return "ya_activa", None
            previa = con.execute(
                "SELECT token FROM invitaciones WHERE email=? AND aceptada_en IS NULL",
                (email,)).fetchone()
            if previa:
                con.execute(
                    "UPDATE invitaciones SET nombre=?, invitada_por=?, creada_en=? "
                    "WHERE token=?",
                    ((nombre or "").strip(), invitada_por, datos.ahora_iso(),
                     previa["token"]))
                return "invitada", previa["token"]
        token = secrets.token_urlsafe(24)
        con.execute(
            "INSERT INTO invitaciones (token, email, nombre, invitada_por, creada_en) "
            "VALUES (?,?,?,?,?)",
            (token, email, (nombre or "").strip(), invitada_por, datos.ahora_iso()))
    return "invitada", token


def invitacion_pendiente(token):
    """La invitación viva de un token del link, o None (usada o cancelada)."""
    if not token:
        return None
    with datos._db() as con:
        fila = con.execute(
            "SELECT token, email, nombre FROM invitaciones "
            "WHERE token=? AND aceptada_en IS NULL", (token,)).fetchone()
    return dict(fila) if fila else None


def invitaciones_pendientes():
    with datos._db() as con:
        return [dict(f) for f in con.execute(
            "SELECT token, email, nombre, invitada_por, creada_en FROM invitaciones "
            "WHERE aceptada_en IS NULL ORDER BY creada_en DESC")]


def cancelar_invitacion(token):
    with datos._db() as con:
        con.execute("DELETE FROM invitaciones WHERE token=? AND aceptada_en IS NULL",
                    (token,))


def crear_sesion(usuario):
    token = secrets.token_urlsafe(32)
    ahora = datetime.now(datos.ZONA_PANAMA)
    with datos._db() as con:
        con.execute(
            "INSERT INTO sesiones (token, usuario, creada_en, expira_en) VALUES (?,?,?,?)",
            (token, usuario, ahora.isoformat(),
             (ahora + timedelta(days=DIAS_SESION)).isoformat()),
        )
    return token


def empleada_de_sesion(token):
    """La empleada de una sesión vigente, renovando su expiración al usarla."""
    if not token:
        return None
    ahora = datetime.now(datos.ZONA_PANAMA)
    with datos._db() as con:
        fila = con.execute(
            """SELECT s.expira_en, e.usuario, e.nombre, e.email, e.email_verificado
               FROM sesiones s JOIN empleadas e ON e.usuario = s.usuario AND e.activa = 1
               WHERE s.token = ?""",
            (token,),
        ).fetchone()
        if fila is None or fila["expira_en"] < ahora.isoformat():
            return None
        con.execute(
            "UPDATE sesiones SET expira_en=? WHERE token=?",
            ((ahora + timedelta(days=DIAS_SESION)).isoformat(), token),
        )
    return {"id": fila["usuario"], "nombre": fila["nombre"],
            "email": fila["email"], "email_verificado": fila["email_verificado"]}


def cerrar_sesion(token):
    if token:
        with datos._db() as con:
            con.execute("DELETE FROM sesiones WHERE token=?", (token,))
