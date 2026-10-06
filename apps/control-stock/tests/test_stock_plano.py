"""La vista plana de Stock del rol Inventario y el bug del lugar.

Del plan (docs/DISENO-rol-inventario.md): lista única sin pestañas de
categoría, buscador arriba y orden por nombre; columnas nombre · cantidad
· tamaño · precio de venta (costos NO); foto solo VER. El conteo escribe
la cantidad ABSOLUTA por el punto único (bitácora incluida), rechaza lo
ilegible con el aviso bajo el campo (regla del lote de formularios) y
vuelve ANCLADA al mismo producto. Y el bug del lugar de la pestaña Stock
de siempre: /?producto=SKU ya no rebota al Calendario.
"""

import pytest
from fastapi.testclient import TestClient

from app import datos, datos_roles, seguridad, stock_escritura
from app.main import app


def _rol_inventario_n():
    with datos._db() as con:
        return con.execute("SELECT n FROM roles WHERE slug=?",
                           (datos_roles.SLUG_INVENTARIO,)).fetchone()["n"]


@pytest.fixture
def cliente_omar(db_limpia):
    seguridad.crear_empleada("omar", "Omar", "clave-de-prueba")
    assert datos_roles.poner_persona(_rol_inventario_n(), "omar", "korto") is None
    c = TestClient(app)
    r = c.post("/login",
               data={"usuario": "omar", "contrasena": "clave-de-prueba"},
               follow_redirects=False)
    assert r.status_code == 303
    return c


@pytest.fixture
def inventario_con_tamanos(monkeypatch, db_limpia):
    """El inventario falso de siempre, con alturas en el Romero para la
    columna de tamaño."""
    productos = [
        {"sku": "PL-ROMERO", "nombre": "Romero", "categoria": "Exterior",
         "disponible": 2, "fisico": 2, "precio_centavos": 350,
         "altura_min": 30, "altura_max": 50},
        {"sku": "PL-ALBAHACA", "nombre": "Albahaca", "categoria": "Exterior",
         "disponible": 0, "fisico": 0, "precio_centavos": 0},
        {"sku": "PL-IXORA", "nombre": "Ixora Roja", "categoria": "Florales",
         "disponible": 4, "fisico": 5, "precio_centavos": 650},
        {"sku": "PL-PALMA", "nombre": "Palma Areca", "categoria": "Exterior",
         "disponible": 41, "fisico": 41, "precio_centavos": 1500},
    ]
    monkeypatch.setattr(datos, "obtener_inventario",
                        lambda refrescar=False: (productos, 1756800000.0))
    return productos


def _cambios():
    with datos._db() as con:
        return [dict(f) for f in con.execute(
            "SELECT sku, antes, despues, tipo_operacion, por FROM stock_cambio")]


# ---------------------------------------------------------------------------
# La pantalla
# ---------------------------------------------------------------------------

def test_para_otros_perfiles_stock_es_la_pestana_de_siempre(cliente):
    r = cliente.get("/stock", follow_redirects=False)
    assert (r.status_code, r.headers["location"]) == (303, "/?tab=stock")


def test_lista_unica_ordenada_por_nombre_sin_categorias(
        cliente_omar, inventario_con_tamanos):
    pantalla = cliente_omar.get("/stock").text
    # Orden alfabético por nombre, no el del proxy.
    assert (pantalla.index("Albahaca") < pantalla.index("Ixora Roja")
            < pantalla.index("Palma Areca") < pantalla.index("Romero"))
    # Sin pestañas de categoría ni chips: lista única.
    for resto in ("Stock online", "Stock global", "Interior", "Florales"):
        assert resto not in pantalla, resto


def test_columnas_tamano_precio_de_venta_y_nunca_costos(
        cliente_omar, inventario_con_tamanos):
    pantalla = cliente_omar.get("/stock").text
    assert "30–50 cm" in pantalla          # tamaño (alto de/a) si existe
    assert "$3.50" in pantalla             # precio de venta
    assert "costo" not in pantalla.lower()  # costos de compra: jamás


def test_buscador_filtra_por_nombre_o_sku(cliente_omar, inventario_con_tamanos):
    pantalla = cliente_omar.get("/stock?q=rome").text
    assert "Romero" in pantalla and "Albahaca" not in pantalla
    pantalla = cliente_omar.get("/stock?q=PL-IXORA").text
    assert "Ixora Roja" in pantalla and "Romero" not in pantalla


def test_la_foto_es_solo_ver(cliente_omar, inventario_con_tamanos):
    pantalla = cliente_omar.get("/stock").text
    # Ni subir ni cambiar ni quitar: ningún formulario apunta a /fotos.
    assert 'action="/fotos' not in pantalla


# ---------------------------------------------------------------------------
# El conteo: parser, punto único, ancla
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("crudo", ["abc", "-3", "2.5", ""])
def test_cantidad_ilegible_rebota_con_aviso_bajo_el_campo(
        cliente_omar, inventario_con_tamanos, ajustes_registrados, crudo):
    r = cliente_omar.post("/stock/cantidad", data={
        "sku": "PL-ROMERO", "cantidad": crudo, "esperada": "2", "q": ""})
    assert r.status_code == 400
    assert "error-campo" in r.text  # el aviso, debajo del campo
    if crudo:
        # Lo tecleado no se borra (regla 5): el campo rebotó con su valor.
        assert f'value="{crudo}"' in r.text
    assert ajustes_registrados == [] and _cambios() == []  # nada se escribió


def test_guardar_vuelve_anclado_al_mismo_producto(
        cliente_omar, inventario_con_tamanos, ajustes_registrados):
    r = cliente_omar.post("/stock/cantidad", data={
        "sku": "PL-ROMERO", "cantidad": "7", "esperada": "2", "q": "rome"},
        follow_redirects=False)
    assert r.status_code == 303
    # El ancla del lugar Y la búsqueda viva: la pantalla se queda en el
    # MISMO producto.
    assert r.headers["location"] == \
        "/stock?aviso=guardado&sku=PL-ROMERO&q=rome#p-PL-ROMERO"
    assert ajustes_registrados == [{
        "ajustes": [{"sku": "PL-ROMERO", "cantidad": 7, "esperada": 2}],
        "empleado": "omar", "motivo": "conteo_inventario"}]
    (fila,) = _cambios()
    assert (fila["sku"], fila["despues"], fila["tipo_operacion"], fila["por"]) \
        == ("PL-ROMERO", 7, "conteo_inventario", "omar")


def test_el_conteo_acepta_cero(cliente_omar, inventario_con_tamanos,
                               ajustes_registrados):
    r = cliente_omar.post("/stock/cantidad", data={
        "sku": "PL-ROMERO", "cantidad": "0", "esperada": "2", "q": ""},
        follow_redirects=False)
    assert r.status_code == 303
    assert ajustes_registrados[0]["ajustes"][0]["cantidad"] == 0


def test_conflicto_repinta_con_el_valor_fresco(cliente_omar,
                                               inventario_con_tamanos,
                                               monkeypatch):
    def conflicto(ajustes, empleado, motivo):
        return {"ok": True, "resultados": [
            {"sku": "PL-ROMERO", "cantidad": 7, "resultado": "conflicto",
             "anterior": 9}]}
    monkeypatch.setattr(datos, "ajustar_en_odoo", conflicto)
    r = cliente_omar.post("/stock/cantidad", data={
        "sku": "PL-ROMERO", "cantidad": "7", "esperada": "2", "q": ""})
    assert r.status_code == 409
    assert "ahora hay 9" in r.text
    assert _cambios() == []  # un conflicto no escribe ni registra


def test_bitacora_caida_es_error_ruidoso(cliente_omar, inventario_con_tamanos,
                                         ajustes_registrados, monkeypatch):
    def revienta(*args, **kwargs):
        raise RuntimeError("disco lleno")
    monkeypatch.setattr(stock_escritura, "_registrar", revienta)
    r = cliente_omar.post("/stock/cantidad", data={
        "sku": "PL-ROMERO", "cantidad": "7", "esperada": "2", "q": ""})
    assert r.status_code == 200
    assert stock_escritura.AVISO_REGISTRO in r.text


def test_cualquier_rol_puede_contar_por_el_mismo_camino(
        cliente, con_inventario, ajustes_registrados):
    """El POST no es exclusivo del rol: todo cambio de cantidad, de quien
    sea, pasa por el punto único y deja bitácora."""
    r = cliente.post("/stock/cantidad", data={
        "sku": "PL-ROMERO", "cantidad": "4", "esperada": "2", "q": ""},
        follow_redirects=False)
    assert r.status_code == 303
    (fila,) = _cambios()
    assert fila["por"] == "genesis"


# ---------------------------------------------------------------------------
# La bitácora de admins
# ---------------------------------------------------------------------------

def test_bitacora_para_admins_con_fecha_de_panama(cliente, con_inventario,
                                                  ajustes_registrados,
                                                  monkeypatch):
    monkeypatch.setenv("AJUSTES_ADMINS", "genesis")
    cliente.post("/ajustar", json={"sku": "PL-ROMERO", "cantidad": 7,
                                   "esperada": 2})
    pantalla = cliente.get("/stock/cambios").text
    assert "PL-ROMERO" in pantalla and "ajuste_rapido" in pantalla
    pantalla = cliente.get("/stock/cambios?sku=PL-OTRA").text
    assert "Sin cambios registrados" in pantalla


def test_bitacora_rechaza_a_quien_no_es_admin(cliente):
    assert cliente.get("/stock/cambios").status_code == 403


# ---------------------------------------------------------------------------
# El bug del lugar de la pestaña Stock de siempre (todos los roles)
# ---------------------------------------------------------------------------

def test_producto_abierto_ya_no_rebota_al_calendario(cliente, con_inventario):
    """Guardar en Odoo desde el detalle recarga /?producto=SKU sin tab
    (app.js, parametrosDeEstado): antes este GET caía en el redirect al
    Calendario y el empleado perdía el producto. Ahora pinta el tablero
    y el arranque reabre ese detalle."""
    r = cliente.get("/?producto=PL-ROMERO&refrescar=1&vista=global",
                    follow_redirects=False)
    assert r.status_code == 200


def test_la_raiz_pelada_sigue_abriendo_el_calendario(cliente):
    r = cliente.get("/", follow_redirects=False)
    assert (r.status_code, r.headers["location"]) == (303, "/calendario")
