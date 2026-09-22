"""El alta de plantas (formulario "Crear planta") y las dos vistas del stock.

La app sigue sin hablar con Odoo directo: crear una planta es un POST al
order-api, igual que los ajustes y la altura. Lo que se prueba aquí es la
parte de la app — validación, orden de los dos pasos y qué se le manda al
order-api— con el cliente del order-api sustituido.
"""

import json

import pytest

from app import datos


@pytest.fixture
def altas_registradas(monkeypatch):
    """Sustituye el POST /api/productos por uno que anota las llamadas."""
    llamadas = []

    def crear(sku, nombre, categoria, precio_centavos,
              altura_min=0, altura_max=0, sin_moto=False, costo_centavos=0,
              nombre_secundario="", nombre_cientifico=""):
        llamadas.append({"sku": sku, "nombre": nombre, "categoria": categoria,
                         "precio_centavos": precio_centavos,
                         "altura_min": altura_min, "altura_max": altura_max,
                         "sin_moto": sin_moto, "costo_centavos": costo_centavos,
                         "nombre_secundario": nombre_secundario,
                         "nombre_cientifico": nombre_cientifico})
        return {"ok": True, "sku": sku, "id": 900 + len(llamadas),
                "nombre": nombre, "resultado": "creado"}

    monkeypatch.setattr(datos, "crear_planta_en_odoo", crear)
    return llamadas


NUEVA = {"nombre": "Lavanda", "sku": "PL-LAVANDA", "categoria": "Exterior",
         "precioCentavos": 750, "costoCentavos": 300, "cantidad": 6,
         "alturaMin": 30, "alturaMax": 45, "sinMoto": False,
         "nombreSecundario": "Espliego", "nombreCientifico": "Lavandula"}


# ---------------------------------------------------------------------------
# El SKU sugerido (se arma en Python; el JS solo lo muestra al escribir)
# ---------------------------------------------------------------------------

def test_sku_sugerido_va_en_mayusculas_y_sin_tildes():
    assert datos.sku_sugerido("Palma Areca") == "PL-PALMA-ARECA"
    assert datos.sku_sugerido("Limón Persa") == "PL-LIMON-PERSA"
    assert datos.sku_sugerido("Piña ornamental") == "PL-PINA-ORNAMENTAL"


def test_sku_sugerido_aguanta_signos_y_espacios_de_mas():
    assert datos.sku_sugerido("  Ficus   Lyrata (3 ramas) ") == "PL-FICUS-LYRATA-3-RAMAS"
    assert datos.sku_sugerido("") == ""
    assert datos.sku_sugerido("¿?") == ""


# ---------------------------------------------------------------------------
# POST /productos/nuevo
# ---------------------------------------------------------------------------

def test_crea_la_planta_y_le_pone_el_stock_inicial(
        cliente, con_inventario, altas_registradas, ajustes_registrados):
    r = cliente.post("/productos/nuevo", json=NUEVA)
    assert r.status_code == 200
    assert r.json()["sku"] == "PL-LAVANDA"
    assert altas_registradas == [{
        "sku": "PL-LAVANDA", "nombre": "Lavanda", "categoria": "Exterior",
        "precio_centavos": 750, "altura_min": 30, "altura_max": 45,
        "sin_moto": False, "costo_centavos": 300,
        "nombre_secundario": "Espliego", "nombre_cientifico": "Lavandula",
    }]
    # El stock va por el ajuste de siempre, con esperada=0: la planta acaba
    # de nacer sin existencias.
    assert ajustes_registrados[0]["ajustes"] == [
        {"sku": "PL-LAVANDA", "cantidad": 6, "esperada": 0}]
    assert ajustes_registrados[0]["motivo"] == "alta_de_planta"


def test_sin_cantidad_no_toca_el_stock(
        cliente, con_inventario, altas_registradas, ajustes_registrados):
    r = cliente.post("/productos/nuevo", json={**NUEVA, "cantidad": 0})
    assert r.status_code == 200
    assert r.json()["stock"] == "sin_stock"
    assert len(altas_registradas) == 1
    assert ajustes_registrados == []


def test_sin_sku_se_arma_desde_el_nombre(
        cliente, con_inventario, altas_registradas, ajustes_registrados):
    r = cliente.post("/productos/nuevo", json={**NUEVA, "sku": "", "nombre": "Palma Areca"})
    assert r.status_code == 200
    assert altas_registradas[0]["sku"] == "PL-PALMA-ARECA"


def test_el_sku_se_guarda_en_mayusculas(
        cliente, con_inventario, altas_registradas, ajustes_registrados):
    cliente.post("/productos/nuevo", json={**NUEVA, "sku": "pl-lavanda"})
    assert altas_registradas[0]["sku"] == "PL-LAVANDA"


def test_nombre_vacio_no_crea_nada(cliente, con_inventario, altas_registradas):
    r = cliente.post("/productos/nuevo", json={**NUEVA, "nombre": "   "})
    assert r.status_code == 400
    assert altas_registradas == []


def test_sku_sin_prefijo_pl_no_crea_nada(cliente, con_inventario, altas_registradas):
    """Sin PL- la planta nacería invisible para el stock proxy y la tienda."""
    r = cliente.post("/productos/nuevo", json={**NUEVA, "sku": "LAVANDA"})
    assert r.status_code == 400
    assert altas_registradas == []


def test_categoria_fuera_de_la_lista_no_crea_nada(cliente, con_inventario, altas_registradas):
    r = cliente.post("/productos/nuevo", json={**NUEVA, "categoria": "Insumos"})
    assert r.status_code == 400
    assert altas_registradas == []


def test_precio_o_cantidad_negativos_no_crean_nada(cliente, con_inventario, altas_registradas):
    assert cliente.post("/productos/nuevo",
                        json={**NUEVA, "precioCentavos": -1}).status_code == 400
    assert cliente.post("/productos/nuevo",
                        json={**NUEVA, "costoCentavos": -1}).status_code == 400
    assert cliente.post("/productos/nuevo",
                        json={**NUEVA, "cantidad": -3}).status_code == 400
    assert altas_registradas == []


def test_el_rechazo_del_order_api_llega_con_su_motivo(
        cliente, con_inventario, monkeypatch):
    """Un SKU repetido lo detecta Odoo: el empleado tiene que leer por qué."""
    def falla(*a, **k):
        raise datos.SinConexion("Ya existe un producto con la referencia PL-LAVANDA.")

    monkeypatch.setattr(datos, "crear_planta_en_odoo", falla)
    r = cliente.post("/productos/nuevo", json=NUEVA)
    assert r.status_code == 502
    assert "PL-LAVANDA" in r.json()["mensaje"]


def test_si_el_stock_falla_la_planta_ya_creada_no_se_repite(
        cliente, con_inventario, altas_registradas, monkeypatch):
    """Crear dos veces la planta sería peor que dejarla en cero: se avisa."""
    def falla(*a, **k):
        raise datos.SinConexion("order-api caído")

    monkeypatch.setattr(datos, "ajustar_en_odoo", falla)
    r = cliente.post("/productos/nuevo", json=NUEVA)
    assert r.status_code == 200
    assert r.json()["stock"] == "falló"
    assert len(altas_registradas) == 1


def test_sin_sesion_no_se_crea_nada(db_limpia, altas_registradas):
    from fastapi.testclient import TestClient

    from app.main import app

    r = TestClient(app).post("/productos/nuevo", json=NUEVA, follow_redirects=False)
    assert r.status_code in (302, 303, 401, 403)
    assert altas_registradas == []


# ---------------------------------------------------------------------------
# Stock online vs Stock global: qué marca lleva cada planta en la pantalla
# ---------------------------------------------------------------------------

def _plantas_de(cliente):
    html = cliente.get("/?tab=stock").text
    crudo = html.split("window.DATOS = ", 1)[1].split(";</script>", 1)[0]
    return {p["sku"]: p for p in json.loads(crudo)["plantas"]}


def test_cada_planta_dice_si_esta_en_la_tienda(cliente, con_inventario):
    plantas = _plantas_de(cliente)
    assert plantas["PL-ROMERO"]["on"] is True
    # La Ixora está activa en Odoo pero el sitio no la publica: solo sale en
    # Stock global, y la tarjeta lo dice.
    assert plantas["PL-IXORA"]["on"] is False


def test_sin_el_catalogo_del_sitio_no_se_inventa(cliente, con_inventario, monkeypatch):
    """Sin saber qué hay publicado, `on` va en None y la pantalla lo avisa en
    vez de mostrar una lista incompleta como si fuera la buena."""
    monkeypatch.setattr(datos, "obtener_publicados",
                        lambda: (None, "el sitio no responde"))
    html = cliente.get("/?tab=stock").text
    crudo = html.split("window.DATOS = ", 1)[1].split(";</script>", 1)[0]
    datos_pantalla = json.loads(crudo)
    assert datos_pantalla["sinPublicados"] == "el sitio no responde"
    assert all(p["on"] is None for p in datos_pantalla["plantas"])


def test_los_nombres_de_la_ficha_llegan_sin_espacios_de_mas(
        cliente, con_inventario, altas_registradas, ajustes_registrados):
    cliente.post("/productos/nuevo", json={**NUEVA,
                                           "nombreSecundario": "  Espliego  ",
                                           "nombreCientifico": " Lavandula "})
    assert altas_registradas[0]["nombre_secundario"] == "Espliego"
    assert altas_registradas[0]["nombre_cientifico"] == "Lavandula"


def test_los_nombres_de_la_ficha_son_opcionales(
        cliente, con_inventario, altas_registradas, ajustes_registrados):
    """Una planta sin nombre secundario ni científico se crea igual; el
    order-api simplemente no escribe las notas."""
    cuerpo = {k: v for k, v in NUEVA.items()
              if k not in ("nombreSecundario", "nombreCientifico")}
    r = cliente.post("/productos/nuevo", json=cuerpo)
    assert r.status_code == 200
    assert altas_registradas[0]["nombre_secundario"] == ""
