"""Las dos casillas del PDF (dueño, 28/09/2026): «Pago 50% abono / 50%
contra entrega» e «Incluir garantía». La venta normal nace DESMARCADA y el
personalizado MARCADO; se cambian en cada cotización y al editar quedan
como se guardaron. Las banderas viven en la orden de Odoo (pago_50_50,
con_garantia, addon v19.0.1.56.0, nacen en True allá) y la propuesta
decide con ellas AL IMPRIMIR — por eso las cotizaciones viejas salen
igual que siempre. Sin 50/50, la tarjeta de Control no pide el abono."""

import pytest

from app import cot_lead, cotizaciones, ventas
from test_ventas import OdooFalso, _agregar


class OdooEspia(OdooFalso):
    """El OdooFalso de siempre, guardando también las banderas del PDF
    (como hace el real) para poder releerlas al editar."""

    # Lo mínimo del espejo CRM que crear_personalizada toca después de
    # crear la orden (no es lo que se prueba acá).
    def crm_lead_write(self, args, kw):
        return True

    def sale_order_line_search_read(self, args, kw):
        # Como el de la base, pero aguantando las líneas display_type
        # (los párrafos de los servicios) que el personalizado crea.
        orden_id = args[0][0][2]
        filas = []
        for indice, l in enumerate(self.ordenes[orden_id]["lineas"], start=1):
            cantidad = l.get("product_uom_qty") or 0
            filas.append({
                "id": indice,
                "name": l.get("name") or self.productos.get(
                    l.get("product_id"), {}).get("name", ""),
                "display_type": l.get("display_type"),
                "product_id": ([l["product_id"],
                                self.productos.get(l["product_id"], {}).get("name", "")]
                               if l.get("product_id") else False),
                "product_uom_qty": cantidad,
                "price_unit": self._precio(l) if not l.get("display_type") else 0,
                "price_subtotal": round(cantidad * self._precio(l), 2)
                                  if not l.get("display_type") else 0,
            })
        return filas

    def sale_order_search_read(self, args, kw):
        # Solo el dominio [["id","in",ids]] de estados_en_odoo.
        dominio = args[0]
        ids = dominio[0][2] if dominio else list(self.ordenes)
        return [{"id": i, **{c: self.ordenes[i].get(c, False)
                             for c in kw["fields"]}}
                for i in ids if i in self.ordenes]

    def sale_order_create(self, args, kw):
        vals = dict(args[0])
        nuevo = super().sale_order_create(args, kw)
        self.ordenes[nuevo]["pago_50_50"] = vals.get("pago_50_50", True)
        self.ordenes[nuevo]["con_garantia"] = vals.get("con_garantia", True)
        self.ordenes[nuevo]["opportunity_id"] = False
        self.ordenes[nuevo]["vals_crear"] = vals
        return nuevo

    def sale_order_write(self, args, kw):
        resultado = super().sale_order_write(args, kw)
        for orden_id in args[0]:
            for campo in ("pago_50_50", "con_garantia", "opportunity_id"):
                if campo in args[1]:
                    self.ordenes[orden_id][campo] = args[1][campo]
        return resultado


@pytest.fixture
def odoo(monkeypatch, tmp_path, db_limpia):
    falso = OdooEspia()
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
    return falso


@pytest.fixture
def cliente_venta(cliente, odoo):
    return cliente


# --- La decisión, en Python -------------------------------------------------

def test_sin_marcador_rigen_los_defaults_de_la_pantalla():
    """Un formulario o borrador de antes del cambio no trae "casillas":
    venta normal desmarcadas, personalizado marcadas."""
    assert ventas.banderas_de({}, False) == {
        "pago_50_50": False, "con_garantia": False}
    assert ventas.banderas_de({}, True) == {
        "pago_50_50": True, "con_garantia": True}


def test_con_marcador_mandan_las_casillas():
    form = {"casillas": "1", "pago_50_50": "1"}
    assert ventas.banderas_de(form, True) == {
        "pago_50_50": True, "con_garantia": False}
    assert ventas.banderas_de({"casillas": "1"}, True) == {
        "pago_50_50": False, "con_garantia": False}


# --- La venta normal: desmarcadas de fábrica --------------------------------

def test_la_cotizacion_de_venta_nace_sin_50_50_ni_garantia(cliente_venta, odoo):
    _agregar(cliente_venta, 501)
    r = cliente_venta.post("/venta/cotizar",
                           data={"cliente": "Marta", "casillas": "1"},
                           follow_redirects=False)
    assert r.status_code == 200
    vals = list(odoo.ordenes.values())[-1]["vals_crear"]
    assert vals["pago_50_50"] is False and vals["con_garantia"] is False


def test_las_casillas_marcadas_viajan_a_la_orden(cliente_venta, odoo):
    _agregar(cliente_venta, 501)
    cliente_venta.post("/venta/cotizar",
                       data={"cliente": "Marta", "casillas": "1",
                             "pago_50_50": "1", "con_garantia": "1"},
                       follow_redirects=False)
    vals = list(odoo.ordenes.values())[-1]["vals_crear"]
    assert vals["pago_50_50"] is True and vals["con_garantia"] is True


def test_la_pantalla_de_venta_nace_desmarcada(cliente_venta, odoo):
    _agregar(cliente_venta, 501)
    r = cliente_venta.get("/venta/nueva")
    assert 'name="pago_50_50"' in r.text and 'name="con_garantia"' in r.text
    import re
    for campo in ("pago_50_50", "con_garantia"):
        caja = re.search(rf'<input[^>]*name="{campo}"[^>]*>', r.text).group(0)
        assert "checked" not in caja


# --- El personalizado: marcadas de fábrica ----------------------------------

def test_el_personalizado_nace_marcado(cliente_venta, odoo):
    r = cliente_venta.get("/venta/servicio-personalizada")
    import re
    for campo in ("pago_50_50", "con_garantia"):
        caja = re.search(rf'<input[^>]*name="{campo}"[^>]*>', r.text).group(0)
        assert "checked" in caja
    # Y al crear sin tocar nada (formulario viejo, sin marcador): marcadas.
    cliente_venta.post("/venta/servicio-personalizada",
                       data={"cliente": "Ana", "servicios": "1",
                             "servicio_texto": "Arreglo", "servicio_monto": "10"},
                       follow_redirects=False)
    vals = list(odoo.ordenes.values())[-1]["vals_crear"]
    assert vals["pago_50_50"] is True and vals["con_garantia"] is True


def test_desmarcar_en_el_personalizado_apaga_las_banderas(cliente_venta, odoo):
    cliente_venta.post("/venta/servicio-personalizada",
                       data={"cliente": "Ana", "servicios": "1",
                             "servicio_texto": "Arreglo", "servicio_monto": "10",
                             "casillas": "1", "con_garantia": "1"},
                       follow_redirects=False)
    vals = list(odoo.ordenes.values())[-1]["vals_crear"]
    assert vals["pago_50_50"] is False and vals["con_garantia"] is True


# --- Editar: quedan como se guardaron ---------------------------------------

def test_al_editar_las_casillas_vuelven_como_se_guardaron(cliente_venta, odoo,
                                                          monkeypatch):
    # El espejo del CRM no es lo que se prueba acá (y el OdooFalso chico
    # no trae ir.model.data ni crm.lead).
    monkeypatch.setattr(cotizaciones, "_oportunidad_espejada",
                        lambda *a, **k: None)
    registro = cotizaciones.crear_personalizada(
        {"id": "genesis", "nombre": "Génesis"}, "Ana", "",
        servicios=[{"texto": "Arreglo", "monto": "10", "descripcion": ""}],
        banderas={"pago_50_50": False, "con_garantia": True})
    datos = cotizaciones.cargar_para_editar(registro["n"])
    assert datos["banderas"] == {"pago_50_50": False, "con_garantia": True}
    # Guardar la edición con las casillas cambiadas las escribe en Odoo.
    cotizaciones.editar_cotizacion(
        registro["n"], [{"texto": "Arreglo", "monto": "12", "descripcion": ""}],
        [], banderas={"pago_50_50": True, "con_garantia": False})
    orden = odoo.ordenes[registro["orden_id"]]
    assert orden["pago_50_50"] is True and orden["con_garantia"] is False
    # Y un guardado SIN casillas (los otros tipos de servicio) no las toca.
    cotizaciones.editar_cotizacion(
        registro["n"], [{"texto": "Arreglo", "monto": "12", "descripcion": ""}],
        [], banderas=None)
    orden = odoo.ordenes[registro["orden_id"]]
    assert orden["pago_50_50"] is True and orden["con_garantia"] is False


# --- La tarjeta de Control no pide abono sin el 50/50 -----------------------

def _fila_orden(pago_50_50):
    return {
        "id": 7, "name": "S00099", "date_order": "2026-09-28",
        "amount_total": 100.0, "state": "sale", "etapa_cobro": "cotizado",
        "total_pagado": 0.0, "saldo_pendiente": 100.0,
        "linear_issue_url": "", "client_order_ref": "",
        "reemplazada_por_id": False, "lead_ref": "PP-X", "lead_real": True,
        "pago_50_50": pago_50_50,
    }


def test_sin_50_50_la_tarjeta_no_pide_abono(monkeypatch, db_limpia):
    monkeypatch.setattr(
        ventas, "_ejecutar",
        lambda modelo, metodo, args, kw=None: [_fila_orden(False)])
    plata = cot_lead.plata_de_la_real({"pp": "PP-X"})
    assert plata["hay_real"] and plata["pide_abono"] is False
    assert plata["abono_50"] is None


def test_con_50_50_el_abono_sigue_saliendo(monkeypatch, db_limpia):
    monkeypatch.setattr(
        ventas, "_ejecutar",
        lambda modelo, metodo, args, kw=None: [_fila_orden(True)])
    plata = cot_lead.plata_de_la_real({"pp": "PP-X"})
    assert plata["pide_abono"] is True and plata["abono_50"] == 50.0


# --- El candado de boda/evento (cierre de la tarea 2) -----------------------

def test_las_rutas_de_boda_y_evento_van_al_unificado(cliente_venta, odoo):
    for tipo in ("boda", "evento"):
        r = cliente_venta.get(f"/venta/servicio/{tipo}", follow_redirects=False)
        assert r.status_code == 303
        assert r.headers["location"] == "/venta/servicio/renta"
        r = cliente_venta.post(f"/venta/servicio/{tipo}", data={},
                               follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/venta"
