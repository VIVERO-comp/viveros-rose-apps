"""URLs de Cloudinary a partir de la copia local de fotos.json."""

import json

import pytest

from app import fotos


@pytest.fixture(autouse=True)
def cache_limpia():
    fotos.reiniciar_cache_fotos()
    yield
    fotos.reiniciar_cache_fotos()


@pytest.fixture
def mapa(tmp_path, monkeypatch):
    ruta = tmp_path / "fotos.json"
    ruta.write_text(json.dumps({
        "cloud": "demo123",
        "porSku": {
            "PL-ROMERO": ["abc111", "def222"],
            "PL-RECORTADA": ["fff333"],
        },
        "recortesPorHash": {"fff333": [0.1, 0.2, 0.9, 0.8]},
    }))
    monkeypatch.setattr(fotos, "RUTA_FOTOS", ruta)


def test_url_foto_usa_el_primer_hash(mapa):
    assert fotos.url_foto("PL-ROMERO") == (
        "https://res.cloudinary.com/demo123/image/upload/"
        "f_auto,q_auto,c_fill,g_auto,ar_1:1,w_160/productos/PL-ROMERO/abc111")


def test_url_foto_antepone_el_recorte_manual(mapa):
    # El recorte decidido en la página de revisión del catálogo va como
    # c_crop antes del resto, igual que en fotos.ts del frontend.
    assert fotos.url_foto("PL-RECORTADA") == (
        "https://res.cloudinary.com/demo123/image/upload/"
        "c_crop,x_0.1,y_0.2,w_0.9,h_0.8/"
        "f_auto,q_auto,c_fill,g_auto,ar_1:1,w_160/productos/PL-RECORTADA/fff333")


def test_url_foto_sin_foto_es_none(mapa):
    assert fotos.url_foto("PL-SIN-FOTO") is None


def test_url_foto_sin_archivo_es_none(tmp_path, monkeypatch):
    # Sin la copia del json (deploy sin el paso de fotos) nada revienta:
    # todas las tarjetas caen al emoji.
    monkeypatch.setattr(fotos, "RUTA_FOTOS", tmp_path / "no-existe.json")
    assert fotos.url_foto("PL-ROMERO") is None


def test_la_copia_del_repo_es_valida():
    # La copia versionada en app/datos_fotos debe poder cargarse siempre.
    datos = json.loads(fotos.RUTA_FOTOS.read_text())
    assert datos["cloud"]
    assert isinstance(datos["porSku"], dict) and datos["porSku"]
