import os
import tempfile

# Antes de importar la app: que ninguna prueba toque la base real.
os.environ["RECEPCION_DB"] = os.path.join(tempfile.mkdtemp(), "pruebas.db")

import pytest

from app import datos


@pytest.fixture
def db_limpia(tmp_path, monkeypatch):
    """Cada caso corre contra una base SQLite recién creada."""
    monkeypatch.setenv("RECEPCION_DB", str(tmp_path / "caso.db"))
    datos.iniciar_db()
