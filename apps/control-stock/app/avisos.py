"""Avisos Web Push a los celulares del equipo.

Pedido de Abraham (23/09/2026): cuando un chat cae en **Esperando
respuesta** — lo mueva un compañero a mano o lo empuje un mensaje nuevo
del cliente — a Rubén le tiene que salir la notificación en el celular,
aunque no tenga la app abierta.

Es el mismo estándar gratis que ya usa el order-api con los repartidores
(Web Push + claves VAPID + pywebpush), pero con suscripción propia: el
navegador amarra la suscripción al ORIGEN, así que la de
inventario.plantaspanama.com no puede salir de la del sitio. Cada celular
que activa los avisos deja una fila en SQLite; el envío va en un hilo
aparte para que ninguna pantalla espere al servicio de push.

Toda la decisión vive aquí en Python: a quién le toca el aviso
(AVISOS_CHATS_USUARIO), qué dice y a dónde lleva. El
JavaScript del navegador solo hace lo único que el navegador no deja
hacer desde el servidor: pedir el permiso y entregar la suscripción.

Sin claves VAPID configuradas la app arranca igual y el aviso queda en el
log: como el correo, es opcional.
"""

import json
import logging
import os

from . import calendario
from .datos import _db

registro = logging.getLogger("control_stock")

# El encargado de contestar los chats: a él le llegan los avisos de
# "Esperando respuesta" (decisión de Abraham, 23/09/2026: "a Rubén
# siempre"). Si algún día cambia la persona, cambia esta variable de
# entorno en el droplet — no hace falta tocar el código. El valor es el
# USUARIO con el que entra a la app: la cuenta activa de Rubén es la de
# Google, ruben@viverorose.com (el usuario viejo `ruben` está inactivo).
USUARIO_CHATS = (os.environ.get("AVISOS_CHATS_USUARIO")
                 or "ruben@viverorose.com").strip().lower()


def iniciar_tablas():
    with _db() as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS suscripcion_push (
                endpoint TEXT PRIMARY KEY,
                usuario TEXT NOT NULL,
                p256dh TEXT NOT NULL,
                auth TEXT NOT NULL,
                creado TEXT NOT NULL
            )
        """)


def clave_publica():
    """La clave VAPID pública: es la que el navegador necesita para
    suscribirse, y por eso sí se le puede dar a la página."""
    return (os.environ.get("VAPID_CLAVE_PUBLICA") or "").strip()


def _clave_privada():
    return (os.environ.get("VAPID_CLAVE_PRIVADA") or "").strip()


def configurado():
    return bool(clave_publica() and _clave_privada())


def guardar(usuario, suscripcion):
    """Guarda (o refresca) el celular de una empleada. El endpoint es la
    llave: si el navegador se vuelve a suscribir, la fila se actualiza en
    vez de duplicarse. Vuelve True si la suscripción venía completa."""
    claves = (suscripcion or {}).get("keys") or {}
    endpoint = (suscripcion or {}).get("endpoint") or ""
    if not endpoint or not claves.get("p256dh") or not claves.get("auth"):
        return False
    iniciar_tablas()
    with _db() as con:
        con.execute("""
            INSERT INTO suscripcion_push (endpoint, usuario, p256dh, auth, creado)
            VALUES (?, ?, ?, ?, datetime('now'))
            ON CONFLICT(endpoint) DO UPDATE SET usuario = excluded.usuario,
                p256dh = excluded.p256dh, auth = excluded.auth
        """, (endpoint, (usuario or "").lower(), claves["p256dh"], claves["auth"]))
    return True


def borrar(endpoint):
    iniciar_tablas()
    with _db() as con:
        con.execute("DELETE FROM suscripcion_push WHERE endpoint = ?", (endpoint,))


def suscripciones(usuario):
    iniciar_tablas()
    with _db() as con:
        filas = con.execute(
            "SELECT endpoint, p256dh, auth FROM suscripcion_push WHERE usuario = ?",
            ((usuario or "").lower(),)).fetchall()
    return [dict(f) for f in filas]


def cuantos(usuario):
    """Cuántos celulares tiene activados esa empleada (para Ajustes)."""
    return len(suscripciones(usuario))


def avisar(usuario, titulo, cuerpo, url="/"):
    """Manda el aviso a todos los celulares de esa empleada, en un hilo
    aparte: la pantalla que lo dispara no espera al servicio de push. Una
    suscripción muerta (el celular la dio de baja) se borra sola."""
    if not configurado():
        registro.info("Aviso sin claves VAPID (queda en el log): %s · %s",
                      titulo, cuerpo)
        return
    destinos = suscripciones(usuario)
    if not destinos:
        return
    carga = json.dumps({"titulo": titulo, "cuerpo": cuerpo, "url": url})
    calendario._en_fondo("push:" + (usuario or "") + ":" + titulo,
                         lambda: _enviar(destinos, carga))


def _enviar(destinos, carga):
    from pywebpush import WebPushException, webpush

    # El mismo nombre de variable que ya usa el order-api (VAPID_CORREO):
    # las claves son las mismas y así el .env no tiene dos vocabularios.
    correo = os.environ.get("VAPID_CORREO") or "info@viverorose.com"
    for destino in destinos:
        try:
            webpush(
                subscription_info={
                    "endpoint": destino["endpoint"],
                    "keys": {"p256dh": destino["p256dh"], "auth": destino["auth"]},
                },
                data=carga,
                vapid_private_key=_clave_privada(),
                vapid_claims={"sub": "mailto:" + correo},
                timeout=10,
            )
        except WebPushException as error:
            # 404/410: el navegador dio de baja ese celular — la fila ya no
            # sirve para nada y se va.
            codigo = getattr(error.response, "status_code", 0)
            if codigo in (404, 410):
                borrar(destino["endpoint"])
            else:
                registro.warning("No se pudo mandar el aviso push: %s", error)
        except Exception:
            registro.exception("Fallo inesperado mandando el aviso push")
