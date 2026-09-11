"""La pestaña Fichas: quién edita, qué se valida y qué se guarda.

Las pruebas corren sin TIENDA_DSN, así que ejercitan el respaldo SQLite;
el SQL del upsert es el mismo que va a Postgres (solo cambia el marcador).
"""

import json

import pytest

from app import fichas

FICHA_BUENA = {
    "descripcion": "Romero (Salvia rosmarinus), aromática mediterránea de sol pleno.",
    "luz": "Sol pleno",
    "riego": "Cada 4–5 días",
    "dificultad": "Facil",
    "nota": "Mejor en maceta de barro con buen drenaje.",
}


@pytest.fixture
def editora(monkeypatch):
    """Génesis (la del cliente autenticado del conftest) puede editar."""
    monkeypatch.setenv("FICHAS_EDITORES", "genesis")


@pytest.fixture
def catalogo(tmp_path, monkeypatch):
    ruta = tmp_path / "catalogo.json"
    ruta.write_text(json.dumps({
        "PL-ROMERO": {"descripcion": "Romero del sitio.", "luz": "Sol pleno",
                      "riego": "Cada 4–5 días", "dificultad": "Facil"},
    }), encoding="utf-8")
    monkeypatch.setenv("FICHAS_CATALOGO", str(ruta))


def test_sin_variable_nadie_edita(monkeypatch):
    monkeypatch.delenv("FICHAS_EDITORES", raising=False)
    assert not fichas.es_editora("genesis")


def test_editoras_por_coma(monkeypatch):
    monkeypatch.setenv("FICHAS_EDITORES", "abraham, Genesis")
    assert fichas.es_editora("abraham")
    assert fichas.es_editora("genesis")  # normalizado a minúsculas
    assert not fichas.es_editora("ruben")


def test_asterisco_abre_a_todos(monkeypatch):
    monkeypatch.setenv("FICHAS_EDITORES", "*")
    assert fichas.es_editora("genesis")
    assert fichas.es_editora("ruben")
    assert fichas.es_editora("stockmaster")


def test_guardar_y_releer(db_limpia, editora):
    fichas.guardar("PL-ROMERO", dict(FICHA_BUENA), "genesis")
    todas = fichas.todas()
    assert todas["PL-ROMERO"]["descripcion"] == FICHA_BUENA["descripcion"]
    assert todas["PL-ROMERO"]["actualizado_por"] == "genesis"


def test_guardar_dos_veces_es_upsert(db_limpia, editora):
    fichas.guardar("PL-ROMERO", dict(FICHA_BUENA), "genesis")
    corregida = dict(FICHA_BUENA, descripcion="Romero, versión corregida.")
    fichas.guardar("PL-ROMERO", corregida, "genesis")
    todas = fichas.todas()
    assert len(todas) == 1
    assert todas["PL-ROMERO"]["descripcion"] == "Romero, versión corregida."


def test_validaciones():
    assert fichas.validar(dict(FICHA_BUENA)) is None
    assert "vacía" in fichas.validar(dict(FICHA_BUENA, descripcion="  "))
    assert "dificultad" in fichas.validar(dict(FICHA_BUENA, dificultad="Imposible")).lower()
    assert "caracteres" in fichas.validar(dict(FICHA_BUENA, nota="x" * 601))


def test_ruta_guarda_para_editora(cliente, editora):
    respuesta = cliente.post("/fichas/PL-ROMERO", json=FICHA_BUENA)
    assert respuesta.status_code == 200
    assert respuesta.json()["resultado"] == "guardada"
    assert fichas.todas()["PL-ROMERO"]["luz"] == "Sol pleno"


def test_ruta_rechaza_a_quien_no_edita(cliente, monkeypatch):
    monkeypatch.setenv("FICHAS_EDITORES", "abraham")  # genesis no está
    respuesta = cliente.post("/fichas/PL-ROMERO", json=FICHA_BUENA)
    assert respuesta.status_code == 403
    assert fichas.todas() == {}


def test_ruta_rechaza_ficha_invalida(cliente, editora):
    respuesta = cliente.post("/fichas/PL-ROMERO", json=dict(FICHA_BUENA, descripcion=""))
    assert respuesta.status_code == 400
    assert fichas.todas() == {}


def test_referencias_del_catalogo(catalogo):
    assert fichas.referencias()["PL-ROMERO"]["descripcion"] == "Romero del sitio."


def test_sin_catalogo_no_rompe(monkeypatch, tmp_path):
    monkeypatch.setenv("FICHAS_CATALOGO", str(tmp_path / "no-existe.json"))
    assert fichas.referencias() == {}


def test_pantalla_manda_fichas_solo_a_editoras(cliente, editora, catalogo):
    pagina = cliente.get("/").text
    assert "tab-fichas" in pagina
    assert "Romero del sitio." in pagina


def test_pantalla_sin_fichas_para_el_resto(cliente, monkeypatch):
    monkeypatch.delenv("FICHAS_EDITORES", raising=False)
    pagina = cliente.get("/").text
    assert "tab-fichas" not in pagina
    assert "/fichas/" not in pagina