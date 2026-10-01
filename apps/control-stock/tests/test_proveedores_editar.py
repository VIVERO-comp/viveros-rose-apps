"""Editar el catálogo de un proveedor (01/10/2026): pedido literal del
dueño, «quiero poder asignar plantas a cada proveedor, precio, etc.».

`app/proveedores.py` ya tenía la LECTURA de `product.supplierinfo`
(`productos_de`, probado en `test_proveedores.py`); esto prueba las CUATRO
escrituras nuevas — agregar, cambiar precio, quitar, y el delay compartido
del proveedor — y el buscador que les da de comer.

Ninguna prueba sale a la red: `ventas._ejecutar` se dobla siempre, como en
`test_proveedores.py` y `test_compras_form.py`. Lo que se cuida acá:

- agregar escribe `product.supplierinfo` con los TRES obligatorios que
  Odoo exige (`min_qty`, `delay`, `currency_id`) siempre puestos;
- cambiar el precio de una línea NUNCA toca la línea de otro proveedor,
  aunque sea del mismo producto;
- quitar borra la RELACIÓN (`product.supplierinfo`) y nunca pide borrar
  `product.template`/`product.product`;
- el delay es del PROVEEDOR: se escribe en TODAS sus líneas a la vez, y
  una línea nueva hereda el que ya tenían las demás;
- sin Odoo, cada escritura lo dice con un error — nunca se calla y nunca
  finge que no había nada que hacer;
- y que desde acá, de verdad, no hay ningún camino para crear un
  `product.template` o un `product.product` nuevo.
"""

import pytest

from app import proveedores, ventas


@pytest.fixture(autouse=True)
def moneda_limpia():
    """Sin esto, la moneda resuelta en una prueba queda pegada para las
    siguientes (mismo motivo que `ventas.reiniciar_cache`)."""
    proveedores.reiniciar_cache_moneda()
    yield
    proveedores.reiniciar_cache_moneda()


@pytest.fixture
def odoo_configurado(monkeypatch):
    for variable in ("ODOO_URL", "ODOO_DB", "ODOO_USER", "ODOO_PASSWORD"):
        monkeypatch.setenv(variable, "de-prueba")


def _odoo(monkeypatch, responde, anota=None):
    """`ventas._ejecutar` doblado, anotando (modelo, metodo, args, kw) de
    cada llamada para poder mirar QUÉ se le mandó a Odoo, sin adivinarlo."""
    def falso(modelo, metodo, args, kw=None):
        if anota is not None:
            anota.append((modelo, metodo, args, kw))
        return responde(modelo, metodo, args, kw)
    monkeypatch.setattr(ventas, "_ejecutar", falso)
    return falso


MONEDA_COMPANIA = [{"id": 1, "currency_id": [2, "USD"]}]


def _basico(supplierinfo_existente=None, delay_lineas=None, ids_lineas=None,
           partner_leido=None):
    """El despachador típico: una compañía con moneda, sin líneas previas
    salvo que se pida lo contrario."""
    def ejecutar(modelo, metodo, args, kw=None):
        if modelo == "res.company":
            return MONEDA_COMPANIA
        if modelo == "product.supplierinfo":
            if metodo == "search_read":
                campos = (kw or {}).get("fields") or []
                dominio = args[0] if args else []
                if campos == ["delay"]:
                    return list(delay_lineas or [])
                if campos == ["id"] and len(dominio) == 1:
                    # `cambiar_dias_entrega`: solo filtra por partner_id.
                    return list(ids_lineas or [])
                # `agregar_producto` preguntando "¿ya existe esta línea?":
                # filtra por partner_id Y product_tmpl_id (2 condiciones).
                return list(supplierinfo_existente or [])
            if metodo == "read":
                return list(partner_leido or [])
            if metodo in ("write", "create", "unlink"):
                return True
        raise AssertionError(f"llamada inesperada: {modelo}.{metodo}")
    return ejecutar


# ---------------------------------------------------------------------------
# 1. Agregar un producto: los TRES obligatorios SIEMPRE puestos
# ---------------------------------------------------------------------------

def test_agregar_producto_crea_con_los_obligatorios_puestos(monkeypatch, odoo_configurado):
    llamadas = []
    _odoo(monkeypatch, _basico(), anota=llamadas)
    error = proveedores.agregar_producto(
        11, 501, precio="4.25", cantidad_minima="10",
        codigo_proveedor="BS-50", nombre_proveedor="Black soil")
    assert error == ""
    creaciones = [l for l in llamadas if l[1] == "create"]
    assert len(creaciones) == 1
    modelo, _metodo, args, _kw = creaciones[0]
    assert modelo == "product.supplierinfo"
    escrito = args[0]
    assert escrito["partner_id"] == 11
    assert escrito["product_tmpl_id"] == 501
    assert escrito["price"] == 4.25
    assert escrito["product_code"] == "BS-50"
    assert escrito["product_name"] == "Black soil"
    # Los tres obligatorios de Odoo, siempre puestos.
    assert escrito["min_qty"] == 10.0
    assert escrito["currency_id"] == 2  # el id, no el par [id, nombre]
    assert escrito["delay"] == proveedores.DELAY_DEFECTO


def test_agregar_producto_sin_nada_escrito_usa_los_defaults_correctos(
        monkeypatch, odoo_configurado):
    """Precio y mínimo vacíos -> 0.0 (sin precio todavía / sin mínimo),
    NUNCA None ni un error: son los mismos defaults de fábrica de Odoo."""
    llamadas = []
    _odoo(monkeypatch, _basico(), anota=llamadas)
    error = proveedores.agregar_producto(11, 501)
    assert error == ""
    [creacion] = [l[2][0] for l in llamadas if l[1] == "create"]
    assert creacion["price"] == 0.0
    assert creacion["min_qty"] == 0.0
    assert creacion["delay"] == 1


def test_agregar_producto_hereda_el_delay_comun_del_proveedor(
        monkeypatch, odoo_configurado):
    """El delay es del PROVEEDOR: una planta nueva no lo pide, hereda el
    que ya comparten todas las demás líneas de ese mismo proveedor."""
    llamadas = []
    _odoo(monkeypatch, _basico(delay_lineas=[{"delay": 5}, {"delay": 5}]),
         anota=llamadas)
    error = proveedores.agregar_producto(11, 777, precio="9")
    assert error == ""
    [creacion] = [l[2][0] for l in llamadas if l[1] == "create"]
    assert creacion["delay"] == 5


def test_agregar_producto_con_delays_mixtos_usa_el_default(
        monkeypatch, odoo_configurado):
    """Si las líneas existentes NO están de acuerdo, no se inventa un
    "común": se usa el default de fábrica (1), nunca un promedio."""
    llamadas = []
    _odoo(monkeypatch, _basico(delay_lineas=[{"delay": 3}, {"delay": 7}]),
         anota=llamadas)
    proveedores.agregar_producto(11, 777, precio="9")
    [creacion] = [l[2][0] for l in llamadas if l[1] == "create"]
    assert creacion["delay"] == proveedores.DELAY_DEFECTO


def test_agregar_producto_que_ya_existe_actualiza_en_vez_de_duplicar(
        monkeypatch, odoo_configurado):
    """`product.supplierinfo` es una fila por PAR proveedor-producto:
    agregar un producto que este MISMO proveedor ya tenía actualiza esa
    fila — nunca crea una segunda."""
    llamadas = []
    _odoo(monkeypatch,
         _basico(supplierinfo_existente=[{"id": 55}]), anota=llamadas)
    error = proveedores.agregar_producto(11, 501, precio="6.00")
    assert error == ""
    assert not [l for l in llamadas if l[1] == "create"]
    [escritura] = [l for l in llamadas if l[1] == "write"]
    _modelo, _metodo, args, _kw = escritura
    assert args[0] == [55]
    assert args[1]["price"] == 6.00


def test_agregar_producto_sin_odoo_lo_dice(monkeypatch):
    for variable in ("ODOO_URL", "ODOO_DB", "ODOO_USER", "ODOO_PASSWORD"):
        monkeypatch.delenv(variable, raising=False)
    error = proveedores.agregar_producto(11, 501, precio="5")
    assert error == "Odoo no está conectado."


def test_agregar_producto_precio_no_numerico_es_error(monkeypatch, odoo_configurado):
    llamadas = []
    _odoo(monkeypatch, _basico(), anota=llamadas)
    error = proveedores.agregar_producto(11, 501, precio="no-es-numero")
    assert error != ""
    assert not llamadas  # ni siquiera se consultó Odoo con un dato ilegible


# ---------------------------------------------------------------------------
# 2. Cambiar el precio de una línea que ya está — nunca toca OTRA
# ---------------------------------------------------------------------------

def test_actualizar_linea_cambia_el_precio(monkeypatch, odoo_configurado):
    llamadas = []
    _odoo(monkeypatch,
         _basico(partner_leido=[{"id": 55, "partner_id": [11, "Agro"]}]),
         anota=llamadas)
    error = proveedores.actualizar_linea(11, 55, precio="7.50")
    assert error == ""
    [escritura] = [l for l in llamadas if l[1] == "write"]
    _modelo, _metodo, args, _kw = escritura
    assert args[0] == [55]
    assert args[1]["price"] == 7.50


def test_actualizar_linea_no_toca_la_misma_linea_de_otro_proveedor(
        monkeypatch, odoo_configurado):
    """La línea 55 es del proveedor 12, no del 11: pedir cambiarla desde
    el 11 se rechaza ANTES de escribir nada en Odoo."""
    llamadas = []
    _odoo(monkeypatch,
         _basico(partner_leido=[{"id": 55, "partner_id": [12, "Otro"]}]),
         anota=llamadas)
    error = proveedores.actualizar_linea(11, 55, precio="999")
    assert error == "Esa línea no es de este proveedor."
    assert not [l for l in llamadas if l[1] == "write"]


def test_actualizar_linea_que_ya_no_existe(monkeypatch, odoo_configurado):
    llamadas = []
    _odoo(monkeypatch, _basico(partner_leido=[]), anota=llamadas)
    error = proveedores.actualizar_linea(11, 55, precio="5")
    assert error != ""
    assert not [l for l in llamadas if l[1] == "write"]


def test_actualizar_linea_sin_odoo_lo_dice(monkeypatch):
    for variable in ("ODOO_URL", "ODOO_DB", "ODOO_USER", "ODOO_PASSWORD"):
        monkeypatch.delenv(variable, raising=False)
    error = proveedores.actualizar_linea(11, 55, precio="5")
    assert error == "Odoo no está conectado."


# ---------------------------------------------------------------------------
# 3. Quitar un producto: borra la RELACIÓN, nunca el producto propio
# ---------------------------------------------------------------------------

def test_quitar_producto_borra_la_relacion_y_no_el_producto(
        monkeypatch, odoo_configurado):
    llamadas = []
    _odoo(monkeypatch,
         _basico(partner_leido=[{"id": 55, "partner_id": [11, "Agro"]}]),
         anota=llamadas)
    error = proveedores.quitar_producto(11, 55)
    assert error == ""
    borrados = [l for l in llamadas if l[1] == "unlink"]
    assert len(borrados) == 1
    modelo, _metodo, args, _kw = borrados[0]
    assert modelo == "product.supplierinfo"
    assert args[0] == [55]
    # En ningún momento se tocó un producto: ni `product.template` ni
    # `product.product` aparecen en ninguna llamada de borrado.
    assert not [l for l in llamadas
               if l[1] == "unlink" and l[0] in ("product.template", "product.product")]


def test_quitar_producto_no_toca_la_linea_de_otro_proveedor(
        monkeypatch, odoo_configurado):
    llamadas = []
    _odoo(monkeypatch,
         _basico(partner_leido=[{"id": 55, "partner_id": [12, "Otro"]}]),
         anota=llamadas)
    error = proveedores.quitar_producto(11, 55)
    assert error == "Esa línea no es de este proveedor."
    assert not [l for l in llamadas if l[1] == "unlink"]


def test_quitar_producto_que_ya_no_esta_no_es_error(monkeypatch, odoo_configurado):
    """Otro clic, otra pestaña: la línea ya no está. Ya se logró lo que
    se pedía — no hay nada que avisar como error."""
    llamadas = []
    _odoo(monkeypatch, _basico(partner_leido=[]), anota=llamadas)
    error = proveedores.quitar_producto(11, 55)
    assert error == ""
    assert not [l for l in llamadas if l[1] == "unlink"]


def test_quitar_producto_sin_odoo_lo_dice(monkeypatch):
    for variable in ("ODOO_URL", "ODOO_DB", "ODOO_USER", "ODOO_PASSWORD"):
        monkeypatch.delenv(variable, raising=False)
    error = proveedores.quitar_producto(11, 55)
    assert error == "Odoo no está conectado."


# ---------------------------------------------------------------------------
# 4. Los días que tarda son del PROVEEDOR: se escriben en TODAS sus líneas
# ---------------------------------------------------------------------------

def test_cambiar_dias_entrega_escribe_en_todas_las_lineas_a_la_vez(
        monkeypatch, odoo_configurado):
    llamadas = []
    _odoo(monkeypatch,
         _basico(ids_lineas=[{"id": 1}, {"id": 2}, {"id": 3}]),
         anota=llamadas)
    error = proveedores.cambiar_dias_entrega(11, "5")
    assert error == ""
    [escritura] = [l for l in llamadas if l[1] == "write"]
    _modelo, _metodo, args, _kw = escritura
    assert sorted(args[0]) == [1, 2, 3]
    assert args[1] == {"delay": 5}


def test_cambiar_dias_entrega_sin_ninguna_linea_no_escribe_nada(
        monkeypatch, odoo_configurado):
    llamadas = []
    _odoo(monkeypatch, _basico(ids_lineas=[]), anota=llamadas)
    error = proveedores.cambiar_dias_entrega(11, "5")
    assert error == ""
    assert not [l for l in llamadas if l[1] == "write"]


def test_cambiar_dias_entrega_rechaza_negativos_y_texto_raro(
        monkeypatch, odoo_configurado):
    llamadas = []
    _odoo(monkeypatch, _basico(), anota=llamadas)
    assert proveedores.cambiar_dias_entrega(11, "-1") != ""
    assert proveedores.cambiar_dias_entrega(11, "no-es-numero") != ""
    assert not llamadas


def test_cambiar_dias_entrega_sin_odoo_lo_dice(monkeypatch):
    for variable in ("ODOO_URL", "ODOO_DB", "ODOO_USER", "ODOO_PASSWORD"):
        monkeypatch.delenv(variable, raising=False)
    error = proveedores.cambiar_dias_entrega(11, "5")
    assert error == "Odoo no está conectado."


def test_delay_comun_solo_si_todas_dicen_lo_mismo():
    assert proveedores.delay_comun([
        {"dias_entrega": 3}, {"dias_entrega": 3}]) == 3
    assert proveedores.delay_comun([
        {"dias_entrega": 3}, {"dias_entrega": 7}]) is None
    assert proveedores.delay_comun([]) is None


# ---------------------------------------------------------------------------
# Desde acá no se puede crear un producto
# ---------------------------------------------------------------------------

def test_desde_aca_no_se_puede_crear_un_producto(monkeypatch, odoo_configurado):
    """Ni agregar, ni actualizar, ni quitar, ni cambiar el delay escriben
    jamás sobre `product.template`/`product.product`: las únicas
    escrituras posibles de este módulo son sobre `product.supplierinfo`.
    Si el producto no existe, la única salida es `/productos/crear`."""
    llamadas = []
    _odoo(monkeypatch, _basico(delay_lineas=[], ids_lineas=[{"id": 1}],
                               partner_leido=[{"id": 1, "partner_id": [11, "Agro"]}]),
         anota=llamadas)
    proveedores.agregar_producto(11, 501, precio="5")
    proveedores.actualizar_linea(11, 1, precio="6")
    proveedores.quitar_producto(11, 1)
    proveedores.cambiar_dias_entrega(11, "2")
    escrituras_a_producto = [
        l for l in llamadas
        if l[0] in ("product.template", "product.product")
        and l[1] in ("create", "write", "unlink")]
    assert escrituras_a_producto == []
    # Y de hecho ninguna llamada tocó siquiera esos dos modelos.
    assert not [l for l in llamadas if l[0] in ("product.template", "product.product")]
    # El módulo no tiene, literalmente, ninguna función que cree un
    # producto: lo único que crea es `product.supplierinfo`.
    assert not hasattr(proveedores, "crear_producto")


# ---------------------------------------------------------------------------
# El buscador para asignar (el mismo catálogo de Compras, sin tocar su
# archivo): dedup por plantilla, y los tres caminos de siempre.
# ---------------------------------------------------------------------------

def test_buscar_para_asignar_vacio_no_pregunta_nada(monkeypatch, odoo_configurado):
    llamadas = []
    monkeypatch.setattr(ventas, "_ejecutar",
                        lambda *a, **k: llamadas.append(1) or [])
    resultado = proveedores.buscar_para_asignar("")
    assert resultado == {"ok": True, "error": "", "productos": []}
    assert not llamadas


def test_buscar_para_asignar_sin_odoo(monkeypatch):
    for variable in ("ODOO_URL", "ODOO_DB", "ODOO_USER", "ODOO_PASSWORD"):
        monkeypatch.delenv(variable, raising=False)
    resultado = proveedores.buscar_para_asignar("tierra")
    assert resultado["ok"] is False
    assert resultado["productos"] == []


def test_buscar_para_asignar_odoo_caido_no_es_lista_vacia(monkeypatch, odoo_configurado):
    def fallar(modelo, metodo, args, kw=None):
        raise RuntimeError("Odoo con un mal rato")
    monkeypatch.setattr(ventas, "_ejecutar", fallar)
    resultado = proveedores.buscar_para_asignar("tierra")
    assert resultado["ok"] is False
    assert resultado["error"]
    assert resultado["productos"] == []


def test_buscar_para_asignar_dedup_por_plantilla(monkeypatch, odoo_configurado):
    """Dos variantes del mismo producto (misma `product_tmpl_id`) salen
    UNA sola vez: `product.supplierinfo` va por la plantilla."""
    variantes = [
        {"id": 9001, "default_code": "PL-ROSA-ROJA", "name": "Rosa roja",
         "list_price": 12.0, "product_tmpl_id": [701, "Rosa roja"]},
        {"id": 9002, "default_code": "PL-ROSA-ROJA-G", "name": "Rosa roja grande",
         "list_price": 18.0, "product_tmpl_id": [701, "Rosa roja"]},
    ]
    monkeypatch.setattr(ventas, "_ejecutar",
                        lambda modelo, metodo, args, kw=None: list(variantes))
    resultado = proveedores.buscar_para_asignar("rosa")
    assert resultado["ok"] is True
    assert len(resultado["productos"]) == 1
    assert resultado["productos"][0]["producto_tmpl_id"] == 701


# ---------------------------------------------------------------------------
# Las rutas: admin-only, como «Marcar Preferido» en la misma pantalla
# ---------------------------------------------------------------------------

def test_no_admin_no_puede_agregar_producto_por_la_ruta(
        cliente, monkeypatch, odoo_configurado, db_limpia):
    monkeypatch.delenv("AJUSTES_ADMINS", raising=False)
    llamadas = []
    _odoo(monkeypatch, _basico(), anota=llamadas)
    respuesta = cliente.post("/compras/proveedores/producto", data={
        "partner_id": "11", "agregar": "501", "precio-501": "5"},
        follow_redirects=False)
    assert respuesta.status_code == 303
    assert not llamadas


def test_admin_si_puede_agregar_producto_por_la_ruta(
        cliente, monkeypatch, odoo_configurado, db_limpia):
    monkeypatch.setenv("AJUSTES_ADMINS", "genesis")
    llamadas = []
    _odoo(monkeypatch, _basico(), anota=llamadas)
    respuesta = cliente.post("/compras/proveedores/producto", data={
        "partner_id": "11", "agregar": "501", "precio-501": "5"},
        follow_redirects=False)
    assert respuesta.status_code == 303
    assert "abrir=11" in respuesta.headers["location"]
    assert [l for l in llamadas if l[1] == "create"]
