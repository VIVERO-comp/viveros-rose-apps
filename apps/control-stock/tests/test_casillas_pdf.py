"""La casilla del PDF «Incluir garantía» (dueño, 28/09/2026). [1/10/2026:
la casilla «Pago 50% abono / 50% contra entrega» se retiró de Vender por
decisión del dueño — las cotizaciones nuevas ya no prometen pago en dos
partes, así que no hay nada que elegir ahí. Vender ya NO manda
`pago_50_50` a Odoo en ningún camino; sin escribirlo, el campo queda en
su default (False) para las órdenes nuevas, y una orden vieja que ya lo
tenga prendido NO se toca al editar — nunca se manda, nunca se pisa.] La
venta normal nace desmarcada (garantía) y el personalizado marcado; se
cambia en cada cotización y al editar queda como se guardó. `cot_lead.py`
y la ficha de Control siguen leyendo `pago_50_50` de la orden tal cual
(fuera del alcance de este cambio): sin el 50/50, la tarjeta no pide el
abono, que sigue probado abajo sin tocar ese archivo."""

import pytest

from app import cot_lead, cotizaciones, ventas
from test_ventas import OdooFalso, _agregar


class OdooEspia(OdooFalso):
    """El OdooFalso de siempre, guardando también la bandera del PDF
    (como hace el real) para poder releerla al editar."""

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
        # El addon real nace con los dos campos en False; la ausencia de
        # "pago_50_50" en vals (Vender ya no lo manda) tiene que quedar en
        # False, nunca heredar el True de antes del 1/10/2026.
        self.ordenes[nuevo]["pago_50_50"] = vals.get("pago_50_50", False)
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

def test_sin_marcador_rige_el_default_de_la_pantalla():
    """Un formulario o borrador de antes del cambio no trae "casillas":
    venta normal desmarcada, personalizado marcada. Ya no hay nada de
    pago_50_50 en el resultado: no hay ninguna casilla que lo pinte."""
    assert ventas.banderas_de({}, False) == {"con_garantia": False}
    assert ventas.banderas_de({}, True) == {"con_garantia": True}


def test_con_marcador_manda_la_casilla():
    form = {"casillas": "1", "pago_50_50": "1"}
    # Aunque un formulario viejo (o un ataque) mande "pago_50_50" en el
    # POST, banderas_de ya no lo lee: solo existe "con_garantia".
    assert ventas.banderas_de(form, True) == {"con_garantia": False}
    assert ventas.banderas_de({"casillas": "1", "con_garantia": "1"}, True) == {
        "con_garantia": True}


# --- La venta normal: ya no hay casilla de 50/50, y Odoo no la recibe -------

def test_la_cotizacion_de_venta_no_manda_pago_50_50(cliente_venta, odoo):
    _agregar(cliente_venta, 501)
    r = cliente_venta.post("/venta/cotizar",
                           data={"cliente": "Marta", "casillas": "1"},
                           follow_redirects=False)
    assert r.status_code == 200
    vals = list(odoo.ordenes.values())[-1]["vals_crear"]
    assert "pago_50_50" not in vals
    assert vals["con_garantia"] is False
    # Y la orden en Odoo queda en su default (False), sin que nadie se lo
    # haya pedido.
    orden = list(odoo.ordenes.values())[-1]
    assert orden["pago_50_50"] is False


def test_marcar_garantia_no_resucita_el_pago_50_50(cliente_venta, odoo):
    _agregar(cliente_venta, 501)
    cliente_venta.post("/venta/cotizar",
                       data={"cliente": "Marta", "casillas": "1",
                             "con_garantia": "1"},
                       follow_redirects=False)
    vals = list(odoo.ordenes.values())[-1]["vals_crear"]
    assert "pago_50_50" not in vals
    assert vals["con_garantia"] is True


def test_la_pantalla_de_venta_ya_no_tiene_la_casilla_del_50_50(cliente_venta, odoo):
    _agregar(cliente_venta, 501)
    r = cliente_venta.get("/venta/nueva")
    assert 'name="pago_50_50"' not in r.text
    assert "50%" not in r.text and "50/50" not in r.text
    assert 'name="con_garantia"' in r.text
    import re
    caja = re.search(r'<input[^>]*name="con_garantia"[^>]*>', r.text).group(0)
    assert "checked" not in caja


# --- El personalizado: la garantía nace marcada; el 50/50 ya no existe ------

def test_el_personalizado_nace_con_garantia_marcada(cliente_venta, odoo):
    r = cliente_venta.get("/venta/servicio-personalizada")
    assert 'name="pago_50_50"' not in r.text
    assert "50%" not in r.text and "50/50" not in r.text
    import re
    caja = re.search(r'<input[^>]*name="con_garantia"[^>]*>', r.text).group(0)
    assert "checked" in caja
    # Y al crear sin tocar nada (formulario viejo, sin marcador): marcada.
    cliente_venta.post("/venta/servicio-personalizada",
                       data={"cliente": "Ana", "servicios": "1",
                             "servicio_texto": "Arreglo", "servicio_monto": "10"},
                       follow_redirects=False)
    vals = list(odoo.ordenes.values())[-1]["vals_crear"]
    assert "pago_50_50" not in vals
    assert vals["con_garantia"] is True


def test_desmarcar_garantia_en_el_personalizado_la_apaga(cliente_venta, odoo):
    cliente_venta.post("/venta/servicio-personalizada",
                       data={"cliente": "Ana", "servicios": "1",
                             "servicio_texto": "Arreglo", "servicio_monto": "10",
                             "casillas": "1"},
                       follow_redirects=False)
    vals = list(odoo.ordenes.values())[-1]["vals_crear"]
    assert "pago_50_50" not in vals
    assert vals["con_garantia"] is False


# --- Ninguna plantilla de Vender imprime "50%" (red de seguridad) ----------

def test_ninguna_plantilla_de_vender_imprime_el_50_50(cliente_venta, odoo):
    _agregar(cliente_venta, 501)
    pantallas = ("/venta/nueva", "/venta/servicio-personalizada")
    for ruta in pantallas:
        r = cliente_venta.get(ruta)
        assert "50%" not in r.text, f"{ruta} todavía habla del 50%"
        assert "50/50" not in r.text, f"{ruta} todavía habla del 50/50"
        assert "pago_50_50" not in r.text, f"{ruta} todavía manda pago_50_50"


# --- Editar: la garantía queda como se guardó; el 50/50 viejo NO se toca ----

def test_al_editar_la_garantia_vuelve_como_se_guardo(cliente_venta, odoo,
                                                      monkeypatch):
    # El espejo del CRM no es lo que se prueba acá (y el OdooFalso chico
    # no trae ir.model.data ni crm.lead).
    monkeypatch.setattr(cotizaciones, "_oportunidad_espejada",
                        lambda *a, **k: None)
    registro = cotizaciones.crear_personalizada(
        {"id": "genesis", "nombre": "Génesis"}, "Ana", "",
        servicios=[{"texto": "Arreglo", "monto": "10", "descripcion": ""}],
        banderas={"con_garantia": True})
    datos = cotizaciones.cargar_para_editar(registro["n"])
    assert datos["banderas"] == {"con_garantia": True}
    # Guardar la edición con la casilla cambiada la escribe en Odoo.
    cotizaciones.editar_cotizacion(
        registro["n"], [{"texto": "Arreglo", "monto": "12", "descripcion": ""}],
        [], banderas={"con_garantia": False})
    orden = odoo.ordenes[registro["orden_id"]]
    assert orden["con_garantia"] is False
    assert "pago_50_50" not in orden["vals_crear"]
    # Y un guardado SIN casillas (los otros tipos de servicio) no la toca.
    cotizaciones.editar_cotizacion(
        registro["n"], [{"texto": "Arreglo", "monto": "12", "descripcion": ""}],
        [], banderas=None)
    orden = odoo.ordenes[registro["orden_id"]]
    assert orden["con_garantia"] is False


def test_editar_nunca_toca_el_pago_50_50_de_una_orden_vieja(cliente_venta, odoo,
                                                             monkeypatch):
    """Las 21 órdenes reales que nacieron con el 50/50 prendido (antes del
    1/10/2026) no cambian al editarlas: el dueño pidió «solo quítalo para
    que no pase en las próximas». Como editar_cotizacion ya nunca manda
    "pago_50_50", una orden que lo tenga en True se queda en True pase lo
    que pase con su garantía."""
    monkeypatch.setattr(cotizaciones, "_oportunidad_espejada",
                        lambda *a, **k: None)
    registro = cotizaciones.crear_personalizada(
        {"id": "genesis", "nombre": "Génesis"}, "Ana", "",
        servicios=[{"texto": "Arreglo", "monto": "10", "descripcion": ""}],
        banderas={"con_garantia": True})
    # Simula una orden "vieja": el 50/50 quedó prendido desde antes.
    odoo.ordenes[registro["orden_id"]]["pago_50_50"] = True
    cotizaciones.editar_cotizacion(
        registro["n"], [{"texto": "Arreglo", "monto": "20", "descripcion": ""}],
        [], banderas={"con_garantia": False})
    orden = odoo.ordenes[registro["orden_id"]]
    assert orden["pago_50_50"] is True
    assert orden["con_garantia"] is False


# --- La tarjeta de Control no pide abono sin el 50/50 (cot_lead.py, sin
#     tocar ni probar su archivo: solo se confirma el comportamiento) ------

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
    """Una orden vieja que ya tenga pago_50_50=True (nadie la apaga)
    sigue mostrando el abono en la ficha, como siempre."""
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
