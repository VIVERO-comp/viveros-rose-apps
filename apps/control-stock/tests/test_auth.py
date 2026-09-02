from fastapi.testclient import TestClient

from app import seguridad
from app.main import app


def test_sin_sesion_todo_redirige_al_login(db_limpia):
    c = TestClient(app)
    for ruta in ["/", "/plantilla.xlsx", "/conteos/1/revisar"]:
        r = c.get(ruta, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/login"
    r = c.post("/ajustar", json={}, follow_redirects=False)
    assert r.status_code == 303


def test_login_malo_no_entra(db_limpia):
    seguridad.crear_empleada("genesis", "Génesis", "clave-de-prueba")
    c = TestClient(app)
    r = c.post("/login", data={"usuario": "genesis", "contrasena": "otra-clave"})
    assert r.status_code == 401
    # El mensaje no distingue usuario inexistente de contraseña mala.
    r2 = c.post("/login", data={"usuario": "nadie", "contrasena": "clave-de-prueba"})
    assert r2.status_code == 401
    assert "incorrectos" in r.text and "incorrectos" in r2.text


def test_login_y_logout(cliente, con_inventario):
    assert cliente.get("/").status_code == 200
    cliente.post("/logout", follow_redirects=False)
    r = cliente.get("/", follow_redirects=False)
    assert r.status_code == 303


def test_hash_no_guarda_la_clave(db_limpia):
    seguridad.crear_empleada("genesis", "Génesis", "clave-de-prueba")
    from app import datos
    with datos._db() as con:
        hash_ = con.execute("SELECT hash FROM empleadas").fetchone()["hash"]
    assert hash_.startswith("pbkdf2_sha256$600000$")
    assert "clave-de-prueba" not in hash_
