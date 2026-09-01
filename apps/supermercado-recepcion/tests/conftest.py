import os
import tempfile

# Antes de importar la app: que ninguna prueba toque la base real.
os.environ["RECEPCION_DB"] = os.path.join(tempfile.mkdtemp(), "pruebas.db")
# Y sin el hilo reintentador de la cola: las pruebas sincronizan a mano.
os.environ["RECEPCION_SIN_HILO"] = "1"

import pytest

from app import datos


@pytest.fixture
def db_limpia(tmp_path, monkeypatch):
    """Cada caso corre contra una base SQLite recién creada."""
    monkeypatch.setenv("RECEPCION_DB", str(tmp_path / "caso.db"))
    datos.iniciar_db()


@pytest.fixture
def con_ordenes(monkeypatch, db_limpia):
    """Las tres órdenes falsas de siempre, como si vinieran del stock-proxy."""
    from ordenes_de_prueba import ordenes_falsas

    monkeypatch.setattr(datos, "obtener_ordenes", ordenes_falsas)


@pytest.fixture
def cliente(db_limpia):
    """Un TestClient ya autenticado como la empleada Génesis."""
    from fastapi.testclient import TestClient

    from app import seguridad
    from app.main import app

    seguridad.crear_empleada("genesis", "Génesis", "clave-de-prueba")
    c = TestClient(app)
    respuesta = c.post("/login", data={"usuario": "genesis", "contrasena": "clave-de-prueba"},
                       follow_redirects=False)
    assert respuesta.status_code == 303
    return c
