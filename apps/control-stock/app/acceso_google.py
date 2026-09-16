"""Login con Google (OpenID Connect), sin dependencias nuevas.

Flujo estándar de código de autorización: /auth/google manda a la pantalla
de Google y el callback canjea el código por un id_token directamente
contra el endpoint de tokens de Google (httpx, HTTPS). Como el id_token
llega directo de Google por TLS —no del navegador—, basta validar `aud`
y `email_verified` sin verificar la firma JWT (es la práctica documentada
por Google para este flujo servidor-a-servidor).

Sin GOOGLE_CLIENT_ID/GOOGLE_CLIENT_SECRET el botón de Google no aparece y
la app queda solo con el login de contraseña de respaldo.
"""

import base64
import json
import os
from urllib.parse import urlencode

import httpx

URL_AUTORIZACION = "https://accounts.google.com/o/oauth2/v2/auth"
URL_TOKENS = "https://oauth2.googleapis.com/token"


class FalloGoogle(Exception):
    """Google no respondió o la respuesta no sirve para autenticar."""


def configurado():
    return bool(os.environ.get("GOOGLE_CLIENT_ID")
                and os.environ.get("GOOGLE_CLIENT_SECRET"))


def url_entrada(redirect_uri, estado):
    """La URL de la pantalla de cuentas de Google."""
    return URL_AUTORIZACION + "?" + urlencode({
        "client_id": os.environ["GOOGLE_CLIENT_ID"],
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "openid email profile",
        "state": estado,
        # Siempre elegir cuenta: en el vivero se comparten dispositivos.
        "prompt": "select_account",
    })


def _reclamos(id_token):
    cuerpo = id_token.split(".")[1]
    cuerpo += "=" * (-len(cuerpo) % 4)
    return json.loads(base64.urlsafe_b64decode(cuerpo))


def canjear_codigo(codigo, redirect_uri):
    """{email, nombre} de la cuenta que autorizó, o FalloGoogle."""
    try:
        respuesta = httpx.post(URL_TOKENS, data={
            "code": codigo,
            "client_id": os.environ["GOOGLE_CLIENT_ID"],
            "client_secret": os.environ["GOOGLE_CLIENT_SECRET"],
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        }, timeout=10)
    except Exception:
        raise FalloGoogle("Google no respondió")
    if respuesta.status_code != 200:
        raise FalloGoogle(f"Google respondió {respuesta.status_code}")
    id_token = respuesta.json().get("id_token")
    if not id_token:
        raise FalloGoogle("La respuesta de Google no trae id_token")
    try:
        reclamos = _reclamos(id_token)
    except Exception:
        raise FalloGoogle("id_token ilegible")
    if reclamos.get("aud") != os.environ["GOOGLE_CLIENT_ID"]:
        raise FalloGoogle("El id_token no es para esta app")
    if not reclamos.get("email") or not reclamos.get("email_verified"):
        raise FalloGoogle("La cuenta no tiene email verificado")
    return {"email": reclamos["email"].strip().lower(),
            "nombre": (reclamos.get("name") or "").strip()}
