"""La vista de PROVEEDORES, segunda pantalla de Compras: lo que de verdad
se le compró a cada proveedor, todo calculado de Odoo salvo la marca
«Preferido» (la única escritura de este módulo, y va a una tabla local).

Ninguna prueba sale a la red: la puerta a Odoo (`ventas._ejecutar`) se
reemplaza siempre, igual que en `test_vehiculos.py` y `test_compras.py`.

Lo que se cuida acá es lo que duele si se rompe:

- que la pantalla sin ningún proveedor explique la situación y NUNCA
  revienta ni confunde "no hay" con "Odoo no contestó";
- que un proveedor sin compras salga "Prospecto" con "—", nunca con un 0;
- que los estados se calculen bien en los bordes (89 y 91 días, y el
  tope de 2 compras de "Evaluando");
- que "Preferido" (la única marca a mano) le gane a todo lo calculado;
- que "a tiempo" y "dañado" —que no existen todavía en ninguna parte—
  salgan siempre "—" con su nota, nunca inventados;
- y que Odoo caído dé un error distinto de "no hay proveedores".
"""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from app import proveedores, ventas

PANAMA = ZoneInfo("America/Panama")


def _fecha_hace(dias):
    """Un `date_order` de Odoo (UTC, "YYYY-MM-DD HH:MM:SS") que cae
    exactamente `dias` días antes de hoy, en hora de Panamá — al mediodía
    de Panamá, para no caer cerca de un borde de medianoche."""
    hoy_panama = datetime.now(PANAMA).date()
    fecha_panama = hoy_panama - timedelta(days=dias)
    dt_panama = datetime(fecha_panama.year, fecha_panama.month,
                         fecha_panama.day, 12, 0, 0, tzinfo=PANAMA)
    return dt_panama.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


@pytest.fixture
def odoo_configurado(monkeypatch):
    for variable in ("ODOO_URL", "ODOO_DB", "ODOO_USER", "ODOO_PASSWORD"):
        monkeypatch.setenv(variable, "de-prueba")


def _ejecutar_fake(partners=(), ordenes=()):
    """Un doble de `ventas._ejecutar` que reparte por modelo: los
    proveedores van a `compras.proveedores()` (res.partner) y las compras
    a `proveedores._ordenes_confirmadas` (purchase.order)."""
    def ejecutar(modelo, metodo, args, kw=None):
        if modelo == "res.partner":
            return list(partners)
        if modelo == "purchase.order":
            return list(ordenes)
        raise AssertionError(f"modelo inesperado: {modelo}")
    return ejecutar


PROV_1 = {"id": 11, "name": "Agroservicios del Istmo", "phone": "6000-1111",
          "email": "ventas@agro.com", "city": "Panamá"}
PROV_2 = {"id": 12, "name": "Vivero El Roble", "phone": "", "email": "",
          "city": ""}


# ---------------------------------------------------------------------------
# listar(): los tres caminos que la pantalla tiene que distinguir
# ---------------------------------------------------------------------------

def test_sin_odoo_configurado_es_ok_false_y_no_lista_vacia(monkeypatch):
    for variable in ("ODOO_URL", "ODOO_DB", "ODOO_USER", "ODOO_PASSWORD"):
        monkeypatch.delenv(variable, raising=False)
    resultado = proveedores.listar()
    assert resultado["ok"] is False
    assert resultado["error"] == "Odoo no está conectado."
    assert resultado["proveedores"] == []


def test_odoo_contesta_y_de_verdad_no_hay_proveedores(monkeypatch, odoo_configurado):
    monkeypatch.setattr(ventas, "_ejecutar", _ejecutar_fake(partners=[]))
    resultado = proveedores.listar()
    # El estado de HOY: ok True, lista vacía. No es una falla.
    assert resultado == {"ok": True, "error": "", "proveedores": []}


def test_odoo_caido_no_se_confunde_con_no_hay_proveedores(monkeypatch, odoo_configurado):
    def fallar(modelo, metodo, args, kw=None):
        raise RuntimeError("Connection refused")
    monkeypatch.setattr(ventas, "_ejecutar", fallar)
    resultado = proveedores.listar()
    assert resultado["ok"] is False
    assert resultado["proveedores"] == []
    # Es un error de verdad, no el string vacío del caso "no hay".
    assert resultado["error"]


def test_falla_al_leer_las_ordenes_tambien_es_ok_false(monkeypatch, odoo_configurado):
    """res.partner contesta bien, purchase.order revienta: no se puede
    calcular el estado de nadie, así que la pantalla entera avisa el
    error en vez de mostrar proveedores con datos a medias."""
    def ejecutar(modelo, metodo, args, kw=None):
        if modelo == "res.partner":
            return [PROV_1]
        raise RuntimeError("el módulo purchase no está instalado")
    monkeypatch.setattr(ventas, "_ejecutar", ejecutar)
    resultado = proveedores.listar()
    assert resultado["ok"] is False
    assert resultado["proveedores"] == []


def test_listar_o_vacio_nunca_revienta(monkeypatch, odoo_configurado):
    def fallar(modelo, metodo, args, kw=None):
        raise RuntimeError("lo que sea")
    monkeypatch.setattr(ventas, "_ejecutar", fallar)
    resultado = proveedores.listar_o_vacio()
    assert resultado["ok"] is False
    assert resultado["proveedores"] == []


# ---------------------------------------------------------------------------
# Un proveedor sin compras: Prospecto, con "—" y nunca un 0
# ---------------------------------------------------------------------------

def test_proveedor_sin_compras_es_prospecto_con_rayas(monkeypatch, odoo_configurado):
    monkeypatch.setattr(ventas, "_ejecutar",
                        _ejecutar_fake(partners=[PROV_1], ordenes=[]))
    resultado = proveedores.listar()
    assert resultado["ok"] is True
    [p] = resultado["proveedores"]
    assert p["estado"] == "PROSPECTO"
    assert p["estado_titulo"] == "Prospecto"
    assert p["hay_compra_alguna"] is False
    assert p["ultima_compra_texto"] == "—"
    assert p["a_tiempo_texto"] == "—"
    assert p["danado_texto"] == "—"
    assert p["compras_totales"] == 0
    assert p["compras_ano"] == 0
    assert p["total_ano"] == 0.0


# ---------------------------------------------------------------------------
# estado_de(): los bordes
# ---------------------------------------------------------------------------

def test_estado_prospecto_sin_ninguna_compra():
    assert proveedores.estado_de(False, 0, None) == "PROSPECTO"


def test_estado_evaluando_con_una_o_dos_compras():
    assert proveedores.estado_de(False, 1, 500) == "EVALUANDO"
    assert proveedores.estado_de(False, 2, 0) == "EVALUANDO"
    # Ni siquiera mirando la fecha: con tan poca historia no hay nada que
    # evaluar todavía, aunque la última compra sea de ayer.
    assert proveedores.estado_de(False, 1, 1) == "EVALUANDO"


def test_estado_activo_hasta_89_dias(monkeypatch):
    assert proveedores.estado_de(False, 5, 89) == "ACTIVO"


def test_estado_activo_en_el_borde_de_90_dias():
    assert proveedores.estado_de(False, 5, 90) == "ACTIVO"


def test_estado_en_pausa_desde_91_dias():
    assert proveedores.estado_de(False, 5, 91) == "PAUSA"


def test_estado_en_pausa_con_reclamo_abierto_aunque_compre_seguido():
    assert proveedores.estado_de(False, 10, 1, reclamo_abierto=True) == "PAUSA"


def test_preferido_le_gana_a_todo_lo_calculado():
    # Sin compras, con historia reciente, o en pausa de verdad: Preferido
    # siempre gana, porque es la única marca que NO se calcula.
    assert proveedores.estado_de(True, 0, None) == "PREFERIDO"
    assert proveedores.estado_de(True, 1, 0) == "PREFERIDO"
    assert proveedores.estado_de(True, 50, 400) == "PREFERIDO"
    assert proveedores.estado_de(True, 10, 5, reclamo_abierto=True) == "PREFERIDO"


# ---------------------------------------------------------------------------
# El camino real: varias órdenes, dos proveedores, estados distintos
# ---------------------------------------------------------------------------

def test_listar_calcula_totales_del_ano_y_estados(monkeypatch, odoo_configurado):
    ordenes = [
        # PROV_1: 3 compras confirmadas este año, la última hace 10 días →
        # Activo, con $100 + $50 = $150 de las dos del año (la tercera es
        # del año pasado y no debe sumar al total del año).
        {"partner_id": [11, "Agro"], "amount_total": 100.0,
         "date_order": _fecha_hace(10), "state": "purchase"},
        {"partner_id": [11, "Agro"], "amount_total": 50.0,
         "date_order": _fecha_hace(40), "state": "done"},
        {"partner_id": [11, "Agro"], "amount_total": 999.0,
         "date_order": "2020-01-15 12:00:00", "state": "purchase"},
        # Una cotización sin confirmar: no cuenta como compra. Si el
        # filtro de estado fallara, este registro NO debería aparecer
        # igual porque el fake solo devuelve lo que la consulta de verdad
        # pediría — se deja fuera a propósito, documentando la regla.
        #
        # PROV_2: una sola compra, de hace años → Evaluando (1-2 compras no
        # mira la fecha) y $0 este año. Fecha fija (no `_fecha_hace`) para
        # que la prueba no dependa de en qué mes del año corre.
        {"partner_id": [12, "Roble"], "amount_total": 840.0,
         "date_order": "2021-06-01 12:00:00", "state": "purchase"},
    ]
    monkeypatch.setattr(ventas, "_ejecutar",
                        _ejecutar_fake(partners=[PROV_1, PROV_2], ordenes=ordenes))
    resultado = proveedores.listar()
    assert resultado["ok"] is True
    por_id = {p["id"]: p for p in resultado["proveedores"]}

    agro = por_id[11]
    assert agro["estado"] == "ACTIVO"
    assert agro["compras_totales"] == 3
    assert agro["compras_ano"] == 2
    assert agro["total_ano"] == 150.0
    assert agro["hay_compra_alguna"] is True
    assert agro["dias_desde_ultima"] == 10

    roble = por_id[12]
    assert roble["estado"] == "EVALUANDO"
    assert roble["compras_totales"] == 1
    assert roble["compras_ano"] == 0  # es del 2021: no es de este año
    assert roble["total_ano"] == 0.0

    # Orden de la lista: Preferido/Activo/Evaluando/Prospecto/Pausa — Agro
    # (Activo) antes que Roble (Evaluando).
    assert [p["id"] for p in resultado["proveedores"]] == [11, 12]


def test_el_resumen_suma_solo_lo_del_ano(monkeypatch, odoo_configurado):
    ordenes = [
        {"partner_id": [11, "Agro"], "amount_total": 100.0,
         "date_order": _fecha_hace(5), "state": "purchase"},
        {"partner_id": [12, "Roble"], "amount_total": 840.0,
         "date_order": _fecha_hace(5), "state": "purchase"},
    ]
    monkeypatch.setattr(ventas, "_ejecutar",
                        _ejecutar_fake(partners=[PROV_1, PROV_2], ordenes=ordenes))
    resultado = proveedores.listar()
    resumen = proveedores.resumen(resultado["proveedores"])
    assert resumen["total_ano"] == 940.0
    assert resumen["compras_ano"] == 2
    assert resumen["preferidos"] == 0


# ---------------------------------------------------------------------------
# «Preferido»: la ÚNICA escritura, en la tabla local
# ---------------------------------------------------------------------------

def test_marcar_y_quitar_preferido(db_limpia):
    proveedores.iniciar_tablas()
    assert proveedores._preferidos() == set()
    error = proveedores.marcar_preferido(11, True, autor="Abraham")
    assert error == ""
    assert proveedores._preferidos() == {11}
    error = proveedores.marcar_preferido(11, False, autor="Abraham")
    assert error == ""
    assert proveedores._preferidos() == set()


def test_marcar_preferido_sin_id_no_revienta(db_limpia):
    proveedores.iniciar_tablas()
    assert proveedores.marcar_preferido("", True) != ""
    assert proveedores.marcar_preferido(None, True) != ""
    assert proveedores._preferidos() == set()


def test_preferido_cambia_el_estado_del_listado(monkeypatch, odoo_configurado, db_limpia):
    proveedores.iniciar_tablas()
    monkeypatch.setattr(ventas, "_ejecutar",
                        _ejecutar_fake(partners=[PROV_1], ordenes=[]))
    # Sin marcar: Prospecto (nunca le compraron nada).
    [p] = proveedores.listar()["proveedores"]
    assert p["estado"] == "PROSPECTO"
    proveedores.marcar_preferido(PROV_1["id"], True)
    [p] = proveedores.listar()["proveedores"]
    assert p["estado"] == "PREFERIDO"
    assert p["preferido"] is True


# ---------------------------------------------------------------------------
# La pantalla, con el TestClient: nunca un 500, y el candado de admin
# ---------------------------------------------------------------------------

def test_pantalla_sin_proveedores_no_revienta_y_explica(cliente, monkeypatch):
    for variable in ("ODOO_URL", "ODOO_DB", "ODOO_USER", "ODOO_PASSWORD"):
        monkeypatch.delenv(variable, raising=False)
    respuesta = cliente.get("/compras/proveedores")
    assert respuesta.status_code == 200
    assert "No se pudo leer Odoo" in respuesta.text


def test_pantalla_con_odoo_vacio_explica_sin_error(cliente, monkeypatch, odoo_configurado):
    monkeypatch.setattr(ventas, "_ejecutar", _ejecutar_fake(partners=[]))
    respuesta = cliente.get("/compras/proveedores")
    assert respuesta.status_code == 200
    assert "Todavía no hay proveedores" in respuesta.text


def test_pantalla_con_proveedores_pinta_la_tarjeta(cliente, monkeypatch, odoo_configurado):
    monkeypatch.setattr(ventas, "_ejecutar",
                        _ejecutar_fake(partners=[PROV_1], ordenes=[]))
    respuesta = cliente.get("/compras/proveedores")
    assert respuesta.status_code == 200
    assert "Agroservicios del Istmo" in respuesta.text
    assert "Prospecto" in respuesta.text


def test_no_admin_no_puede_marcar_preferido(cliente, monkeypatch, odoo_configurado, db_limpia):
    proveedores.iniciar_tablas()
    monkeypatch.delenv("AJUSTES_ADMINS", raising=False)
    respuesta = cliente.post("/compras/proveedores/preferido",
                             data={"partner_id": "11", "preferido": "1"},
                             follow_redirects=False)
    assert respuesta.status_code == 303
    assert proveedores._preferidos() == set()


def test_admin_si_puede_marcar_preferido(cliente, monkeypatch, odoo_configurado, db_limpia):
    proveedores.iniciar_tablas()
    monkeypatch.setenv("AJUSTES_ADMINS", "genesis")
    respuesta = cliente.post("/compras/proveedores/preferido",
                             data={"partner_id": "11", "preferido": "1"},
                             follow_redirects=False)
    assert respuesta.status_code == 303
    assert proveedores._preferidos() == {11}


# ---------------------------------------------------------------------------
# La ficha de UN proveedor: lo que le compramos, de `product.supplierinfo`.
# Pedido del dueño (30/09/2026) mientras esta pantalla se construía:
# "quiero poder asignar plantas a cada proveedor, precio, etc." — por
# ahora SOLO LEE. `ventas._ejecutar` reparte también por "product.
# supplierinfo"/"product.template"/"product.product".
# ---------------------------------------------------------------------------

def _ejecutar_fake_con_supplierinfo(partners=(), ordenes=(), supplierinfo=(),
                                    templates=(), variantes=()):
    def ejecutar(modelo, metodo, args, kw=None):
        if modelo == "res.partner":
            return list(partners)
        if modelo == "purchase.order":
            return list(ordenes)
        if modelo == "product.supplierinfo":
            return list(supplierinfo)
        if modelo == "product.template":
            return list(templates)
        if modelo == "product.product":
            return list(variantes)
        raise AssertionError(f"modelo inesperado: {modelo}")
    return ejecutar


def test_productos_de_sin_odoo_configurado(monkeypatch):
    for variable in ("ODOO_URL", "ODOO_DB", "ODOO_USER", "ODOO_PASSWORD"):
        monkeypatch.delenv(variable, raising=False)
    resultado = proveedores.productos_de(11)
    assert resultado["ok"] is False
    assert resultado["productos"] == []


def test_productos_de_sin_ningun_producto_asignado(monkeypatch, odoo_configurado):
    """El caso de HOY (30/09/2026), casi seguro: `product.supplierinfo`
    vacío. `ok` True con lista vacía, no una falla."""
    monkeypatch.setattr(ventas, "_ejecutar",
                        _ejecutar_fake_con_supplierinfo(supplierinfo=[]))
    resultado = proveedores.productos_de(11)
    assert resultado == {"ok": True, "error": "", "productos": []}


def test_productos_de_odoo_caido_no_se_confunde_con_sin_productos(
        monkeypatch, odoo_configurado):
    def fallar(modelo, metodo, args, kw=None):
        raise RuntimeError("el modelo no existe")
    monkeypatch.setattr(ventas, "_ejecutar", fallar)
    resultado = proveedores.productos_de(11)
    assert resultado["ok"] is False
    assert resultado["productos"] == []
    assert resultado["error"]


def test_productos_de_arma_nombre_sku_y_precio(monkeypatch, odoo_configurado):
    supplierinfo = [
        {"id": 1, "product_tmpl_id": [501, "Tierra negra"],
         "product_name": False, "product_code": False,
         "min_qty": 10.0, "price": 4.25},
        # Sin precio todavía: "sin precio", nunca un $0.00 inventado.
        {"id": 2, "product_tmpl_id": [502, "Abono orgánico"],
         "product_name": False, "product_code": False,
         "min_qty": 0.0, "price": False},
    ]
    templates = [{"id": 501, "name": "Tierra negra"},
                {"id": 502, "name": "Abono orgánico"}]
    variantes = [{"id": 9001, "product_tmpl_id": [501, "Tierra negra"],
                 "default_code": "IN-TIERRA-NEGRA"},
                {"id": 9002, "product_tmpl_id": [502, "Abono orgánico"],
                 "default_code": "IN-ABONO-ORGANICO"}]
    monkeypatch.setattr(ventas, "_ejecutar", _ejecutar_fake_con_supplierinfo(
        supplierinfo=supplierinfo, templates=templates, variantes=variantes))
    resultado = proveedores.productos_de(11)
    assert resultado["ok"] is True
    por_sku = {p["sku"]: p for p in resultado["productos"]}
    tierra = por_sku["IN-TIERRA-NEGRA"]
    assert tierra["nombre"] == "Tierra negra"
    assert tierra["precio"] == 4.25
    assert tierra["cantidad_minima"] == 10.0
    abono = por_sku["IN-ABONO-ORGANICO"]
    assert abono["precio"] is None  # "sin precio", no 0.0


def test_productos_de_si_falla_el_nombre_muestra_igual_la_linea(
        monkeypatch, odoo_configurado):
    """El nombre/SKU bonito es adorno: si esa segunda consulta revienta,
    la línea de `product.supplierinfo` se muestra igual con lo que trajo
    ella misma (su `product_name`, si lo tiene)."""
    def ejecutar(modelo, metodo, args, kw=None):
        if modelo == "product.supplierinfo":
            return [{"id": 1, "product_tmpl_id": [501, "Tierra negra"],
                     "product_name": "Tierra negra a granel",
                     "product_code": "TN-50", "min_qty": 1.0, "price": 4.0}]
        raise RuntimeError("Odoo con un mal rato")
    monkeypatch.setattr(ventas, "_ejecutar", ejecutar)
    resultado = proveedores.productos_de(11)
    assert resultado["ok"] is True
    [p] = resultado["productos"]
    assert p["nombre"] == "Tierra negra a granel"
    assert p["sku"] == "TN-50"
    assert p["precio"] == 4.0


def test_uno_encuentra_por_id_y_devuelve_none_si_no_esta(monkeypatch, odoo_configurado):
    monkeypatch.setattr(ventas, "_ejecutar",
                        _ejecutar_fake_con_supplierinfo(partners=[PROV_1]))
    encontrado = proveedores.uno(PROV_1["id"])
    assert encontrado["id"] == PROV_1["id"]
    assert proveedores.uno(999999) is None
    assert proveedores.uno("no-es-un-id") is None


def test_pantalla_abre_la_ficha_con_sus_productos(cliente, monkeypatch, odoo_configurado):
    supplierinfo = [{"id": 1, "product_tmpl_id": [501, "Tierra negra"],
                     "product_name": False, "product_code": False,
                     "min_qty": 10.0, "price": 4.25}]
    templates = [{"id": 501, "name": "Tierra negra"}]
    variantes = [{"id": 9001, "product_tmpl_id": [501, "Tierra negra"],
                 "default_code": "IN-TIERRA-NEGRA"}]
    monkeypatch.setattr(ventas, "_ejecutar", _ejecutar_fake_con_supplierinfo(
        partners=[PROV_1], ordenes=[], supplierinfo=supplierinfo,
        templates=templates, variantes=variantes))
    respuesta = cliente.get(f"/compras/proveedores?abrir={PROV_1['id']}")
    assert respuesta.status_code == 200
    assert "Tierra negra" in respuesta.text
    assert "IN-TIERRA-NEGRA" in respuesta.text


def test_pantalla_abrir_un_proveedor_sin_productos_lo_explica(
        cliente, monkeypatch, odoo_configurado):
    monkeypatch.setattr(ventas, "_ejecutar", _ejecutar_fake_con_supplierinfo(
        partners=[PROV_1], ordenes=[], supplierinfo=[]))
    respuesta = cliente.get(f"/compras/proveedores?abrir={PROV_1['id']}")
    assert respuesta.status_code == 200
    assert "Todavía no tiene ningún producto asignado" in respuesta.text


def test_pantalla_abrir_un_id_que_no_existe_no_revienta(cliente, monkeypatch, odoo_configurado):
    monkeypatch.setattr(ventas, "_ejecutar",
                        _ejecutar_fake_con_supplierinfo(partners=[PROV_1]))
    respuesta = cliente.get("/compras/proveedores?abrir=999999")
    assert respuesta.status_code == 200
    assert "ya no está en la lista" in respuesta.text
