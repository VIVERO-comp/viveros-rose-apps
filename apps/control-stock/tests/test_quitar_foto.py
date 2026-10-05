"""«Quitar foto» del modal de Stock (5/10/2026).

El puntero de la foto propia (fotos_subidas) se MUDA a la papelera
fotos_quitadas —nunca se destruye, y de Cloudinary no se borra nada— y el
producto vuelve a la foto de debajo (catálogo/Odoo) o al emoji. La tienda
online no cambia: acá no se toca ni el sitio ni Odoo ni los json del
catálogo.
"""

import json

import pytest

from app import datos, fotos


@pytest.fixture(autouse=True)
def fotos_sin_json(tmp_path, monkeypatch):
    """Ninguna prueba lee los fotos.json reales empaquetados en el repo:
    por defecto no hay foto de catálogo debajo (cada caso pone la suya)."""
    fotos.reiniciar_cache_fotos()
    monkeypatch.setattr(fotos, "RUTA_FOTOS", tmp_path / "sin-fotos.json")
    monkeypatch.setattr(fotos, "RUTA_FOTOS_APPS", tmp_path / "sin-apps.json")
    yield
    fotos.reiniciar_cache_fotos()


def _papelera():
    with datos._db() as con:
        return [dict(f) for f in con.execute(
            "SELECT * FROM fotos_quitadas ORDER BY n")]


def test_quitar_muda_el_puntero_a_la_papelera(cliente):
    datos.fijar_foto_subida("PL-ROMERO", "cafe00000000", "genesis")
    r = cliente.post("/fotos/PL-ROMERO/quitar")
    assert r.status_code == 200
    assert r.json()["resultado"] == "quitada"
    # El puntero ya no está: el producto queda sin foto interna propia…
    assert "PL-ROMERO" not in datos.fotos_subidas()
    # …pero nada se destruyó: la fila entera vive en la papelera, con
    # quién la subió y quién la quitó. Revertir a mano es re-insertarla.
    filas = _papelera()
    assert len(filas) == 1
    assert filas[0]["sku"] == "PL-ROMERO"
    assert filas[0]["hash"] == "cafe00000000"
    assert filas[0]["subida_por"] == "genesis"
    assert filas[0]["quitada_por"] == "genesis"
    assert filas[0]["quitada_en"]


def test_sin_foto_propia_no_hay_nada_que_quitar(cliente):
    # El botón no sale sin foto propia; un POST a mano (o una carrera de
    # dos pestañas) recibe 404 y la papelera no gana filas.
    r = cliente.post("/fotos/PL-ROMERO/quitar")
    assert r.status_code == 404
    assert r.json()["error"] == "sin_foto_propia"
    assert _papelera() == []


def test_sku_invalido_se_rechaza(cliente):
    r = cliente.post("/fotos/PL.RARO/quitar")
    assert r.status_code == 400
    assert r.json()["error"] == "sku_invalido"


def test_sin_sesion_no_quita_nada(db_limpia, monkeypatch):
    # Mismo candado que «Cambiar foto»: sin sesión el middleware manda al
    # login y el puntero queda intacto.
    from fastapi.testclient import TestClient

    from app.main import app

    monkeypatch.delenv("SIN_LOGIN", raising=False)
    datos.fijar_foto_subida("PL-ROMERO", "cafe00000000", "genesis")
    c = TestClient(app)
    r = c.post("/fotos/PL-ROMERO/quitar", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/login"
    assert datos.fotos_subidas() == {"PL-ROMERO": "cafe00000000"}
    assert _papelera() == []


def test_la_pantalla_marca_quien_tiene_foto_propia(cliente, con_inventario):
    # fp es el dato con el que el modal decide si «Quitar foto» sale: true
    # solo para el SKU con puntero en fotos_subidas (lo decide el servidor,
    # el JS solo lo lee).
    datos.fijar_foto_subida("PL-ROMERO", "cafe00000000", "genesis")
    r = cliente.get("/?tab=stock")
    assert r.status_code == 200
    assert r.text.count('"fp": true') == 1
    assert r.text.count('"fp": false') == 3  # el resto del inventario falso


def test_al_quitar_se_destapa_la_foto_del_catalogo(cliente, tmp_path, monkeypatch):
    # Con foto del catálogo debajo, quitar la propia la destapa: la
    # respuesta es JSON directo (sin redirect, el modal repinta en el
    # sitio y la lista no pierde pestaña/categoría/búsqueda).
    ruta = tmp_path / "fotos.json"
    ruta.write_text(json.dumps({"cloud": "demo123",
                                "porSku": {"PL-ROMERO": ["abc111"]}}))
    monkeypatch.setattr(fotos, "RUTA_FOTOS", ruta)
    fotos.reiniciar_cache_fotos()
    datos.fijar_foto_subida("PL-ROMERO", "cafe00000000", "genesis")
    r = cliente.post("/fotos/PL-ROMERO/quitar", follow_redirects=False)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/json")
    assert "productos/PL-ROMERO/abc111" in r.json()["img"]


def test_sin_nada_debajo_vuelve_el_respaldo_de_odoo(cliente, monkeypatch):
    from app import ventas

    monkeypatch.setattr(ventas, "configurado", lambda: True)
    datos.fijar_foto_subida("PL-ROMERO", "cafe00000000", "genesis")
    r = cliente.post("/fotos/PL-ROMERO/quitar")
    assert r.json()["img"] == "/stock/foto/PL-ROMERO"
    # Y sin Odoo configurado los campos van en null: la pantalla cae al
    # emoji de siempre, nunca se inventa una URL.
    monkeypatch.setattr(ventas, "configurado", lambda: False)
    datos.fijar_foto_subida("PL-ROMERO", "cafe00000000", "genesis")
    r = cliente.post("/fotos/PL-ROMERO/quitar")
    assert r.json()["img"] is None


def test_cambiar_foto_despues_de_quitar_vuelve_a_funcionar(cliente, monkeypatch):
    datos.fijar_foto_subida("PL-ROMERO", "cafe00000000", "genesis")
    assert cliente.post("/fotos/PL-ROMERO/quitar").status_code == 200
    # El pincel de nuevo, con Cloudinary doblado: el puntero vuelve.
    monkeypatch.setattr(fotos, "subida_configurada", lambda: True)
    monkeypatch.setattr(fotos, "subir_foto", lambda contenido, sku: "beef11111111")
    monkeypatch.setenv("CLOUDINARY_CLOUD_NAME", "demo123")
    r = cliente.post("/fotos/PL-ROMERO",
                     files={"archivo": ("x.jpg", b"img-nueva", "image/jpeg")})
    assert r.status_code == 200
    assert r.json()["resultado"] == "aplicada"
    assert datos.fotos_subidas() == {"PL-ROMERO": "beef11111111"}
    # Y quitar otra vez apila una SEGUNDA fila: la papelera es historial,
    # no un cajón de una sola foto.
    assert cliente.post("/fotos/PL-ROMERO/quitar").status_code == 200
    assert [f["hash"] for f in _papelera()] == ["cafe00000000", "beef11111111"]
