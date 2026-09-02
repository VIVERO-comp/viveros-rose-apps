"""Autenticación propia de la app: empleadas con contraseña y sesiones.

Sin OAuth y sin depender de otros servicios: las credenciales viven en el
mismo SQLite de la app. Contraseñas con PBKDF2-SHA256 (stdlib, 600k
iteraciones, sal por empleada); sesiones con token aleatorio en cookie
HttpOnly, con expiración deslizante. Revocar acceso = desactivar la
empleada (borra también sus sesiones) o cerrar sesión.

Las altas y resets se hacen por consola en el servidor:
    python -m app.usuarias crear <usuario> "<Nombre>"
"""

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta

from . import datos

ITERACIONES = 600_000
DIAS_SESION = 30


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
            "SELECT usuario, nombre, activa FROM empleadas ORDER BY usuario")]


def verificar(usuario, contrasena):
    """La empleada activa si usuario y contraseña coinciden; si no, None."""
    with datos._db() as con:
        fila = con.execute(
            "SELECT usuario, nombre, hash FROM empleadas WHERE usuario=? AND activa=1",
            (usuario,),
        ).fetchone()
    if fila is None:
        # Mismo costo aunque el usuario no exista: no se filtra quién existe.
        _derivar(contrasena, "00" * 16)
        return None
    _, iteraciones, sal, esperado = fila["hash"].split("$")
    calculado = _derivar(contrasena, sal, int(iteraciones))
    if hmac.compare_digest(calculado, esperado):
        return {"id": fila["usuario"], "nombre": fila["nombre"]}
    return None


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
            """SELECT s.expira_en, e.usuario, e.nombre
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
    return {"id": fila["usuario"], "nombre": fila["nombre"]}


def cerrar_sesion(token):
    if token:
        with datos._db() as con:
            con.execute("DELETE FROM sesiones WHERE token=?", (token,))
