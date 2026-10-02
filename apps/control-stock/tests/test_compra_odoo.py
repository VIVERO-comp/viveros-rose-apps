"""Compras · Fase 2: la orden de compra en Odoo y la entrada de stock.

Ninguna prueba sale a la red. Linear va en modo muestra (sin
`LINEAR_API_KEY`, igual que el resto de las pruebas de Compras) y Odoo es
un doble de `ventas._ejecutar`, que es la ÚNICA puerta del módulo: con
reemplazar esa función no queda ni un camino que pueda hablar con un Odoo
de verdad.

Lo que se cuida acá es lo que duele si se rompe:

- que la orden se cree con sus líneas y **sin ni un campo de impuesto** —
  los impuestos los decide Odoo con lo que tenga el producto, y el código
  no los toca (ni para ponerlos ni para vaciarlos);
- que una compra sin proveedor de Odoo o sin productos **no se mueva** a
  «Pedido» y lo diga en palabras claras ANTES, no después;
- que si Odoo no contesta la compra **se mueva igual** —Linear manda sobre
  el estado— y quede la marca «falta la orden en Odoo» con su reintento;
- que el reintento **no cree una segunda orden** (la busca por su `origin`,
  y confirma la que quedó en borrador);
- que al recibir se escriba en el movimiento **lo que llegó** y no lo que
  se pidió;
- que **lo dañado no entre al stock** y quede anotado en la app;
- que una recepción parcial deje el resto pendiente y se pueda **repetir**;
- y que ningún redirect nuevo se vaya sin su ancla.
"""

import pytest

from app import compra_odoo, compras, control, linear_leads, ventas

USUARIO = "genesis"


@pytest.fixture(autouse=True)
def muestra_limpia(monkeypatch, db_limpia):
    monkeypatch.delenv("LINEAR_API_KEY", raising=False)
    monkeypatch.delenv("CALENDARIO_ESCRITURA", raising=False)
    monkeypatch.delenv("AJUSTES_ADMINS", raising=False)
    monkeypatch.delenv("LINEAR_ASSIGNEE_ID", raising=False)
    linear_leads.reiniciar_muestra()
    compras.reiniciar_muestra()
    compras.iniciar_tablas()
    compra_odoo.iniciar_tablas()
    control.iniciar_tablas()


@pytest.fixture
def de_dueno(monkeypatch):
    """La sesión de las pruebas es el dueño: mueve todo el tablero."""
    monkeypatch.setenv("AJUSTES_ADMINS", "genesis")


# ---------------------------------------------------------------------------
# El Odoo falso: una base en memoria con purchase.order, sus líneas, su
# entrada de stock y los movimientos. Suficiente para que el camino real del
# módulo corra completo, incluido el pendiente de una recepción parcial.
# ---------------------------------------------------------------------------

class OdooFalso:
    """Lo mínimo de Odoo que esta fase usa, con memoria de lo que se le
    escribió — para poder mirar el input sin adivinarlo.

    Modela la pieza que importa de verdad: al confirmar una orden nace su
    `stock.picking` con un movimiento por línea, y al validar con menos de
    lo pedido **queda un pendiente** (el backorder que Odoo crea solo con
    `skip_backorder`), que es lo que hace que la recepción se pueda
    repetir.
    """

    def __init__(self):
        self.ordenes = {}
        self.lineas = {}
        self.pickings = {}
        self.movimientos = {}
        self.llamadas = []
        self.siguiente = {"orden": 1, "linea": 1, "picking": 1, "mov": 1}
        self.productos = {
            41: ("IN-TIERRA", "Tierra negra"),
            42: ("MC-MACETA", "Maceta barro 30"),
        }
        self.revienta = None      # un texto para que todo falle

    # -- la puerta ---------------------------------------------------------
    def __call__(self, modelo, metodo, args, kw=None):
        self.llamadas.append((modelo, metodo, args, kw or {}))
        if self.revienta:
            raise RuntimeError(self.revienta)
        funcion = getattr(self, f"_{modelo.replace('.', '_')}__{metodo}", None)
        if funcion is None:
            raise AssertionError(f"El Odoo falso no sabe {modelo}.{metodo}")
        return funcion(args, kw or {})

    def escrituras(self, modelo, metodo):
        return [(a, k) for m, me, a, k in self.llamadas
                if m == modelo and me == metodo]

    # -- purchase.order ----------------------------------------------------
    def _purchase_order__create(self, args, _kw):
        valores = args[0]
        oid = self.siguiente["orden"]
        self.siguiente["orden"] += 1
        ids_linea = []
        for _a, _b, linea in valores.get("order_line") or []:
            lid = self.siguiente["linea"]
            self.siguiente["linea"] += 1
            self.lineas[lid] = {
                "id": lid, "orden": oid, "product_id": linea["product_id"],
                "name": linea.get("name") or "",
                "product_qty": float(linea["product_qty"]),
                "qty_received": 0.0,
                "price_unit": float(linea.get("price_unit") or 0.0),
            }
            ids_linea.append(lid)
        self.ordenes[oid] = {
            "id": oid, "name": "P%05d" % oid, "state": "draft",
            "origin": valores.get("origin") or "",
            "partner_id": valores.get("partner_id"),
            "order_line": ids_linea, "picking_ids": [],
            # Ninguna línea mandó impuesto, así que Odoo no le pone nada: es
            # el estado medido del catálogo hoy (ningún producto tiene
            # impuesto de compra y el valor de la compañía está vacío).
            "amount_tax": 0.0,
            "amount_total": sum(self.lineas[l]["product_qty"]
                                * self.lineas[l]["price_unit"]
                                for l in ids_linea),
        }
        return oid

    def _purchase_order__read(self, args, kw):
        campos = kw.get("fields")
        return [self._recorte(self.ordenes[int(i)], campos)
                for i in args[0] if int(i) in self.ordenes]

    def _purchase_order__search_read(self, args, kw):
        dominio = args[0] if args else []
        filas = list(self.ordenes.values())
        for campo, operador, valor in dominio:
            if operador == "=":
                filas = [f for f in filas if f.get(campo) == valor]
            elif operador == "!=":
                filas = [f for f in filas if f.get(campo) != valor]
        return [self._recorte(f, kw.get("fields")) for f in filas]

    def _purchase_order__button_confirm(self, args, _kw):
        for oid in args[0]:
            orden = self.ordenes[int(oid)]
            orden["state"] = "purchase"
            self._nacer_entrada(orden, [
                (self.lineas[l]["product_id"], self.lineas[l]["product_qty"])
                for l in orden["order_line"]])
        return True

    def _nacer_entrada(self, orden, renglones):
        """Lo que Odoo hace solo al confirmar: la entrada de stock con un
        movimiento por renglón. Y lo mismo al crear un pendiente."""
        pid = self.siguiente["picking"]
        self.siguiente["picking"] += 1
        self.pickings[pid] = {"id": pid, "name": "WH/IN/%05d" % pid,
                              "state": "assigned", "orden": orden["id"]}
        for producto, cantidad in renglones:
            mid = self.siguiente["mov"]
            self.siguiente["mov"] += 1
            self.movimientos[mid] = {
                "id": mid, "picking_id": pid, "product_id": producto,
                "product_uom_qty": float(cantidad), "quantity": 0.0,
                "picked": False, "state": "assigned",
            }
        orden["picking_ids"] = list(orden["picking_ids"]) + [pid]
        return pid

    # -- purchase.order.line ----------------------------------------------
    def _purchase_order_line__read(self, args, kw):
        return [self._recorte(self.lineas[int(i)], kw.get("fields"))
                for i in args[0] if int(i) in self.lineas]

    # -- stock.picking -----------------------------------------------------
    def _stock_picking__read(self, args, kw):
        return [self._recorte(self.pickings[int(i)], kw.get("fields"))
                for i in args[0] if int(i) in self.pickings]

    def _stock_picking__button_validate(self, args, kw):
        """Valida con lo escrito en los movimientos y crea el pendiente.

        Es la parte del Odoo real que esta fase apoya entera: lo procesado
        sube `qty_received` de la línea de compra, y lo que no llegó nace de
        nuevo en otra entrada — el «quedan 5 por recibir».
        """
        assert (kw.get("context") or {}).get("skip_backorder") is True, \
            "sin skip_backorder Odoo abre un asistente que nadie puede contestar"
        for pid in args[0]:
            picking = self.pickings[int(pid)]
            orden = self.ordenes[picking["orden"]]
            faltantes = []
            for mov in self._movimientos_de(picking["id"]):
                hecho = float(mov["quantity"])
                for lid in orden["order_line"]:
                    if self.lineas[lid]["product_id"] == mov["product_id"]:
                        self.lineas[lid]["qty_received"] += hecho
                        break
                mov["state"] = "done"
                mov["product_uom_qty"] = hecho
                resto = max(0.0, self._pedido_de(orden, mov["product_id"])
                            - self._recibido_de(orden, mov["product_id"]))
                if resto > 0:
                    faltantes.append((mov["product_id"], resto))
            picking["state"] = "done"
            if faltantes:
                self._nacer_entrada(orden, faltantes)
        return True

    def _pedido_de(self, orden, producto):
        return sum(self.lineas[l]["product_qty"] for l in orden["order_line"]
                   if self.lineas[l]["product_id"] == producto)

    def _recibido_de(self, orden, producto):
        return sum(self.lineas[l]["qty_received"] for l in orden["order_line"]
                   if self.lineas[l]["product_id"] == producto)

    def _movimientos_de(self, picking_id):
        return [m for m in self.movimientos.values()
                if m["picking_id"] == picking_id]

    # -- stock.move --------------------------------------------------------
    def _stock_move__search_read(self, args, kw):
        dominio = args[0] if args else []
        filas = list(self.movimientos.values())
        for campo, operador, valor in dominio:
            if operador == "=":
                filas = [f for f in filas if f.get(campo) == valor]
        filas.sort(key=lambda m: m["id"])
        return [self._recorte(f, kw.get("fields")) for f in filas]

    def _stock_move__write(self, args, _kw):
        for mid in args[0]:
            self.movimientos[int(mid)].update(args[1])
        return True

    # -- product.product (lo usa producto_por_sku) -------------------------
    def _product_product__search_read(self, args, kw):
        sku = None
        for campo, _op, valor in args[0]:
            if campo == "default_code":
                sku = valor
        for pid, (codigo, nombre) in self.productos.items():
            if codigo == sku:
                return [self._recorte(
                    {"id": pid, "default_code": codigo, "name": nombre,
                     "list_price": 1.0}, kw.get("fields"))]
        return []

    # -- res.partner (el selector de proveedores del formulario) ----------
    def _res_partner__search_read(self, _args, _kw):
        return [{"id": 9, "name": "Agroservicios del Istmo",
                 "phone": "6000-0000", "email": "", "city": "Panamá"}]

    # -- ayuda -------------------------------------------------------------
    def _recorte(self, fila, campos):
        salida = {"id": fila["id"]}
        for campo in (campos or fila.keys()):
            if campo == "id":
                continue
            valor = fila.get(campo)
            if campo == "product_id" and valor in self.productos:
                valor = [valor, self.productos[valor][1]]
            salida[campo] = valor
        return salida


@pytest.fixture
def odoo(monkeypatch):
    falso = OdooFalso()
    monkeypatch.setattr(ventas, "configurado", lambda: True)
    monkeypatch.setattr(ventas, "_ejecutar", falso)
    return falso


def _compra_lista(que="50 sacos de tierra", proveedor_id=9, lineas=True,
                  costo=4.25):
    """Una compra de muestra con proveedor de Odoo y dos productos."""
    nueva = compras.crear(que, proveedor_nombre="Agroservicios del Istmo",
                          proveedor_id=proveedor_id, autor="Génesis")
    if lineas:
        compras.agregar_linea(nueva["ref"], producto_id=41, sku="IN-TIERRA",
                              nombre="Tierra negra", cantidad=40, costo=costo)
        compras.agregar_linea(nueva["ref"], producto_id=42, sku="MC-MACETA",
                              nombre="Maceta barro 30", cantidad=10, costo=3.0)
    return nueva["ref"]


# ---------------------------------------------------------------------------
# 1. La orden nace con sus líneas y sin impuestos
# ---------------------------------------------------------------------------

def test_la_orden_nace_con_sus_lineas_y_queda_confirmada(odoo):
    ref = _compra_lista()
    resultado = compra_odoo.asegurar_orden(ref, autor="Génesis")
    assert resultado["ok"] and not resultado["ya_estaba"]
    assert resultado["orden"]["nombre"] == "P00001"
    creada = odoo.ordenes[1]
    assert creada["partner_id"] == 9
    assert creada["state"] == "purchase"      # confirmada, no en borrador
    lineas = [odoo.lineas[l] for l in creada["order_line"]]
    assert [(l["product_id"], l["product_qty"], l["price_unit"])
            for l in lineas] == [(41, 40.0, 4.25), (42, 10.0, 3.0)]
    # Y el número queda guardado en la compra, que es lo que la tarjeta lee.
    compra = compras.uno(ref)
    assert compra["orden_compra_id"] == 1
    assert compra["orden_compra"] == "P00001"


def test_la_orden_no_manda_ni_un_campo_de_impuesto(odoo):
    """Los impuestos los decide Odoo con lo que tenga el producto: el código
    no los pone **ni los vacía**. Vaciar `taxes_id` también sería tocar lo
    fiscal, y eso no se toca (es la misma regla que ya cumple Vender)."""
    compra_odoo.asegurar_orden(_compra_lista(), autor="G")
    (args, _kw), = odoo.escrituras("purchase.order", "create")
    for _a, _b, linea in args[0]["order_line"]:
        for campo in linea:
            assert "tax" not in campo and "impuesto" not in campo, campo
    assert odoo.ordenes[1]["amount_tax"] == 0.0


def test_una_linea_sin_costo_no_manda_precio(odoo):
    """El costo es OPCIONAL y None es «no se sabe»: sin precio no se manda
    `price_unit` y Odoo pone lo que sepa. Un 0 inventado dejaría una orden
    diciendo que el proveedor regala."""
    nueva = compras.crear("Tierra", proveedor_nombre="Agroservicios del Istmo",
                          proveedor_id=9, autor="G")
    compras.agregar_linea(nueva["ref"], producto_id=41, sku="IN-TIERRA",
                          nombre="Tierra negra", cantidad=5)
    compra_odoo.asegurar_orden(nueva["ref"], autor="G")
    (args, _kw), = odoo.escrituras("purchase.order", "create")
    _a, _b, linea = args[0]["order_line"][0]
    assert "price_unit" not in linea
    assert linea["product_qty"] == 5.0


def test_una_linea_sin_id_se_resuelve_por_su_sku(odoo):
    """Una línea se puede haber guardado sin el id de Odoo (no contestó al
    agregarla). El SKU es lo durable, y una línea de compra de Odoo exige
    `product_id`: se le pregunta por el SKU antes de dar la orden por
    imposible."""
    nueva = compras.crear("Tierra", proveedor_nombre="Agroservicios",
                          proveedor_id=9, autor="G")
    compras.agregar_linea(nueva["ref"], producto_id=None, sku="IN-TIERRA",
                          nombre="Tierra negra", cantidad=3)
    assert compra_odoo.asegurar_orden(nueva["ref"], autor="G")["ok"]
    (args, _kw), = odoo.escrituras("purchase.order", "create")
    assert args[0]["order_line"][0][2]["product_id"] == 41


def test_un_producto_que_odoo_no_encuentra_no_deja_una_orden_a_medias(odoo):
    nueva = compras.crear("Mezcla", proveedor_nombre="Agroservicios",
                          proveedor_id=9, autor="G")
    compras.agregar_linea(nueva["ref"], producto_id=41, sku="IN-TIERRA",
                          nombre="Tierra negra", cantidad=2)
    compras.agregar_linea(nueva["ref"], producto_id=None, sku="IN-FANTASMA",
                          nombre="Lo que no existe", cantidad=1)
    resultado = compra_odoo.asegurar_orden(nueva["ref"], autor="G")
    assert not resultado["ok"]
    assert "IN-FANTASMA" in resultado["error"]
    assert odoo.escrituras("purchase.order", "create") == []


# ---------------------------------------------------------------------------
# 2. Sin proveedor o sin líneas rebota, y lo dice antes de mover
# ---------------------------------------------------------------------------

def test_sin_proveedor_de_odoo_no_hay_orden_y_lo_dice_claro(odoo):
    nueva = compras.crear("Flete del camión", proveedor_nombre="Don Pepe",
                          proveedor_id=None, autor="G")
    compras.agregar_linea(nueva["ref"], producto_id=41, sku="IN-TIERRA",
                          nombre="Tierra negra", cantidad=1)
    falta = compra_odoo.falta_para_pedir(nueva["ref"])
    assert "Don Pepe" in falta and "contacto de Odoo" in falta
    assert not compra_odoo.asegurar_orden(nueva["ref"])["ok"]
    assert odoo.escrituras("purchase.order", "create") == []


def test_sin_productos_no_hay_orden_y_lo_dice_claro(odoo):
    ref = _compra_lista(lineas=False)
    falta = compra_odoo.falta_para_pedir(ref)
    assert "ningún producto" in falta
    assert not compra_odoo.asegurar_orden(ref)["ok"]
    assert odoo.escrituras("purchase.order", "create") == []


def test_una_compra_sin_lo_necesario_no_se_mueve_a_pedido(cliente, de_dueno,
                                                          odoo):
    """El mensaje llega ANTES de mover: un dato que falta se arregla, no se
    arrastra. Y la compra se queda donde estaba."""
    ref = _compra_lista(lineas=False)
    respuesta = cliente.post("/compras/estado",
                             data={"ref": ref, "estado": "PEDIDO"},
                             follow_redirects=False)
    assert respuesta.status_code == 303
    assert "error=" in respuesta.headers["location"]
    assert "#" in respuesta.headers["location"]
    assert compras.uno(ref)["estado"] == "POR_PEDIR"


def test_mover_a_pedido_crea_la_orden_desde_la_pantalla(cliente, de_dueno,
                                                        odoo):
    ref = _compra_lista()
    respuesta = cliente.post("/compras/estado",
                             data={"ref": ref, "estado": "PEDIDO"},
                             follow_redirects=False)
    destino = respuesta.headers["location"]
    assert "P00001" in destino and "error=" not in destino
    assert compras.uno(ref)["estado"] == "PEDIDO"
    assert compras.uno(ref)["orden_compra"] == "P00001"


# ---------------------------------------------------------------------------
# 3. Si Odoo falla, la compra se mueve igual y la falta se VE
# ---------------------------------------------------------------------------

def test_si_odoo_no_contesta_la_compra_se_mueve_igual(cliente, de_dueno, odoo):
    """Linear es el tablero y manda sobre el estado. Lo que no puede pasar es
    un issue que diga «pedido» con un Odoo que no se enteró en silencio."""
    ref = _compra_lista()
    odoo.revienta = "Odoo no contesta"
    respuesta = cliente.post("/compras/estado",
                             data={"ref": ref, "estado": "PEDIDO"},
                             follow_redirects=False)
    destino = respuesta.headers["location"]
    assert compras.uno(ref)["estado"] == "PEDIDO"      # se movió igual
    assert "error=" in destino and "#" in destino
    assert not compras.uno(ref)["orden_compra_id"]


def test_la_tarjeta_dice_que_falta_la_orden_y_el_panel_la_ofrece(
        cliente, de_dueno, odoo):
    ref = _compra_lista()
    odoo.revienta = "Odoo no contesta"
    cliente.post("/compras/estado", data={"ref": ref, "estado": "PEDIDO"})
    odoo.revienta = None
    texto = cliente.get("/compras").text
    assert "falta la orden en Odoo" in texto
    panel = cliente.get(f"/compras?abrir={ref}").text
    assert 'action="/compras/orden"' in panel
    assert "Crear la orden en Odoo" in panel


def test_la_marca_de_falta_la_decide_python_y_no_se_guarda(odoo):
    ref = _compra_lista()
    compra = compras.con_lineas(compras.uno(ref))
    assert compra["falta_orden"] is False          # todavía en «Por pedir»
    compras.mover(ref, "PEDIDO", autor="G")
    assert compras.con_lineas(compras.uno(ref))["falta_orden"] is True
    compra_odoo.asegurar_orden(ref, autor="G")
    assert compras.con_lineas(compras.uno(ref))["falta_orden"] is False


def test_una_compra_sin_productos_no_estrena_la_marca(odoo):
    """Una compra anotada SIN productos no tiene orden que hacer —lo que se
    compra está en su título, y eso es un camino que el formulario ofrece a
    propósito—, así que no estrena una marca de falla que nadie puede
    resolver."""
    ref = _compra_lista(lineas=False)
    compras.mover(ref, "ABONADO", autor="G")
    assert compras.con_lineas(compras.uno(ref))["falta_orden"] is False


def test_el_reintento_no_crea_una_segunda_orden(cliente, de_dueno, odoo):
    """La orden lleva en `origin` la referencia de la compra, y antes de
    crear nada se busca si ya existe una con ese origen: si el intento
    anterior la creó y se cortó antes de guardar el número, el reintento la
    ENCUENTRA."""
    ref = _compra_lista()
    compras.mover(ref, "PEDIDO", autor="G")
    assert compra_odoo.asegurar_orden(ref, autor="G")["ok"]
    # Se le borra el número a la compra, como si el guardado se hubiera
    # cortado justo después de que Odoo la creó.
    compras.guardar_orden(ref, None, "")
    segundo = compra_odoo.asegurar_orden(ref, autor="G")
    assert segundo["ok"] and segundo["ya_estaba"] is True
    assert len(odoo.escrituras("purchase.order", "create")) == 1
    assert compras.uno(ref)["orden_compra"] == "P00001"


def test_el_reintento_confirma_la_orden_que_quedo_en_borrador(odoo):
    """El otro corte posible: Odoo creó la orden y no llegó a confirmarla.
    Sin confirmar no hay entrada de stock, así que el reintento confirma en
    vez de dar la orden por hecha."""
    ref = _compra_lista()
    oid = odoo._purchase_order__create([{
        "partner_id": 9, "origin": compra_odoo._origen(ref),
        "order_line": [(0, 0, {"product_id": 41, "name": "Tierra",
                               "product_qty": 40.0})]}], {})
    compras.guardar_orden(ref, oid, "P00001")
    assert odoo.ordenes[oid]["state"] == "draft"
    resultado = compra_odoo.asegurar_orden(ref, autor="G")
    assert resultado["ok"] and resultado["ya_estaba"] is True
    assert odoo.ordenes[oid]["state"] == "purchase"
    # La orden la armó la prueba a mano (como el intento que se cortó): el
    # módulo no creó NINGUNA, solo confirmó la que ya estaba.
    assert odoo.escrituras("purchase.order", "create") == []


def test_una_orden_que_espera_aprobacion_no_se_da_por_hecha(odoo):
    """La doble validación de Odoo deja la orden en «to approve», y ahí
    todavía no tiene entrada de stock. No se fuerza nada: se dice."""
    ref = _compra_lista()

    def sin_confirmar(args, _kw):
        for oid in args[0]:
            odoo.ordenes[int(oid)]["state"] = compra_odoo.ESTADO_POR_APROBAR
        return True

    odoo._purchase_order__button_confirm = sin_confirmar
    resultado = compra_odoo.asegurar_orden(ref, autor="G")
    assert not resultado["ok"]
    assert "aprobación" in resultado["error"]
    # El número SÍ quedó guardado: la orden existe, y el reintento tiene que
    # encontrarla por su id en vez de crear otra.
    assert compras.uno(ref)["orden_compra"] == "P00001"


def test_la_orden_lleva_la_referencia_de_la_compra_en_origin(odoo):
    """`origin` es de dónde salió la orden y la llave del reintento. Se
    verificó antes de usarlo que el `origin` de `purchase.order` no lo lee
    nadie más en los tres repos — el que tiene dueño es el de `sale.order`,
    que el order-api usa justo para esto mismo con los pedidos en línea."""
    ref = _compra_lista()
    compra_odoo.asegurar_orden(ref, autor="G")
    assert odoo.ordenes[1]["origin"] == f"Compra {ref} · Control Viverorose"


def test_un_numero_de_orden_muerto_no_crea_una_orden_vacia(odoo):
    """Si el número guardado ya no está en Odoo se vuelve a empezar — pero
    una compra sin productos no puede terminar creando una orden VACÍA, que
    es basura que alguien tendría que ir a borrar."""
    ref = _compra_lista(lineas=False)
    compras.guardar_orden(ref, 777, "P00777")
    resultado = compra_odoo.asegurar_orden(ref, autor="G")
    assert not resultado["ok"]
    assert "ningún producto" in resultado["error"]
    assert odoo.escrituras("purchase.order", "create") == []


def test_sin_odoo_conectado_lo_dice_y_no_inventa_nada(monkeypatch):
    monkeypatch.setattr(ventas, "configurado", lambda: False)
    ref = _compra_lista()
    resultado = compra_odoo.asegurar_orden(ref, autor="G")
    assert not resultado["ok"]
    assert "Odoo no está conectado" in resultado["error"]


# ---------------------------------------------------------------------------
# 4. Recibir: entra lo que llegó bueno, no lo que se pidió
# ---------------------------------------------------------------------------

def _pedida(odoo):
    """Una compra con su orden ya creada y confirmada en Odoo."""
    ref = _compra_lista()
    compras.mover(ref, "PEDIDO", autor="G")
    assert compra_odoo.asegurar_orden(ref, autor="G")["ok"]
    return ref


def test_la_recepcion_lista_los_renglones_de_la_entrada(odoo):
    ref = _pedida(odoo)
    estado = compra_odoo.recepcion(ref)
    assert estado["ok"] and not estado["cerrada"]
    assert estado["orden"]["nombre"] == "P00001"
    assert [(r["nombre"], r["esperado"], r["pedido"], r["recibido"])
            for r in estado["renglones"]] == [
        ("Tierra negra", 40.0, 40.0, 0.0),
        ("Maceta barro 30", 10.0, 10.0, 0.0)]


def test_al_recibir_se_valida_lo_que_llego_y_no_lo_que_se_pidio(odoo):
    ref = _pedida(odoo)
    renglones = compra_odoo.recepcion(ref)["renglones"]
    tierra, macetas = renglones[0]["movimiento"], renglones[1]["movimiento"]
    resultado = compra_odoo.recibir(
        ref, llegadas={str(tierra): "35", str(macetas): "10"}, autor="Génesis")
    assert resultado["ok"], resultado["error"]
    escritas = {a[0][0]: a[1]["quantity"]
                for a, _k in odoo.escrituras("stock.move", "write")}
    # 35 y no 40: lo que llegó manda sobre lo que se pidió.
    assert escritas[tierra] == 35.0
    assert escritas[macetas] == 10.0
    assert resultado["entraron"] == 45.0


def test_lo_danado_no_entra_al_stock_y_queda_anotado(odoo):
    ref = _pedida(odoo)
    renglones = compra_odoo.recepcion(ref)["renglones"]
    tierra = renglones[0]["movimiento"]
    macetas = renglones[1]["movimiento"]
    resultado = compra_odoo.recibir(
        ref, llegadas={str(tierra): "40", str(macetas): "10"},
        danadas={str(macetas): "3"}, autor="Génesis")
    assert resultado["ok"], resultado["error"]
    escritas = {a[0][0]: a[1]["quantity"]
                for a, _k in odoo.escrituras("stock.move", "write")}
    # Llegaron 10 macetas y 3 venían rotas: al stock entran 7.
    assert escritas[macetas] == 7.0
    assert escritas[tierra] == 40.0
    assert resultado["danadas"] == 3.0
    anotado = compra_odoo.danado_de(ref)
    assert [(d["nombre"], d["cantidad"], d["sku"]) for d in anotado] == [
        ("Maceta barro 30", 3.0, "MC-MACETA")]
    assert anotado[0]["quien"] == "Génesis"
    assert "dañadas no entraron" in resultado["aviso"]


def test_mas_danadas_que_llegadas_rebota_sin_tocar_odoo(odoo):
    ref = _pedida(odoo)
    tierra = compra_odoo.recepcion(ref)["renglones"][0]["movimiento"]
    resultado = compra_odoo.recibir(ref, llegadas={str(tierra): "5"},
                                    danadas={str(tierra): "9"})
    assert not resultado["ok"]
    assert "Tierra negra" in resultado["error"]
    assert odoo.escrituras("stock.move", "write") == []
    assert compra_odoo.danado_de(ref) == []


def test_recibir_con_todo_en_cero_rebota(odoo):
    """Validar una entrada con todo en cero la cerraría sin que entrara
    nada, y el pendiente quedaría igual."""
    ref = _pedida(odoo)
    resultado = compra_odoo.recibir(ref, llegadas={})
    assert not resultado["ok"]
    assert "No anotaste nada que llegó" in resultado["error"]
    assert odoo.escrituras("stock.picking", "button_validate") == []


def test_una_entrada_que_odoo_no_valido_no_se_da_por_hecha(odoo):
    """El silencio peligroso: si `button_validate` devolviera su asistente
    en vez de validar, la respuesta se tragaría como «acción de ventana» y
    la pantalla diría que el stock subió cuando no subió. Se relee el
    estado del picking, y eso lo convierte en un error que se ve."""
    ref = _pedida(odoo)
    tierra = compra_odoo.recepcion(ref)["renglones"][0]["movimiento"]
    odoo._stock_picking__button_validate = lambda _a, _k: True   # no hace nada
    resultado = compra_odoo.recibir(ref, llegadas={str(tierra): "40"},
                                    danadas={str(tierra): "1"}, autor="G")
    assert not resultado["ok"]
    assert "el stock NO subió" in resultado["error"]
    assert compra_odoo.danado_de(ref) == []


def test_lo_danado_no_se_anota_si_odoo_rechaza_la_entrada(odoo):
    """Si la entrada no se pudo validar, nada llegó: anotar roturas de una
    recepción que no pasó dejaría un número que nadie puede explicar."""
    ref = _pedida(odoo)
    tierra = compra_odoo.recepcion(ref)["renglones"][0]["movimiento"]

    def revienta(_args, _kw):
        raise RuntimeError("el almacén está cerrado")

    odoo._stock_picking__button_validate = revienta
    resultado = compra_odoo.recibir(ref, llegadas={str(tierra): "10"},
                                    danadas={str(tierra): "2"})
    assert not resultado["ok"]
    assert "el almacén está cerrado" in resultado["error"]
    assert compra_odoo.danado_de(ref) == []


# ---------------------------------------------------------------------------
# 5. La recepción parcial deja el resto pendiente y se repite
# ---------------------------------------------------------------------------

def test_una_recepcion_parcial_deja_el_resto_pendiente_y_se_repite(odoo):
    """Se pidieron 40 y llegaron 35: la orden queda con 5 por recibir, que es
    como Odoo ya lo maneja. No se cierra nada a la fuerza."""
    ref = _pedida(odoo)
    primeros = compra_odoo.recepcion(ref)["renglones"]
    tierra, macetas = primeros[0]["movimiento"], primeros[1]["movimiento"]
    primera = compra_odoo.recibir(
        ref, llegadas={str(tierra): "35", str(macetas): "10"}, autor="G")
    assert primera["ok"] and primera["pendiente"] == 5.0
    assert "quedan 5 por recibir" in primera["aviso"]

    # La semana que viene llega el resto: la misma pantalla sirve otra vez.
    segunda_lectura = compra_odoo.recepcion(ref)
    assert segunda_lectura["ok"] and not segunda_lectura["cerrada"]
    assert [(r["nombre"], r["esperado"], r["recibido"])
            for r in segunda_lectura["renglones"]] == [
        ("Tierra negra", 5.0, 35.0)]
    resto = segunda_lectura["renglones"][0]["movimiento"]
    segunda = compra_odoo.recibir(ref, llegadas={str(resto): "5"}, autor="G")
    assert segunda["ok"] and segunda["pendiente"] == 0.0
    assert "no queda nada por recibir" in segunda["aviso"]
    assert compra_odoo.recepcion(ref)["cerrada"] is True


def test_una_orden_ya_recibida_no_se_puede_recibir_de_nuevo(odoo):
    ref = _pedida(odoo)
    renglones = compra_odoo.recepcion(ref)["renglones"]
    compra_odoo.recibir(
        ref, llegadas={str(renglones[0]["movimiento"]): "40",
                       str(renglones[1]["movimiento"]): "10"}, autor="G")
    assert compra_odoo.recepcion(ref)["cerrada"] is True
    resultado = compra_odoo.recibir(ref, llegadas={"1": "5"})
    assert not resultado["ok"]
    assert "ya no tiene nada por recibir" in resultado["error"]


def test_lo_danado_se_acumula_entre_recepciones(odoo):
    """Llega una parte hoy con tres macetas rotas y el resto la semana que
    viene con dos más: las dos quedan. Machacar la fila anterior borraría la
    primera rotura."""
    ref = _pedida(odoo)
    renglones = compra_odoo.recepcion(ref)["renglones"]
    compra_odoo.recibir(
        ref, llegadas={str(renglones[0]["movimiento"]): "40",
                       str(renglones[1]["movimiento"]): "6"},
        danadas={str(renglones[1]["movimiento"]): "3"}, autor="Mary")
    segundos = compra_odoo.recepcion(ref)["renglones"]
    compra_odoo.recibir(ref, llegadas={str(segundos[0]["movimiento"]): "4"},
                        danadas={str(segundos[0]["movimiento"]): "2"},
                        autor="Rubén")
    assert [(d["cantidad"], d["quien"]) for d in compra_odoo.danado_de(ref)] \
        == [(3.0, "Mary"), (2.0, "Rubén")]
    assert compra_odoo.total_danado(ref) == 5.0


def test_sin_orden_no_hay_nada_que_recibir_y_lo_dice(odoo):
    ref = _compra_lista()
    estado = compra_odoo.recepcion(ref)
    assert not estado["ok"]
    assert "todavía no tiene orden de compra" in estado["error"]


def test_si_odoo_no_contesta_la_recepcion_lo_dice_y_no_es_un_500(odoo):
    ref = _pedida(odoo)
    odoo.revienta = "Odoo no contesta"
    estado = compra_odoo.recepcion(ref)
    assert not estado["ok"] and estado["renglones"] == []
    assert "Odoo no contesta" in estado["error"]
    # Y una lista vacía NO se lee como «no hay nada que recibir».
    assert estado["cerrada"] is False


# ---------------------------------------------------------------------------
# 6. Las pantallas
# ---------------------------------------------------------------------------

def test_mover_a_recibido_lleva_a_la_pantalla_de_recibir(cliente, de_dueno,
                                                         odoo):
    ref = _pedida(odoo)
    respuesta = cliente.post("/compras/estado",
                             data={"ref": ref, "estado": "RECIBIDO"},
                             follow_redirects=False)
    destino = respuesta.headers["location"]
    assert destino.startswith("/compras/recibir?ref=")
    assert destino.endswith(compras.ANCLA_RECIBIR)
    assert compras.uno(ref)["estado"] == "RECIBIDO"


def test_la_pantalla_de_recibir_pinta_los_renglones(cliente, de_dueno, odoo):
    ref = _pedida(odoo)
    texto = cliente.get(f"/compras/recibir?ref={ref}").text
    assert 'id="cp-recibir"' in texto
    assert "Tierra negra" in texto and "Maceta barro 30" in texto
    assert 'name="llego-' in texto and 'name="roto-' in texto
    # Sin un archivo de JavaScript nuevo: los dos de siempre y nada más.
    assert texto.count("<script") == 2
    assert "menu.js" in texto and "calendario.js" in texto


def test_recibir_desde_la_pantalla_sube_el_stock_y_vuelve_a_la_tarjeta(
        cliente, de_dueno, odoo):
    ref = _pedida(odoo)
    renglones = compra_odoo.recepcion(ref)["renglones"]
    respuesta = cliente.post("/compras/recibir", data={
        "ref": ref,
        f"llego-{renglones[0]['movimiento']}": "40",
        f"llego-{renglones[1]['movimiento']}": "8",
        f"roto-{renglones[1]['movimiento']}": "2",
    }, follow_redirects=False)
    destino = respuesta.headers["location"]
    assert respuesta.status_code == 303
    assert destino.endswith(f"#c-{ref}")
    assert "aviso=" in destino
    escritas = {a[0][0]: a[1]["quantity"]
                for a, _k in odoo.escrituras("stock.move", "write")}
    assert escritas[renglones[1]["movimiento"]] == 6.0   # 8 − 2 dañadas
    assert compra_odoo.total_danado(ref) == 2.0


def test_un_error_de_un_campo_repinta_la_pantalla_sin_redirect(cliente,
                                                               de_dueno, odoo):
    """Regla 5: el error de un campo (acá «más dañadas que llegadas») NO
    redirige — repinta la misma pantalla con lo tecleado y el mensaje
    debajo del campo que falló."""
    ref = _pedida(odoo)
    tierra = compra_odoo.recepcion(ref)["renglones"][0]["movimiento"]
    respuesta = cliente.post("/compras/recibir", data={
        "ref": ref, f"llego-{tierra}": "2", f"roto-{tierra}": "7",
    }, follow_redirects=False)
    assert respuesta.status_code == 200
    texto = respuesta.text
    # Lo tecleado sigue en pantalla, y el error salió debajo del campo.
    assert 'value="2"' in texto and 'value="7"' in texto
    assert "error-campo" in texto
    assert f'name="roto-{tierra}"' in texto
    # Sin banner genérico arriba: el error es de UN campo.
    assert "Ups." not in texto


def test_un_llego_ilegible_rebota_con_su_campo_y_sin_tocar_odoo(odoo):
    """El dedo clásico: «1O» (con la letra o) en vez de «10». Antes se
    volvía 0 en silencio y el renglón quedaba pendiente sin que nadie lo
    pidiera; ahora se rechaza diciendo renglón y campo."""
    ref = _pedida(odoo)
    tierra = compra_odoo.recepcion(ref)["renglones"][0]["movimiento"]
    resultado = compra_odoo.recibir(ref, llegadas={str(tierra): "1O"})
    assert not resultado["ok"]
    assert "Tierra negra" in resultado["error"]
    assert "1O" in resultado["error"]
    assert resultado["campo"] == f"llego-{tierra}"
    assert odoo.escrituras("stock.move", "write") == []


def test_unas_danadas_ilegibles_no_entran_al_stock_como_buenas(odoo):
    """Si «Dañadas» no se entiende, antes caía a 0 y las rotas entraban al
    stock como buenas. Ahora se rechaza, sin escribirle nada a Odoo."""
    ref = _pedida(odoo)
    renglones = compra_odoo.recepcion(ref)["renglones"]
    tierra, macetas = renglones[0]["movimiento"], renglones[1]["movimiento"]
    resultado = compra_odoo.recibir(
        ref, llegadas={str(tierra): "40", str(macetas): "10"},
        danadas={str(macetas): "3x"})
    assert not resultado["ok"]
    assert resultado["campo"] == f"roto-{macetas}"
    assert odoo.escrituras("stock.move", "write") == []
    assert compra_odoo.danado_de(ref) == []


def test_un_negativo_tampoco_se_vuelve_cero(odoo):
    ref = _pedida(odoo)
    tierra = compra_odoo.recepcion(ref)["renglones"][0]["movimiento"]
    resultado = compra_odoo.recibir(ref, llegadas={str(tierra): "-5"})
    assert not resultado["ok"]
    assert "negativo" in resultado["error"]
    assert resultado["campo"] == f"llego-{tierra}"
    assert odoo.escrituras("stock.move", "write") == []


def test_el_vacio_sigue_valiendo_cero(odoo):
    """Vacío NO es ilegible: dejar un renglón en blanco sigue siendo «de
    este no llegó nada», que queda pendiente como siempre."""
    ref = _pedida(odoo)
    renglones = compra_odoo.recepcion(ref)["renglones"]
    tierra, macetas = renglones[0]["movimiento"], renglones[1]["movimiento"]
    resultado = compra_odoo.recibir(
        ref, llegadas={str(tierra): "40", str(macetas): ""})
    assert resultado["ok"], resultado["error"]
    escritas = {a[0][0]: a[1]["quantity"]
                for a, _k in odoo.escrituras("stock.move", "write")}
    assert escritas[macetas] == 0.0


def test_recibir_con_un_dedo_conserva_lo_tecleado_de_todos_los_renglones(
        cliente, de_dueno, odoo):
    """El POST con un «1O» repinta la pantalla con TODO lo tecleado (el
    renglón bueno incluido), el campo malo marcado y enfocado."""
    ref = _pedida(odoo)
    renglones = compra_odoo.recepcion(ref)["renglones"]
    tierra, macetas = renglones[0]["movimiento"], renglones[1]["movimiento"]
    respuesta = cliente.post("/compras/recibir", data={
        "ref": ref, f"llego-{tierra}": "1O", f"roto-{tierra}": "",
        f"llego-{macetas}": "8", f"roto-{macetas}": "2",
    }, follow_redirects=False)
    assert respuesta.status_code == 200
    texto = respuesta.text
    assert 'value="1O"' in texto          # lo tecleado no se borra
    assert 'value="8"' in texto and 'value="2"' in texto
    assert 'aria-invalid="true"' in texto and "autofocus" in texto
    assert texto.count('class="error-campo"') == 1
    # Y Odoo quedó sin tocar.
    assert odoo.escrituras("stock.move", "write") == []


def test_un_empleado_no_recibe_una_compra_ajena_ni_por_post(cliente, odoo):
    """El candado está en el SERVIDOR, igual que para mover: que la pantalla
    no se le muestre no basta, porque un POST se puede mandar a mano."""
    ref = _pedida(odoo)
    compras._muestra_uno(ref)["resp"] = "Mary"
    for ruta in ("/compras/recibir", "/compras/orden"):
        respuesta = cliente.post(ruta, data={"ref": ref},
                                 follow_redirects=False)
        assert respuesta.status_code == 303, ruta
        assert "no%20la%20mov%C3%A9s%20vos" in respuesta.headers["location"]
    assert odoo.escrituras("stock.move", "write") == []


def test_la_pantalla_de_recibir_con_una_compra_que_no_esta_vuelve_al_tablero(
        cliente, de_dueno):
    respuesta = cliente.get("/compras/recibir?ref=VIV-999",
                            follow_redirects=False)
    assert respuesta.status_code == 303
    assert respuesta.headers["location"].startswith("/compras?")
    assert "#" in respuesta.headers["location"]


def test_ningun_redirect_nuevo_se_va_sin_ancla(cliente, de_dueno, odoo):
    """El barrido de siempre, extendido a los POST de esta tanda."""
    ref = _pedida(odoo)
    renglones = compra_odoo.recepcion(ref)["renglones"]
    posts = [
        ("/compras/orden", {"ref": ref}),
        ("/compras/orden", {"ref": ""}),
        ("/compras/orden", {"ref": "VIV-999"}),
        ("/compras/recibir", {"ref": ref,
                              f"llego-{renglones[0]['movimiento']}": "1"}),
        ("/compras/recibir", {"ref": ref}),       # nada llegó: rebota
        ("/compras/recibir", {"ref": "VIV-999"}),
        ("/compras/estado", {"ref": ref, "estado": "RECIBIDO"}),
    ]
    for ruta, datos in posts:
        respuesta = cliente.post(ruta, data=datos, follow_redirects=False)
        assert respuesta.status_code == 303, ruta
        assert "#" in respuesta.headers["location"], (ruta, datos)


def test_el_panel_del_tablero_ofrece_registrar_lo_que_llego(cliente, de_dueno,
                                                            odoo):
    ref = _pedida(odoo)
    texto = cliente.get(f"/compras?abrir={ref}").text
    assert "Registrar lo que llegó" in texto
    assert f"/compras/recibir?ref={ref}" in texto
    assert "Crear la orden en Odoo" not in texto    # ya la tiene
