"""Pruebas de Compras/Gastos con un Odoo simulado.

Lo que importa aquí son las dos reglas del negocio: que una compra de
proyecto NO lleve líneas de inventario (las plantas del contenedor nunca
fueron stock del vivero) y que una compra normal sí las suba; y que solo el
monto y la descripción sean obligatorios, para que la empleada pueda cargar
el gasto con lo que tenga a mano y completarlo después.
"""

from datetime import datetime

import pytest

from app import compras, ventas
from app.datos import ZONA_PANAMA

EMPLEADA = {"id": "genesis", "nombre": "Génesis"}


class OdooCompras:
    """Odoo de mentira: los modelos que toca compras.py.

    Los totales del proyecto y de la venta se CALCULAN al leerlos, como en
    Odoo (son campos computados). Si el simulado los guardara, las pruebas no
    verían el error de mostrar un total viejo.
    """

    def __init__(self):
        self.gastos = {}
        self.lineas = {}
        self.leads = {}
        self.ordenes = {}
        self.productos = {}
        self.siguiente = 100

    def _nuevo(self):
        self.siguiente += 1
        return self.siguiente

    def ejecutar(self, modelo, metodo, args, kw=None):
        return getattr(self, (modelo + "_" + metodo).replace(".", "_"))(args, kw or {})

    # ---- semillas ----
    def sembrar_proyecto(self, ref, nombre, cotizado=0.0, vendido=0.0):
        lead_id = self._nuevo()
        self.leads[lead_id] = {"lead_ref": ref, "name": nombre,
                               "cotizado": cotizado, "vendido": vendido}
        return lead_id

    def sembrar_orden(self, nombre, cliente, total):
        orden_id = self._nuevo()
        self.ordenes[orden_id] = {"name": nombre, "partner_id": [7, cliente],
                                  "amount_total": total, "state": "sale"}
        return orden_id

    def sembrar_producto(self, sku, nombre, precio=1.0, hay=0):
        producto_id = self._nuevo()
        self.productos[producto_id] = {"default_code": sku, "name": nombre,
                                       "list_price": precio, "qty_available": hay}
        return producto_id

    # ---- gastos ----
    def vivero_rose_proyecto_gasto_create(self, args, kw):
        valores = dict(args[0])
        lineas = valores.pop("linea_ids", [])
        gasto_id = self._nuevo()
        valores.setdefault("entro_inventario", False)
        valores["destino"] = ("proyecto" if valores.get("lead_id")
                              else "venta" if valores.get("order_id") else "vivero")
        self.gastos[gasto_id] = valores
        for _, _, linea in lineas:
            self.lineas[self._nuevo()] = dict(linea, gasto_id=gasto_id)
        return gasto_id

    def vivero_rose_proyecto_gasto_write(self, args, kw):
        for gasto_id in args[0]:
            self.gastos[gasto_id].update(args[1])
            valores = self.gastos[gasto_id]
            valores["destino"] = ("proyecto" if valores.get("lead_id")
                                  else "venta" if valores.get("order_id") else "vivero")
        return True

    def vivero_rose_proyecto_gasto_unlink(self, args, kw):
        for gasto_id in args[0]:
            self.gastos.pop(gasto_id, None)
        return True

    def _gasto_leido(self, gasto_id):
        valores = self.gastos[gasto_id]
        lead = valores.get("lead_id")
        orden = valores.get("order_id")
        return {
            "id": gasto_id,
            "concepto": valores.get("concepto"),
            "monto": valores.get("monto"),
            "fecha": valores.get("fecha"),
            "categoria": valores.get("categoria") or False,
            "proveedor": valores.get("proveedor") or False,
            "forma_pago": valores.get("forma_pago") or False,
            "empleada": valores.get("empleada") or False,
            "nota": valores.get("nota") or False,
            "destino": valores.get("destino"),
            "lead_id": [lead, self.leads[lead]["name"]] if lead else False,
            "order_id": [orden, self.ordenes[orden]["name"]] if orden else False,
            "recibo_nombre": valores.get("recibo_nombre") or False,
            "entro_inventario": valores.get("entro_inventario", False),
        }

    def vivero_rose_proyecto_gasto_search_read(self, args, kw):
        dominio = args[0] if args else []
        filas = [self._gasto_leido(i) for i in self.gastos]
        for clausula in dominio:
            if not isinstance(clausula, list):
                continue  # los operadores "|" del buscador no se simulan
            campo, operador, valor = clausula
            if operador == "=":
                filas = [f for f in filas if f.get(campo) == valor]
            elif operador == ">=":
                filas = [f for f in filas if (f.get(campo) or "") >= valor]
        return filas

    def vivero_rose_proyecto_gasto_read(self, args, kw):
        return [{**self._gasto_leido(i),
                 "recibo": self.gastos[i].get("recibo")} for i in args[0]]

    def vivero_rose_gasto_linea_search_read(self, args, kw):
        gasto_id = args[0][0][2]
        filas = []
        for linea_id, linea in self.lineas.items():
            if linea["gasto_id"] != gasto_id:
                continue
            producto = self.productos[linea["product_id"]]
            filas.append({"id": linea_id,
                          "product_id": [linea["product_id"], producto["name"]],
                          "sku": producto["default_code"],
                          "cantidad": linea["cantidad"],
                          "costo_unitario": linea.get("costo_unitario", 0.0)})
        return filas

    # ---- proyectos y ventas ----
    def crm_lead_search_read(self, args, kw):
        filas = []
        for lead_id, lead in self.leads.items():
            gastado = sum(g.get("monto", 0.0) for g in self.gastos.values()
                          if g.get("lead_id") == lead_id)
            filas.append({"id": lead_id, "lead_ref": lead["lead_ref"],
                          "name": lead["name"],
                          "total_cotizado": lead["cotizado"],
                          "total_vendido": lead["vendido"],
                          "total_gastado": gastado,
                          "ganancia_proyecto": lead["vendido"] - gastado})
        return filas

    def sale_order_read(self, args, kw):
        filas = []
        for orden_id in args[0]:
            orden = self.ordenes[orden_id]
            gastado = sum(g.get("monto", 0.0) for g in self.gastos.values()
                          if g.get("order_id") == orden_id)
            filas.append({"id": orden_id, "name": orden["name"],
                          "partner_id": orden["partner_id"],
                          "amount_total": orden["amount_total"],
                          "total_gastado": gastado})
        return filas

    def sale_order_search_read(self, args, kw):
        return [{"id": i, "name": o["name"], "partner_id": o["partner_id"],
                 "amount_total": o["amount_total"]}
                for i, o in self.ordenes.items()]

    def product_product_search_read(self, args, kw):
        return [{"id": i, "default_code": p["default_code"], "name": p["name"],
                 "list_price": p["list_price"], "qty_available": p["qty_available"]}
                for i, p in self.productos.items()]


@pytest.fixture
def odoo(monkeypatch):
    falso = OdooCompras()
    monkeypatch.setattr(ventas, "_ejecutar",
                        lambda modelo, metodo, args, kw=None: falso.ejecutar(modelo, metodo, args, kw))
    monkeypatch.setattr(compras, "activo", lambda: True)
    return falso


def hoy():
    return datetime.now(ZONA_PANAMA).date().isoformat()


def test_compra_del_vivero_guarda_lo_obligatorio_y_deja_vacio_lo_opcional(odoo):
    n = compras.crear(EMPLEADA, {"concepto": "Abono orgánico", "monto": "180",
                                 "destino": "vivero"})
    compra = compras.obtener(n)
    assert compra["concepto"] == "Abono orgánico"
    assert compra["monto"] == 180.0
    assert compra["destino"] == "vivero"
    assert compra["fecha"] == hoy()
    # Lo opcional queda vacío, no inventado.
    assert compra["categoria"] == "" and compra["proveedor"] == ""
    assert compra["forma_pago"] == ""
    assert compra["empleada"] == "Génesis"


def test_no_se_puede_cargar_sin_descripcion_ni_con_monto_en_cero(odoo):
    with pytest.raises(ValueError):
        compras.crear(EMPLEADA, {"concepto": "  ", "monto": "10"})
    with pytest.raises(ValueError):
        compras.crear(EMPLEADA, {"concepto": "Abono", "monto": "0"})


def test_destino_proyecto_exige_elegir_el_proyecto(odoo):
    with pytest.raises(ValueError):
        compras.crear(EMPLEADA, {"concepto": "Contenedor", "monto": "2400",
                                 "destino": "proyecto"})


def test_compra_de_proyecto_no_lleva_lineas_de_inventario(odoo):
    """La regla del negocio: las plantas del contenedor nunca fueron stock."""
    lead = odoo.sembrar_proyecto("PROYECTO-01", "Proyecto City Mall",
                                 cotizado=8000.0, vendido=8000.0)
    palma = odoo.sembrar_producto("PL-PALMA", "Palma Areca", 15.0, hay=41)
    n = compras.crear(EMPLEADA,
                      {"concepto": "Contenedor de palmas", "monto": "2400",
                       "destino": "proyecto", "proyecto_id": str(lead)},
                      lineas=[{"producto_id": palma, "cantidad": 30, "costo": 20.0}])
    assert compras.lineas_de(n) == []
    assert compras.obtener(n)["destino"] == "proyecto"


def test_compra_normal_si_guarda_lo_que_entra_al_inventario(odoo):
    abono = odoo.sembrar_producto("IN-ABONO5", "Abono orgánico 5 kg", 4.0, hay=12)
    n = compras.crear(EMPLEADA,
                      {"concepto": "Abono orgánico · 20 sacos", "monto": "180",
                       "destino": "vivero"},
                      lineas=[{"producto_id": abono, "cantidad": 20, "costo": 9.0}])
    lineas = compras.lineas_de(n)
    assert [(l["sku"], l["cantidad"]) for l in lineas] == [("IN-ABONO5", 20)]


def test_subir_al_inventario_manda_los_skus_al_order_api(odoo, monkeypatch):
    llamadas = {}

    class RespuestaFalsa:
        status_code = 200

        @staticmethod
        def json():
            return {"ok": True, "resultados": [
                {"sku": "IN-ABONO5", "cantidad": 20, "resultado": "aplicado",
                 "anterior": 12, "nueva": 32}]}

    def post_falso(url, headers=None, json=None, timeout=None):
        llamadas["url"] = url
        llamadas["cuerpo"] = json
        return RespuestaFalsa()

    monkeypatch.setenv("ORDER_API_URL", "http://order-api")
    monkeypatch.setenv("ORDER_API_KEY", "clave")
    monkeypatch.setattr(compras.httpx, "post", post_falso)

    abono = odoo.sembrar_producto("IN-ABONO5", "Abono orgánico 5 kg", 4.0, hay=12)
    n = compras.crear(EMPLEADA, {"concepto": "Abono", "monto": "180",
                                 "destino": "vivero"},
                      lineas=[{"producto_id": abono, "cantidad": 20, "costo": 9.0}])
    compras.subir_al_inventario(n, compras.lineas_de(n), "genesis")

    assert llamadas["url"].endswith("/api/stock/entradas")
    assert llamadas["cuerpo"]["entradas"] == [{"sku": "IN-ABONO5", "cantidad": 20}]
    assert llamadas["cuerpo"]["motivo"] == f"compra_{n}"
    # Queda marcada para no aplicarla dos veces.
    assert compras.obtener(n)["entro_inventario"] is True


def test_proyectos_muestran_su_cuenta_con_las_compras_debajo(odoo):
    lead = odoo.sembrar_proyecto("PROYECTO-01", "Proyecto City Mall",
                                 cotizado=8000.0, vendido=8000.0)
    compras.crear(EMPLEADA, {"concepto": "Contenedor de palmas", "monto": "2400",
                             "destino": "proyecto", "proyecto_id": str(lead)})
    tarjetas = compras.por_proyecto()
    assert len(tarjetas) == 1
    tarjeta = tarjetas[0]
    assert tarjeta["ref"] == "PROYECTO-01"
    assert tarjeta["gastado"] == 2400.0
    assert tarjeta["ganancia"] == 5600.0
    assert [c["concepto"] for c in tarjeta["compras"]] == ["Contenedor de palmas"]


def test_ventas_solo_lista_las_que_tienen_compras(odoo):
    con_gasto = odoo.sembrar_orden("S00042", "Morris Cohen", 900.0)
    odoo.sembrar_orden("S00043", "Otra venta", 300.0)
    compras.crear(EMPLEADA, {"concepto": "Gasolina camioneta", "monto": "45",
                             "destino": "venta", "orden_id": str(con_gasto)})
    tarjetas = compras.por_venta()
    assert [t["ref"] for t in tarjetas] == ["S00042"]
    assert tarjetas[0]["gastado"] == 45.0
    assert tarjetas[0]["ganancia"] == 855.0


def test_resumen_del_mes_parte_el_gasto_por_destino(odoo):
    lead = odoo.sembrar_proyecto("PROYECTO-01", "City Mall", 8000.0, 8000.0)
    orden = odoo.sembrar_orden("S00042", "Morris", 900.0)
    compras.crear(EMPLEADA, {"concepto": "Contenedor", "monto": "2400",
                             "destino": "proyecto", "proyecto_id": str(lead)})
    compras.crear(EMPLEADA, {"concepto": "Gasolina", "monto": "45",
                             "destino": "venta", "orden_id": str(orden)})
    compras.crear(EMPLEADA, {"concepto": "Abono", "monto": "180",
                             "destino": "vivero", "categoria": "insumos"})
    resumen = compras.resumen_del_mes()
    assert resumen["total"] == 2625.0
    assert resumen["proyectos"] == 2400.0
    assert resumen["ventas"] == 45.0
    assert resumen["vivero"] == 180.0
    assert resumen["categorias"][0]["nombre"] == "Otros"


def test_editar_corrige_los_datos_opcionales(odoo):
    n = compras.crear(EMPLEADA, {"concepto": "Abono", "monto": "180",
                                 "destino": "vivero"})
    compras.editar(n, {"concepto": "Abono orgánico", "monto": "185",
                       "destino": "vivero", "categoria": "insumos",
                       "proveedor": "Agroservicios El Roble",
                       "forma_pago": "yappy", "fecha": "2026-09-17"})
    compra = compras.obtener(n)
    assert compra["monto"] == 185.0
    assert compra["categoria_nombre"] == "Insumos"
    assert compra["proveedor"] == "Agroservicios El Roble"
    assert compra["forma_pago_nombre"] == "Yappy"
    assert compra["fecha_bonita"] == "17/09/2026"


def test_lineas_del_form_arma_los_pares_producto_cantidad():
    form = {"producto_1": "55", "cantidad_1": "20", "costo_1": "9.00",
            "producto_2": "56", "cantidad_2": "3", "costo_2": "",
            "concepto": "Compra"}
    assert compras.lineas_del_form(form) == [
        {"producto_id": 55, "cantidad": 20, "costo": 9.0},
        {"producto_id": 56, "cantidad": 3, "costo": 0.0},
    ]
