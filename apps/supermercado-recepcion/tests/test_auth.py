"""La app exige sesión: login, logout y quién firma cada confirmación."""

from fastapi.testclient import TestClient

from app import datos, seguridad
from app.main import app


def test_sin_sesion_todo_redirige_al_login(db_limpia):
    anonimo = TestClient(app)
    for ruta in ("/", "/devoluciones", "/orden/S00774", "/intercambios/nuevo", "/historial"):
        r = anonimo.get(ruta, follow_redirects=False)
        assert (r.status_code, r.headers["location"]) == (303, "/login"), ruta
    # Los estáticos y el login sí abren sin sesión (el manifest vive ahí).
    assert anonimo.get("/static/manifest.webmanifest").status_code == 200
    assert anonimo.get("/login").status_code == 200


def test_login_incorrecto_muestra_error_sin_cookie(db_limpia):
    seguridad.crear_empleada("genesis", "Génesis", "clave-de-prueba")
    anonimo = TestClient(app)
    r = anonimo.post("/login", data={"usuario": "genesis", "contrasena": "mala"})
    assert r.status_code == 401
    assert "Usuario o contraseña incorrectos." in r.text
    assert "sesion" not in anonimo.cookies
    # Usuario inexistente: mismo mensaje, sin revelar quién existe.
    r = anonimo.post("/login", data={"usuario": "nadie", "contrasena": "x"})
    assert r.status_code == 401 and "incorrectos" in r.text


def test_login_correcto_y_saludo_real(cliente):
    r = cliente.get("/")
    assert "Hola, Génesis" in r.text and "Salir" in r.text


def test_logout_cierra_la_sesion(cliente):
    cliente.post("/logout")
    assert cliente.get("/", follow_redirects=False).status_code == 303


def test_desactivar_revoca_al_instante(cliente):
    seguridad.desactivar("genesis")
    assert cliente.get("/", follow_redirects=False).status_code == 303


def test_confirmacion_firmada_por_la_empleada_real(cliente, con_ordenes):
    import json

    cliente.post("/orden/S00770/confirmar", data={})
    with datos._db() as con:
        fila = con.execute("SELECT payload FROM pendientes_odoo WHERE pedido='S00770'").fetchone()
    assert json.loads(fila["payload"])["empleadoId"] == "genesis"


def test_contrasena_hasheada_nunca_en_claro(db_limpia):
    seguridad.crear_empleada("otra", "Otra", "secreta-123")
    with datos._db() as con:
        fila = con.execute("SELECT hash FROM empleadas WHERE usuario='otra'").fetchone()
    assert "secreta-123" not in fila["hash"]
    assert fila["hash"].startswith("pbkdf2_sha256$600000$")
