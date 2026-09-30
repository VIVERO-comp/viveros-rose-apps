"""Pruebas de "venta normal" en Vender (dueño, 28/09/2026):

- El botón "+ Venta" arriba de los servicios (el rename y el arreglo de
  .solo-pc viven en test_ventas.py, con el resto de /venta).
- La planta personalizada: un renglón libre (nombre, cantidad, precio) que
  viaja con el comodín CUSTOM-PLANTA, buscado por su código — nunca
  creado por esta app, y nunca visible en el buscador de plantas.
- El stock junto al precio del buscador (datos.obtener_inventario, el
  mismo que lee Stock).
- Guardar venta (confirma la orden) vs. Generar cotización (borrador).
- lead_ref en la orden nueva, solo si hay un lead pendiente.

Mismo Odoo simulado que test_ventas.py (OdooFalso), con doble siempre
(monkeypatch.setattr(ventas, "_ejecutar", ...)) — nunca contra un Odoo
real.
"""

import pytest

from app import linear_leads, ventas
from test_ventas import OdooFalso

EMPLEADA = {"id": "genesis", "nombre": "Génesis"}


@pytest.fixture
def odoo(monkeypatch, tmp_path, db_limpia):
    falso = OdooFalso()
    for variable, valor in {
        "ODOO_URL": "http://odoo-de-prueba:8069", "ODOO_DB": "pruebas",
        "ODOO_USER": "prueba", "ODOO_PASSWORD": "prueba",
        "VENTA_DIARIO_YAPPY": "9", "VENTA_DIARIO_EFECTIVO": "10",
        "VENTA_TAG_LOCAL": "1", "VENTA_CLIENTE_LOCAL": "74",
        "VENTA_FOTOS_DIR": str(tmp_path / "fotos"),
    }.items():
        monkeypatch.setenv(variable, valor)
    monkeypatch.setattr(ventas, "_ejecutar", falso.ejecutar)
    ventas._cache_cargos.clear()
    # El "no encontrado" del comodín no se cachea (a propósito, puede
    # crearse en Odoo sin reiniciar el servidor), pero el "sí encontrado"
    # de un caso anterior SÍ, y son procesos de prueba compartidos: hay
    # que arrancar cada caso en blanco.
    ventas._id_personalizada_planta["id"] = None
    return falso


@pytest.fixture
def con_comodin(odoo):
    """El comodín CUSTOM-PLANTA ya existe en este Odoo, como en
    odoo-pruebas (id 216, tipo consu, sin impuesto, precio 0)."""
    odoo.productos[216] = {"default_code": ventas.CODIGO_PERSONALIZADA_PLANTA,
                           "name": "Planta personalizada", "list_price": 0.0}
    return odoo


@pytest.fixture
def cliente_venta(cliente, odoo):
    return cliente


# ---------------------------------------------------------------------------
# El renglón libre viaja con el comodín, sin tocar impuestos
# ---------------------------------------------------------------------------

def test_renglon_libre_viaja_con_el_comodin_y_el_nombre(con_comodin):
    ventas.agregar_renglon_planta(
        "genesis", "Croton grande de otro vivero", "2", "9.50")
    registro = ventas.crear_cotizacion(EMPLEADA, "Ana", "")
    lineas = con_comodin.ordenes[registro["orden_id"]]["lineas"]
    libres = [l for l in lineas if l["product_id"] == 216]
    assert len(libres) == 1
    assert libres[0]["name"] == "Croton grande de otro vivero"
    assert libres[0]["product_uom_qty"] == 2.0
    assert libres[0]["price_unit"] == 9.5
    assert "tax_id" not in libres[0] and "taxes_id" not in libres[0]
    # El renglón libre queda en el resumen de la tarjeta del historial.
    assert "Croton grande de otro vivero" in registro["resumen"]


def test_renglon_libre_y_planta_del_catalogo_no_tocan_impuestos(con_comodin):
    ventas.agregar_al_carrito("genesis", 501, 1)  # PL-ROMERO
    ventas.agregar_renglon_planta("genesis", "Maceta grande", "1", "5")
    registro = ventas.crear_cotizacion(EMPLEADA, "Ana", "")
    lineas = con_comodin.ordenes[registro["orden_id"]]["lineas"]
    assert len(lineas) == 2
    for linea in lineas:
        assert "tax_id" not in linea
        assert "taxes_id" not in linea


def test_solo_renglones_libres_alcanza_sin_ninguna_planta_del_catalogo(con_comodin):
    # Una venta 100% de planta personalizada, sin nada del inventario.
    ventas.agregar_renglon_planta("genesis", "Bonsái de un vivero vecino", "1", "40")
    registro = ventas.crear_cotizacion(EMPLEADA, "Tamara", "")
    lineas = con_comodin.ordenes[registro["orden_id"]]["lineas"]
    assert len(lineas) == 1
    assert lineas[0]["product_id"] == 216


def test_comodin_ausente_deshabilita_el_renglon_sin_reventar(odoo):
    # Sin sembrar el producto 216: no existe en este Odoo (por ejemplo el
    # Odoo real, antes de que Abraham lo cree ahí).
    assert ventas.id_producto_personalizada_planta() is None
    ventas.agregar_renglon_planta("genesis", "Algo raro", "1", "10")
    with pytest.raises(ValueError, match="CUSTOM-PLANTA"):
        ventas.crear_cotizacion(EMPLEADA, "Ana", "")


def test_renglon_libre_vacio_no_bloquea_una_venta_normal(con_comodin):
    # Sin ningún renglón libre agregado: crear_cotizacion no debe ni
    # preguntarle a Odoo por el comodín (lineas_de_renglones_planta corta
    # antes si la lista viene vacía).
    ventas.agregar_al_carrito("genesis", 501, 1)
    registro = ventas.crear_cotizacion(EMPLEADA, "Ana", "")
    assert registro is not None


def test_nombre_sin_letras_no_es_un_nombre(con_comodin):
    with pytest.raises(ValueError):
        ventas.agregar_renglon_planta("genesis", ":", "1", "5")
    with pytest.raises(ValueError):
        ventas.agregar_renglon_planta("genesis", "🤍", "1", "5")
    with pytest.raises(ValueError):
        ventas.agregar_renglon_planta("genesis", "   ", "1", "5")
    assert ventas.renglones_planta_de("genesis") == []


def test_nombre_con_letras_se_respeta_tal_cual(con_comodin):
    ventas.agregar_renglon_planta("genesis", "soy pobre pero digno.", "1", "5")
    renglones = ventas.renglones_planta_de("genesis")
    assert renglones[0]["texto"] == "soy pobre pero digno."


def test_precio_o_cantidad_ilegibles_avisan(con_comodin):
    with pytest.raises(ValueError):
        ventas.agregar_renglon_planta("genesis", "Palma", "1", "no-numero")
    with pytest.raises(ValueError):
        ventas.agregar_renglon_planta("genesis", "Palma", "0", "5")
    with pytest.raises(ValueError):
        ventas.agregar_renglon_planta("genesis", "Palma", "1", "")
    assert ventas.renglones_planta_de("genesis") == []


def test_quitar_renglon_planta(con_comodin):
    ventas.agregar_renglon_planta("genesis", "Palma", "1", "5")
    n = ventas.renglones_planta_de("genesis")[0]["n"]
    ventas.quitar_renglon_planta("genesis", n)
    assert ventas.renglones_planta_de("genesis") == []


# ---------------------------------------------------------------------------
# El comodín nunca sale en el buscador de plantas (ni, por lo tanto, en
# Stock — que lee del mismo tipo de dominio "default_code like PL-" del
# lado del stock-proxy).
# ---------------------------------------------------------------------------

def test_codigo_del_comodin_no_calza_con_el_prefijo_pl():
    # El filtro real de buscar_productos es "default_code like 'PL-'"; si
    # el código del comodín alguna vez calzara ahí, se colaría en el
    # buscador y en Stock. Con guion: "PL-ANTA" no cuenta.
    assert "PL-" not in ventas.CODIGO_PERSONALIZADA_PLANTA


def test_buscar_productos_exige_el_prefijo_pl_en_el_dominio(odoo, monkeypatch):
    """No basta con que el comodín no aparezca en los resultados del
    OdooFalso (que no aplica el dominio): lo que protege de verdad es que
    buscar_productos SIGA mandando el filtro "default_code like 'PL-'" a
    Odoo. Se espía la llamada real."""
    dominios = []
    original = ventas._ejecutar

    def espia(modelo, metodo, args, kw=None):
        if modelo == "product.product" and metodo == "search_read":
            dominios.append(args[0])
        return original(modelo, metodo, args, kw)

    monkeypatch.setattr(ventas, "_ejecutar", espia)
    ventas.buscar_productos("planta personalizada")
    assert dominios and ["default_code", "like", "PL-"] in dominios[0]


# ---------------------------------------------------------------------------
# El stock junto al precio del buscador
# ---------------------------------------------------------------------------

def test_stock_se_muestra_junto_al_precio(cliente_venta, con_inventario):
    r = cliente_venta.get("/venta/nueva?q=romero")
    assert "$3.50" in r.text
    assert "2 en stock" in r.text


def test_stock_agotado_se_distingue(cliente_venta, con_inventario):
    con_inventario[0]["disponible"] = 0  # PL-ROMERO, agotado
    r = cliente_venta.get("/venta/nueva?q=romero")
    assert "0 en stock" in r.text
    assert "stock-agotado" in r.text


def test_stock_desconocido_no_dice_cero(cliente_venta, monkeypatch):
    """Si el inventario no contesta, "no hay" nunca se confunde con "no
    sé" (regla del proyecto): la pantalla lo dice, no inventa un 0."""
    from app import datos

    def revienta(refrescar=False):
        raise datos.SinConexion("el stock-proxy no responde")

    monkeypatch.setattr(datos, "obtener_inventario", revienta)
    r = cliente_venta.get("/venta/nueva?q=romero")
    assert r.status_code == 200
    assert "no disponible ahora" in r.text
    assert "0 en stock" not in r.text


def test_json_en_vivo_trae_el_stock(cliente_venta, con_inventario):
    r = cliente_venta.get("/venta/buscar?q=romero")
    assert r.json()["resultados"][0]["disponible"] == 2


# ---------------------------------------------------------------------------
# Guardar venta (confirma) vs. Generar cotización (borrador)
# ---------------------------------------------------------------------------

def test_generar_cotizacion_deja_la_orden_en_borrador(con_comodin):
    ventas.agregar_al_carrito("genesis", 501, 2)
    registro = ventas.crear_cotizacion(EMPLEADA, "Ana", "")
    assert con_comodin.ordenes[registro["orden_id"]]["state"] == "draft"
    assert registro["estado"] == "cotizacion"


def test_guardar_venta_confirma_la_misma_orden(con_comodin):
    ventas.agregar_al_carrito("genesis", 501, 2)
    registro = ventas.crear_cotizacion(EMPLEADA, "Ana", "", confirmar=True)
    assert con_comodin.ordenes[registro["orden_id"]]["state"] == "sale"
    assert registro["estado"] == "vendida"


def test_guardar_venta_por_la_pantalla(cliente_venta, con_comodin):
    r = cliente_venta.post("/venta/carrito/agregar",
                           data={"producto_id": 501, "cantidad": 1},
                           follow_redirects=False)
    assert r.status_code == 303
    r = cliente_venta.post("/venta/vender", data={"cliente": "Ana", "celular": ""})
    assert r.status_code == 200
    assert "Venta confirmada" in r.text
    ventas_locales = ventas.ventas_todas()
    assert ventas_locales[0]["estado"] == "vendida"


def test_venta_confirmada_y_cotizacion_salen_en_la_lista_de_vender(cliente_venta, con_comodin):
    # Punto 6 del encargo: la lista "Ventas y cotizaciones locales" ya lee
    # ventas_locales, así que lo nuevo aparece solo — verificado, no dado
    # por hecho.
    cliente_venta.post("/venta/carrito/agregar",
                       data={"producto_id": 501, "cantidad": 1})
    cliente_venta.post("/venta/vender", data={"cliente": "Ana", "celular": ""})
    cliente_venta.post("/venta/carrito/agregar",
                       data={"producto_id": 502, "cantidad": 1})
    cliente_venta.post("/venta/cotizar", data={"cliente": "Beto", "celular": ""})
    r = cliente_venta.get("/venta")
    assert "Ana" in r.text and "Beto" in r.text
    assert '<span class="badge b-ok">Venta</span>' in r.text
    assert '<span class="badge b-bajo">Cotización</span>' in r.text
    # "vendida" no ofrece Facturar/Reintentar: el cobro vive en Odoo.
    assert "Descargar orden (PDF)" in r.text


def test_la_orden_vendida_baja_con_target_blank_y_download(cliente_venta, con_comodin):
    """A esta le faltaba la regla del 28/09/2026 (la fusión de "venta
    normal" nació después del hotfix de los PDF): mismo problema, mismo
    arreglo — descarga Y pestaña nueva a la vez."""
    cliente_venta.post("/venta/carrito/agregar",
                       data={"producto_id": 501, "cantidad": 1})
    cliente_venta.post("/venta/vender", data={"cliente": "Ana", "celular": ""})
    pagina = cliente_venta.get("/venta").text
    encontrado = False
    for trozo in pagina.split("<a ")[1:]:
        enlace = trozo.split(">")[0]
        if "/cotizacion.pdf" in enlace:
            encontrado = True
            assert 'target="_blank"' in enlace
            assert 'rel="noopener"' in enlace
            assert "download" in enlace
    assert encontrado, "no se encontró el enlace de la orden vendida"


def test_la_orden_vendida_tiene_boton_compartir_oculto_con_su_nombre(
        cliente_venta, con_comodin):
    cliente_venta.post("/venta/carrito/agregar",
                       data={"producto_id": 501, "cantidad": 1})
    cliente_venta.post("/venta/vender", data={"cliente": "Ana", "celular": ""})
    registro = ventas.ventas_todas()[0]
    esperado = ventas.nombre_de_pdf(registro["orden"].replace("/", "-"),
                                    registro["cliente"])
    pagina = cliente_venta.get("/venta").text
    marca = f'data-compartir="/venta/{registro["n"]}/cotizacion.pdf"'
    assert marca in pagina
    pos = pagina.index(marca)
    inicio = pagina.rindex("<", 0, pos)
    fin = pagina.index(">", pos)
    tag = pagina[inicio:fin + 1]
    assert "hidden" in tag
    assert f'data-nombre="{esperado}"' in tag


# ---------------------------------------------------------------------------
# lead_ref: solo si hay lead pendiente
# ---------------------------------------------------------------------------

def test_lead_ref_se_escribe_si_hay_lead_pendiente(con_comodin, monkeypatch):
    ventas.agregar_al_carrito("genesis", 501, 1)
    ventas.poner_lead_pendiente("genesis", "LEAD-70", "Ana")
    monkeypatch.setattr(
        linear_leads, "uno",
        lambda ref, leads=None: {"pp": "PP-ABCDE"} if ref == "LEAD-70" else None)
    registro = ventas.crear_cotizacion(EMPLEADA, "Ana", "")
    orden = con_comodin.ordenes[registro["orden_id"]]
    assert orden["lead_ref"] == "PP-ABCDE"


def test_lead_ref_no_se_escribe_sin_lead_pendiente(con_comodin):
    ventas.agregar_al_carrito("genesis", 501, 1)
    registro = ventas.crear_cotizacion(EMPLEADA, "Ana", "")
    orden = con_comodin.ordenes[registro["orden_id"]]
    assert not orden["lead_ref"]


def test_lead_ref_no_bloquea_si_linear_no_contesta(con_comodin, monkeypatch):
    ventas.agregar_al_carrito("genesis", 501, 1)
    ventas.poner_lead_pendiente("genesis", "LEAD-70", "Ana")

    def revienta(ref, leads=None):
        raise RuntimeError("Linear no contesta")

    monkeypatch.setattr(linear_leads, "uno", revienta)
    registro = ventas.crear_cotizacion(EMPLEADA, "Ana", "")  # no revienta
    orden = con_comodin.ordenes[registro["orden_id"]]
    assert not orden["lead_ref"]


def test_la_orden_vendida_lleva_data_pdf_con_su_nombre(cliente_venta, con_comodin):
    """En iPhone «Descargar» abre la hoja nativa (30/09/2026): el enlace
    lleva `data-pdf` y el nombre calculado en Python en `download`."""
    cliente_venta.post("/venta/carrito/agregar",
                       data={"producto_id": 501, "cantidad": 1})
    cliente_venta.post("/venta/vender", data={"cliente": "Ana", "celular": ""})
    registro = ventas.ventas_todas()[0]
    esperado = ventas.nombre_de_pdf(registro["orden"].replace("/", "-"),
                                    registro["cliente"])
    pagina = cliente_venta.get("/venta").text
    encontrado = False
    for trozo in pagina.split("<a ")[1:]:
        enlace = trozo.split(">")[0]
        if "/cotizacion.pdf" in enlace:
            encontrado = True
            assert "data-pdf" in enlace
            assert f'download="{esperado}"' in enlace
    assert encontrado, "no se encontró el enlace de la orden vendida"
