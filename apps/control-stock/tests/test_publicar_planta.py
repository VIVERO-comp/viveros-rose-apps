"""El interruptor "Publicada en la tienda" de la ficha de la planta.

Lo que se protege aquí, que es lo que pidió el dueño: despublicar una planta
la saca del sitio y NO toca nada más. Ni la archiva, ni le mueve el stock, ni
le borra o le desasocia una sola foto. Volver a marcarla la repone.
"""

import pytest

from app import datos


@pytest.fixture
def order_api_falso(monkeypatch):
    """Anota las llamadas al order-api en vez de salir a la red."""
    llamadas = []

    def fijar(sku, publicado):
        llamadas.append({"sku": sku, "publicado": publicado})
        return {"ok": True, "sku": sku, "publicado": publicado,
                "resultado": "aplicado"}

    monkeypatch.setattr(datos, "fijar_publicacion_en_odoo", fijar)
    return llamadas


def test_despublicar_manda_la_casilla_en_false(cliente, con_inventario, order_api_falso):
    r = cliente.post("/productos/PL-ROMERO/publicacion", json={"publicado": False})
    assert r.status_code == 200
    assert r.json()["publicado"] is False
    assert order_api_falso == [{"sku": "PL-ROMERO", "publicado": False}]


def test_publicar_de_nuevo_la_repone(cliente, con_inventario, order_api_falso):
    r = cliente.post("/productos/PL-ROMERO/publicacion", json={"publicado": True})
    assert r.status_code == 200
    assert order_api_falso == [{"sku": "PL-ROMERO", "publicado": True}]


def test_despublicar_no_toca_stock_ni_fotos(cliente, con_inventario, monkeypatch,
                                            order_api_falso):
    """El corazón del pedido: sacar del sitio no puede borrar nada.

    Se comprueba de las dos maneras: que no se llame a lo que escribe stock
    (la trampa delata a quien enganche un ajuste aquí) y que el puntero de la
    foto de la planta siga exactamente igual después de despublicarla."""
    ajustes = []
    monkeypatch.setattr(datos, "ajustar_en_odoo",
                        lambda *a, **k: ajustes.append(a) or {"ok": True, "resultados": []})
    # Una foto propia de la planta, como la que deja el pincel de la ficha.
    datos.fijar_foto_subida("PL-ROMERO", "abc123hash", "genesis")
    fotos_antes = datos.fotos_subidas()

    cliente.post("/productos/PL-ROMERO/publicacion", json={"publicado": False})

    assert ajustes == []
    assert datos.fotos_subidas() == fotos_antes
    assert datos.fotos_subidas()["PL-ROMERO"] == "abc123hash"
    # Y la planta sigue entera en la pantalla, con su stock y su foto.
    pagina = cliente.get("/?tab=stock").text
    assert "PL-ROMERO" in pagina


def test_cuerpo_sin_la_casilla_es_400(cliente, con_inventario, order_api_falso):
    assert cliente.post("/productos/PL-ROMERO/publicacion",
                        json={}).status_code == 400
    assert cliente.post("/productos/PL-ROMERO/publicacion",
                        json={"publicado": "si"}).status_code == 400
    assert order_api_falso == []


def test_si_odoo_falla_se_dice_y_no_se_da_por_hecho(cliente, con_inventario, monkeypatch):
    def caido(sku, publicado):
        raise datos.SinConexion("Odoo no responde")

    monkeypatch.setattr(datos, "fijar_publicacion_en_odoo", caido)
    r = cliente.post("/productos/PL-ROMERO/publicacion", json={"publicado": False})
    assert r.status_code == 502
    assert "Odoo no responde" in r.json()["mensaje"]


def test_sin_sesion_no_se_publica_nada(con_inventario, order_api_falso):
    from fastapi.testclient import TestClient

    from app.main import app

    anonimo = TestClient(app, follow_redirects=False)
    r = anonimo.post("/productos/PL-ROMERO/publicacion", json={"publicado": False})
    assert r.status_code == 303
    assert order_api_falso == []


def test_la_pantalla_lleva_la_casilla_de_cada_planta(cliente, con_inventario, monkeypatch):
    """El dato viaja a la vista como `pub`, aparte de `on` (lo que de verdad
    se ve hoy en el sitio): la ficha necesita los dos para no mentir."""
    productos = [dict(p) for p in con_inventario]
    productos[0]["publicado"] = False
    monkeypatch.setattr(datos, "obtener_inventario",
                        lambda refrescar=False: (productos, 1756800000.0))
    pagina = cliente.get("/?tab=stock").text
    assert '"pub": false' in pagina or '"pub":false' in pagina
