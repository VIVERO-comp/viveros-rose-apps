"""Los vehículos de entrega en la ficha del producto.

Tres Boolean de product.template en Odoo (viaja_moto/carro/pickup) que la
ficha lee, pinta solo si el producto está publicado, y guarda con el
marcador escondido `tiene_vehiculo` (el mismo patrón que `casillas` en
Vender). Ninguna prueba sale a la red: la puerta XML-RPC (ventas._ejecutar)
se reemplaza siempre.
"""

import pytest

from app import datos, vehiculos, ventas


@pytest.fixture(autouse=True)
def cache_fria():
    vehiculos.reiniciar_cache()
    yield
    vehiculos.reiniciar_cache()


@pytest.fixture
def odoo_configurado(monkeypatch):
    for variable in ("ODOO_URL", "ODOO_DB", "ODOO_USER", "ODOO_PASSWORD"):
        monkeypatch.setenv(variable, "de-prueba")


# ---------------------------------------------------------------------------
# leer(): el mapa {sku: {moto, carro, pickup}} desde Odoo, tolerante
# ---------------------------------------------------------------------------

def test_leer_sin_odoo_configurado_devuelve_none(monkeypatch):
    for variable in ("ODOO_URL", "ODOO_DB", "ODOO_USER", "ODOO_PASSWORD"):
        monkeypatch.delenv(variable, raising=False)
    assert vehiculos.leer() is None


def test_leer_arma_el_mapa_por_sku(monkeypatch, odoo_configurado):
    filas = [
        {"default_code": "PL-ROMERO", "viaja_moto": True,
         "viaja_carro": True, "viaja_pickup": False},
        {"default_code": "PL-PALMA", "viaja_moto": False,
         "viaja_carro": False, "viaja_pickup": True},
    ]
    monkeypatch.setattr(ventas, "_ejecutar",
                        lambda modelo, metodo, args, kw=None: filas)
    assert vehiculos.leer() == {
        "PL-ROMERO": {"moto": True, "carro": True, "pickup": False},
        "PL-PALMA": {"moto": False, "carro": False, "pickup": True},
    }


def test_leer_con_odoo_viejo_devuelve_none_sin_reventar(monkeypatch, odoo_configurado):
    # El Odoo de pruebas puede tener el addon anterior: el search_read con
    # un campo desconocido revienta con Fault y leer() lo traga.
    def fallar(modelo, metodo, args, kw=None):
        raise RuntimeError("Invalid field 'viaja_moto'")

    monkeypatch.setattr(ventas, "_ejecutar", fallar)
    assert vehiculos.leer() is None
    # Y el None queda cacheado: la siguiente pintada no vuelve a insistir.
    monkeypatch.setattr(ventas, "_ejecutar",
                        lambda *a, **k: pytest.fail("no debía releer"))
    assert vehiculos.leer() is None


def test_leer_cachea_el_mapa(monkeypatch, odoo_configurado):
    llamadas = []

    def ejecutar(modelo, metodo, args, kw=None):
        llamadas.append(metodo)
        return [{"default_code": "PL-ROMERO", "viaja_moto": True,
                 "viaja_carro": False, "viaja_pickup": False}]

    monkeypatch.setattr(ventas, "_ejecutar", ejecutar)
    primero = vehiculos.leer()
    segundo = vehiculos.leer()
    assert primero == segundo
    assert llamadas == ["search_read"]  # una sola ida a Odoo


# ---------------------------------------------------------------------------
# para_planta(): qué viaja en el datos_json (la sección solo si publicada)
# ---------------------------------------------------------------------------

def test_para_planta_publicada_con_dato():
    mapa = {"PL-ROMERO": {"moto": True, "carro": False, "pickup": False}}
    assert vehiculos.para_planta("PL-ROMERO", True, mapa) == {
        "moto": True, "carro": False, "pickup": False}


def test_para_planta_sin_publicar_no_pinta_seccion():
    mapa = {"PL-ROMERO": {"moto": True, "carro": False, "pickup": False}}
    assert vehiculos.para_planta("PL-ROMERO", False, mapa) is None


def test_para_planta_sin_mapa_no_pinta_seccion():
    # Odoo viejo o caído: leer() dio None y la ficha no promete nada.
    assert vehiculos.para_planta("PL-ROMERO", True, None) is None
    assert vehiculos.para_planta("PL-OTRA", True, {}) is None


# ---------------------------------------------------------------------------
# decidir_guardado(): el candado del POST (marcador + publicado)
# ---------------------------------------------------------------------------

def test_sin_marcador_no_se_escribe_nada(monkeypatch):
    # El clásico de los checkboxes: un form sin la sección no trae nada, y
    # eso NO significa «desmarcó todo».
    monkeypatch.setattr(vehiculos, "publicado_de", lambda sku: True)
    assert vehiculos.decidir_guardado("PL-ROMERO", {"viaja_moto": True}) is None
    assert vehiculos.decidir_guardado("PL-ROMERO", {}) is None


def test_con_marcador_ausente_es_false(monkeypatch):
    monkeypatch.setattr(vehiculos, "publicado_de", lambda sku: True)
    assert vehiculos.decidir_guardado(
        "PL-ROMERO", {"tiene_vehiculo": 1, "viaja_moto": True}) == {
        "viaja_moto": True, "viaja_carro": False, "viaja_pickup": False}


def test_con_marcador_y_todo_desmarcado_escribe_false(monkeypatch):
    monkeypatch.setattr(vehiculos, "publicado_de", lambda sku: True)
    assert vehiculos.decidir_guardado("PL-ROMERO", {"tiene_vehiculo": 1}) == {
        "viaja_moto": False, "viaja_carro": False, "viaja_pickup": False}


def test_producto_sin_publicar_no_se_escribe(monkeypatch):
    monkeypatch.setattr(vehiculos, "publicado_de", lambda sku: False)
    assert vehiculos.decidir_guardado(
        "PL-ROMERO", {"tiene_vehiculo": 1, "viaja_moto": True}) is None


def test_publicado_desconocido_confia_en_el_marcador(monkeypatch):
    # Inventario caído: el marcador solo existe si el servidor pintó la
    # sección, y pintarla exigió publicado. Se escribe.
    monkeypatch.setattr(vehiculos, "publicado_de", lambda sku: None)
    assert vehiculos.decidir_guardado(
        "PL-ROMERO", {"tiene_vehiculo": 1, "viaja_pickup": True}) == {
        "viaja_moto": False, "viaja_carro": False, "viaja_pickup": True}


def test_publicado_de_lee_el_inventario(monkeypatch):
    productos = [{"sku": "PL-ROMERO", "publicado": False},
                 {"sku": "PL-PALMA"}]
    monkeypatch.setattr(datos, "obtener_inventario",
                        lambda refrescar=False: (productos, 0.0))
    assert vehiculos.publicado_de("PL-ROMERO") is False
    assert vehiculos.publicado_de("PL-PALMA") is True   # sin campo = publicada
    assert vehiculos.publicado_de("PL-NO-EXISTE") is None


def test_publicado_de_sin_inventario_devuelve_none(monkeypatch):
    def caido(refrescar=False):
        raise datos.SinConexion("sin proxy")

    monkeypatch.setattr(datos, "obtener_inventario", caido)
    assert vehiculos.publicado_de("PL-ROMERO") is None


# ---------------------------------------------------------------------------
# fijar_en_odoo(): la escritura real
# ---------------------------------------------------------------------------

def test_fijar_sin_odoo_configurado_simula_exito(monkeypatch):
    for variable in ("ODOO_URL", "ODOO_DB", "ODOO_USER", "ODOO_PASSWORD"):
        monkeypatch.delenv(variable, raising=False)
    respuesta = vehiculos.fijar_en_odoo(
        "PL-ROMERO", {"viaja_moto": True, "viaja_carro": False,
                      "viaja_pickup": False})
    assert respuesta["resultado"] == "aplicado"


def test_fijar_escribe_los_tres_boolean(monkeypatch, odoo_configurado):
    llamadas = []

    def ejecutar(modelo, metodo, args, kw=None):
        llamadas.append((modelo, metodo, args))
        if metodo == "search":
            return [42]
        return True

    monkeypatch.setattr(ventas, "_ejecutar", ejecutar)
    vehiculos.fijar_en_odoo("PL-ROMERO", {"viaja_moto": True})
    assert llamadas[0][:2] == ("product.template", "search")
    assert llamadas[1] == ("product.template", "write",
                           [[42], {"viaja_moto": True, "viaja_carro": False,
                                   "viaja_pickup": False}])


def test_fijar_sku_que_no_esta_en_odoo(monkeypatch, odoo_configurado):
    monkeypatch.setattr(ventas, "_ejecutar",
                        lambda modelo, metodo, args, kw=None: [])
    with pytest.raises(datos.SinConexion):
        vehiculos.fijar_en_odoo("PL-FANTASMA", {"viaja_moto": True})


def test_fijar_con_odoo_caido_sube_sin_conexion(monkeypatch, odoo_configurado):
    def fallar(modelo, metodo, args, kw=None):
        raise ConnectionError("Odoo caído")

    monkeypatch.setattr(ventas, "_ejecutar", fallar)
    with pytest.raises(datos.SinConexion):
        vehiculos.fijar_en_odoo("PL-ROMERO", {"viaja_moto": True})


def test_fijar_reinicia_la_cache_al_escribir(monkeypatch, odoo_configurado):
    # Tras escribir, la próxima pintada relee de Odoo (no sirve el mapa viejo).
    monkeypatch.setattr(
        ventas, "_ejecutar",
        lambda modelo, metodo, args, kw=None: [7] if metodo == "search" else [])
    vehiculos._cache["mapa"] = {"valor": {"PL-X": {}}, "en": 9e12}
    vehiculos.fijar_en_odoo("PL-ROMERO", {})
    assert vehiculos._cache == {}


# ---------------------------------------------------------------------------
# La pantalla: la sección "Entrega en línea" y el veh de cada planta
# ---------------------------------------------------------------------------

@pytest.fixture
def editora(monkeypatch):
    monkeypatch.setenv("FICHAS_EDITORES", "genesis")


def test_pantalla_trae_la_seccion_de_vehiculos(cliente, editora):
    pagina = cliente.get("/?tab=stock").text
    assert 'id="ficha-veh"' in pagina
    assert "Entrega en línea" in pagina
    assert "En qué vehículos puede viajar. Se guarda en Odoo." in pagina
    # Nace tapada: la destapa el JS solo cuando p.veh viene del servidor.
    assert "ficha-veh" in pagina and "hidden" in pagina


def test_datos_json_llevan_los_vehiculos(cliente, editora, con_inventario,
                                         monkeypatch):
    # La Ixora queda sin publicar: aunque Odoo tenga sus vehículos, su veh
    # viaja null y la sección no se le pinta (misma regla que en Odoo).
    for producto in con_inventario:
        if producto["sku"] == "PL-IXORA":
            producto["publicado"] = False
    monkeypatch.setattr(vehiculos, "leer", lambda: {
        "PL-ROMERO": {"moto": True, "carro": True, "pickup": False},
        "PL-IXORA": {"moto": True, "carro": True, "pickup": True},
    })
    pagina = cliente.get("/?tab=stock").text
    assert '"veh": {"moto": true, "carro": true, "pickup": false}' in pagina
    assert '"pub": false, "veh": null' in pagina  # la Ixora, sin publicar


def test_datos_json_sin_odoo_van_con_veh_null(cliente, editora, con_inventario,
                                              monkeypatch):
    # Odoo viejo o caído: leer() da None y ninguna planta promete la sección.
    monkeypatch.setattr(vehiculos, "leer", lambda: None)
    pagina = cliente.get("/?tab=stock").text
    assert '"veh": null' in pagina
    assert '"veh": {' not in pagina


# ---------------------------------------------------------------------------
# El POST de «Guardar ficha»: los tres Boolean viajan (o no) a Odoo
# ---------------------------------------------------------------------------

FICHA_BUENA = {
    "descripcion": "Romero (Salvia rosmarinus), aromática de sol pleno.",
    "luz": "Sol pleno", "riego": "Cada 4–5 días", "dificultad": "Facil",
    "nota": "",
}


@pytest.fixture
def vehiculos_capturados(monkeypatch):
    """Captura lo que la ruta manda a Odoo, con el producto publicado."""
    llamadas = []

    def fijar(sku, valores):
        llamadas.append((sku, valores))
        return {"ok": True, "sku": sku, "resultado": "aplicado"}

    monkeypatch.setattr(vehiculos, "fijar_en_odoo", fijar)
    monkeypatch.setattr(vehiculos, "publicado_de", lambda sku: True)
    return llamadas


def test_ruta_guarda_los_vehiculos(cliente, editora, vehiculos_capturados):
    respuesta = cliente.post("/fichas/PL-ROMERO", json=dict(
        FICHA_BUENA, tiene_vehiculo=1, viaja_moto=True, viaja_pickup=True))
    assert respuesta.status_code == 200
    assert respuesta.json()["vehiculos"] == {
        "moto": True, "carro": False, "pickup": True}
    assert vehiculos_capturados == [("PL-ROMERO", {
        "viaja_moto": True, "viaja_carro": False, "viaja_pickup": True})]


def test_ruta_sin_marcador_no_toca_los_vehiculos(cliente, editora,
                                                 vehiculos_capturados):
    # Un form de antes del cambio (o con la sección tapada) no trae el
    # marcador: la ficha se guarda igual y Odoo no se toca.
    respuesta = cliente.post("/fichas/PL-ROMERO", json=dict(FICHA_BUENA))
    assert respuesta.status_code == 200
    assert respuesta.json()["vehiculos"] is None
    assert vehiculos_capturados == []


def test_ruta_sin_publicar_no_toca_los_vehiculos(cliente, editora, monkeypatch):
    llamadas = []
    monkeypatch.setattr(vehiculos, "fijar_en_odoo",
                        lambda sku, valores: llamadas.append(sku))
    monkeypatch.setattr(vehiculos, "publicado_de", lambda sku: False)
    respuesta = cliente.post("/fichas/PL-ROMERO", json=dict(
        FICHA_BUENA, tiene_vehiculo=1, viaja_moto=True))
    assert respuesta.status_code == 200
    assert respuesta.json()["vehiculos"] is None
    assert llamadas == []


def test_si_odoo_falla_con_los_vehiculos_no_se_guarda_la_prosa(cliente, editora,
                                                               monkeypatch):
    from app import fichas

    def fallar(sku, valores):
        raise datos.SinConexion("Odoo no responde")

    monkeypatch.setattr(vehiculos, "fijar_en_odoo", fallar)
    monkeypatch.setattr(vehiculos, "publicado_de", lambda sku: True)
    respuesta = cliente.post("/fichas/PL-ROMERO", json=dict(
        FICHA_BUENA, tiene_vehiculo=1, viaja_moto=True))
    assert respuesta.status_code == 502
    assert fichas.todas() == {}
