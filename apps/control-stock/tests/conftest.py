import os
import tempfile

# Antes de importar la app: que ninguna prueba toque la base ni los archivos
# reales.
os.environ["CONTROL_STOCK_DB"] = os.path.join(tempfile.mkdtemp(), "pruebas.db")
os.environ["CONTROL_STOCK_ARCHIVOS"] = tempfile.mkdtemp()

import pytest

from app import datos

INVENTARIO_FALSO = [
    {"sku": "PL-ROMERO", "nombre": "Romero", "categoria": "Aromáticas",
     "disponible": 2, "fisico": 2},
    {"sku": "PL-ALBAHACA", "nombre": "Albahaca", "categoria": "Aromáticas",
     "disponible": 0, "fisico": 0},
    {"sku": "PL-IXORA", "nombre": "Ixora Roja", "categoria": "Ornamentales",
     "disponible": 4, "fisico": 5},
    {"sku": "PL-PALMA", "nombre": "Palma Areca", "categoria": "Ornamentales",
     "disponible": 41, "fisico": 41},
]


@pytest.fixture
def db_limpia(tmp_path, monkeypatch):
    """Cada caso corre contra una base SQLite recién creada."""
    monkeypatch.setenv("CONTROL_STOCK_DB", str(tmp_path / "caso.db"))
    monkeypatch.setenv("CONTROL_STOCK_ARCHIVOS", str(tmp_path / "archivos"))
    datos.iniciar_db()
    datos.reiniciar_cache_proxy()


@pytest.fixture
def con_inventario(monkeypatch, db_limpia):
    """El inventario falso de siempre, como si viniera del stock-proxy."""
    productos = [dict(p) for p in INVENTARIO_FALSO]
    monkeypatch.setattr(datos, "obtener_inventario",
                        lambda refrescar=False: (productos, 1756800000.0))
    return productos


@pytest.fixture
def ajustes_registrados(monkeypatch):
    """Sustituye el cliente del order-api por uno que anota las llamadas y
    responde 'aplicado' a todo."""
    llamadas = []

    def ajustar(ajustes, empleado, motivo):
        llamadas.append({"ajustes": ajustes, "empleado": empleado, "motivo": motivo})
        return {"ok": True, "resultados": [
            {"sku": a["sku"], "cantidad": a["cantidad"], "resultado": "aplicado",
             "anterior": a["esperada"]}
            for a in ajustes
        ]}

    monkeypatch.setattr(datos, "ajustar_en_odoo", ajustar)
    return llamadas


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
