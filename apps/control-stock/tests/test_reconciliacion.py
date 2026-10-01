"""Pruebas de reconciliacion.py (M1 del proyecto Orquesta), con un Odoo
simulado y sin red.

Mismo patrón que test_cot_lead.py: se reemplaza `ventas._ejecutar` con un
doble que entiende solo lo que reconciliacion.py pide. El doble además es
el CANDADO: revienta con AssertionError si algún código intenta un método
que no sea de lectura — la regla «M1 jamás escribe en Odoo» se prueba, no
se promete. La otra mitad del candado es la prueba que lee el código
fuente del módulo y falla si nombra un método de escritura de Odoo.

Las fuentes que no son Odoo (Linear, el SQLite local, el CSV externo) se
reemplazan función por función: cada prueba arma exactamente lo que su
caso necesita, y las de huecos dejan la fuente caída para verificar que el
informe LO DICE en vez de inventar ceros.
"""

import re
from datetime import datetime, timedelta, timezone

import pytest

from app import datos, reconciliacion, ventas

# Las funciones ORIGINALES de las fuentes no-Odoo, capturadas antes de que
# el fixture las reemplace: las pruebas de huecos y de lectura real las
# necesitan tal cual (monkeypatch cambia el atributo del módulo, así que
# después del fixture ya no se pueden recuperar de ahí).
_LEADS_ORIGINAL = reconciliacion._leads_de_linear
_VENTAS_LOCALES_ORIGINAL = reconciliacion._ventas_locales
_PAGOS_EXTERNOS_ORIGINAL = reconciliacion._pagos_externos


def _hace(dias, horas=0):
    """Un datetime de Odoo (UTC sin zona) a N días de ahora."""
    cuando = datetime.now(timezone.utc) - timedelta(days=dias, hours=horas)
    return cuando.strftime("%Y-%m-%d %H:%M:%S")


def _fecha_hace(dias):
    """Una fecha (date) de Odoo a N días de ahora, para validity_date."""
    return (datetime.now(timezone.utc) - timedelta(days=dias)).strftime(
        "%Y-%m-%d")


# ---------------------------------------------------------------------------
# El doble de Odoo: solo lectura, con candado
# ---------------------------------------------------------------------------

class OdooReconcilia:
    """Un Odoo mínimo para M1: sale.order, res.partner, account.move,
    stock.picking, crm.lead, ir.model.data y account.journal.

    `ejecutar` REVIENTA si el método no es de lectura: ninguna prueba que
    pase por aquí puede esconder una escritura. También anota cada llamada
    en `llamadas`, para poder afirmarlo en positivo.
    """

    LECTURAS = {"search_read", "read", "search_count", "fields_get"}

    def __init__(self):
        self.partners = {}
        self.ordenes = {}
        self.facturas = {}
        self.salidas = {}
        self.pagos = {}
        self.oportunidades = {}
        self.diarios = {}      # id -> nombre
        self.datos_modelo = []  # filas de ir.model.data
        self.llamadas = []
        self.fallar = False
        self._ids = {"partner": 100, "orden": 9000, "factura": 5000,
                     "salida": 7000, "oportunidad": 300, "diario": 40,
                     "pago": 8000}

    # -- armado del mundo ---------------------------------------------------

    def _nuevo(self, clase):
        self._ids[clase] += 1
        return self._ids[clase]

    def con_etapas_flujo(self):
        """Las 4 etapas del Flujo con sus xml_ids (ids 6..9, como hoy en
        producción — pero el módulo debe resolverlos, no saberlos)."""
        for i, clave in enumerate(reconciliacion.ETAPAS_FLUJO):
            self.datos_modelo.append({
                "module": reconciliacion.MODULO_ADDON,
                "name": f"etapa_flujo_{clave}", "res_id": 6 + i})
        return {clave: 6 + i
                for i, clave in enumerate(reconciliacion.ETAPAS_FLUJO)}

    def agregar_diario(self, nombre):
        did = self._nuevo("diario")
        self.diarios[did] = nombre
        return did

    def agregar_partner(self, nombre, phone=""):
        pid = self._nuevo("partner")
        self.partners[pid] = {"name": nombre, "phone": phone}
        return pid

    def agregar_factura(self, amount_total, amount_residual, state="posted",
                        move_type="out_invoice", diario=("VEN", "Ventas"),
                        payment_state="paid", name=None, create_date=None,
                        creado_por="Admin Odoo", partner_id=None):
        fid = self._nuevo("factura")
        partner = ([partner_id, self.partners[partner_id]["name"]]
                   if partner_id else False)
        self.facturas[fid] = {
            "id": fid, "name": name or f"FAC/{fid}", "move_type": move_type,
            "state": state, "amount_total": amount_total,
            "amount_residual": amount_residual,
            "payment_state": payment_state, "invoice_date": _fecha_hace(1),
            "journal_id": list(diario) if diario else False,
            "partner_id": partner,
            "create_date": create_date or _hace(2),
            "create_uid": [9, creado_por],
        }
        return fid

    def agregar_pago(self, amount, partner_id=None, state="paid",
                     name=None, create_date=None, creado_por="Admin Odoo"):
        pid = self._nuevo("pago")
        partner = ([partner_id, self.partners[partner_id]["name"]]
                   if partner_id else False)
        self.pagos[pid] = {
            "id": pid, "name": name or f"PBNK/{pid}", "amount": amount,
            "state": state, "partner_id": partner,
            "create_date": create_date or _hace(2),
            "create_uid": [9, creado_por],
        }
        return pid

    def agregar_salida(self, state="assigned"):
        sid = self._nuevo("salida")
        self.salidas[sid] = {"id": sid, "state": state,
                             "date_done": _hace(0) if state == "done" else False,
                             "scheduled_date": _hace(0)}
        return sid

    def agregar_oportunidad(self, stage_id):
        oid = self._nuevo("oportunidad")
        self.oportunidades[oid] = {"id": oid,
                                   "stage_id": [stage_id, f"Etapa {stage_id}"]}
        return oid

    def agregar_orden(self, partner_id, name, state="draft", dias_atras=1,
                      amount_total=0.0, validity_date=None, facturas=(),
                      salidas=(), oportunidad=None, create_date=None,
                      creado_por="Admin Odoo"):
        oid = self._nuevo("orden")
        self.ordenes[oid] = {
            "id": oid, "name": name, "state": state,
            "date_order": _hace(dias_atras),
            "validity_date": validity_date or False,
            "partner_id": [partner_id, self.partners[partner_id]["name"]],
            "amount_total": amount_total,
            "invoice_ids": list(facturas),
            "picking_ids": list(salidas),
            "opportunity_id": ([oportunidad, "Oportunidad"]
                               if oportunidad else False),
            "client_order_ref": False,
            "tag_ids": [],
            # Si nadie lo dice, la orden se creó cuando dice su fecha:
            # ayer por defecto, así las pruebas viejas no caen en la
            # sección «Creado hoy» sin pedirlo.
            "create_date": create_date or _hace(dias_atras),
            "create_uid": [9, creado_por],
        }
        return oid

    # -- la puerta ------------------------------------------------------------

    def ejecutar(self, modelo, metodo, args, kw=None):
        assert metodo in self.LECTURAS, (
            f"ESCRITURA PROHIBIDA: alguien intentó {modelo}.{metodo} — "
            "reconciliacion.py es de solo lectura.")
        self.llamadas.append((modelo, metodo))
        if self.fallar:
            raise ConnectionError("Odoo no contesta")
        kw = kw or {}
        manejador = getattr(self, (modelo + "_" + metodo).replace(".", "_"))
        return manejador(args, kw)

    # -- dominios: hojas ANDadas --------------------------------------------

    @staticmethod
    def _coincide(fila, dominio):
        for campo, op, valor in dominio:
            actual = fila.get(campo)
            if isinstance(actual, (list, tuple)):
                actual = actual[0] if actual else False
            if op == "=":
                if actual != valor:
                    return False
            elif op == "!=":
                if actual == valor:
                    return False
            elif op == ">=":
                if not (actual and str(actual) >= str(valor)):
                    return False
            elif op == "<":
                if not (actual and str(actual) < str(valor)):
                    return False
            elif op == "in":
                if actual not in valor:
                    return False
            else:
                raise NotImplementedError(f"operador no simulado: {op}")
        return True

    @staticmethod
    def _proyectar(filas, campos):
        return [{"id": f["id"], **{c: f.get(c, False) for c in campos}}
                for f in filas]

    def sale_order_search_read(self, args, kw):
        filas = [o for o in self.ordenes.values()
                 if self._coincide(o, args[0])]
        return self._proyectar(filas, kw.get("fields", []))

    def res_partner_read(self, args, kw):
        return [{"id": pid, **{c: self.partners[pid].get(c, False)
                               for c in kw.get("fields", [])}}
                for pid in args[0] if pid in self.partners]

    def account_move_read(self, args, kw):
        return self._proyectar([self.facturas[fid] for fid in args[0]
                                if fid in self.facturas],
                               kw.get("fields", []))

    def account_move_search_read(self, args, kw):
        filas = [f for f in self.facturas.values()
                 if self._coincide(f, args[0])]
        return self._proyectar(filas, kw.get("fields", []))

    def account_payment_search_read(self, args, kw):
        filas = [p for p in self.pagos.values()
                 if self._coincide(p, args[0])]
        return self._proyectar(filas, kw.get("fields", []))

    def stock_picking_read(self, args, kw):
        return self._proyectar([self.salidas[sid] for sid in args[0]
                                if sid in self.salidas],
                               kw.get("fields", []))

    def crm_lead_read(self, args, kw):
        return self._proyectar([self.oportunidades[oid] for oid in args[0]
                                if oid in self.oportunidades],
                               kw.get("fields", []))

    def ir_model_data_search_read(self, args, kw):
        filas = [{"id": i + 1, **f} for i, f in enumerate(self.datos_modelo)]
        filas = [f for f in filas if self._coincide(f, args[0])]
        return [{c: f.get(c, False) for c in ["id"] + kw.get("fields", [])}
                for f in filas]

    def account_journal_search_read(self, args, kw):
        filas = [{"id": did, "name": nombre}
                 for did, nombre in self.diarios.items()]
        return [f for f in filas if self._coincide(f, args[0])]


# ---------------------------------------------------------------------------
# El mundo de cada prueba
# ---------------------------------------------------------------------------

@pytest.fixture
def odoo(monkeypatch):
    """El doble conectado y las otras fuentes en silencio limpio (sin
    datos y sin huecos): cada prueba enciende lo que su caso necesita."""
    doble = OdooReconcilia()
    for variable in ("ODOO_URL", "ODOO_DB", "ODOO_USER", "ODOO_PASSWORD"):
        monkeypatch.setenv(variable, "prueba")
    monkeypatch.setattr(ventas, "_ejecutar", doble.ejecutar)
    monkeypatch.delenv("LINEAR_API_KEY", raising=False)
    # Por defecto Linear SÍ tiene datos (lista vacía de leads): las
    # pruebas de huecos lo vuelven a tumbar explícitamente.
    monkeypatch.setattr(reconciliacion, "_leads_de_linear",
                        lambda: ([], None))
    monkeypatch.setattr(reconciliacion, "_ventas_locales",
                        lambda: ({}, None))
    monkeypatch.setattr(reconciliacion, "_cotizaciones_locales",
                        lambda: ({}, None))
    monkeypatch.setattr(reconciliacion, "_pagos_externos",
                        lambda: ([], None))
    return doble


def _con_leads(monkeypatch, *leads):
    monkeypatch.setattr(reconciliacion, "_leads_de_linear",
                        lambda: (list(leads), None))


def _lead(celular, estado, pago="", ref="LEAD-1"):
    return {"id": "uuid-" + ref, "ref": ref, "celular": celular,
            "estado": estado, "pago": pago}


def _ventas_por_nombre(datos):
    return {v["nombre"]: v for v in datos["ventas"]}


# ---------------------------------------------------------------------------
# Las 8 clases, una a una
# ---------------------------------------------------------------------------

def test_clase_a_cotizada_vigente(odoo):
    p = odoo.agregar_partner("Clienta A", "6111-1111")
    odoo.agregar_orden(p, "S00101", state="draft", dias_atras=3,
                       amount_total=50.0)
    datos = reconciliacion.informe_datos()
    venta = _ventas_por_nombre(datos)["S00101"]
    assert venta["clase"] == "A"
    assert datos["contadores"]["A"] == 1
    assert venta["pagado"] == 0.0 and venta["debe"] == 50.0


def test_clase_b_sin_validity_date_anota_el_motivo(odoo):
    p = odoo.agregar_partner("Clienta B", "6111-2222")
    odoo.agregar_orden(p, "S00102", state="draft", dias_atras=20,
                       amount_total=80.0)
    venta = _ventas_por_nombre(reconciliacion.informe_datos())["S00102"]
    assert venta["clase"] == "B"
    assert reconciliacion.TEXTO_SIN_VALIDEZ in venta["motivo"]


def test_clase_b_con_validity_date_no_anota_ese_motivo(odoo):
    p = odoo.agregar_partner("Clienta B2", "6111-2223")
    odoo.agregar_orden(p, "S00103", state="sent", dias_atras=2,
                       amount_total=80.0, validity_date=_fecha_hace(1))
    venta = _ventas_por_nombre(reconciliacion.informe_datos())["S00103"]
    assert venta["clase"] == "B"
    assert reconciliacion.TEXTO_SIN_VALIDEZ not in venta["motivo"]


def test_limite_13_dias_vigente_14_vencida(odoo):
    p = odoo.agregar_partner("Al Límite", "6111-3333")
    odoo.agregar_orden(p, "S00213", state="draft", dias_atras=13,
                       amount_total=10.0)
    odoo.agregar_orden(p, "S00214", state="draft", dias_atras=14,
                       amount_total=10.0)
    ventas_ = _ventas_por_nombre(reconciliacion.informe_datos())
    assert ventas_["S00213"]["clase"] == "A"
    assert ventas_["S00214"]["clase"] == "B"


def test_b_sigue_vencida_con_nota_si_el_cliente_volvio(odoo, monkeypatch):
    p = odoo.agregar_partner("Volvió", "6111-4444")
    odoo.agregar_orden(p, "S00104", state="draft", dias_atras=20,
                       amount_total=30.0)
    _con_leads(monkeypatch, _lead("6111-4444", "HABLANDO"))
    venta = _ventas_por_nombre(reconciliacion.informe_datos())["S00104"]
    # Vencida no se reactiva sola: sigue en B, pero la nota lo dice.
    assert venta["clase"] == "B"
    assert reconciliacion.TEXTO_VOLVIO in venta["motivo"]


def test_clase_c_debe(odoo):
    p = odoo.agregar_partner("Con Abono", "6111-5555")
    f = odoo.agregar_factura(100.0, 50.0, payment_state="partial")
    odoo.agregar_orden(p, "S00105", state="sale", amount_total=100.0,
                       facturas=[f], salidas=[odoo.agregar_salida()])
    venta = _ventas_por_nombre(reconciliacion.informe_datos())["S00105"]
    assert venta["clase"] == "C"
    assert venta["pagado"] == 50.0 and venta["debe"] == 50.0


def test_c_entregada_con_saldo_sigue_siendo_c(odoo):
    p = odoo.agregar_partner("Entregada Debe", "6111-6666")
    f = odoo.agregar_factura(200.0, 80.0, payment_state="partial")
    odoo.agregar_orden(p, "S00106", state="sale", amount_total=200.0,
                       facturas=[f],
                       salidas=[odoo.agregar_salida(state="done")])
    venta = _ventas_por_nombre(reconciliacion.informe_datos())["S00106"]
    assert venta["clase"] == "C"  # NO es G ni E
    assert "Entregado, debe $80.00" in venta["motivo"]
    assert venta["entregado_odoo"] is True


def test_clase_d_pagada_sin_entregar(odoo):
    p = odoo.agregar_partner("Pagada", "6111-7777")
    f = odoo.agregar_factura(75.0, 0.0)
    odoo.agregar_orden(p, "S00107", state="sale", amount_total=75.0,
                       facturas=[f], salidas=[odoo.agregar_salida()])
    venta = _ventas_por_nombre(reconciliacion.informe_datos())["S00107"]
    assert venta["clase"] == "D"
    assert venta["entregado_odoo"] is False


def test_clase_e_pagada_y_entregada(odoo, monkeypatch):
    p = odoo.agregar_partner("Completa", "6111-8888")
    f = odoo.agregar_factura(60.0, 0.0)
    odoo.agregar_orden(p, "S00108", state="sale", amount_total=60.0,
                       facturas=[f],
                       salidas=[odoo.agregar_salida(state="done")])
    _con_leads(monkeypatch, _lead("6111-8888", "GANADO", pago="Pagado 100%"))
    venta = _ventas_por_nombre(reconciliacion.informe_datos())["S00108"]
    assert venta["clase"] == "E"
    assert reconciliacion.TEXTO_LINEAR_SIN_CERRAR not in venta["motivo"]


def test_e_con_linear_sin_cerrar(odoo, monkeypatch):
    p = odoo.agregar_partner("Linear Atrasado", "6111-9999")
    f = odoo.agregar_factura(60.0, 0.0)
    odoo.agregar_orden(p, "S00209", state="sale", amount_total=60.0,
                       facturas=[f],
                       salidas=[odoo.agregar_salida(state="done")])
    _con_leads(monkeypatch, _lead("6111-9999", "AGENDADO", pago="Pagado 100%"))
    venta = _ventas_por_nombre(reconciliacion.informe_datos())["S00209"]
    # Odoo validó: sigue E, con la nota — nunca G.
    assert venta["clase"] == "E"
    assert reconciliacion.TEXTO_LINEAR_SIN_CERRAR in venta["motivo"]


def test_clase_f_por_etapa_manual_del_flujo(odoo):
    etapas = odoo.con_etapas_flujo()
    p = odoo.agregar_partner("Pagó Afuera", "6122-1111")
    oportunidad = odoo.agregar_oportunidad(etapas["abono"])
    odoo.agregar_orden(p, "S00210", state="sale", amount_total=90.0,
                       oportunidad=oportunidad)
    venta = _ventas_por_nombre(reconciliacion.informe_datos())["S00210"]
    # Regla 3: etapa MANUAL + $0 en Odoo = F, con su texto fijo.
    assert venta["clase"] == "F"
    assert venta["motivo"] == reconciliacion.TEXTO_F
    # F NUNCA significa pagado: el dinero sigue saliendo de Odoo.
    assert venta["pagado"] == 0.0 and venta["debe"] == 90.0


def test_clase_f_por_ventas_locales(odoo, monkeypatch):
    p = odoo.agregar_partner("Local Dice Pagada", "6122-2222")
    oid = odoo.agregar_orden(p, "S00211", state="sale", amount_total=40.0)
    monkeypatch.setattr(reconciliacion, "_ventas_locales",
                        lambda: ({oid: "vendida"}, None))
    venta = _ventas_por_nombre(reconciliacion.informe_datos())["S00211"]
    assert venta["clase"] == "F"
    assert venta["motivo"] == reconciliacion.TEXTO_F


def test_clase_g_entrega_sin_confirmar(odoo, monkeypatch):
    p = odoo.agregar_partner("Entregó El Calendario", "6122-3333")
    f = odoo.agregar_factura(55.0, 0.0)
    odoo.agregar_orden(p, "S00212", state="sale", amount_total=55.0,
                       facturas=[f], salidas=[odoo.agregar_salida()])
    _con_leads(monkeypatch,
               _lead("6122-3333", "ENTREGADO", pago="Pagado 100%"))
    venta = _ventas_por_nombre(reconciliacion.informe_datos())["S00212"]
    assert venta["clase"] == "G"
    assert venta["entregado_calendario"] is True
    assert venta["entregado_odoo"] is False


def test_clase_h_etiqueta_automatica_sin_plata(odoo, monkeypatch):
    p = odoo.agregar_partner("Pago Fantasma", "6122-4444")
    odoo.agregar_orden(p, "S00215", state="sale", amount_total=70.0)
    _con_leads(monkeypatch,
               _lead("6122-4444", "POR_AGENDAR", pago="Pagado 100%"))
    venta = _ventas_por_nombre(reconciliacion.informe_datos())["S00215"]
    # Regla 2: la etiqueta AUTOMÁTICA solo nace de un pago real — con $0
    # en Odoo es anomalía (H), no F.
    assert venta["clase"] == "H"
    assert venta["motivo"] == reconciliacion.TEXTO_H_ETIQUETA


def test_desempate_h_gana_a_f_y_g(odoo, monkeypatch):
    etapas = odoo.con_etapas_flujo()
    p = odoo.agregar_partner("Todo A La Vez", "6122-5555")
    oportunidad = odoo.agregar_oportunidad(etapas["pagado"])
    odoo.agregar_orden(p, "S00216", state="sale", amount_total=100.0,
                       oportunidad=oportunidad,
                       salidas=[odoo.agregar_salida()])
    # H (etiqueta automática + $0), F (etapa manual + $0) y G (lead
    # Entregado, salida sin validar) a la vez: gana H.
    _con_leads(monkeypatch,
               _lead("6122-5555", "ENTREGADO", pago="Abono 50%"))
    venta = _ventas_por_nombre(reconciliacion.informe_datos())["S00216"]
    assert venta["clase"] == "H"


def test_desempate_f_gana_a_g(odoo, monkeypatch):
    etapas = odoo.con_etapas_flujo()
    p = odoo.agregar_partner("Efe Contra Ge", "6122-6666")
    oportunidad = odoo.agregar_oportunidad(etapas["abono"])
    odoo.agregar_orden(p, "S00217", state="sale", amount_total=100.0,
                       oportunidad=oportunidad,
                       salidas=[odoo.agregar_salida()])
    # Sin etiqueta de pago en Linear (no hay H): F y G a la vez → F.
    _con_leads(monkeypatch, _lead("6122-6666", "ENTREGADO"))
    venta = _ventas_por_nombre(reconciliacion.informe_datos())["S00217"]
    assert venta["clase"] == "F"


# ---------------------------------------------------------------------------
# El CSV externo: texto y señal, jamás un pago
# ---------------------------------------------------------------------------

def test_csv_externo_da_f_pero_nunca_pagado(odoo, monkeypatch):
    p = odoo.agregar_partner("Informó El Dueño", "6133-1111")
    odoo.agregar_orden(p, "S00118", state="sale", amount_total=120.0)
    monkeypatch.setattr(reconciliacion, "_pagos_externos", lambda: ([{
        "telefono": "6133-1111", "cliente": "Informó El Dueño",
        "monto": "120", "metodo": "yappy", "fecha": "2026-09-30",
        "entregado": "si"}], None))
    venta = _ventas_por_nombre(reconciliacion.informe_datos())["S00118"]
    assert venta["clase"] == "F"
    # La fila del CSV NO se convierte en pago en NINGÚN campo:
    assert venta["pagado"] == 0.0
    assert venta["debe"] == 120.0
    assert reconciliacion.TEXTO_PAGO_EXTERNO in venta["fuentes_otras"]
    # Y tampoco movió la entrega: el CSV decía entregado y Odoo no.
    assert venta["entregado_odoo"] is False


# ---------------------------------------------------------------------------
# S00084, Super Extra, canceladas
# ---------------------------------------------------------------------------

def test_s00084_se_clasifica_normal_con_marca_prueba(odoo):
    p = odoo.agregar_partner("Prueba Flujo OST", "6144-1111")
    odoo.agregar_orden(p, "S00084", state="sale", amount_total=2.50)
    datos = reconciliacion.informe_datos()
    venta = _ventas_por_nombre(datos)["S00084"]
    assert venta["marca_prueba"] is True
    # No se excluye: entra a los contadores con su clase normal.
    assert venta["clase"] in reconciliacion.CLASES
    assert sum(datos["contadores"][c] for c in reconciliacion.CLASES) == 1


def test_super_extra_fuera_del_dinero_y_contada_aparte(odoo):
    odoo.agregar_diario(reconciliacion.DIARIO_FUERA_DE_ALCANCE)
    # Dos facturas impagas del diario viejo, sueltas (el universo real
    # tiene 29 por $3,109.85):
    odoo.agregar_factura(100.0, 100.0, payment_state="not_paid",
                         diario=(41, reconciliacion.DIARIO_FUERA_DE_ALCANCE),
                         name="SE/001")
    odoo.agregar_factura(50.0, 50.0, payment_state="not_paid",
                         diario=(41, reconciliacion.DIARIO_FUERA_DE_ALCANCE),
                         name="SE/002")
    # Y una orden cuya única factura vive en ese diario — PAGADA allá, pero
    # ese dinero no cuenta para ninguna clase:
    p = odoo.agregar_partner("Cliente Súper", "6144-2222")
    f = odoo.agregar_factura(30.0, 0.0,
                             diario=(41, reconciliacion.DIARIO_FUERA_DE_ALCANCE),
                             name="SE/003")
    odoo.agregar_orden(p, "S00119", state="sale", amount_total=30.0,
                       facturas=[f])
    datos = reconciliacion.informe_datos()
    venta = _ventas_por_nombre(datos)["S00119"]
    assert venta["pagado"] == 0.0  # el diario fuera de alcance no suma
    fuera = datos["fuera_de_alcance"]
    assert fuera["n"] == 3
    assert fuera["total"] == 180.0
    assert any("SE/001" in renglon for renglon in fuera["detalle"])


def test_cancelada_reciente_informativa_fuera_de_contadores(odoo):
    p = odoo.agregar_partner("Canceló", "6144-3333")
    odoo.agregar_orden(p, "S00120", state="cancel", dias_atras=5,
                       amount_total=15.0)
    datos = reconciliacion.informe_datos()
    venta = _ventas_por_nombre(datos)["S00120"]
    assert venta["clase"] == "CANCELADA"
    assert sum(datos["contadores"][c] for c in reconciliacion.CLASES) == 0


def test_cancelada_vieja_ni_aparece(odoo):
    p = odoo.agregar_partner("Canceló Hace Mucho", "6144-4444")
    odoo.agregar_orden(p, "S00121", state="cancel", dias_atras=45,
                       amount_total=15.0)
    assert reconciliacion.informe_datos()["ventas"] == []


# ---------------------------------------------------------------------------
# Huecos: lo que no se sabe se dice, nunca se inventa
# ---------------------------------------------------------------------------

def test_sin_odoo_configurado_todo_en_huecos(odoo, monkeypatch):
    monkeypatch.delenv("ODOO_URL", raising=False)
    datos = reconciliacion.informe_datos()
    assert datos["ventas"] == []
    assert datos["contadores"]["rojo"] == 0
    assert any("Odoo" in h for h in datos["huecos"])


def test_odoo_caido_es_hueco_no_ceros_callados(odoo):
    odoo.fallar = True
    datos = reconciliacion.informe_datos()
    assert datos["ventas"] == []
    assert any("Odoo no contestó" in h for h in datos["huecos"])


def test_linear_sin_credenciales_reporta_sin_datos(odoo, monkeypatch):
    # El camino REAL (no el doble): sin LINEAR_API_KEY la fuente entera
    # es «sin datos» — jamás la muestra de linear_leads.
    monkeypatch.setattr(reconciliacion, "_leads_de_linear", _LEADS_ORIGINAL)
    p = odoo.agregar_partner("Sin Linear", "6155-1111")
    odoo.agregar_orden(p, "S00122", state="draft", amount_total=20.0)
    datos = reconciliacion.informe_datos()
    assert any("Linear" in h for h in datos["huecos"])
    venta = _ventas_por_nombre(datos)["S00122"]
    assert venta["entregado_calendario"] is None
    assert "Calendario/Linear: sin datos" in venta["fuentes_otras"]


def test_token_clave_cuenta_como_ausencia(monkeypatch):
    monkeypatch.setenv("LINEAR_API_KEY", "CLAVE_1")
    assert reconciliacion._linear_con_datos() is False
    monkeypatch.setenv("LINEAR_API_KEY", "lin_api_algoreal")
    assert reconciliacion._linear_con_datos() is True


def test_sqlite_sin_tabla_es_hueco(monkeypatch, tmp_path):
    # La base existe pero no tiene las tablas: la fuente lo dice.
    monkeypatch.setenv("CONTROL_STOCK_DB", str(tmp_path / "vacia.db"))
    filas, hueco = _VENTAS_LOCALES_ORIGINAL()
    assert filas == {}
    assert hueco and "ventas_locales" in hueco


def test_sqlite_real_se_lee(db_limpia):
    # Con las tablas de verdad (db_limpia), la lectura real funciona.
    with datos._db() as con:
        con.execute(
            "INSERT INTO ventas_locales (creado_en, empleada, cliente,"
            " orden_id, estado) VALUES ('2026-10-01', 'genesis', 'X', 77,"
            " 'pagado')")
    filas, hueco = _VENTAS_LOCALES_ORIGINAL()
    assert hueco is None
    assert filas == {77: "pagado"}


def test_csv_ausente_no_es_hueco(monkeypatch, tmp_path):
    monkeypatch.setenv("CONTROL_STOCK_DB", str(tmp_path / "x.db"))
    filas, hueco = _PAGOS_EXTERNOS_ORIGINAL()
    assert filas == [] and hueco is None


def test_csv_presente_se_lee(monkeypatch, tmp_path):
    monkeypatch.setenv("CONTROL_STOCK_DB", str(tmp_path / "x.db"))
    (tmp_path / "pagos_externos.csv").write_text(
        "telefono,cliente,monto,metodo,fecha,entregado\n"
        "6133-1111,Fulana,120,yappy,2026-09-30,si\n", encoding="utf-8")
    filas, hueco = _PAGOS_EXTERNOS_ORIGINAL()
    assert hueco is None
    assert filas[0]["telefono"] == "6133-1111"


# ---------------------------------------------------------------------------
# El contrato y el rojo
# ---------------------------------------------------------------------------

def test_contrato_exacto_de_informe_datos(odoo):
    p = odoo.agregar_partner("Contrato", "6166-1111")
    odoo.agregar_orden(p, "S00123", state="draft", amount_total=10.0)
    odoo.agregar_orden(p, "S00199", state="draft", amount_total=5.0,
                       create_date=_hace(0))
    datos = reconciliacion.informe_datos()
    assert set(datos) == {"contadores", "ventas", "fuera_de_alcance",
                          "creado_hoy", "huecos"}
    assert set(datos["contadores"]) == set("ABCDEFGH") | {"rojo"}
    assert set(datos["fuera_de_alcance"]) == {"n", "total", "detalle", "nombre"}
    venta = datos["ventas"][0]
    assert set(venta) == {
        "orden_id", "nombre", "cliente", "telefono", "total", "pagado",
        "debe", "clase", "motivo", "marca_prueba", "entregado_odoo",
        "entregado_calendario", "historica", "fuentes_odoo",
        "fuentes_otras"}
    assert isinstance(venta["fuentes_odoo"], list)
    assert isinstance(venta["fuentes_otras"], list)
    hoy_creado = datos["creado_hoy"]
    assert set(hoy_creado) == {"ordenes", "facturas", "pagos",
                               "total_ordenes"}
    assert set(hoy_creado["ordenes"][0]) == {
        "nombre", "cliente", "monto", "estado", "creado_por", "sospecha",
        "historica"}
    assert isinstance(hoy_creado["total_ordenes"], float)


def test_rojo_suma_f_g_h(odoo, monkeypatch):
    etapas = odoo.con_etapas_flujo()
    # F: etapa manual + $0.
    p1 = odoo.agregar_partner("Efe", "6177-1111")
    odoo.agregar_orden(p1, "S00124", state="sale", amount_total=10.0,
                       oportunidad=odoo.agregar_oportunidad(etapas["pagado"]))
    # H: etiqueta automática + $0.
    p2 = odoo.agregar_partner("Hache", "6177-2222")
    odoo.agregar_orden(p2, "S00125", state="sale", amount_total=10.0)
    # A: una sana.
    p3 = odoo.agregar_partner("Ah", "6177-3333")
    odoo.agregar_orden(p3, "S00126", state="draft", amount_total=10.0)
    _con_leads(monkeypatch, _lead("6177-2222", "POR_AGENDAR",
                                  pago="Abono 50%", ref="LEAD-2"))
    datos = reconciliacion.informe_datos()
    assert datos["contadores"]["F"] == 1
    assert datos["contadores"]["H"] == 1
    assert datos["contadores"]["A"] == 1
    assert datos["contadores"]["rojo"] == 2


def test_entrega_siempre_en_dos_renglones(odoo, monkeypatch):
    p = odoo.agregar_partner("Dos Renglones", "6177-4444")
    f = odoo.agregar_factura(20.0, 0.0)
    odoo.agregar_orden(p, "S00127", state="sale", amount_total=20.0,
                       facturas=[f],
                       salidas=[odoo.agregar_salida(state="done")])
    _con_leads(monkeypatch, _lead("6177-4444", "AGENDADO"))
    venta = _ventas_por_nombre(reconciliacion.informe_datos())["S00127"]
    assert any(r.startswith("Salida de Odoo:") for r in venta["fuentes_odoo"])
    assert any(r.startswith("Calendario/Linear:")
               for r in venta["fuentes_otras"])


def test_varias_cotizaciones_del_mismo_lead_no_son_anomalia(odoo, monkeypatch):
    # El caso Tamara: dos cotizaciones vivas del mismo teléfono, las dos
    # clasificadas independientes, ninguna H por coexistir.
    p = odoo.agregar_partner("Tamara", "6188-1111")
    odoo.agregar_orden(p, "S00079", state="draft", dias_atras=2,
                       amount_total=787.0)
    odoo.agregar_orden(p, "S00081", state="draft", dias_atras=1,
                       amount_total=1525.0)
    _con_leads(monkeypatch, _lead("6188-1111", "COTIZADO"))
    datos = reconciliacion.informe_datos()
    ventas_ = _ventas_por_nombre(datos)
    assert ventas_["S00079"]["clase"] == "A"
    assert ventas_["S00081"]["clase"] == "A"
    assert datos["contadores"]["H"] == 0


# ---------------------------------------------------------------------------
# Creado hoy fuera de Orquesta (solo lectura, con quién lo creó)
# ---------------------------------------------------------------------------

def test_creado_hoy_aparece_con_su_creador(odoo):
    p = odoo.agregar_partner("De Hoy", "6211-1111")
    odoo.agregar_orden(p, "S00130", state="sale", amount_total=500.0,
                       create_date=_hace(0), creado_por="Sesión Externa")
    odoo.agregar_factura(500.0, 0.0, create_date=_hace(0), partner_id=p,
                         creado_por="Sesión Externa", name="FAC/HOY")
    odoo.agregar_pago(500.0, p, create_date=_hace(0),
                      creado_por="Sesión Externa", name="PAGO/HOY")
    # Y una orden de AYER, que NO es de esta sección:
    odoo.agregar_orden(p, "S00131", state="draft", amount_total=20.0,
                       dias_atras=1)
    hoy_creado = reconciliacion.informe_datos()["creado_hoy"]
    assert [o["nombre"] for o in hoy_creado["ordenes"]] == ["S00130"]
    assert hoy_creado["ordenes"][0]["creado_por"] == "Sesión Externa"
    assert hoy_creado["ordenes"][0]["cliente"] == "De Hoy"
    assert [f["nombre"] for f in hoy_creado["facturas"]] == ["FAC/HOY"]
    assert hoy_creado["facturas"][0]["sospecha"] == ""
    assert hoy_creado["facturas"][0]["historica"] is False
    assert [g["nombre"] for g in hoy_creado["pagos"]] == ["PAGO/HOY"]
    assert hoy_creado["pagos"][0]["monto"] == 500.0
    assert hoy_creado["pagos"][0]["sospecha"] == ""
    assert hoy_creado["pagos"][0]["historica"] is False
    assert hoy_creado["total_ordenes"] == 500.0


def test_limite_de_creado_hoy_es_la_medianoche_de_panama(odoo):
    # Una creación 1/10 03:00 UTC es 30/09 22:00 en Panamá: NO es de hoy.
    # La de las 05:00 UTC es exactamente la medianoche de Panamá: SÍ.
    hoy_pma = datetime.now(datos.ZONA_PANAMA).date()
    p = odoo.agregar_partner("Madrugadora", "6211-2222")
    odoo.agregar_orden(p, "S00132", state="draft", amount_total=10.0,
                       create_date=f"{hoy_pma} 03:00:00")
    odoo.agregar_orden(p, "S00133", state="draft", amount_total=900.0,
                       create_date=f"{hoy_pma} 05:00:00")
    hoy_creado = reconciliacion.informe_datos()["creado_hoy"]
    nombres = [o["nombre"] for o in hoy_creado["ordenes"]]
    assert "S00132" not in nombres
    assert "S00133" in nombres


def test_sospecha_duplicado_con_monto_igual_y_con_1_por_ciento(odoo):
    p = odoo.agregar_partner("Repetida", "6211-3333")
    # La vieja (no cancelada) contra la que se sospecha:
    odoo.agregar_orden(p, "S00140", state="sale", amount_total=100.0,
                       dias_atras=3)
    # Creada hoy con el MISMO monto:
    odoo.agregar_orden(p, "S00141", state="draft", amount_total=100.0,
                       create_date=_hace(0))
    # Creada hoy con 1% de diferencia (|101 − 100| ≤ máx(1% de 101, $1)):
    odoo.agregar_orden(p, "S00142", state="draft", amount_total=101.0,
                       create_date=_hace(0))
    hoy_creado = reconciliacion.informe_datos()["creado_hoy"]
    por_nombre = {o["nombre"]: o for o in hoy_creado["ordenes"]}
    assert por_nombre["S00141"]["sospecha"] == (
        "posible duplicado de S00140 (mismo cliente, monto parecido)")
    assert por_nombre["S00142"]["sospecha"] == (
        "posible duplicado de S00140 (mismo cliente, monto parecido)")


def test_sospecha_no_dispara_sin_motivo(odoo):
    p1 = odoo.agregar_partner("Una", "6211-4444")
    p2 = odoo.agregar_partner("Otra", "6211-5555")
    odoo.agregar_orden(p1, "S00143", state="sale", amount_total=100.0,
                       dias_atras=3)
    # Mismo cliente, monto lejos: no es sospecha.
    odoo.agregar_orden(p1, "S00144", state="draft", amount_total=150.0,
                       create_date=_hace(0))
    # Mismo monto, OTRO cliente: tampoco.
    odoo.agregar_orden(p2, "S00145", state="draft", amount_total=100.0,
                       create_date=_hace(0))
    # Y una cancelada del mismo cliente y monto no cuenta como "otra".
    odoo.agregar_orden(p2, "S00146", state="cancel", dias_atras=2,
                       amount_total=100.0)
    hoy_creado = reconciliacion.informe_datos()["creado_hoy"]
    por_nombre = {o["nombre"]: o for o in hoy_creado["ordenes"]}
    assert por_nombre["S00144"]["sospecha"] == ""
    assert por_nombre["S00145"]["sospecha"] == ""


def test_creado_hoy_no_toca_el_dinero_de_las_clases(odoo):
    # La sección es informativa: una orden de hoy se clasifica igual que
    # cualquier otra, y un pago de hoy NO le pone plata a nadie (el
    # pagado sigue saliendo de las facturas de cada orden).
    p = odoo.agregar_partner("Hoy Sin Plata", "6211-6666")
    odoo.agregar_orden(p, "S00147", state="sale", amount_total=200.0,
                       create_date=_hace(0))
    odoo.agregar_pago(200.0, p, create_date=_hace(0))
    datos_informe = reconciliacion.informe_datos()
    venta = _ventas_por_nombre(datos_informe)["S00147"]
    assert venta["pagado"] == 0.0 and venta["debe"] == 200.0
    assert len(datos_informe["creado_hoy"]["pagos"]) == 1


# ---------------------------------------------------------------------------
# La tanda histórica (ORDENES_HISTORICAS): clase intacta, motivo marcado
# ---------------------------------------------------------------------------

def test_historica_clase_d_cambia_el_motivo_entero(odoo):
    # Una de la tanda vieja, pagada completa con la salida sin validar:
    # sigue siendo D (el dinero está bien), pero el motivo ya no suena a
    # entrega pendiente de hoy.
    p = odoo.agregar_partner("Venta Vieja", "6222-1111")
    f = odoo.agregar_factura(300.0, 0.0)
    odoo.agregar_orden(p, "S00113", state="sale", amount_total=300.0,
                       facturas=[f], salidas=[odoo.agregar_salida()])
    venta = _ventas_por_nombre(reconciliacion.informe_datos())["S00113"]
    assert venta["clase"] == "D"
    assert venta["historica"] is True
    assert venta["motivo"] == reconciliacion.TEXTO_D_HISTORICA


def test_historica_de_otra_clase_gana_el_sufijo(odoo):
    # Emiraf (S00078): confirmada con saldo — C de siempre, con la marca.
    p = odoo.agregar_partner("Emiraf", "6222-2222")
    odoo.agregar_orden(p, "S00078", state="sale", amount_total=3420.0)
    venta = _ventas_por_nombre(reconciliacion.informe_datos())["S00078"]
    assert venta["clase"] == "C"
    assert venta["historica"] is True
    assert venta["motivo"].endswith(reconciliacion.SUFIJO_HISTORICA)


def test_venta_real_de_hoy_no_lleva_la_marca(odoo):
    # S00107 (Sofia) es venta real: la lista es cerrada, no heurística —
    # ni la fecha ni el creador la vuelven histórica.
    p = odoo.agregar_partner("Sofia", "6222-3333")
    f = odoo.agregar_factura(80.0, 0.0)
    odoo.agregar_orden(p, "S00107", state="sale", amount_total=80.0,
                       facturas=[f], salidas=[odoo.agregar_salida()],
                       create_date=_hace(0), creado_por="Sesión Externa")
    venta = _ventas_por_nombre(reconciliacion.informe_datos())["S00107"]
    assert venta["clase"] == "D"
    assert venta["historica"] is False
    assert venta["motivo"] == "Pagada completa en Odoo; salida sin validar"


def test_creado_hoy_marca_las_historicas(odoo, tmp_path, capsys):
    p = odoo.agregar_partner("Externa Vieja", "6222-4444")
    odoo.agregar_orden(p, "S00116", state="sale", amount_total=50.0,
                       create_date=_hace(0), creado_por="Sesión Externa")
    odoo.agregar_orden(p, "S00290", state="sale", amount_total=999.0,
                       create_date=_hace(0))
    por_nombre = {o["nombre"]: o for o in
                  reconciliacion.informe_datos()["creado_hoy"]["ordenes"]}
    assert por_nombre["S00116"]["historica"] is True
    assert por_nombre["S00290"]["historica"] is False
    assert reconciliacion._cli(["informe", "--salida", str(tmp_path)]) == 0
    impreso = capsys.readouterr().out
    assert "S00116" in impreso and "— histórica registrada el 1/10" in impreso
    # La normal de hoy no gana la marca en el renglón del CLI.
    renglon_normal = next(l for l in impreso.splitlines() if "S00290" in l)
    assert "histórica" not in renglon_normal


# ---------------------------------------------------------------------------
# La prueba ANTI-ESCRITURA (las dos mitades del candado)
# ---------------------------------------------------------------------------

def test_el_codigo_fuente_no_nombra_metodos_de_escritura():
    """(a) El texto de reconciliacion.py no puede contener, como literal,
    ningún método de escritura de Odoo. Si esta prueba falla, alguien
    metió una llamada que muta — se quita la llamada, no la prueba."""
    ruta = reconciliacion.__file__
    with open(ruta, encoding="utf-8") as archivo:
        fuente = archivo.read()
    prohibidos = re.findall(
        r"""['"](?:create|write|unlink|copy|action_\w*|button_\w*)['"]""",
        fuente)
    assert prohibidos == [], (
        f"reconciliacion.py nombra métodos de escritura de Odoo: {prohibidos}")


def test_el_doble_revienta_ante_una_escritura(odoo):
    """(b) El doble de Odoo de ESTAS pruebas rechaza cualquier método que
    no sea de lectura: si el módulo intentara escribir, ninguna prueba
    pasaría por accidente."""
    with pytest.raises(AssertionError, match="ESCRITURA PROHIBIDA"):
        odoo.ejecutar("sale.order", "unlink", [[1]])
    with pytest.raises(AssertionError, match="ESCRITURA PROHIBIDA"):
        odoo.ejecutar("sale.order", "action_confirm", [[1]])


def test_un_informe_completo_solo_lee(odoo, monkeypatch):
    """Un informe de punta a punta, con todas las fuentes vivas, deja en
    el doble SOLO llamadas de lectura."""
    etapas = odoo.con_etapas_flujo()
    odoo.agregar_diario(reconciliacion.DIARIO_FUERA_DE_ALCANCE)
    p = odoo.agregar_partner("Recorrido", "6199-1111")
    f = odoo.agregar_factura(100.0, 50.0, payment_state="partial")
    odoo.agregar_orden(p, "S00128", state="sale", amount_total=100.0,
                       facturas=[f], salidas=[odoo.agregar_salida()],
                       oportunidad=odoo.agregar_oportunidad(etapas["cotizado"]))
    _con_leads(monkeypatch, _lead("6199-1111", "COTIZADO"))
    reconciliacion.informe_datos()
    assert odoo.llamadas, "el informe no le preguntó nada a Odoo"
    assert all(metodo in OdooReconcilia.LECTURAS
               for _, metodo in odoo.llamadas)


def test_la_puerta_leer_rechaza_metodos_vetados(odoo):
    with pytest.raises(RuntimeError, match="SOLO lectura"):
        reconciliacion._leer("sale.order", "unlink", [[1]])


# ---------------------------------------------------------------------------
# La línea de comandos
# ---------------------------------------------------------------------------

def test_cli_informe_imprime_y_escribe_csv(odoo, tmp_path, capsys):
    p = odoo.agregar_partner("Para El CSV", "6199-2222")
    odoo.agregar_orden(p, "S00129", state="draft", amount_total=35.0)
    odoo.agregar_orden(p, "S00134", state="draft", amount_total=35.0,
                       create_date=_hace(0), creado_por="Sesión Externa")
    salida = reconciliacion._cli(["informe", "--salida", str(tmp_path)])
    assert salida == 0
    impreso = capsys.readouterr().out
    assert "A Cotizada: 2" in impreso
    assert "Rojo (F+G+H): 0" in impreso
    # La sección de lo creado hoy, con quién lo creó, la sospecha y el
    # total para comparar contra lo que otra sesión reporte.
    assert ("Creado hoy fuera de Orquesta: 1 pedidos · 0 facturas · "
            "0 pagos") in impreso
    assert "creado por Sesión Externa" in impreso
    assert "posible duplicado de S00129" in impreso
    assert "Total de pedidos creados hoy: $35.00" in impreso
    archivos = list(tmp_path.glob("reconciliacion_*.csv"))
    assert len(archivos) == 1
    contenido = archivos[0].read_text(encoding="utf-8")
    assert "S00129" in contenido
    # El CSV no cambia con la extensión: mismas columnas de siempre.
    assert contenido.splitlines()[0].startswith("clase,orden,cliente,total")
    assert "creado_por" not in contenido.splitlines()[0]
