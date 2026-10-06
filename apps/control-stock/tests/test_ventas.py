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
            501: {"default_code": "PL-ROMERO", "name": "ROMERO",
                  "list_price": 3.5, "sale_ok": True},
            502: {"default_code": "PL-JADE", "name": "JADE",
                  "list_price": 5.25, "sale_ok": True},
            # F2 (6/10/2026): una maceta y un insumo CON ITBMS (el 7% real
            # de producción, id 1, percent, NO incluido en el precio).
            503: {"default_code": "MC-BARRO-M", "name": "MACETA DE BARRO M",
                  "list_price": 29.25, "sale_ok": True, "taxes_id": [1]},
            504: {"default_code": "IN-TIERRA-NEGRA", "name": "TIERRA NEGRA",
                  "list_price": 10.0, "sale_ok": True, "taxes_id": [1]},
        }
        self.impuestos = {
            1: {"amount": 7.0, "amount_type": "percent",
                "price_include": False},
        }
        self.partners = {74: {"name": "Cliente Local"}}
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
        # Desde F2 (6/10/2026) el fake evalúa el dominio DE VERDAD
        # (prefijos, sale_ok, texto): el buscador de Vender (PL-/MC-/IN-)
        # se diferencia del de antes (solo PL-) por el dominio, y un fake
        # que lo ignore daría por probado lo que no probó.
        filas = [{"id": i, **p} for i, p in self.productos.items()
                 if self._evaluar_dominio(args[0], p)]
        filas.sort(key=lambda f: f["name"])
        limite = kw.get("limit")
        return filas[:limite] if limite else filas

    def product_product_search(self, args, kw):
        """Los productos de los cargos (SV-ENVIO, SV-CARGO-INSTALACION) se
        resuelven por su código y se crean si no están, como en Odoo."""
        codigo = next((c[2] for c in args[0]
                       if isinstance(c, (list, tuple)) and c[0] == "default_code"), None)
        return [i for i, p in self.productos.items()
                if p["default_code"] == codigo]

    def account_tax_read(self, args, kw):
        return [{"id": i, **{c: self.impuestos[i].get(c) for c in kw["fields"]}}
                for i in args[0] if i in self.impuestos]

    def _impuesto_de(self, linea):
        """El impuesto de un renglón, como lo calcula Odoo: sale de los
        impuestos del PRODUCTO (la app no manda taxes en la línea),
        porcentual sobre el importe, redondeado por línea."""
        producto = self.productos.get(linea.get("product_id"), {})
        monto = 0.0
        for impuesto_id in producto.get("taxes_id") or []:
            datos = self.impuestos[impuesto_id]
            if datos["amount_type"] == "percent" and not datos["price_include"]:
                monto += (linea["product_uom_qty"] * self._precio(linea)
                          * datos["amount"] / 100.0)
        return round(monto, 2)

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
    # Desde el comodín de la vista previa (5/10/2026) el fake guarda el
    # dict completo y evalúa el dominio de verdad (notación polaca de
    # Odoo, con "|" y "not like"): la exclusión _sin_comodines y la
    # búsqueda del comodín por `comment` no se pueden probar con un fake
    # que ignore las condiciones.
    @staticmethod
    def _evaluar_dominio(dominio, partner):
        def condicion(c):
            campo, op, valor = c
            val, v = str(partner.get(campo) or ""), str(valor)
            if op in ("=", "=="):
                return val == v
            if op == "=ilike":
                return val.lower() == v.lower()
            if op == "ilike":
                return v.lower() in val.lower()
            if op == "like":
                return v in val
            if op == "not like":
                # Como en Odoo: el que no tiene el campo también pasa.
                return v not in val
            return False

        def parcial(i):
            token = dominio[i]
            if token == "|":
                a, i = parcial(i + 1)
                b, i = parcial(i)
                return a or b, i
            if token == "&":
                a, i = parcial(i + 1)
                b, i = parcial(i)
                return a and b, i
            if token == "!":
                a, i = parcial(i + 1)
                return not a, i
            return condicion(token), i + 1

        i, resultado = 0, True
        while i < len(dominio):
            r, i = parcial(i)
            resultado = resultado and r
        return resultado

    def res_partner_search(self, args, kw):
        ids = [i for i, p in self.partners.items()
               if self._evaluar_dominio(args[0], p)]
        limite = kw.get("limit")
        return ids[:limite] if limite else ids

    def res_partner_read(self, args, kw):
        # La puerta única B.2 lee el nombre del cliente que calzó antes de
        # decidir si reusa, avisa o confirma.
        campos = kw.get("fields") or ["name"]
        return [{"id": i, **{c: self.partners[i].get(c) for c in campos}}
                for i in args[0] if i in self.partners]

    def res_partner_create(self, args, kw):
        nuevo = self._nuevo_id()
        self.partners[nuevo] = dict(args[0])
        self.partners_vals = getattr(self, "partners_vals", [])
        self.partners_vals.append(args[0])
        return nuevo

    def res_partner_write(self, args, kw):
        # El comodín de la vista previa se reescribe en cada vistazo.
        self.partners_writes = getattr(self, "partners_writes", [])
        for pid in args[0]:
            self.partners[pid].update(args[1])
            self.partners_writes.append((pid, dict(args[1])))
        return True

    # ---- órdenes ----
    def sale_order_create(self, args, kw):
        vals = args[0]
        nuevo = self._nuevo_id()
        lineas = [l[2] for l in vals["order_line"]]
        # Las lineas display_type (secciones y los parrafos de los cargos)
        # no llevan cantidad ni precio, igual que en el Odoo real.
        con_monto = [l for l in lineas if not l.get("display_type")]
        total = sum(l["product_uom_qty"] * self._precio(l) for l in con_monto)
        # El ITBMS por línea (F2, 6/10/2026), desde los impuestos del
        # producto — las plantas y los servicios van exentos y suman $0.
        impuesto = sum(self._impuesto_de(l) for l in con_monto)
        self.ordenes[nuevo] = {
            "name": f"S{nuevo}", "partner_id": vals["partner_id"],
            "tag_ids": vals.get("tag_ids"), "lineas": lineas,
            "client_order_ref": vals.get("client_order_ref"),
            # lead_ref (28/09/2026): el PP-XXXXX del lead pendiente, cuando
            # lo hay. Campo nuevo de sale.order (addon 19.0.1.55.0).
            "lead_ref": vals.get("lead_ref"),
            "amount_total": round(total + impuesto, 2),
            "amount_untaxed": round(total, 2),
            "state": "draft", "invoice_ids": [],
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
                con_monto = [l for l in lineas if not l.get("display_type")]
                base = round(sum(l["product_uom_qty"] * self._precio(l)
                                 for l in con_monto), 2)
                orden["amount_untaxed"] = base
                orden["amount_total"] = round(
                    base + sum(self._impuesto_de(l) for l in con_monto), 2)
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
        asistente = self._nuevo_id()
        # Desde los items 5-7 el asistente puede venir como anticipo
        # ('fixed'): se guardan los valores para que create_invoices sepa
        # qué factura armar, como en el Odoo real.
        self.asistentes_factura = getattr(self, "asistentes_factura", {})
        self.asistentes_factura[asistente] = dict(args[0])
        return asistente

    def sale_advance_payment_inv_create_invoices(self, args, kw):
        orden_id = kw["context"]["active_ids"][0]
        orden = self.ordenes[orden_id]
        valores = getattr(self, "asistentes_factura", {}).get(
            args[0][0], {"advance_payment_method": "delivered"})
        if valores.get("advance_payment_method") == "fixed":
            # El anticipo factura el monto fijo SIN exigir la entrega — y
            # con UNA sola línea («Anticipo»), como el Odoo real: la
            # factura del pago no lista las plantas.
            if orden["state"] != "sale":
                raise xmlrpc.client.Fault(1, "...\norden sin confirmar")
            monto = round(float(valores.get("fixed_amount") or 0), 2)
            factura = self._nuevo_id()
            self.facturas[factura] = {
                "name": f"INV/2026/{factura}", "state": "draft",
                "amount_total": monto, "payment_state": "not_paid",
                "lineas": [{"name": "Anticipo", "quantity": 1,
                            "price_unit": monto, "price_subtotal": monto}],
            }
            orden["invoice_ids"].append(factura)
            return True
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


def test_boton_venta_visible_en_computadora(cliente_venta):
    """El 28/09/2026 se encontró que el botón "+ Venta" llevaba la clase
    .solo-pc creyendo que era "solo computadora", cuando en realidad esa
    clase queda display:none en TODOS los anchos —solo la reactivan las
    tarjetas de Stock, dentro de su propio media query— así que el botón
    no se veía nunca, en ningún ancho. Ahora usa su propia clase
    (.venta-boton-pc), escondida solo en el media query móvil."""
    r = cliente_venta.get("/venta")
    # Diseño Orquesta (2/10/2026): el botón es la píldora negra de la
    # cabecera (computadora) y el botón de la barra de abajo (teléfono) —
    # los DOS llevan el enlace a /venta/nueva, y ninguno usa .solo-pc.
    assert 'class="vd-nueva" href="/venta/nueva"' in r.text
    assert 'class="vd-btn" href="/venta/nueva"' in r.text
    assert "Nueva venta" in r.text and "NUEVA VENTA" in r.text
    assert "solo-pc" not in r.text


def test_buscar_muestra_precio_y_foto(cliente_venta, con_inventario):
    r = cliente_venta.get("/venta/nueva?q=romero")
    assert "ROMERO" in r.text and "$3.50" in r.text
    assert "/venta/foto/501" in r.text
    # El stock junto al precio (28/09/2026), del mismo inventario que lee
    # Stock: PL-ROMERO tiene 2 disponibles en el inventario falso.
    assert "2 en stock" in r.text


def test_buscar_en_vivo_devuelve_json(cliente_venta, con_inventario):
    r = cliente_venta.get("/venta/buscar?q=romero")
    assert r.status_code == 200
    # "precio_num" (30/09/2026): el buscador de la pantalla de editar arma
    # la fila en el navegador y necesita el número crudo, no solo el
    # "$3.50" ya formateado para mostrar.
    assert r.json()["resultados"] == [
        {"id": 501, "sku": "PL-ROMERO", "nombre": "ROMERO", "precio": "$3.50",
         "precio_num": 3.5, "disponible": 2}]


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


def test_registrar_pago_completo_con_yappy(cliente_venta, odoo):
    """El flujo nuevo (items 5-7, 5/10/2026): «Registrar pago» confirma,
    factura y paga — y la ENTREGA NO SE TOCA. El candado en este sentido:
    pagar jamás valida la salida en Odoo ni escribe el hecho de entrega."""
    _agregar(cliente_venta, 501)
    _agregar(cliente_venta, 502)
    r = cliente_venta.post("/venta/pagar", data={"cliente": "María"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/venta/pago/")
    n = r.headers["location"].rsplit("/", 1)[1]
    r = cliente_venta.post(f"/venta/pago/{n}", data={"metodo": "yappy"})
    assert "Pago registrado" in r.text
    assert "La entrega queda pendiente" in r.text
    registro = ventas.obtener_venta(int(n))
    assert registro["estado"] == "pagado" and registro["metodo"] == "yappy"
    assert registro["factura"].startswith("INV/")
    assert odoo.pagos == [{"journal_id": 9, "factura_id": registro["factura_id"]}]
    assert any(p["name"] == "María" for p in odoo.partners.values())  # cliente con nombre: partner creado
    # EL CANDADO: ninguna salida quedó validada — el pago no entrega.
    assert all(p["state"] != "done" for p in odoo.pickings.values())
    # Y el hecho local quedó: pago confirmado (estado 2), sin entrega.
    from app import venta_estado
    hechos = venta_estado.estado_de("venta", int(n))
    assert hechos["pago_confirmado"] == 1 and hechos["estado"] == 2
    assert hechos["entrega_marcada"] == 0 and hechos["fecha_entrega"] is None
    assert hechos["pago_monto"] == 8.75  # amount_untaxed (3.50 + 5.25)


def test_doble_post_del_mismo_pago_no_duplica_nada(cliente_venta, odoo):
    """Idempotencia (Requerido 4): repetir «Registrar pago» —doble POST
    idéntico, retry— no duplica. Los pasos sellados ya protegen Odoo
    (con estado 'pagado' ningún paso corre: ni otra factura ni otro
    pago), y el hecho local tampoco se acumula dos veces: el sello va
    solo cuando la llamada movió la venta a 'pagado'."""
    from app import venta_estado
    _agregar(cliente_venta, 501)
    _agregar(cliente_venta, 502)
    r = cliente_venta.post("/venta/pagar", data={"cliente": "María"},
                           follow_redirects=False)
    n = int(r.headers["location"].rsplit("/", 1)[1])
    cliente_venta.post(f"/venta/pago/{n}", data={"metodo": "yappy"})
    # El doble POST por la ruta Y el retry directo del módulo.
    cliente_venta.post(f"/venta/pago/{n}", data={"metodo": "yappy"})
    ventas.registrar_pago(n, "yappy", por="Génesis")
    # Odoo: UNA factura y UN pago, clavado.
    assert len(odoo.pagos) == 1
    assert len(next(iter(odoo.ordenes.values()))["invoice_ids"]) == 1
    # venta_estado: el monto NO se acumuló otra vez y hay UN solo hecho.
    hechos = venta_estado.estado_de("venta", n)
    assert hechos["pago_monto"] == 8.75  # no 17.50 ni 26.25
    pagos = [c for c in venta_estado.historial_de("venta", n)
             if c["hecho"] == "pago"]
    assert len(pagos) == 1


def test_la_url_vieja_de_cobrar_sigue_viva_y_hace_lo_nuevo(cliente_venta, odoo):
    """/venta/cobrar no desaparece: el GET redirige a /venta/pago y el
    POST registra el pago — sin entregar, igual que la ruta nueva."""
    _agregar(cliente_venta, 501)
    r = cliente_venta.post("/venta/pagar", data={"cliente": ""}, follow_redirects=False)
    n = r.headers["location"].rsplit("/", 1)[1]
    r = cliente_venta.get(f"/venta/cobrar/{n}", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == f"/venta/pago/{n}"
    r = cliente_venta.post(f"/venta/cobrar/{n}", data={"metodo": "yappy"})
    assert "Pago registrado" in r.text
    assert ventas.obtener_venta(int(n))["estado"] == "pagado"
    assert all(p["state"] != "done" for p in odoo.pickings.values())


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

    # Revienta al publicar la factura: la venta queda "confirmada" (el
    # flujo nuevo ya no pasa por "entregada") con el error guardado, y la
    # factura ya creada espera en borrador.
    odoo.fallar_una_vez = ("account.move", "action_post")
    r = cliente_venta.post(f"/venta/pago/{n}", data={"metodo": "yappy"},
                           follow_redirects=False)
    assert r.status_code == 303  # de vuelta a la pantalla de pago
    registro = ventas.obtener_venta(n)
    assert registro["estado"] == "confirmada"
    assert "odoo dijo que no" in registro["ultimo_error"]
    pantalla = cliente_venta.get(f"/venta/pago/{n}")
    assert "REINTENTAR" in pantalla.text

    # El reintento retoma desde ahí: publica ESA factura (no crea otra) y paga.
    r = cliente_venta.post(f"/venta/pago/{n}", data={"metodo": "yappy"})
    assert "Pago registrado" in r.text
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
    # La tarjeta simple (BLOQUE 32) dice el paso pendiente en su línea
    # contextual; el botón Reintentar vive en el panel que abre.
    pagina = cliente_venta.get("/venta")
    assert "factura pendiente" in pagina.text
    panel = cliente_venta.get(f"/venta?abrir=v{n}")
    assert "Reintentar" in panel.text


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
    # El dato se queda cancelado (tabla local y Odoo) — eso no cambia. Lo
    # que cambió el 30/09/2026 es que la lista de /venta ya no lo PINTA:
    # una cancelada desaparece de la pantalla sin borrarse de nada.
    assert ventas.obtener_venta(registro["n"])["estado"] == "cancelada"
    assert odoo.ordenes[registro["orden_id"]]["state"] == "cancel"
    pagina = cliente_venta.get("/venta")
    assert registro["orden"] not in pagina.text
    assert "Cancelada" not in pagina.text and "Cobrar" not in pagina.text


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
    # BLOQUE 32: Mandar factura y el PDF viven en el panel de la tarjeta.
    pagina = cliente_venta.get(f"/venta?abrir=v{n}")
    assert "Mandar factura" in pagina.text
    assert "wa.me/50761234567" in pagina.text
    token = ventas.obtener_venta(int(n))["token"]
    assert token and f"/f/{token}" in pagina.text
    # El PDF nativo sigue, en el control único del 30/09/2026 — conserva
    # la palabra factura para no confundirse con «Mandar factura».
    assert ">Descargar / Compartir factura</a>" in pagina.text


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
    registro = ventas.ventas_todas()[0]
    assert registro["resumen"] == "1\u00d7 ROMERO, 3\u00d7 JADE"
    # BLOQUE 32: lo comprado se lee en el panel de la tarjeta.
    pagina = cliente_venta.get(f"/venta?abrir=v{registro['n']}")
    assert "1\u00d7 ROMERO, 3\u00d7 JADE" in pagina.text


def test_cotizacion_publica_por_whatsapp(cliente_venta, odoo):
    _agregar(cliente_venta, 501)
    cliente_venta.post("/venta/cotizar",
                       data={"cliente": "María", "celular": "6123-4567"})
    # BLOQUE 32: las acciones viven en el panel de la tarjeta.
    pagina = cliente_venta.get(
        f"/venta?abrir=v{ventas.ventas_todas()[0]['n']}")
    # "Mandar cotización" se fue de las ventas locales (dueño, 23/09/2026:
    # "pon facturar y mandar factura y ya"); la ruta pública /f/<token>
    # sigue viva y se manda la FACTURA después de facturar.
    assert "Mandar cotizaci\u00f3n" not in pagina.text
    # El r\u00f3tulo pas\u00f3 a "Facturar / Pagado" (due\u00f1o, 30/09/2026): ver
    # test_cotizacion_dice_facturar_pagado_y_ofrece_su_pdf abajo.
    assert ">Facturar / Pagado<" in pagina.text
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
    app no puede borrar pedidos en Odoo. Y desde el 5/10/2026 el segundo
    vistazo tampoco pregunta nada: la vista previa no resuelve clientes."""
    llamadas = _pdf_falso(monkeypatch)
    _agregar(cliente_venta, 501)
    cliente_venta.post("/venta/vista-previa", data={"cliente": "Marta"})
    cliente_venta.post("/venta/vista-previa",
                       data={"cliente": "Marta", "envio": "5"})
    previas = [i for i, o in odoo.ordenes.items()
               if (o.get("client_order_ref") or "").startswith(ventas.REF_VISTA_PREVIA)]
    assert len(previas) == 1
    # El segundo vistazo ya trae el cargo de envío, con su párrafo gris
    # debajo (la subsección que estrenaron los cargos, 24/09/2026).
    lineas = odoo.ordenes[previas[0]]["lineas"]
    assert len([l for l in lineas if not l.get("display_type")]) == 2
    assert len([l for l in lineas if l.get("display_type") == "line_subsection"]) == 1
    assert [r for r, _ in llamadas] == ["sale.report_saleorder"] * 2


# --- El partner comodín de la vista previa (bug Nº2, 5/10/2026) --------------
# La vista previa NUNCA crea ni reutiliza un cliente real: trabaja sobre UN
# partner comodín por empleada (marcado en `comment`), al que cada vistazo
# le reescribe lo tecleado. El cliente real nace recién al concretar, por la
# puerta única y con sus avisos.

def _comodines(odoo):
    return {i: p for i, p in odoo.partners.items()
            if ventas.MARCA_COMODIN in str(p.get("comment") or "")}


def test_la_vista_previa_no_crea_ningun_cliente_real(cliente_venta, odoo, monkeypatch):
    _pdf_falso(monkeypatch)
    _agregar(cliente_venta, 501)
    cliente_venta.post("/venta/vista-previa",
                       data={"cliente": "Marta", "celular": "6000-0000"})
    # El único partner nuevo es el comodín, con su marca; ningún "Marta"
    # real quedó en la libreta.
    creados = getattr(odoo, "partners_vals", [])
    assert len(creados) == 1
    assert ventas.MARCA_COMODIN in creados[0]["comment"]
    assert creados[0]["customer_rank"] == 0
    reales = [p for i, p in odoo.partners.items()
              if i != 74 and i not in _comodines(odoo)]
    assert reales == []
    # El comodín carga lo tecleado (eso es lo que imprime el PDF).
    comodin = next(iter(_comodines(odoo).values()))
    assert comodin["name"] == "Marta"
    assert comodin["phone"] == "6000-0000"


def test_dos_vistazos_reusan_el_mismo_comodin(cliente_venta, odoo, monkeypatch):
    _pdf_falso(monkeypatch)
    _agregar(cliente_venta, 501)
    cliente_venta.post("/venta/vista-previa",
                       data={"cliente": "Marta", "celular": "6000-0000"})
    cliente_venta.post("/venta/vista-previa",
                       data={"cliente": "Rosa Díaz", "celular": "6111-2222"})
    # UN solo create (el primer vistazo); el segundo solo reescribió.
    assert len(odoo.partners_vals) == 1
    assert len(_comodines(odoo)) == 1
    comodin_id, comodin = next(iter(_comodines(odoo).items()))
    assert comodin["name"] == "Rosa Díaz"
    assert comodin["phone"] == "6111-2222"
    assert any(pid == comodin_id for pid, _ in odoo.partners_writes)
    # Y la orden fija apunta al comodín: eso es lo que recibe el PDF.
    previa = next(o for o in odoo.ordenes.values()
                  if (o.get("client_order_ref") or "").startswith(ventas.REF_VISTA_PREVIA))
    assert previa["partner_id"] == comodin_id


def test_el_vistazo_siguiente_no_arrastra_datos_del_anterior(cliente_venta, odoo, monkeypatch):
    """Lo no tecleado se VACÍA en el comodín: la cédula de Marta no puede
    salir impresa en el PDF de Rosa."""
    _pdf_falso(monkeypatch)
    _agregar(cliente_venta, 501)
    cliente_venta.post("/venta/vista-previa",
                       data={"cliente": "Marta", "celular": "6000-0000",
                             "cedula": "8-111-2222"})
    comodin = next(iter(_comodines(odoo).values()))
    assert comodin["vat"] == "8-111-2222"
    cliente_venta.post("/venta/vista-previa", data={"cliente": "Rosa Díaz"})
    comodin = next(iter(_comodines(odoo).values()))
    assert comodin["name"] == "Rosa Díaz"
    assert not comodin["vat"] and not comodin["ref"] and not comodin["phone"]


def test_cada_empleada_tiene_su_comodin(odoo, monkeypatch):
    monkeypatch.setattr(ventas, "descargar_pdf", lambda *_a: b"%PDF-1.4")
    a = ventas._partner_vista_previa("genesis", "Marta")
    b = ventas._partner_vista_previa("ruben", "Marta")
    assert a != b
    assert ventas._partner_vista_previa("genesis", "Otra") == a


def test_concretar_despues_del_vistazo_crea_el_cliente_real_por_la_puerta_unica(
        cliente_venta, odoo, monkeypatch):
    """El partner real nace al tocar «Generar cotización», no antes — y la
    búsqueda de la puerta única NO tropieza con el comodín aunque este
    cargue el mismo nombre y teléfono del vistazo."""
    _pdf_falso(monkeypatch)
    _agregar(cliente_venta, 501)
    cliente_venta.post("/venta/vista-previa",
                       data={"cliente": "Marta", "celular": "6000-0000"})
    r = cliente_venta.post("/venta/cotizar",
                           data={"cliente": "Marta", "celular": "6000-0000"},
                           follow_redirects=False)
    assert r.status_code == 200, r.headers.get("location")
    assert "Cotización creada" in r.text
    reales = [p for i, p in odoo.partners.items()
              if i != 74 and i not in _comodines(odoo)]
    assert [p["name"] for p in reales] == ["Marta"]
    assert reales[0]["customer_rank"] == 1


def test_concretar_con_un_homonimo_real_si_pregunta(cliente_venta, odoo, monkeypatch):
    """Los avisos B.2 viven al concretar, intactos: si en Odoo ya existe
    una Marta REAL, «Generar cotización» pregunta (ClienteAjeno) aunque la
    vista previa no haya preguntado nada."""
    _pdf_falso(monkeypatch)
    odoo.partners[88] = {"name": "Marta", "phone": "6999-9999"}
    _agregar(cliente_venta, 501)
    cliente_venta.post("/venta/vista-previa", data={"cliente": "Marta"})
    r = cliente_venta.post("/venta/cotizar", data={"cliente": "Marta"},
                           follow_redirects=False)
    assert r.status_code == 303
    assert "conflicto=88" in r.headers["location"]


def test_la_vista_previa_lleva_el_precio_a_mano(cliente_venta, odoo, monkeypatch):
    """El bug del 28/09/2026: la vista previa armaba sus líneas sin pasar
    por _linea_de_planta y siempre salía con el precio de lista de Odoo,
    aunque la empleada hubiera escrito uno a mano. Con precio editado la
    orden temporal debe llevar price_unit; sin editar, Odoo sigue
    poniendo el suyo."""
    _pdf_falso(monkeypatch)
    _agregar(cliente_venta, 501)
    _agregar(cliente_venta, 502)
    cliente_venta.post("/venta/carrito/precio",
                       data={"producto_id": 501, "precio": "9.00"},
                       follow_redirects=False)
    cliente_venta.post("/venta/vista-previa", data={"cliente": "Marta"})
    previa = next(o for o in odoo.ordenes.values()
                 if (o.get("client_order_ref") or "").startswith(ventas.REF_VISTA_PREVIA))
    editada = next(l for l in previa["lineas"] if l["product_id"] == 501)
    sin_editar = next(l for l in previa["lineas"] if l["product_id"] == 502)
    assert editada["price_unit"] == 9.0
    assert "price_unit" not in sin_editar


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


# --- Las opciones de envío al cotizar (dueño, 28/09/2026) -------------------
# El empleado elige UNA: Carro o Camioneta (pick up), ciudad o fuera, con su
# precio ya puesto —editable— o Personalizado con monto y nota. Los precios
# viven en Ajustes (tabla config), no en el código.

from app import datos as datos_app


def test_precios_de_envio_de_fabrica(db_limpia):
    """Sin nada guardado en Ajustes rigen los de fábrica: carro 10/25,
    camioneta 25/45."""
    assert ventas.precios_envio() == {
        "carro_ciudad": 10.0, "carro_fuera": 25.0,
        "camioneta_ciudad": 25.0, "camioneta_fuera": 45.0}


def test_los_precios_se_editan_en_ajustes_no_en_el_codigo(db_limpia):
    datos_app.fijar_config("envio_precio_carro_ciudad", "12.50")
    assert ventas.precios_envio()["carro_ciudad"] == 12.5
    # Un valor guardado que no sirve (ilegible o cero) no rompe la venta:
    # vuelve el de fábrica.
    datos_app.fijar_config("envio_precio_camioneta_fuera", "no-es-numero")
    datos_app.fijar_config("envio_precio_carro_fuera", "0")
    precios = ventas.precios_envio()
    assert precios["camioneta_fuera"] == 45.0 and precios["carro_fuera"] == 25.0


def test_la_opcion_fija_sale_con_su_precio_pero_el_escrito_manda(db_limpia):
    assert ventas.resolver_envio({"envio_opcion": "carro_ciudad"}) == {
        "envio": 10.0, "envio_opcion": "carro_ciudad", "envio_nota": ""}
    # Si el viaje pasa de lo que cubre, el empleado escribe otro monto.
    assert ventas.resolver_envio(
        {"envio_opcion": "carro_ciudad", "envio": "18"})["envio"] == 18.0


def test_sin_envio_cobra_cero_aunque_el_campo_traiga_algo(db_limpia):
    """«Sin envío» es la opción por defecto y gana sobre un monto olvidado
    en el campo: nadie cobra un envío que no se eligió."""
    assert ventas.resolver_envio({"envio_opcion": "", "envio": "18"}) == {
        "envio": 0.0, "envio_opcion": "", "envio_nota": ""}
    # Un valor inventado en el radio tampoco cobra nada.
    assert ventas.resolver_envio(
        {"envio_opcion": "en-helicoptero", "envio": "18"})["envio"] == 0.0


def test_personalizado_lleva_monto_y_nota(db_limpia):
    """Más de 30 minutos no tiene opción fija: se usa Personalizado y la
    nota sale en la línea («Envío · Chame, 50 min»)."""
    cargos = ventas.resolver_envio({
        "envio_opcion": "personalizado", "envio": "50",
        "envio_nota": "Chame, 50 min"})
    assert cargos == {"envio": 50.0, "envio_opcion": "personalizado",
                      "envio_nota": "Chame, 50 min"}
    assert ventas.nombre_linea_envio(cargos) == "Envío · Chame, 50 min"


def test_un_formulario_viejo_sin_opcion_sigue_mandando_el_monto(db_limpia):
    """Los formularios y borradores de antes del cambio no traen
    envio_opcion: el monto suelto sigue funcionando como siempre."""
    assert ventas.resolver_envio({"envio": "7"}) == {"envio": 7.0}
    assert ventas.nombre_linea_envio({"envio": 7.0}) == "Envío a domicilio"


def test_la_linea_de_odoo_dice_vehiculo_y_zona(db_limpia, monkeypatch):
    monkeypatch.setattr(ventas, "_id_producto_cargo", lambda c, n: 7000)
    cargos = ventas.resolver_envio({"envio_opcion": "camioneta_ciudad"})
    lineas = ventas.lineas_de_cargos(cargos)
    renglones = [l for l in lineas if not l.get("display_type")]
    # En la línea va «Camioneta» a secas: el «(pick up)» es solo del
    # formulario, para que nadie crea que es el cliente recogiendo.
    assert renglones[0]["name"] == "Envío · Camioneta · Ciudad de Panamá"
    assert renglones[0]["price_unit"] == 25.0
    cargos = ventas.resolver_envio({"envio_opcion": "carro_ciudad"})
    lineas = ventas.lineas_de_cargos(cargos)
    assert [l["name"] for l in lineas if not l.get("display_type")] == [
        "Envío · Carro · Ciudad de Panamá"]


def test_la_opcion_vuelve_a_su_casilla_al_editar():
    """Al releer una cotización, el nombre de la línea devuelve la opción;
    lo que no calza (cotizaciones viejas) se edita como Personalizado."""
    for opcion in ventas.OPCIONES_ENVIO:
        nombre = f"Envío · {opcion['vehiculo']} · {opcion['zona']}"
        assert ventas.opcion_de_linea_envio(nombre) == (opcion["clave"], "")
    assert ventas.opcion_de_linea_envio("Envío · Chame, 50 min") == (
        "personalizado", "Chame, 50 min")
    assert ventas.opcion_de_linea_envio("Envío a domicilio") == (
        "personalizado", "")


def test_la_pantalla_ofrece_las_opciones_y_sin_envio_por_defecto(cliente_venta, odoo):
    _agregar(cliente_venta, 501)
    r = cliente_venta.get("/venta/nueva")
    assert r.status_code == 200
    assert "Sin envío" in r.text
    assert "Camioneta (pick up)" in r.text
    assert "$45.00" in r.text          # el precio de camioneta · fuera
    # Sin borrador, «Sin envío» es la marcada.
    import re
    radio_sin = re.search(r'<input[^>]*name="envio_opcion" value=""[^>]*>', r.text)
    assert radio_sin and "checked" in radio_sin.group(0)


def test_la_cotizacion_generada_lleva_el_envio_elegido(cliente_venta, odoo, monkeypatch):
    """De punta a punta por la vista previa: la opción elegida entra a la
    orden de Odoo como su línea, con el precio de Ajustes."""
    _pdf_falso(monkeypatch)
    _agregar(cliente_venta, 501)
    r = cliente_venta.post("/venta/vista-previa",
                           data={"cliente": "Marta",
                                 "envio_opcion": "camioneta_ciudad"},
                           follow_redirects=False)
    assert r.status_code == 200
    previas = [o for o in odoo.ordenes.values()
               if (o.get("client_order_ref") or "").startswith(ventas.REF_VISTA_PREVIA)]
    renglones = [l for l in previas[0]["lineas"] if not l.get("display_type")]
    envio = [l for l in renglones if str(l.get("name", "")).startswith("Envío")]
    assert envio and envio[0]["name"] == "Envío · Camioneta · Ciudad de Panamá"
    assert envio[0]["price_unit"] == 25.0


def test_los_precios_de_envio_se_guardan_desde_ajustes(cliente_venta, monkeypatch):
    monkeypatch.setenv("AJUSTES_ADMINS", "genesis")
    r = cliente_venta.post("/ajustes/envio", data={
        "carro_ciudad": "12", "carro_fuera": "28",
        "camioneta_ciudad": "30", "camioneta_fuera": "55"},
        follow_redirects=False)
    assert r.status_code == 303 and "envio-guardado" in r.headers["location"]
    assert ventas.precios_envio() == {
        "carro_ciudad": 12.0, "carro_fuera": 28.0,
        "camioneta_ciudad": 30.0, "camioneta_fuera": 55.0}
    # Un precio ilegible o en cero no guarda NINGUNO: todo o nada.
    r = cliente_venta.post("/ajustes/envio", data={
        "carro_ciudad": "gratis", "carro_fuera": "28",
        "camioneta_ciudad": "30", "camioneta_fuera": "55"},
        follow_redirects=False)
    assert "envio-invalido" in r.headers["location"]
    assert ventas.precios_envio()["carro_ciudad"] == 12.0


def test_los_precios_de_envio_solo_los_toca_un_admin(cliente_venta, monkeypatch):
    monkeypatch.delenv("AJUSTES_ADMINS", raising=False)
    r = cliente_venta.post("/ajustes/envio", data={
        "carro_ciudad": "1", "carro_fuera": "1",
        "camioneta_ciudad": "1", "camioneta_fuera": "1"},
        follow_redirects=False)
    assert r.status_code == 403
    assert ventas.precios_envio()["carro_ciudad"] == 10.0


# --- El nombre del archivo del PDF (28/09/2026: orden + cliente) -----------
#
# Abraham, en el celular: los botones de PDF abrían el visor en la misma
# pantalla y no había cómo volver. La regla nueva es bajar Y abrir en
# pestaña nueva a la vez (ver tests/test_descarga_pdf.py); esto de acá es
# solo el nombre del archivo que le llega al teléfono.

def test_nombre_de_pdf_con_acentos_y_espacios():
    assert ventas.nombre_de_pdf("S00092", "José Núñez") == "S00092-jose-nunez.pdf"


def test_nombre_de_pdf_con_varios_espacios_y_puntuacion():
    assert ventas.nombre_de_pdf("S00050", "  Salomón   Kortovich, S.A. ") == \
        "S00050-salomon-kortovich-s-a.pdf"


def test_nombre_de_pdf_sin_cliente_es_solo_la_orden():
    assert ventas.nombre_de_pdf("S00092", "") == "S00092.pdf"
    assert ventas.nombre_de_pdf("S00092", None) == "S00092.pdf"


def test_nombre_de_pdf_con_cliente_de_puros_simbolos_cae_a_la_orden():
    # Ni una letra ni un número: el mismo camino que un cliente vacío, sin
    # dejar un nombre con un guion colgando (ej. "S00092-.pdf").
    assert ventas.nombre_de_pdf("S00092", ":") == "S00092.pdf"
    assert ventas.nombre_de_pdf("S00092", "🤍") == "S00092.pdf"
    assert ventas.nombre_de_pdf("S00092", "***") == "S00092.pdf"


def test_la_factura_baja_con_target_blank_y_download(cliente_venta, odoo):
    """El botón «Factura» de una venta pagada: la regla nueva del
    28/09/2026 (revierte la del 22/09/2026) es bajar Y abrir en pestaña
    nueva a la vez, para que la app nunca quede tapada en el celular."""
    _agregar(cliente_venta, 501)
    r = cliente_venta.post("/venta/pagar", data={"cliente": "María"},
                           follow_redirects=False)
    n = r.headers["location"].rsplit("/", 1)[1]
    cliente_venta.post(f"/venta/cobrar/{n}", data={"metodo": "yappy"})
    # BLOQUE 32: el enlace de la factura vive en el panel de la tarjeta.
    pagina = cliente_venta.get(f"/venta?abrir=v{n}").text
    encontrado = False
    for trozo in pagina.split("<a ")[1:]:
        enlace = trozo.split(">")[0]
        if f"/venta/{n}/factura.pdf" in enlace:
            encontrado = True
            assert 'target="_blank"' in enlace
            assert 'rel="noopener"' in enlace
            assert "download" in enlace
    assert encontrado, "no se encontró el enlace de la factura"


def test_la_pantalla_de_exito_tambien_baja_con_target_blank_y_download(
        cliente_venta, odoo):
    """El botón grande de "Pago registrado" (venta_exito.html) usa `pdf_href`:
    misma regla del 28/09/2026 que el enlace de Factura en /venta."""
    _agregar(cliente_venta, 501)
    r = cliente_venta.post("/venta/pagar", data={"cliente": "María"},
                           follow_redirects=False)
    n = r.headers["location"].rsplit("/", 1)[1]
    pagina = cliente_venta.post(f"/venta/cobrar/{n}", data={"metodo": "yappy"}).text
    assert "Pago registrado" in pagina
    encontrado = False
    for trozo in pagina.split("<a ")[1:]:
        enlace = trozo.split(">")[0]
        if f"/venta/{n}/factura.pdf" in enlace:
            encontrado = True
            assert 'target="_blank"' in enlace
            assert 'rel="noopener"' in enlace
            assert "download" in enlace
    assert encontrado, "no se encontró el botón de la factura en la pantalla de éxito"


# --- UN solo control por PDF (30/09/2026): el Compartir aparte se fue ---

def test_la_factura_ya_no_tiene_boton_compartir_aparte(cliente_venta, odoo):
    """UN solo control (Abraham, 30/09/2026): «Descargar / Compartir
    factura» reemplaza el par Factura + Compartir. El botón aparte con
    `data-compartir` ya no existe junto al PDF."""
    _agregar(cliente_venta, 501)
    r = cliente_venta.post("/venta/pagar", data={"cliente": "María"},
                           follow_redirects=False)
    n = r.headers["location"].rsplit("/", 1)[1]
    cliente_venta.post(f"/venta/cobrar/{n}", data={"metodo": "yappy"})
    # BLOQUE 32: el control del PDF vive en el panel de la tarjeta.
    pagina = cliente_venta.get(f"/venta?abrir=v{n}").text
    assert f'data-compartir="/venta/{n}/factura.pdf"' not in pagina
    assert ">Descargar / Compartir factura</a>" in pagina


def test_la_pantalla_de_exito_ya_no_tiene_boton_compartir_aparte(cliente_venta, odoo):
    """UN solo control (Abraham, 30/09/2026): el botón dorado de la
    pantalla de éxito dice «Descargar / Compartir» y el Compartir aparte
    se retiró — el texto lo pone Python (`pdf_texto`)."""
    _agregar(cliente_venta, 501)
    r = cliente_venta.post("/venta/pagar", data={"cliente": "María"},
                           follow_redirects=False)
    n = r.headers["location"].rsplit("/", 1)[1]
    pagina = cliente_venta.post(f"/venta/cobrar/{n}", data={"metodo": "yappy"}).text
    assert "data-compartir" not in pagina
    assert "Descargar / Compartir factura" in pagina


# --- El marcador `data-pdf` (30/09/2026): en iPhone «Descargar» abre la
# hoja nativa. Qué enlace es un PDF lo decide Python: cada plantilla marca
# sus enlaces de descarga de PDF con `data-pdf`, y el nombre del archivo
# viaja en el propio atributo `download` — compartir.js no adivina por la
# URL ni arma nombres.

def test_la_factura_lleva_el_marcador_data_pdf_con_su_nombre(cliente_venta, odoo):
    _agregar(cliente_venta, 501)
    r = cliente_venta.post("/venta/pagar", data={"cliente": "María"},
                           follow_redirects=False)
    n = r.headers["location"].rsplit("/", 1)[1]
    cliente_venta.post(f"/venta/cobrar/{n}", data={"metodo": "yappy"})
    registro = ventas.obtener_venta(int(n))
    esperado = ventas.nombre_de_pdf(
        (registro["factura"] or str(n)).replace("/", "-"), registro["cliente"])
    # BLOQUE 32: el enlace vive en el panel de la tarjeta.
    pagina = cliente_venta.get(f"/venta?abrir=v{n}").text
    encontrado = False
    for trozo in pagina.split("<a ")[1:]:
        enlace = trozo.split(">")[0]
        if f"/venta/{n}/factura.pdf" in enlace:
            encontrado = True
            assert "data-pdf" in enlace
            assert f'download="{esperado}"' in enlace
    assert encontrado, "no se encontró el enlace de la factura"


def test_la_pantalla_de_exito_lleva_el_marcador_data_pdf(cliente_venta, odoo):
    _agregar(cliente_venta, 501)
    r = cliente_venta.post("/venta/pagar", data={"cliente": "María"},
                           follow_redirects=False)
    n = r.headers["location"].rsplit("/", 1)[1]
    pagina = cliente_venta.post(f"/venta/cobrar/{n}", data={"metodo": "yappy"}).text
    registro = ventas.obtener_venta(int(n))
    esperado = ventas.nombre_de_pdf(
        (registro["factura"] or str(n)).replace("/", "-"), registro["cliente"])
    encontrado = False
    for trozo in pagina.split("<a ")[1:]:
        enlace = trozo.split(">")[0]
        if f"/venta/{n}/factura.pdf" in enlace:
            encontrado = True
            assert "data-pdf" in enlace
            assert f'download="{esperado}"' in enlace
    assert encontrado, "no se encontró el botón de la factura en la pantalla de éxito"


def test_el_enlace_de_repuesto_de_la_vista_previa_no_lleva_data_pdf(
        cliente_venta, odoo, monkeypatch):
    """La vista previa es inline A PROPÓSITO (tiene su propio botón de
    salida): su enlace de repuesto no se intercepta en iPhone."""
    _pdf_falso(monkeypatch)
    _agregar(cliente_venta, 501)
    pagina = cliente_venta.post("/venta/vista-previa",
                                data={"cliente": "Marta"}).text
    encontrado = False
    for trozo in pagina.split("<a ")[1:]:
        enlace = trozo.split(">")[0]
        if "vista-previa.pdf" in enlace:
            encontrado = True
            assert "data-pdf" not in enlace
    assert encontrado, "no se encontró el enlace de repuesto de la vista previa"


# --- Facturar/Pagado + el PDF de la cotización, lado a lado (30/09/2026) ---
# Pedido del dueño, literal: "pon compartir al lado de facturar y pon
# facturar/pagado". La cotización local gana el mismo control «Descargar /
# Compartir (PDF)» que ya usan las tarjetas hermanas, sin tocar Python: el
# nombre del archivo (`nombre_cotizacion_pdf`) ya viajaba en el contexto
# para TODAS las ventas, no solo las "vendida".

def test_cotizacion_dice_facturar_pagado_y_ofrece_su_pdf(cliente_venta, odoo):
    _agregar(cliente_venta, 501)
    cliente_venta.post("/venta/cotizar", data={"cliente": "María"})
    registro = ventas.ventas_todas()[0]
    assert registro["estado"] == "cotizacion"
    esperado = ventas.nombre_de_pdf(registro["orden"].replace("/", "-"),
                                    registro["cliente"])
    # BLOQUE 32: Facturar/Pagado y el PDF viven juntos en el panel.
    pagina = cliente_venta.get(f"/venta?abrir=v{registro['n']}").text
    assert ">Facturar / Pagado<" in pagina
    encontrado = False
    for trozo in pagina.split("<a ")[1:]:
        enlace = trozo.split(">")[0]
        if f"/venta/{registro['n']}/cotizacion.pdf" in enlace:
            encontrado = True
            assert 'target="_blank"' in enlace
            assert 'rel="noopener"' in enlace
            assert "data-pdf" in enlace
            assert f'download="{esperado}"' in enlace
            assert esperado  # el nombre no queda vacío
    assert encontrado, "no se encontró el PDF de la cotización junto a Facturar / Pagado"


def test_cotizacion_conserva_cancelar(cliente_venta, odoo):
    """El botón Cancelar sigue vivo, después del PDF nuevo."""
    _agregar(cliente_venta, 501)
    cliente_venta.post("/venta/cotizar", data={"cliente": "María"})
    registro = ventas.ventas_todas()[0]
    # BLOQUE 32: Cancelar vive en el panel de la tarjeta.
    pagina = cliente_venta.get(f"/venta?abrir=v{registro['n']}").text
    assert f'action="/venta/cancelar/{registro["n"]}"' in pagina
    assert ">Cancelar</button>" in pagina


def test_vendida_no_gana_ni_pierde_controles(cliente_venta, odoo):
    """El cambio es solo para "cotizacion": "vendida" sigue con su único
    control «Descargar / Compartir (PDF)», sin Facturar/Pagado ni Cancelar
    duplicados."""
    cliente_venta.post("/venta/carrito/agregar",
                       data={"producto_id": 501, "cantidad": 1})
    cliente_venta.post("/venta/vender", data={"cliente": "Ana", "celular": ""})
    registro = ventas.ventas_todas()[0]
    assert registro["estado"] == "vendida"
    # BLOQUE 32: el único control vive en el panel — el PDF es el botón
    # principal (sin el «(PDF)» entre paréntesis de la tarjeta vieja).
    pagina = cliente_venta.get(f"/venta?abrir=v{registro['n']}").text
    assert pagina.count("Descargar / Compartir PDF") == 1
    assert "Facturar / Pagado" not in pagina
    assert f'action="/venta/cancelar/{registro["n"]}"' not in pagina


def test_pagado_no_gana_ni_pierde_controles(cliente_venta, odoo):
    """"pagado" sigue con «Descargar / Compartir factura» + «Mandar
    factura», sin el PDF de cotización ni Facturar/Pagado de más."""
    _agregar(cliente_venta, 501)
    r = cliente_venta.post("/venta/pagar",
                           data={"cliente": "María", "celular": "6123-4567"},
                           follow_redirects=False)
    n = r.headers["location"].rsplit("/", 1)[1]
    cliente_venta.post(f"/venta/cobrar/{n}", data={"metodo": "yappy"})
    # BLOQUE 32: los controles viven en el panel de la tarjeta.
    pagina = cliente_venta.get(f"/venta?abrir=v{n}").text
    assert ">Descargar / Compartir factura</a>" in pagina
    assert "Facturar / Pagado" not in pagina
    assert f"/venta/{n}/cotizacion.pdf" not in pagina


# ---------------------------------------------------------------------------
# El cierre del bug de la coma (2/10/2026): los parsers de dinero de
# ventas.py entienden la coma como decimal, y el texto o el negativo se
# rechazan — _monto_escrito ya no los vuelve un $0 callado.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("crudo, esperado", [
    ("12,50", 12.5),   # coma decimal: NO debe volverse 1250
    ("12.50", 12.5),
    ("0,50", 0.5),
    ("1000", 1000.0),
])
def test_num_positivo_coma_y_punto(crudo, esperado):
    assert ventas._num_positivo(crudo) == pytest.approx(esperado)


def test_num_positivo_vacio_cae_al_defecto():
    assert ventas._num_positivo("") is None
    assert ventas._num_positivo("", defecto=1.0) == 1.0
    assert ventas._num_positivo(None, defecto=0.0) == 0.0


@pytest.mark.parametrize("crudo", ["abc", "doce", "-1", "-0,50", "-12.50"])
def test_num_positivo_rechaza_texto_y_negativo(crudo):
    assert ventas._num_positivo(crudo) is None
    assert ventas._num_positivo(crudo, permitir_cero=True) is None


def test_num_positivo_cero_solo_si_se_permite():
    assert ventas._num_positivo("0") is None
    assert ventas._num_positivo("0", permitir_cero=True) == 0.0
    assert ventas._num_positivo("0,00", permitir_cero=True) == 0.0


@pytest.mark.parametrize("crudo, esperado", [
    ("12,50", 12.5), ("12.50", 12.5), ("", 0.0),
])
def test_monto_de_envio_coma_punto_y_vacio(crudo, esperado):
    assert ventas._monto_escrito({"envio": crudo}) == pytest.approx(esperado)
    # Sin el campo siquiera: 0, sin cargo.
    assert ventas._monto_escrito({}) == 0.0


@pytest.mark.parametrize("crudo", ["abc", "quince"])
def test_monto_de_envio_ilegible_avisa(crudo):
    """Antes `except ValueError: 0.0` tragaba el texto y la cotización
    salía sin el envío que se quiso cobrar."""
    with pytest.raises(ValueError, match="no se entiende"):
        ventas._monto_escrito({"envio": crudo})


@pytest.mark.parametrize("crudo", ["-5", "-0,50"])
def test_monto_de_envio_negativo_avisa(crudo):
    with pytest.raises(ValueError, match="negativo"):
        ventas._monto_escrito({"envio": crudo})


def test_resolver_envio_con_coma(db_limpia):
    """El personalizado y el formulario viejo entienden «12,50»."""
    assert ventas.resolver_envio(
        {"envio_opcion": "personalizado", "envio": "12,50",
         "envio_nota": "Chame"})["envio"] == 12.5
    assert ventas.resolver_envio({"envio": "12,50"}) == {"envio": 12.5}


def test_resolver_envio_rechaza_lo_ilegible_donde_el_monto_cuenta(db_limpia):
    with pytest.raises(ValueError):
        ventas.resolver_envio({"envio_opcion": "personalizado", "envio": "abc"})
    with pytest.raises(ValueError):
        ventas.resolver_envio({"envio_opcion": "carro_ciudad", "envio": "-5"})
    with pytest.raises(ValueError):
        ventas.resolver_envio({"envio": "abc"})  # formulario viejo


def test_sin_envio_sigue_ignorando_el_campo_aunque_sea_ilegible(db_limpia):
    """Con «Sin envío» el monto no viaja a ninguna parte: un «abc»
    olvidado ahí no bloquea la venta (el contrato documentado: ese campo
    se ignora entero)."""
    assert ventas.resolver_envio({"envio_opcion": "", "envio": "abc"}) == {
        "envio": 0.0, "envio_opcion": "", "envio_nota": ""}


# ---------------------------------------------------------------------------
# Términos en la cotización (item 5 de Jay, 5/10/2026): el default del
# tipo se muestra y se guarda con la venta; el override queda registrado
# con quién, cuándo y qué decía el default.
# ---------------------------------------------------------------------------

def test_el_formulario_muestra_el_termino_default_de_plantas(cliente_venta):
    _agregar(cliente_venta, 501)
    pagina = cliente_venta.get("/venta/nueva").text
    assert "Términos de pago" in pagina
    assert "100% antes de proceder" in pagina


def test_cotizar_guarda_el_termino_default_sin_override(cliente_venta):
    from app import venta_estado
    _agregar(cliente_venta, 501)
    cliente_venta.post("/venta/cotizar", data={"cliente": "", "termino": ""})
    n = ventas.ventas_todas()[0]["n"]
    guardado = venta_estado.termino_de("venta", n)
    assert guardado["termino"] == "100% antes de proceder"
    assert guardado["tipo_venta"] == "plant retail"
    assert venta_estado.overrides_de("venta", n) == []
    # Y la venta nació en el estado 1 con su tipo (conversión por tipo).
    hechos = venta_estado.estado_de("venta", n)
    assert hechos["estado"] == 1 and hechos["tipo_venta"] == "plant retail"


def test_override_del_termino_queda_registrado(cliente_venta):
    from app import venta_estado
    _agregar(cliente_venta, 501)
    cliente_venta.post("/venta/cotizar",
                       data={"cliente": "Ana",
                             "termino": "50% ahora, 50% al entregar"})
    n = ventas.ventas_todas()[0]["n"]
    assert venta_estado.termino_de("venta", n)["termino"] == \
        "50% ahora, 50% al entregar"
    registros = venta_estado.overrides_de("venta", n)
    assert len(registros) == 1
    assert registros[0]["default_que_habia"] == "100% antes de proceder"
    assert registros[0]["por"] == "Génesis"
    assert registros[0]["en"]
def test_agregar_dos_veces_el_mismo_producto_suma_sin_duplicar(db_limpia):
    """Sugerencia del review del Arquitecto (5/10/2026): fija el contrato
    de agregar_al_carrito — repetir el mismo producto SUMA la cantidad en
    su única fila (ON CONFLICT ... DO UPDATE), jamás duplica el renglón."""
    from app import datos as _datos
    ventas.agregar_al_carrito("genesis", 501, 2)
    ventas.agregar_al_carrito("genesis", 501, 3)
    with _datos._db() as con:
        filas = con.execute(
            "SELECT producto_id, cantidad FROM venta_carrito "
            "WHERE usuario='genesis'").fetchall()
    assert [(f["producto_id"], f["cantidad"]) for f in filas] == [(501, 5)]


# ---------------------------------------------------------------------------
# F2 (6/10/2026): Vender cotiza macetas (MC-) e insumos (IN-) CON ITBMS.
# El impuesto viaja POR EL PRODUCTO: la línea va con product_id y sin tocar
# taxes, y Odoo aplica solo los impuestos que el producto ya tiene (el 7%
# en macetas e insumos; las plantas van exentas). Lo que amarran estas
# pruebas: el buscador de Vender trae los tres prefijos, la pantalla
# muestra el ITBMS, y el total de la app CUADRA con el amount_total que
# calcula Odoo — nadie ve $29.25 para que Odoo cobre $31.30.
# ---------------------------------------------------------------------------

def test_buscador_de_vender_trae_maceta_e_insumo(cliente_venta, con_inventario):
    # La maceta sale en la recarga server-rendered de Nueva venta...
    r = cliente_venta.get("/venta/nueva?q=maceta")
    assert "MACETA DE BARRO M" in r.text and "MC-BARRO-M" in r.text
    # ...y el insumo en el buscador en vivo (venta.js), por /venta/buscar.
    r = cliente_venta.get("/venta/buscar?q=tierra")
    nombres = [p["sku"] for p in r.json()["resultados"]]
    assert "IN-TIERRA-NEGRA" in nombres


def test_buscador_de_vender_manda_los_tres_prefijos_a_odoo(cliente_venta, monkeypatch):
    """Como test_buscar_productos_exige_el_prefijo_pl: lo que protege es
    el DOMINIO que viaja a Odoo, no lo que el fake devuelva."""
    dominios = []
    original = ventas._ejecutar

    def espia(modelo, metodo, args, kw=None):
        if modelo == "product.product" and metodo == "search_read":
            dominios.append(args[0])
        return original(modelo, metodo, args, kw)

    monkeypatch.setattr(ventas, "_ejecutar", espia)
    cliente_venta.get("/venta/buscar?q=romero")
    assert dominios
    likes = [d for d in dominios[0]
             if isinstance(d, list) and d[0] == "default_code" and d[1] == "like"]
    assert likes == [["default_code", "like", "PL-"],
                     ["default_code", "like", "MC-"],
                     ["default_code", "like", "IN-"]]
    assert ["sale_ok", "=", True] in dominios[0]   # vendibles, como siempre


def test_buscador_de_editar_sigue_solo_plantas(cliente_venta):
    """La pantalla de editar arma su cuenta en el navegador y no sabe de
    ITBMS: su fetch manda solo_plantas=1 y no se le ofrecen macetas."""
    r = cliente_venta.get("/venta/buscar?q=maceta&solo_plantas=1")
    assert r.json()["resultados"] == []
    # Y el fetch de venta.js de verdad manda la bandera.
    import pathlib
    from app import main as modulo_main
    js = (pathlib.Path(modulo_main.__file__).parent
          / "static" / "venta.js").read_text()
    assert "/venta/buscar?solo_plantas=1&q=" in js


def test_maceta_con_itbms_cuadra_con_odoo(cliente_venta, odoo):
    _agregar(cliente_venta, 503)                      # maceta $29.25 + 7%
    pagina = cliente_venta.get("/venta/nueva").text
    assert "<span>ITBMS</span><b>$2.05</b>" in pagina
    assert '>$31.30</b>' in pagina                    # el total-final, con impuesto
    cliente_venta.post("/venta/cotizar", data={"cliente": ""})
    orden = next(iter(odoo.ordenes.values()))
    assert "tax_id" not in orden["lineas"][0]         # la app NO pisa impuestos
    assert orden["amount_untaxed"] == 29.25
    assert orden["amount_total"] == 31.30             # lo puso Odoo, por el producto
    assert ventas.ventas_todas()[0]["total"] == 31.30  # y la app guarda ESE


def test_insumo_con_itbms_cuadra_con_odoo(cliente_venta, odoo):
    _agregar(cliente_venta, 504, veces=2)             # insumo $10.00 × 2 + 7%
    pagina = cliente_venta.get("/venta/nueva").text
    assert "<span>ITBMS</span><b>$1.40</b>" in pagina
    assert '>$21.40</b>' in pagina
    cliente_venta.post("/venta/cotizar", data={"cliente": ""})
    orden = next(iter(odoo.ordenes.values()))
    assert orden["amount_total"] == 21.40
    assert ventas.ventas_todas()[0]["total"] == 21.40


def test_planta_sin_itbms_sigue_igual(cliente_venta, odoo):
    """El caso viejo, intacto: una planta exenta no estrena renglón de
    ITBMS ni cambia un centavo."""
    _agregar(cliente_venta, 501)
    pagina = cliente_venta.get("/venta/nueva").text
    assert "<span>ITBMS</span>" not in pagina
    assert '>$3.50</b>' in pagina
    cliente_venta.post("/venta/cotizar", data={"cliente": ""})
    orden = next(iter(odoo.ordenes.values()))
    assert orden["amount_total"] == 3.50 == orden["amount_untaxed"]
    assert ventas.ventas_todas()[0]["total"] == 3.50


def test_venta_mezclada_planta_maceta_y_servicio(cliente_venta, odoo):
    """Planta exenta + maceta con 7% + instalación (servicio exento, con
    su producto SV- de impuestos explícitamente vacíos): el ITBMS es SOLO
    el de la maceta y el total cuadra con Odoo."""
    _agregar(cliente_venta, 501)                      # planta  $3.50
    _agregar(cliente_venta, 503)                      # maceta $29.25 (+$2.05)
    cliente_venta.post("/venta/cotizar",
                       data={"cliente": "", "instalacion": "20"})
    orden = next((o for o in odoo.ordenes.values() if o.get("tag_ids")), None)
    assert orden is not None
    assert orden["amount_untaxed"] == 52.75           # 3.50 + 29.25 + 20.00
    assert orden["amount_total"] == 54.80             # + 2.05 de la maceta
    assert ventas.ventas_todas()[0]["total"] == 54.80
    # Y la pantalla lo decía ANTES de cotizar: carrito con ITBMS aparte.
    lineas, total = ventas.carrito_de("genesis")
    assert lineas == [] and total == 0.0              # ya se limpió al cotizar


def test_itbms_tambien_sobre_el_precio_a_mano(cliente_venta, odoo):
    """Si la empleada le escribe el precio a mano a una maceta, Odoo cobra
    el 7% sobre ESE precio — la pantalla también."""
    _agregar(cliente_venta, 503)
    cliente_venta.post("/venta/carrito/precio",
                       data={"producto_id": 503, "precio": "40.00"},
                       follow_redirects=False)
    lineas, total = ventas.carrito_de("genesis")
    assert lineas[0]["itbms"] == 2.80                 # 7% de $40, no de $29.25
    assert ventas.itbms_del_carrito(lineas) == 2.80
    cliente_venta.post("/venta/cotizar", data={"cliente": ""})
    orden = next(iter(odoo.ordenes.values()))
    assert orden["amount_total"] == 42.80
