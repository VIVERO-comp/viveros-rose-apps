"""Pruebas de la pestaña Crear Venta, con un Odoo simulado.

El simulado reproduce las reglas que importan del real: los precios los pone
el servidor, la factura solo puede crearse si la entrega está validada
(invoice_policy = delivery), y cada paso puede fallar a pedido para probar
los reintentos.
"""

import base64
import xmlrpc.client

import pytest

from app import ventas


class OdooFalso:
    def __init__(self):
        self.productos = {
            501: {"default_code": "PL-ROMERO", "name": "ROMERO", "list_price": 3.5},
            502: {"default_code": "PL-JADE", "name": "JADE", "list_price": 5.25},
        }
        self.partners = {74: "Cliente Local"}
        self.ordenes = {}
        self.pickings = {}
        self.movimientos = {}
        self.facturas = {}
        self.asistentes_pago = {}
        self.pagos = []
        self.siguiente = 1000
        self.fallar_una_vez = None  # (modelo, metodo) que revienta UNA vez

    def _nuevo_id(self):
        self.siguiente += 1
        return self.siguiente

    def ejecutar(self, modelo, metodo, args, kw=None):
        kw = kw or {}
        if self.fallar_una_vez == (modelo, metodo):
            self.fallar_una_vez = None
            raise xmlrpc.client.Fault(1, "Traceback...\n\nodoo dijo que no")
        manejador = getattr(self, (modelo + "_" + metodo).replace(".", "_"))
        return manejador(args, kw)

    # ---- productos ----
    def product_product_search_read(self, args, kw):
        texto = next(c[2].lower() for c in args[0]
                     if isinstance(c, list) and c[0] == "name")
        return [{"id": i, **p} for i, p in self.productos.items()
                if texto in p["name"].lower() or texto in p["default_code"].lower()]

    def product_product_read(self, args, kw):
        if kw.get("fields") == ["image_128"]:
            return [{"id": i, "image_128": base64.b64encode(b"\xff\xd8foto").decode()}
                    for i in args[0]]
        return [{"id": i, **self.productos[i]} for i in args[0] if i in self.productos]

    # ---- partners ----
    def res_partner_search(self, args, kw):
        nombre = args[0][0][2].lower()
        return [i for i, n in self.partners.items() if n.lower() == nombre]

    def res_partner_create(self, args, kw):
        nuevo = self._nuevo_id()
        self.partners[nuevo] = args[0]["name"]
        self.partners_vals = getattr(self, "partners_vals", [])
        self.partners_vals.append(args[0])
        return nuevo

    # ---- órdenes ----
    def sale_order_create(self, args, kw):
        vals = args[0]
        nuevo = self._nuevo_id()
        lineas = [l[2] for l in vals["order_line"]]
        total = sum(l["product_uom_qty"] * self.productos[l["product_id"]]["list_price"]
                    for l in lineas)
        self.ordenes[nuevo] = {
            "name": f"S{nuevo}", "partner_id": vals["partner_id"],
            "tag_ids": vals["tag_ids"], "lineas": lineas,
            "amount_total": round(total, 2), "state": "draft", "invoice_ids": [],
        }
        return nuevo

    def sale_order_read(self, args, kw):
        return [{"id": i, **{c: self.ordenes[i][c] for c in kw["fields"]}} for i in args[0]]

    def sale_order_action_confirm(self, args, kw):
        for orden_id in args[0]:
            orden = self.ordenes[orden_id]
            orden["state"] = "sale"
            picking = self._nuevo_id()
            self.pickings[picking] = {"sale_id": orden_id, "state": "assigned"}
            for linea in orden["lineas"]:
                self.movimientos[self._nuevo_id()] = {
                    "picking_id": picking, "product_uom_qty": linea["product_uom_qty"],
                    "quantity": 0, "picked": False,
                }
        return True

    def sale_order_line_search_read(self, args, kw):
        orden_id = args[0][0][2]
        return [{
            "id": indice,
            "name": self.productos[l["product_id"]]["name"],
            "product_uom_qty": l["product_uom_qty"],
            "price_unit": self.productos[l["product_id"]]["list_price"],
            "price_subtotal": round(
                l["product_uom_qty"]
                * self.productos[l["product_id"]]["list_price"], 2),
        } for indice, l in enumerate(self.ordenes[orden_id]["lineas"], start=1)]

    def sale_order_action_cancel(self, args, kw):
        for orden_id in args[0]:
            self.ordenes[orden_id]["state"] = "cancel"
        return True

    # ---- entrega ----
    def stock_picking_search_read(self, args, kw):
        orden_id = args[0][0][2]
        return [{"id": i, "state": p["state"]} for i, p in self.pickings.items()
                if p["sale_id"] == orden_id and p["state"] not in ("done", "cancel")]

    def stock_move_search_read(self, args, kw):
        picking = args[0][0][2]
        return [{"id": i, "product_uom_qty": m["product_uom_qty"]}
                for i, m in self.movimientos.items() if m["picking_id"] == picking]

    def stock_move_write(self, args, kw):
        for movimiento_id in args[0]:
            self.movimientos[movimiento_id].update(args[1])
        return True

    def stock_picking_button_validate(self, args, kw):
        for picking_id in args[0]:
            pendientes = [m for m in self.movimientos.values()
                          if m["picking_id"] == picking_id and not m["picked"]]
            assert not pendientes, "button_validate sin cantidades marcadas"
            self.pickings[picking_id]["state"] = "done"
        return True

    # ---- factura ----
    def sale_advance_payment_inv_create(self, args, kw):
        return self._nuevo_id()

    def sale_advance_payment_inv_create_invoices(self, args, kw):
        orden_id = kw["context"]["active_ids"][0]
        orden = self.ordenes[orden_id]
        entregado = any(p["sale_id"] == orden_id and p["state"] == "done"
                        for p in self.pickings.values())
        if orden["state"] != "sale" or not entregado:
            raise xmlrpc.client.Fault(1, "...\nThere is nothing to invoice!")
        factura = self._nuevo_id()
        self.facturas[factura] = {
            "name": f"INV/2026/{factura}", "state": "draft",
            "amount_total": orden["amount_total"], "payment_state": "not_paid",
            "lineas": [{
                "name": self.productos[l["product_id"]]["name"],
                "quantity": l["product_uom_qty"],
                "price_unit": self.productos[l["product_id"]]["list_price"],
                "price_subtotal": round(
                    l["product_uom_qty"]
                    * self.productos[l["product_id"]]["list_price"], 2),
            } for l in orden["lineas"]],
        }
        orden["invoice_ids"].append(factura)
        return True

    def account_move_line_search_read(self, args, kw):
        factura_id = args[0][0][2]
        return [{"id": indice, **linea} for indice, linea
                in enumerate(self.facturas[factura_id]["lineas"], start=1)]

    def account_move_read(self, args, kw):
        return [{"id": i, **{c: self.facturas[i][c] for c in kw["fields"]}} for i in args[0]]

    def account_move_action_post(self, args, kw):
        for factura_id in args[0]:
            self.facturas[factura_id]["state"] = "posted"
        return True

    # ---- pago ----
    def account_payment_register_create(self, args, kw):
        asistente = self._nuevo_id()
        self.asistentes_pago[asistente] = {
            "journal_id": args[0]["journal_id"],
            "factura_id": kw["context"]["active_ids"][0],
        }
        return asistente

    def account_payment_register_action_create_payments(self, args, kw):
        datos_pago = self.asistentes_pago[args[0][0]]
        factura = self.facturas[datos_pago["factura_id"]]
        assert factura["state"] == "posted", "pago sobre factura sin publicar"
        factura["payment_state"] = "paid"
        self.pagos.append(datos_pago)
        return True


@pytest.fixture
def odoo(monkeypatch, tmp_path, db_limpia):
    """El Odoo simulado enchufado en la única puerta XML-RPC del módulo."""
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
    return falso


@pytest.fixture
def cliente_venta(cliente, odoo):
    return cliente


def _agregar(cliente_web, producto_id, veces=1):
    for _ in range(veces):
        r = cliente_web.post("/venta/carrito/agregar",
                             data={"producto_id": producto_id, "cantidad": 1},
                             follow_redirects=False)
        assert r.status_code == 303


def test_sin_configurar_muestra_aviso(cliente, monkeypatch):
    monkeypatch.delenv("ODOO_URL", raising=False)
    r = cliente.get("/venta")
    assert r.status_code == 200
    assert "no está configurada" in r.text
    assert "PAGADO Y CONFIRMAR" not in r.text


def test_buscar_muestra_precio_y_foto(cliente_venta):
    r = cliente_venta.get("/venta/nueva?q=romero")
    assert "ROMERO" in r.text and "$3.50" in r.text
    assert "/venta/foto/501" in r.text


def test_buscar_en_vivo_devuelve_json(cliente_venta):
    r = cliente_venta.get("/venta/buscar?q=romero")
    assert r.status_code == 200
    assert r.json()["resultados"] == [
        {"id": 501, "sku": "PL-ROMERO", "nombre": "ROMERO", "precio": "$3.50"}]


def test_agregar_conserva_la_busqueda(cliente_venta):
    r = cliente_venta.post("/venta/carrito/agregar",
                           data={"producto_id": 501, "cantidad": 1, "q": "romero"},
                           follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/venta/nueva?q=romero"


def test_carrito_agrega_edita_y_quita(cliente_venta):
    _agregar(cliente_venta, 501, veces=2)
    r = cliente_venta.get("/venta/nueva")
    assert "$7.00" in r.text  # 2 × 3.50, calculado en el servidor
    cliente_venta.post("/venta/carrito/cantidad",
                       data={"producto_id": 501, "cantidad": 5}, follow_redirects=False)
    assert "$17.50" in cliente_venta.get("/venta/nueva").text
    cliente_venta.post("/venta/carrito/quitar",
                       data={"producto_id": 501}, follow_redirects=False)
    assert "Todavía no has añadido plantas" in cliente_venta.get("/venta/nueva").text


def test_precio_manda_el_de_odoo(cliente_venta, odoo):
    _agregar(cliente_venta, 501)
    odoo.productos[501]["list_price"] = 9.99  # cambió en Odoo tras agregarlo
    r = cliente_venta.post("/venta/cotizar", data={"cliente": ""})
    assert r.status_code == 200
    assert "$9.99" in r.text


def test_cotizar_usa_cliente_local_y_etiqueta(cliente_venta, odoo):
    _agregar(cliente_venta, 501)
    r = cliente_venta.post("/venta/cotizar", data={"cliente": ""})
    assert "Cotización creada" in r.text
    orden = next(iter(odoo.ordenes.values()))
    assert orden["partner_id"] == 74            # Cliente Local
    assert orden["tag_ids"] == [[6, 0, [1]]]    # etiqueta LOCAL
    registro = ventas.ventas_todas()[0]
    assert registro["estado"] == "cotizacion" and registro["cliente"] == "Cliente Local"
    assert ventas.carrito_de("genesis") == ([], 0.0)


def test_cotizar_con_carrito_vacio_avisa(cliente_venta):
    r = cliente_venta.post("/venta/cotizar", data={"cliente": ""}, follow_redirects=False)
    assert r.status_code == 303 and "error=" in r.headers["location"]


def test_cobro_completo_con_yappy(cliente_venta, odoo):
    _agregar(cliente_venta, 501)
    _agregar(cliente_venta, 502)
    r = cliente_venta.post("/venta/pagar", data={"cliente": "María"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/venta/cobrar/")
    n = r.headers["location"].rsplit("/", 1)[1]
    r = cliente_venta.post(f"/venta/cobrar/{n}", data={"metodo": "yappy"})
    assert "Venta cobrada" in r.text
    registro = ventas.obtener_venta(int(n))
    assert registro["estado"] == "pagado" and registro["metodo"] == "yappy"
    assert registro["factura"].startswith("INV/")
    assert odoo.pagos == [{"journal_id": 9, "factura_id": registro["factura_id"]}]
    assert "María" in odoo.partners.values()  # cliente con nombre: partner creado
    # La entrega quedó validada (sin eso Odoo no habría facturado).
    assert all(p["state"] == "done" for p in odoo.pickings.values())


def test_efectivo_usa_su_diario(cliente_venta, odoo):
    _agregar(cliente_venta, 501)
    r = cliente_venta.post("/venta/pagar", data={"cliente": ""}, follow_redirects=False)
    n = r.headers["location"].rsplit("/", 1)[1]
    cliente_venta.post(f"/venta/cobrar/{n}", data={"metodo": "efectivo"})
    assert odoo.pagos[0]["journal_id"] == 10


def test_fallo_a_medias_queda_reflejado_y_reintenta(cliente_venta, odoo):
    _agregar(cliente_venta, 501)
    r = cliente_venta.post("/venta/pagar", data={"cliente": ""}, follow_redirects=False)
    n = int(r.headers["location"].rsplit("/", 1)[1])

    # Revienta al publicar la factura: la venta queda "entregada" con el
    # error guardado, y la factura ya creada espera en borrador.
    odoo.fallar_una_vez = ("account.move", "action_post")
    r = cliente_venta.post(f"/venta/cobrar/{n}", data={"metodo": "yappy"},
                           follow_redirects=False)
    assert r.status_code == 303  # de vuelta a la pantalla de cobro
    registro = ventas.obtener_venta(n)
    assert registro["estado"] == "entregada"
    assert "odoo dijo que no" in registro["ultimo_error"]
    pantalla = cliente_venta.get(f"/venta/cobrar/{n}")
    assert "REINTENTAR" in pantalla.text

    # El reintento retoma desde ahí: publica ESA factura (no crea otra) y paga.
    r = cliente_venta.post(f"/venta/cobrar/{n}", data={"metodo": "yappy"})
    assert "Venta cobrada" in r.text
    registro = ventas.obtener_venta(n)
    assert registro["estado"] == "pagado" and registro["ultimo_error"] is None
    assert len(next(iter(odoo.ordenes.values()))["invoice_ids"]) == 1


def test_historial_muestra_estado_parcial(cliente_venta, odoo):
    _agregar(cliente_venta, 501)
    r = cliente_venta.post("/venta/pagar", data={"cliente": ""}, follow_redirects=False)
    n = int(r.headers["location"].rsplit("/", 1)[1])
    odoo.fallar_una_vez = ("sale.advance.payment.inv", "create_invoices")
    cliente_venta.post(f"/venta/cobrar/{n}", data={"metodo": "efectivo"},
                      follow_redirects=False)
    pagina = cliente_venta.get("/venta")
    assert "factura pendiente" in pagina.text and "Reintentar" in pagina.text


def test_foto_se_cachea_en_disco(cliente_venta, odoo, monkeypatch):
    r = cliente_venta.get("/venta/foto/501")
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
    # Segunda vez: sale del disco aunque Odoo ya no responda.
    monkeypatch.setattr(ventas, "_ejecutar", None)
    assert cliente_venta.get("/venta/foto/501").status_code == 200


def test_celular_va_al_contacto_y_al_registro(cliente_venta, odoo):
    _agregar(cliente_venta, 501)
    r = cliente_venta.post("/venta/pagar",
                           data={"cliente": "María", "celular": "6567-3062"},
                           follow_redirects=False)
    assert r.status_code == 303
    assert odoo.partners_vals[0] == {"name": "María", "customer_rank": 1,
                                     "company_type": "person", "mobile": "6567-3062"}
    assert ventas.ventas_todas()[0]["celular"] == "6567-3062"


def test_borrador_sobrevive_los_reloads(cliente_venta):
    r = cliente_venta.post("/venta/borrador",
                           data={"cliente": "María", "celular": "6567-3062"})
    assert r.status_code == 204
    pagina = cliente_venta.get("/venta/nueva")
    assert 'value="María"' in pagina.text and 'value="6567-3062"' in pagina.text
    # Y se limpia al crear la venta.
    _agregar(cliente_venta, 501)
    cliente_venta.post("/venta/cotizar", data={"cliente": "María", "celular": "6567-3062"})
    assert ventas.borrador_de("genesis") == {"nombre": "", "celular": ""}


def test_cancelar_cotizacion(cliente_venta, odoo):
    _agregar(cliente_venta, 501)
    cliente_venta.post("/venta/cotizar", data={"cliente": ""})
    registro = ventas.ventas_todas()[0]
    r = cliente_venta.post(f"/venta/cancelar/{registro['n']}", follow_redirects=False)
    assert r.status_code == 303 and "error=" not in r.headers["location"]
    assert ventas.obtener_venta(registro["n"])["estado"] == "cancelada"
    assert odoo.ordenes[registro["orden_id"]]["state"] == "cancel"
    pagina = cliente_venta.get("/venta")
    assert "Cancelada" in pagina.text and "Cobrar" not in pagina.text


def test_cancelar_solo_cotizaciones(cliente_venta, odoo):
    _agregar(cliente_venta, 501)
    r = cliente_venta.post("/venta/pagar", data={"cliente": ""}, follow_redirects=False)
    n = r.headers["location"].rsplit("/", 1)[1]
    cliente_venta.post(f"/venta/cobrar/{n}", data={"metodo": "yappy"})
    r = cliente_venta.post(f"/venta/cancelar/{n}", follow_redirects=False)
    assert r.status_code == 303 and "error=" in r.headers["location"]
    assert ventas.obtener_venta(int(n))["estado"] == "pagado"


def test_mandar_factura_solo_con_celular(cliente_venta, odoo):
    _agregar(cliente_venta, 501)
    r = cliente_venta.post("/venta/pagar",
                           data={"cliente": "María", "celular": "6123-4567"},
                           follow_redirects=False)
    n = r.headers["location"].rsplit("/", 1)[1]
    cliente_venta.post(f"/venta/cobrar/{n}", data={"metodo": "yappy"})
    pagina = cliente_venta.get("/venta")
    assert "Mandar factura" in pagina.text
    assert "wa.me/50761234567" in pagina.text
    token = ventas.obtener_venta(int(n))["token"]
    assert token and f"/f/{token}" in pagina.text
    # El PDF nativo sigue, ahora rotulado "Factura".
    assert ">Factura</a>" in pagina.text and "Factura PDF" not in pagina.text


def test_factura_publica_sin_sesion(cliente_venta, odoo):
    _agregar(cliente_venta, 501)
    r = cliente_venta.post("/venta/pagar",
                           data={"cliente": "María", "celular": "6123-4567"},
                           follow_redirects=False)
    n = r.headers["location"].rsplit("/", 1)[1]
    cliente_venta.post(f"/venta/cobrar/{n}", data={"metodo": "yappy"})
    cliente_venta.get("/venta")  # genera el token del enlace
    token = ventas.obtener_venta(int(n))["token"]
    cliente_venta.cookies.clear()  # el cliente final no tiene sesion
    pagina = cliente_venta.get(f"/f/{token}")
    assert pagina.status_code == 200
    assert "ROMERO" in pagina.text and "$3.50" in pagina.text
    assert "Yappy" in pagina.text and "María" in pagina.text
    assert cliente_venta.get("/f/token-falso").status_code == 404


def test_tarjeta_muestra_lo_comprado(cliente_venta, odoo):
    _agregar(cliente_venta, 501)
    _agregar(cliente_venta, 502, veces=3)
    cliente_venta.post("/venta/cotizar", data={"cliente": ""})
    assert ventas.ventas_todas()[0]["resumen"] == "1\u00d7 ROMERO, 3\u00d7 JADE"
    pagina = cliente_venta.get("/venta")
    assert "1\u00d7 ROMERO, 3\u00d7 JADE" in pagina.text


def test_cotizacion_publica_por_whatsapp(cliente_venta, odoo):
    _agregar(cliente_venta, 501)
    cliente_venta.post("/venta/cotizar",
                       data={"cliente": "María", "celular": "6123-4567"})
    pagina = cliente_venta.get("/venta")
    assert "Mandar cotizaci\u00f3n" in pagina.text
    assert "wa.me/50761234567" in pagina.text
    registro = ventas.ventas_todas()[0]
    token = ventas.obtener_venta(registro["n"])["token"]
    cliente_venta.cookies.clear()
    documento = cliente_venta.get(f"/f/{token}")
    assert documento.status_code == 200
    assert "COTIZACI\u00d3N" in documento.text and "ROMERO" in documento.text
    assert "Pendiente" in documento.text and "FACTURA" not in documento.text
