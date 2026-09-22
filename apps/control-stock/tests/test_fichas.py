"""La pestaña Fichas: quién edita, qué se valida y qué se guarda.

Las pruebas corren sin TIENDA_DSN, así que ejercitan el respaldo SQLite;
el SQL del upsert es el mismo que va a Postgres (solo cambia el marcador).
"""

import json

import pytest

from app import datos, fichas

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


def test_pantalla_editoras_ven_el_formulario_de_ficha(cliente, editora, catalogo):
    # La pestaña Fichas ya no existe: la ficha se edita en la vista de
    # detalle del producto (tocar una tarjeta del Stock en computadora).
    pagina = cliente.get("/?tab=stock").text
    assert "tab-fichas" not in pagina
    assert "tab-detalle" in pagina
    assert "ficha-descripcion" in pagina  # el formulario editable
    assert "Romero del sitio." in pagina  # la referencia precargada


def test_pantalla_sin_permiso_ve_la_ficha_solo_lectura(cliente, monkeypatch, catalogo):
    monkeypatch.delenv("FICHAS_EDITORES", raising=False)
    pagina = cliente.get("/?tab=stock").text
    assert "tab-detalle" in pagina
    assert "ficha-descripcion" not in pagina  # sin formulario de edición
    # Los textos sí viajan: el detalle los muestra en solo lectura.
    assert "Romero del sitio." in pagina


# ---------------------------------------------------------------------------
# Altura de la planta: se edita en la ficha pero se guarda en Odoo
# ---------------------------------------------------------------------------

@pytest.fixture
def odoo_falso(monkeypatch):
    """Captura lo que la ficha manda al order-api, sin salir a la red."""
    llamadas = []

    def fijar(sku, altura_min, altura_max):
        llamadas.append((sku, altura_min, altura_max))
        return {"ok": True, "sku": sku, "altura_min": altura_min,
                "altura_max": altura_max, "resultado": "aplicado"}

    monkeypatch.setattr(datos, "fijar_altura_en_odoo", fijar)
    return llamadas


def test_limpiar_altura_acepta_texto_y_vacio():
    assert fichas.limpiar_altura({"altura_min": "70", "altura_max": "110"}) == {
        "altura_min": 70, "altura_max": 110}
    # Campos vacíos o basura = sin altura (0), que es como se borra.
    assert fichas.limpiar_altura({"altura_min": "", "altura_max": None}) == {
        "altura_min": 0, "altura_max": 0}
    assert fichas.limpiar_altura({"altura_min": "ochenta"}) == {
        "altura_min": 0, "altura_max": 0}


def test_validar_altura():
    assert fichas.validar_altura({"altura_min": 70, "altura_max": 110}) is None
    # Una sola medida es válida.
    assert fichas.validar_altura({"altura_min": 70, "altura_max": 0}) is None
    assert fichas.validar_altura({"altura_min": 0, "altura_max": 0}) is None
    assert "mínima" in fichas.validar_altura({"altura_min": 0, "altura_max": 90})
    assert "menor" in fichas.validar_altura({"altura_min": 90, "altura_max": 20})
    assert "1000" in fichas.validar_altura({"altura_min": 1200, "altura_max": 0})


def test_guardar_ficha_manda_la_altura_a_odoo(cliente, editora, odoo_falso):
    respuesta = cliente.post("/fichas/PL-ROMERO",
                             json=dict(FICHA_BUENA, altura_min="30", altura_max="45"))
    assert respuesta.status_code == 200
    assert respuesta.json()["altura_min"] == 30
    assert odoo_falso == [("PL-ROMERO", 30, 45)]


def test_altura_invalida_no_guarda_nada(cliente, editora, odoo_falso):
    respuesta = cliente.post("/fichas/PL-ROMERO",
                             json=dict(FICHA_BUENA, altura_min="90", altura_max="20"))
    assert respuesta.status_code == 400
    assert odoo_falso == []      # no se tocó Odoo
    assert fichas.todas() == {}  # ni la prosa


def test_si_odoo_falla_no_se_guarda_la_prosa(cliente, editora, monkeypatch):
    def fallar(sku, altura_min, altura_max):
        raise datos.SinConexion("No hay conexión con el servidor de pedidos")

    monkeypatch.setattr(datos, "fijar_altura_en_odoo", fallar)
    respuesta = cliente.post("/fichas/PL-ROMERO",
                             json=dict(FICHA_BUENA, altura_min="30", altura_max="45"))
    assert respuesta.status_code == 502
    assert fichas.todas() == {}


def test_pantalla_editoras_ven_las_casillas_de_altura(cliente, editora, catalogo):
    pagina = cliente.get("/?tab=stock").text
    assert "ficha-altura-min" in pagina
    assert "ficha-altura-max" in pagina
