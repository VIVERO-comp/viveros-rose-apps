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
        # Plantillas para la foto de la pantalla de Stock: una con imagen de
        # ficha, una solo con adjunto (foto de referencia) y una sin nada.
        self.plantillas = {
            701: {"default_code": "PL-CON-FICHA",
                  "image_128": base64.b64encode(b"\xff\xd8ficha").decode()},
            702: {"default_code": "PL-CON-ADJUNTO", "image_128": False},
            703: {"default_code": "PL-SIN-NADA", "image_128": False},
        }
        self.adjuntos = {801: {"res_id": 702,
                               "datas": base64.b64encode(b"\x89PNGadjunto").decode()}}
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

    def _precio(self, linea):
        """El precio del renglón, como en Odoo: el escrito a mano
        (price_unit) manda; sin él, el de la lista de precios."""
        if linea.get("price_unit") is not None:
            return linea["price_unit"]
        return self.productos.get(linea.get("product_id"), {}).get("list_price", 0.0)

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

    def product_product_search(self, args, kw):
        """Los productos de los cargos (SV-ENVIO, SV-CARGO-INSTALACION) se
        resuelven por su código y se crean si no están, como en Odoo."""
        codigo = next((c[2] for c in args[0]
                       if isinstance(c, (list, tuple)) and c[0] == "default_code"), None)
        return [i for i, p in self.productos.items()
                if p["default_code"] == codigo]

    def product_product_create(self, args, kw):
        nuevo = self._nuevo_id()
        self.productos[nuevo] = {"default_code": args[0]["default_code"],
                                 "name": args[0]["name"], "list_price": 0.0}
        return nuevo

    def product_product_read(self, args, kw):
        if kw.get("fields") == ["image_128"]:
            return [{"id": i, "image_128": base64.b64encode(b"\xff\xd8foto").decode()}
                    for i in args[0]]
        return [{"id": i, **self.productos[i]} for i in args[0] if i in self.productos]

    def product_template_search_read(self, args, kw):
        sku = args[0][0][2]
        return [{"id": i, "image_128": p["image_128"]}
                for i, p in self.plantillas.items()
                if p["default_code"] == sku][:1]

    def ir_attachment_search_read(self, args, kw):
        res_id = next(c[2] for c in args[0]
                      if isinstance(c, list) and c[0] == "res_id")
        return [{"id": i, "datas": a["datas"]}
                for i, a in self.adjuntos.items() if a["res_id"] == res_id][:1]

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
        # Las lineas display_type (secciones y los parrafos de los cargos)
        # no llevan cantidad ni precio, igual que en el Odoo real.
        total = sum(l["product_uom_qty"] * self._precio(l)
                    for l in lineas if not l.get("display_type"))
        self.ordenes[nuevo] = {
            "name": f"S{nuevo}", "partner_id": vals["partner_id"],
            "tag_ids": vals.get("tag_ids"), "lineas": lineas,
            "client_order_ref": vals.get("client_order_ref"),
            "amount_total": round(total, 2), "state": "draft", "invoice_ids": [],
        }
        return nuevo

    def sale_order_search(self, args, kw):
        """Solo lo que usa la vista previa: buscar SU orden por referencia."""
        ref = next((c[2] for c in args[0] if c[0] == "client_order_ref"), None)
        return [i for i, o in self.ordenes.items()
                if (ref is None or o.get("client_order_ref") == ref)
                and o["state"] == "draft"]

    def sale_order_write(self, args, kw):
        for orden_id in args[0]:
            orden = self.ordenes[orden_id]
            vals = args[1]
            if "order_line" in vals:
                lineas = [l[2] for l in vals["order_line"] if l[0] == 0]
                orden["lineas"] = lineas
                orden["amount_total"] = round(
                    sum(l["product_uom_qty"] * self._precio(l)
                        for l in lineas if not l.get("display_type")), 2)
            for campo in ("partner_id", "client_order_ref"):
                if campo in vals:
                    orden[campo] = vals[campo]
        return True

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
            "name": self.productos.get(l.get("product_id"), {}).get("name", ""),
            "product_uom_qty": l["product_uom_qty"],
            "price_unit": self._precio(l),
            "price_subtotal": round(l["product_uom_qty"] * self._precio(l), 2),
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
                "name": self.productos.get(l.get("product_id"), {}).get("name", ""),
                "quantity": l["product_uom_qty"],
                "price_unit": self._precio(l),
                "price_subtotal": round(l["product_uom_qty"] * self._precio(l), 2),
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
    # Los ids de los productos de cargo se cachean por proceso: cada caso
    # tiene su propio Odoo simulado, así que el caché arranca vacío.
    ventas._cache_cargos.clear()
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


def test_agregar_limpia_la_busqueda_y_ancla_en_plantas(cliente_venta):
    # Pedido del dueño (22/09/2026): al elegir una planta la búsqueda se
    # borra (la q no viaja de vuelta) y el redirect lleva el ancla #plantas
    # para no saltar al tope de la página.
    r = cliente_venta.post("/venta/carrito/agregar",
                           data={"producto_id": 501, "cantidad": 1, "q": "romero"},
                           follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/venta/nueva#plantas"


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


# ---------------------------------------------------------------------------
# Precio escrito a mano en el carrito (pedido del dueño, 23/09/2026: "por
# si acaso le vendo más caro"). Solo esa línea cambia, el resto sigue con
# el precio de Odoo, y la orden se crea con el precio escrito.
# ---------------------------------------------------------------------------

def test_precio_a_mano_manda_en_el_carrito_y_en_la_orden(cliente_venta, odoo):
    _agregar(cliente_venta, 501, veces=2)
    r = cliente_venta.post("/venta/carrito/precio",
                           data={"producto_id": 501, "precio": "9.00"},
                           follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/venta/nueva#plantas"
    pagina = cliente_venta.get("/venta/nueva")
    assert "$18.00" in pagina.text                 # 2 × 9.00, no 2 × 3.50
    assert "↺ Odoo $3.50" in pagina.text           # el de Odoo, a un toque
    cliente_venta.post("/venta/cotizar", data={"cliente": ""})
    orden = next(iter(odoo.ordenes.values()))
    assert orden["lineas"][0]["price_unit"] == 9.0
    assert orden["amount_total"] == 18.0


def test_precio_a_mano_vuelve_al_de_odoo(cliente_venta):
    _agregar(cliente_venta, 501)
    cliente_venta.post("/venta/carrito/precio",
                       data={"producto_id": 501, "precio": "9.00"},
                       follow_redirects=False)
    # El ↺ de la fila: manda odoo=1 y el precio escrito se descarta.
    cliente_venta.post("/venta/carrito/precio",
                       data={"producto_id": 501, "precio": "9.00", "odoo": "1"},
                       follow_redirects=False)
    lineas, total = ventas.carrito_de("genesis")
    assert total == 3.5 and not lineas[0]["precio_editado"]


def test_precio_a_mano_solo_toca_su_linea(cliente_venta, odoo):
    _agregar(cliente_venta, 501)
    _agregar(cliente_venta, 502)
    cliente_venta.post("/venta/carrito/precio",
                       data={"producto_id": 501, "precio": "9"},
                       follow_redirects=False)
    cliente_venta.post("/venta/cotizar", data={"cliente": ""})
    orden = next(iter(odoo.ordenes.values()))
    caro = next(l for l in orden["lineas"] if l["product_id"] == 501)
    normal = next(l for l in orden["lineas"] if l["product_id"] == 502)
    assert caro["price_unit"] == 9.0
    assert "price_unit" not in normal      # ese lo sigue poniendo Odoo
    assert orden["amount_total"] == 14.25  # 9.00 + 5.25


def test_precio_ilegible_no_rompe_el_carrito(cliente_venta):
    _agregar(cliente_venta, 501)
    r = cliente_venta.post("/venta/carrito/precio",
                           data={"producto_id": 501, "precio": "carísimo"},
                           follow_redirects=False)
    assert r.status_code == 303
    lineas, total = ventas.carrito_de("genesis")
    assert total == 3.5 and not lineas[0]["precio_editado"]


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


def test_stock_foto_usa_la_imagen_de_la_ficha(cliente_venta):
    r = cliente_venta.get("/stock/foto/PL-CON-FICHA")
    assert r.status_code == 200 and r.content == b"\xff\xd8ficha"


def test_stock_foto_cae_al_adjunto(cliente_venta):
    # Sin imagen de ficha, sirve la foto de referencia adjunta al producto
    # (los adjuntos son la fuente de fotos que maneja el dueño en Odoo).
    r = cliente_venta.get("/stock/foto/PL-CON-ADJUNTO")
    assert r.status_code == 200 and r.content == b"\x89PNGadjunto"


def test_stock_foto_sin_foto_es_404(cliente_venta):
    # El 404 dispara el onerror de la tarjeta y queda el emoji.
    assert cliente_venta.get("/stock/foto/PL-SIN-NADA").status_code == 404
    assert cliente_venta.get("/stock/foto/PL-NO-EXISTE").status_code == 404


def test_stock_foto_rechaza_sku_invalido(cliente_venta):
    # Nada de rutas raras hacia el caché en disco.
    assert ventas.foto_por_sku("../etc/passwd") is None
    assert ventas.foto_por_sku("") is None


def test_stock_foto_se_cachea_en_disco(cliente_venta, monkeypatch):
    assert cliente_venta.get("/stock/foto/PL-CON-FICHA").status_code == 200
    monkeypatch.setattr(ventas, "_ejecutar", None)
    assert cliente_venta.get("/stock/foto/PL-CON-FICHA").status_code == 200


def test_celular_va_al_contacto_y_al_registro(cliente_venta, odoo):
    _agregar(cliente_venta, 501)
    r = cliente_venta.post("/venta/pagar",
                           data={"cliente": "María", "celular": "6567-3062"},
                           follow_redirects=False)
    assert r.status_code == 303
    # "phone" y no "mobile": este Odoo no tiene el campo mobile en res.partner.
    assert odoo.partners_vals[0] == {"name": "María", "customer_rank": 1,
                                     "company_type": "person", "phone": "6567-3062"}
    assert ventas.ventas_todas()[0]["celular"] == "6567-3062"


def test_datos_opcionales_del_cliente_van_al_contacto(cliente_venta, odoo):
    _agregar(cliente_venta, 501)
    r = cliente_venta.post("/venta/cotizar",
                           data={"cliente": "Ana", "celular": "",
                                 "ruc": "155712345-2-2021", "cedula": "8-123-4567",
                                 "correo": "ana@jardines.com", "direccion": "Vía España"})
    assert "Cotización creada" in r.text
    # El RUC manda en el Tax ID y la cédula queda además en la referencia.
    assert odoo.partners_vals[0] == {
        "name": "Ana", "customer_rank": 1, "company_type": "person",
        "vat": "155712345-2-2021", "ref": "8-123-4567",
        "email": "ana@jardines.com", "street": "Vía España"}


def test_sin_ruc_la_cedula_va_al_tax_id(cliente_venta, odoo):
    _agregar(cliente_venta, 501)
    cliente_venta.post("/venta/cotizar",
                       data={"cliente": "Beto", "cedula": "8-999-1111"})
    assert odoo.partners_vals[0]["vat"] == "8-999-1111"


def test_borrador_guarda_los_datos_opcionales(cliente_venta):
    cliente_venta.post("/venta/borrador",
                       data={"cliente": "Ana", "celular": "",
                             "ruc": "155712345-2-2021", "cedula": "8-123-4567",
                             "correo": "", "direccion": ""})
    pagina = cliente_venta.get("/venta/nueva")
    assert 'value="8-123-4567"' in pagina.text
    assert 'value="155712345-2-2021"' in pagina.text


def test_borrador_sobrevive_los_reloads(cliente_venta):
    r = cliente_venta.post("/venta/borrador",
                           data={"cliente": "María", "celular": "6567-3062"})
    assert r.status_code == 204
    pagina = cliente_venta.get("/venta/nueva")
    assert 'value="María"' in pagina.text and 'value="6567-3062"' in pagina.text
    # Y se limpia al crear la venta.
    _agregar(cliente_venta, 501)
    cliente_venta.post("/venta/cotizar", data={"cliente": "María", "celular": "6567-3062"})
    limpio = ventas.borrador_de("genesis")
    assert limpio["nombre"] == "" and limpio["celular"] == ""
    assert limpio["servicios"] == [] and limpio["renglones"] == []
    # Todos los campos extra (datos de factura, montos y párrafos de los
    # cargos) vuelven a vacío, sean los que sean.
    assert all(limpio[c] == "" for c in ventas.CAMPOS_EXTRA)


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
    # "Mandar cotización" se fue de las ventas locales (dueño, 23/09/2026:
    # "pon facturar y mandar factura y ya"); la ruta pública /f/<token>
    # sigue viva y se manda la FACTURA después de facturar.
    assert "Mandar cotizaci\u00f3n" not in pagina.text
    assert ">Facturar<" in pagina.text
    registro = ventas.ventas_todas()[0]
    token = ventas.obtener_venta(registro["n"])["token"]
    cliente_venta.cookies.clear()
    documento = cliente_venta.get(f"/f/{token}")
    assert documento.status_code == 200
    assert "COTIZACI\u00d3N" in documento.text and "ROMERO" in documento.text
    assert "Pendiente" in documento.text and "FACTURA" not in documento.text


def test_el_pdf_usa_el_nombre_de_la_plantilla_no_el_xml_id(monkeypatch):
    """La URL /report/pdf/... lleva el report_name, no el xml_id de la
    acción: la propuesta de servicio devolvía 404 por eso."""
    llamadas = []

    def falso(modelo, metodo, args, kw=None):
        llamadas.append(modelo)
        if modelo == "ir.model.data":
            return [{"res_id": 566}]
        if modelo == "ir.actions.report":
            return [{"report_name": "vivero_rose_pedidos.plantilla_propuesta_venta"}]
        raise AssertionError(modelo)

    monkeypatch.setattr(ventas, "_ejecutar", falso)
    ventas._cache_plantillas.clear()
    referencia = "vivero_rose_pedidos.reporte_propuesta_venta"
    assert ventas._plantilla_de_reporte(referencia) == \
        "vivero_rose_pedidos.plantilla_propuesta_venta"
    # Queda en caché: no vuelve a preguntarle a Odoo.
    ventas._plantilla_de_reporte(referencia)
    assert llamadas.count("ir.model.data") == 1


def test_un_reporte_que_no_existe_se_usa_tal_cual(monkeypatch):
    monkeypatch.setattr(ventas, "_ejecutar",
                        lambda *a, **k: [])  # sin coincidencia en ir.model.data
    ventas._cache_plantillas.clear()
    assert ventas._plantilla_de_reporte("sale.report_saleorder") == "sale.report_saleorder"


# --- Los cargos opcionales y el producto con que se cobran ------------------

def test_el_cargo_de_instalacion_usa_su_propio_producto(monkeypatch):
    """El cargo de instalación NO se cobra con el SV-INSTALACION de las
    cotizaciones de servicio: ese producto se llama "Instalación,
    transporte y mantenimiento inicial" en Odoo y la FACTURA imprime el
    nombre del producto, así que le prometía al cliente un trabajo que no
    pagó. Con producto propio, cotización y factura dicen "Instalación"."""
    pedidos = []
    monkeypatch.setattr(ventas, "_id_producto_cargo",
                        lambda codigo, nombre: pedidos.append((codigo, nombre)) or 7000)
    lineas = ventas.lineas_de_cargos({"envio": 5, "instalacion": 40})
    assert pedidos == [("SV-ENVIO", "Envío a domicilio"),
                       ("SV-CARGO-INSTALACION", "Instalación y siembra")]
    # El renglón lleva el título y, debajo, su descripción de fábrica como
    # subsección (24/09/2026) — o la que la empleada haya escrito.
    renglones = [l for l in lineas if not l.get("display_type")]
    parrafos = [l["name"] for l in lineas if l.get("display_type") == "line_subsection"]
    assert [l["name"] for l in renglones] == ["Envío a domicilio", "Instalación y siembra"]
    assert [l["price_unit"] for l in renglones] == [5.0, 40.0]
    assert parrafos[0].startswith("Entrega de las plantas")
    assert parrafos[1].startswith("Siembra en sitio")


def test_el_parrafo_escrito_a_mano_le_gana_al_de_fabrica(monkeypatch):
    monkeypatch.setattr(ventas, "_id_producto_cargo", lambda c, n: 7000)
    lineas = ventas.lineas_de_cargos(
        {"mantenimiento": 25, "mantenimiento_desc": "Dos visitas al mes."})
    parrafos = [l["name"] for l in lineas if l.get("display_type") == "line_subsection"]
    assert parrafos == ["Dos visitas al mes."]


def test_una_cotizacion_vieja_sigue_reconociendo_su_cargo():
    """Las cotizaciones hechas antes del cambio llevan el cargo sobre el
    producto viejo: al reabrirlas, el monto tiene que volver a su casilla
    del formulario y no aparecer como un servicio suelto."""
    assert ventas.CODIGOS_CARGO["SV-INSTALACION"] == "instalacion"
    assert ventas.CODIGOS_CARGO["SV-CARGO-INSTALACION"] == "instalacion"
    assert ventas.CODIGOS_CARGO["SV-ENVIO"] == "envio"


# --- Vista previa del PDF antes de generar la cotización (23/09/2026) -------

def _pdf_falso(monkeypatch):
    llamadas = []
    monkeypatch.setattr(ventas, "descargar_pdf",
                        lambda reporte, registro: llamadas.append((reporte, registro))
                        or b"%PDF-1.4 vista previa")
    return llamadas


def test_la_vista_previa_no_crea_la_venta(cliente_venta, odoo, monkeypatch):
    """Ver el PDF antes de generar NO puede cobrar ni comprometer nada: no
    deja registro local, no vacía el carrito y no toca el CRM."""
    _pdf_falso(monkeypatch)
    _agregar(cliente_venta, 501, veces=2)
    r = cliente_venta.post("/venta/vista-previa",
                           data={"cliente": "Marta", "celular": "60000000",
                                 "envio": "5", "instalacion": "40"},
                           follow_redirects=False)
    assert r.status_code == 200, r.headers.get("location")
    assert "Vista previa" in r.text and "SALIR" in r.text
    assert ventas.ventas_todas() == []              # ninguna venta creada
    assert ventas.carrito_de("genesis")[0]          # el carrito sigue lleno
    # Y el borrador guardó lo escrito: salir devuelve el formulario igual.
    borrador = ventas.borrador_de("genesis")
    assert borrador["nombre"] == "Marta" and borrador["envio"] == "5"


def test_la_vista_previa_reusa_una_sola_orden_por_empleada(cliente_venta, odoo, monkeypatch):
    """La orden del vistazo se reescribe, no se acumula: el usuario de la
    app no puede borrar pedidos en Odoo."""
    llamadas = _pdf_falso(monkeypatch)
    _agregar(cliente_venta, 501)
    cliente_venta.post("/venta/vista-previa", data={"cliente": "Marta"})
    cliente_venta.post("/venta/vista-previa", data={"cliente": "Marta", "envio": "5"})
    previas = [i for i, o in odoo.ordenes.items()
               if (o.get("client_order_ref") or "").startswith(ventas.REF_VISTA_PREVIA)]
    assert len(previas) == 1
    # El segundo vistazo ya trae el cargo de envío, con su párrafo gris
    # debajo (la subsección que estrenaron los cargos, 24/09/2026).
    lineas = odoo.ordenes[previas[0]]["lineas"]
    assert len([l for l in lineas if not l.get("display_type")]) == 2
    assert len([l for l in lineas if l.get("display_type") == "line_subsection"]) == 1
    assert [r for r, _ in llamadas] == ["sale.report_saleorder"] * 2


def test_el_pdf_de_la_vista_previa_se_ve_dentro_de_la_pantalla(cliente_venta, odoo, monkeypatch):
    _pdf_falso(monkeypatch)
    _agregar(cliente_venta, 501)
    cliente_venta.post("/venta/vista-previa", data={"cliente": "Marta"})
    r = cliente_venta.get("/venta/vista-previa.pdf")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    # inline, no attachment: una ventana nueva en el celular deja atrapado.
    assert r.headers["content-disposition"].startswith("inline")


def test_sin_carrito_la_vista_previa_avisa(cliente_venta, odoo, monkeypatch):
    _pdf_falso(monkeypatch)
    r = cliente_venta.post("/venta/vista-previa", data={"cliente": "Marta"},
                           follow_redirects=False)
    assert r.status_code == 303
    assert "Agrega%20al%20menos%20una%20planta" in r.headers["location"]
