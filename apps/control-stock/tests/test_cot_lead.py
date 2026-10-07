"""Pruebas de cot_lead.py: la capa de datos que conecta un lead del CRM con
sus cotizaciones/ventas de Odoo, con un Odoo simulado.

Mismo patrón que test_cotizaciones.py y test_ventas.py: se reemplaza
`ventas._ejecutar` con un doble que entiende SOLO lo que cot_lead.py
produce (dominios simples, sin relaciones), y `ventas.descargar_pdf` para
el PDF. No se toca sqlite: cot_lead.py no guarda nada propio.

La conexión es por `lead_ref`/`lead_real` (corregido el 28/09/2026):
`client_order_ref` es del order-api (el VR-XXXXXX de un pedido en línea) y
aquí solo se usa de solo lectura, para descartar referencias internas. El
doble de Odoo simula los dos campos nuevos del addon aunque hoy no existan
todavía en el Odoo real — este módulo se prueba contra el contrato, no
contra el estado actual de producción.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app import cot_lead, linear_leads, ventas


def _fecha_hace(dias):
    """Una fecha en el formato que Odoo devuelve para datetime (UTC, sin
    zona), a N días de ahora — para simular órdenes recientes o viejas."""
    return (datetime.now(timezone.utc) - timedelta(days=dias)).strftime(
        "%Y-%m-%d %H:%M:%S")


class OdooCotLead:
    """Un Odoo mínimo: solo sale.order y res.partner, con un evaluador de
    dominio que entiende exactamente las formas que cot_lead.py arma
    (listas de hojas `[campo, op, valor]`, ANDadas implícitamente — nunca
    usa "|", porque el filtrado por teléfono/nombre lo hace en Python)."""

    def __init__(self):
        self.partners = {}
        self.ordenes = {}
        self._siguiente_partner = 100
        self._siguiente_orden = 9000
        self.fallar = False  # simula Odoo caído

    def agregar_partner(self, nombre, phone=""):
        self._siguiente_partner += 1
        pid = self._siguiente_partner
        self.partners[pid] = {"name": nombre, "phone": phone}
        return pid

    def agregar_orden(self, partner_id, name, dias_atras=0, amount_total=0.0,
                      state="draft", etapa_cobro="cotizado", total_pagado=0.0,
                      client_order_ref=None, lead_ref=None, lead_real=False,
                      linear_issue_url=None, reemplazada_por_id=False):
        self._siguiente_orden += 1
        oid = self._siguiente_orden
        self.ordenes[oid] = {
            "id": oid,
            "partner_id": [partner_id, self.partners[partner_id]["name"]],
            "name": name,
            "date_order": _fecha_hace(dias_atras),
            "amount_total": amount_total,
            "state": state,
            "etapa_cobro": etapa_cobro,
            "total_pagado": total_pagado,
            "saldo_pendiente": round(amount_total - total_pagado, 2),
            # client_order_ref: el VR-XXXXXX del order-api (o una ref
            # interna de la app) — nunca lo escribe cot_lead.py.
            "client_order_ref": client_order_ref or False,
            "lead_ref": lead_ref or False,
            "lead_real": bool(lead_real),
            "linear_issue_url": linear_issue_url or False,
            "reemplazada_por_id": reemplazada_por_id,
            # La casilla del 50/50 (28/09/2026): en el Odoo real nace en
            # True; el falso hace lo mismo para que "no puesta" no se
            # confunda con "apagada".
            "pago_50_50": True,
        }
        return oid

    def ejecutar(self, modelo, metodo, args, kw=None):
        if self.fallar:
            raise ConnectionError("Odoo no contesta")
        kw = kw or {}
        manejador = getattr(self, (modelo + "_" + metodo).replace(".", "_"))
        return manejador(args, kw)

    # ---- dominio: solo hojas ANDadas, sin "|" ----
    def _coincide(self, orden, dominio):
        for hoja in dominio:
            campo, op, valor = hoja
            actual = orden.get(campo)
            if op == "=ilike":
                if str(actual or "").strip().lower() != str(valor or "").strip().lower():
                    return False
            elif op == "=":
                # Igualdad sin comodines, para dominios que filtran por un
                # campo exacto (por ejemplo lead_real).
                if actual != valor:
                    return False
            elif op == "!=":
                if actual == valor:
                    return False
            elif op == "in":
                # El dominio de `plata_de_varios` (BLOQUE 43): todos los
                # PP del tablero en UNA consulta. Se compara sin distinguir
                # mayúsculas, igual que el "=ilike" de una sola orden.
                vistos = {str(v or "").strip().lower() for v in (valor or [])}
                if str(actual or "").strip().lower() not in vistos:
                    return False
            else:
                raise NotImplementedError(f"operador no soportado en la prueba: {op}")
        return True

    def sale_order_search_read(self, args, kw):
        dominio = args[0]
        campos = kw.get("fields", [])
        filas = [o for o in self.ordenes.values() if self._coincide(o, dominio)]
        return [{"id": f["id"], **{c: f.get(c, False) for c in campos}} for f in filas]

    def sale_order_search(self, args, kw):
        dominio = args[0]
        return [o["id"] for o in self.ordenes.values() if self._coincide(o, dominio)]

    def sale_order_read(self, args, kw):
        campos = kw.get("fields", [])
        return [{"id": oid, **{c: self.ordenes[oid].get(c, False) for c in campos}}
                for oid in args[0] if oid in self.ordenes]

    def sale_order_write(self, args, kw):
        for oid in args[0]:
            self.ordenes[oid].update(args[1])
        return True

    def res_partner_read(self, args, kw):
        campos = kw.get("fields", [])
        return [{"id": pid, **{c: self.partners[pid].get(c, False) for c in campos}}
                for pid in args[0] if pid in self.partners]


@pytest.fixture
def odoo(monkeypatch):
    falso = OdooCotLead()
    monkeypatch.setattr(ventas, "_ejecutar", falso.ejecutar)
    return falso


# ---------------------------------------------------------------------------
# El caso de Tamara: dos órdenes conectadas al mismo lead, UNA real
# ---------------------------------------------------------------------------

def test_dos_ordenes_conectadas_una_es_la_real(odoo):
    lead = {"ref": "LEAD-91", "pp": "PP-70211", "nombre": "Tamara",
           "celular": "6552-0966", "url": "https://linear.app/x/issue/LEAD-91"}
    partner = odoo.agregar_partner("Tamara", "6552-0966")
    o1 = odoo.agregar_orden(partner, "S00079", dias_atras=5, amount_total=787.0,
                            lead_ref="PP-70211", lead_real=True)
    o2 = odoo.agregar_orden(partner, "S00081", dias_atras=1, amount_total=1525.0,
                            lead_ref="PP-70211")

    resultado = cot_lead.ordenes_del_lead(lead)
    assert resultado["ok"] is True
    ordenes = {o["orden_id"]: o for o in resultado["ordenes"]}
    assert set(ordenes) == {o1, o2}
    assert ordenes[o1]["es_real"] is True
    assert ordenes[o2]["es_real"] is False
    # Son trabajos distintos, no versiones: las dos siguen en borrador, sin
    # que una haya reemplazado a la otra (reemplazada_por_id intacto).
    assert ordenes[o1]["reemplazada"] is False
    assert ordenes[o2]["reemplazada"] is False


def test_marcar_real_se_lo_quita_a_la_otra(odoo):
    lead = {"ref": "LEAD-91", "pp": "PP-70211", "nombre": "Tamara",
           "celular": "6552-0966", "url": "https://linear.app/x/issue/LEAD-91"}
    partner = odoo.agregar_partner("Tamara", "6552-0966")
    o1 = odoo.agregar_orden(partner, "S00079", amount_total=787.0,
                            lead_ref="PP-70211", lead_real=True)
    o2 = odoo.agregar_orden(partner, "S00081", amount_total=1525.0,
                            lead_ref="PP-70211")

    cot_lead.marcar_real(o2, lead)

    ordenes = {o["orden_id"]: o for o in cot_lead.ordenes_del_lead(lead)["ordenes"]}
    assert ordenes[o2]["es_real"] is True
    assert ordenes[o1]["es_real"] is False


def test_marcar_real_exige_estar_conectada(odoo):
    lead = {"ref": "LEAD-1", "pp": "PP-11111", "nombre": "Nadie",
           "celular": "", "url": "https://x/issue/LEAD-1"}
    partner = odoo.agregar_partner("Nadie")
    suelta = odoo.agregar_orden(partner, "S00099", amount_total=100.0)
    with pytest.raises(ValueError, match="conéctala primero"):
        cot_lead.marcar_real(suelta, lead)


def test_desconectar_limpia_lead_ref_y_lead_real(odoo):
    lead = {"ref": "LEAD-91", "pp": "PP-70211", "nombre": "Tamara",
           "celular": "", "url": "https://x/issue/LEAD-91"}
    partner = odoo.agregar_partner("Tamara")
    o1 = odoo.agregar_orden(partner, "S00079", amount_total=787.0,
                            lead_ref="PP-70211", lead_real=True,
                            linear_issue_url="https://x/issue/LEAD-91")
    cot_lead.desconectar(o1)
    fila = odoo.ordenes[o1]
    assert fila["lead_ref"] is False
    assert fila["lead_real"] is False
    # El link del kanban de cobro NO es la conexión: desconectar no lo toca.
    assert fila["linear_issue_url"] == "https://x/issue/LEAD-91"


def test_quitar_real_no_desconecta(odoo):
    lead = {"ref": "LEAD-91", "pp": "PP-70211", "nombre": "Tamara",
           "celular": "", "url": "https://x/issue/LEAD-91"}
    partner = odoo.agregar_partner("Tamara")
    o1 = odoo.agregar_orden(partner, "S00079", amount_total=787.0,
                            lead_ref="PP-70211", lead_real=True)
    cot_lead.quitar_real(o1)
    fila = odoo.ordenes[o1]
    assert fila["lead_real"] is False
    assert fila["lead_ref"] == "PP-70211"


def test_conectar_rechaza_orden_de_otro_lead(odoo):
    partner = odoo.agregar_partner("Ximena")
    orden = odoo.agregar_orden(partner, "S00050", amount_total=50.0,
                               lead_ref="PP-70203")
    lead_ajeno = {"ref": "LEAD-1", "pp": "PP-11111", "nombre": "Otro",
                 "celular": "", "url": ""}
    with pytest.raises(ValueError, match="ya está conectada a otro lead"):
        cot_lead.conectar(orden, lead_ajeno)


def test_conectar_sin_pp_avisa(odoo):
    partner = odoo.agregar_partner("Sin PP")
    orden = odoo.agregar_orden(partner, "S00060", amount_total=10.0)
    # El aviso dice el código que falta en palabras (A10: «PP-XXXXX» era
    # notación de código), pero sigue nombrando el PP-.
    with pytest.raises(ValueError, match="PP-"):
        cot_lead.conectar(orden, {"ref": "LEAD-9", "pp": "", "nombre": "x"})


def test_conectar_no_toca_client_order_ref(odoo):
    """El candado del cambio del 28/09/2026: conectar/desconectar/marcar
    real nunca escriben `client_order_ref` — ese campo es del order-api
    (el VR-XXXXXX de un pedido en línea) y aquí es de solo lectura."""
    lead = {"ref": "LEAD-1", "pp": "PP-11111", "nombre": "Cliente en línea",
           "celular": "", "url": "https://x/issue/LEAD-1"}
    partner = odoo.agregar_partner("Cliente en línea")
    orden = odoo.agregar_orden(partner, "S00200", amount_total=200.0,
                               total_pagado=200.0, etapa_cobro="pagado",
                               client_order_ref="VR-549312")

    cot_lead.conectar(orden, lead)
    assert odoo.ordenes[orden]["client_order_ref"] == "VR-549312"
    assert odoo.ordenes[orden]["lead_ref"] == "PP-11111"

    cot_lead.marcar_real(orden, lead)
    assert odoo.ordenes[orden]["client_order_ref"] == "VR-549312"

    cot_lead.desconectar(orden)
    assert odoo.ordenes[orden]["client_order_ref"] == "VR-549312"
    assert odoo.ordenes[orden]["lead_ref"] is False


def test_conectar_llena_el_link_solo_si_estaba_vacio(odoo):
    lead = {"ref": "LEAD-1", "pp": "PP-11111", "nombre": "x",
           "celular": "", "url": "https://x/issue/LEAD-1-nuevo"}
    partner = odoo.agregar_partner("x")
    con_link_previo = odoo.agregar_orden(
        partner, "S00001", amount_total=10.0,
        linear_issue_url="https://x/issue/LEAD-1-viejo")
    sin_link = odoo.agregar_orden(partner, "S00002", amount_total=10.0)

    cot_lead.conectar(con_link_previo, lead)
    cot_lead.conectar(sin_link, lead)

    # El que ya tenía un link no se pisa; el que no tenía se llena.
    assert odoo.ordenes[con_link_previo]["linear_issue_url"] == \
        "https://x/issue/LEAD-1-viejo"
    assert odoo.ordenes[sin_link]["linear_issue_url"] == \
        "https://x/issue/LEAD-1-nuevo"


# ---------------------------------------------------------------------------
# Candidatas
# ---------------------------------------------------------------------------

def test_orden_de_otro_lead_no_es_candidata(odoo):
    lead = {"ref": "LEAD-62", "pp": "PP-8HTJC", "nombre": "Ilayda Yerusalmi",
           "celular": "", "url": ""}
    partner = odoo.agregar_partner("Ilayda Yerusalmi")
    odoo.agregar_orden(partner, "S00090", dias_atras=2, amount_total=158.75,
                       lead_ref="PP-99999")  # de otro lead

    resultado = cot_lead.candidatas_del_lead(lead)
    assert resultado["ok"] is True
    assert resultado["candidatas"] == []


def test_pedido_en_linea_vr_es_candidata_y_client_order_ref_no_cambia(odoo):
    """El caso que motivó el cambio del 28/09/2026: un pedido en línea
    (`client_order_ref = "VR-549312"`, ya pagado) tiene que poder
    conectarse a un lead, y su número público no se toca en ningún paso."""
    lead = {"ref": "LEAD-70", "pp": "PP-70070", "nombre": "Cliente Online",
           "celular": "6000-1234", "url": "https://x/issue/LEAD-70"}
    partner = odoo.agregar_partner("Cliente Online", "6000-1234")
    orden = odoo.agregar_orden(
        partner, "S00095", dias_atras=3, amount_total=99.99,
        total_pagado=99.99, etapa_cobro="pagado",
        client_order_ref="VR-549312")

    candidatas = cot_lead.candidatas_del_lead(lead)
    assert [c["orden_id"] for c in candidatas["candidatas"]] == [orden]

    cot_lead.conectar(orden, lead)
    cot_lead.marcar_real(orden, lead)
    cot_lead.desconectar(orden)

    # El VR- sobrevive intacto a conectar, marcar real y desconectar.
    assert odoo.ordenes[orden]["client_order_ref"] == "VR-549312"


def test_busqueda_por_telefono_con_y_sin_guion(odoo):
    lead_con_guion = {"ref": "LEAD-1", "pp": "PP-11111", "nombre": "",
                      "celular": "6552-0966", "url": ""}
    partner = odoo.agregar_partner("Tamara Cliente", "65520966")  # sin guion en Odoo
    orden = odoo.agregar_orden(partner, "S00079", dias_atras=5, amount_total=787.0)

    resultado = cot_lead.candidatas_del_lead(lead_con_guion)
    assert resultado["ok"] is True
    assert [c["orden_id"] for c in resultado["candidatas"]] == [orden]

    lead_sin_guion = {**lead_con_guion, "celular": "65520966"}
    otro_partner = odoo.agregar_partner("Marcos Cliente", "6552-0966")  # con guion en Odoo
    otra_orden = odoo.agregar_orden(otro_partner, "S00080", dias_atras=1, amount_total=99.0)

    resultado2 = cot_lead.candidatas_del_lead(lead_sin_guion)
    ids = {c["orden_id"] for c in resultado2["candidatas"]}
    assert ids == {orden, otra_orden}


def test_busqueda_por_numero_de_orden(odoo):
    lead = {"ref": "LEAD-9", "pp": "PP-90000", "nombre": "Nadie Conocido",
           "celular": "", "url": ""}
    partner = odoo.agregar_partner("Cliente a otro nombre", "")
    orden = odoo.agregar_orden(partner, "S00090", dias_atras=5, amount_total=158.75)
    # No calza ni por teléfono ni por nombre: solo el buscador libre la trae.
    resultado_sin_texto = cot_lead.candidatas_del_lead(lead)
    assert resultado_sin_texto["candidatas"] == []

    resultado = cot_lead.candidatas_del_lead(lead, texto_libre="s00090")
    assert [c["orden_id"] for c in resultado["candidatas"]] == [orden]


def test_orden_mas_vieja_de_60_dias_queda_fuera(odoo):
    lead = {"ref": "LEAD-1", "pp": "PP-11111", "nombre": "Cliente Viejo",
           "celular": "", "url": ""}
    partner = odoo.agregar_partner("Cliente Viejo")
    odoo.agregar_orden(partner, "S00001", dias_atras=61, amount_total=50.0)
    reciente = odoo.agregar_orden(partner, "S00002", dias_atras=59, amount_total=50.0)

    resultado = cot_lead.candidatas_del_lead(lead)
    assert [c["orden_id"] for c in resultado["candidatas"]] == [reciente]


def test_referencias_internas_nunca_son_candidatas(odoo):
    lead = {"ref": "LEAD-1", "pp": "PP-11111", "nombre": "Cliente Local",
           "celular": "", "url": ""}
    partner = odoo.agregar_partner("Cliente Local")
    odoo.agregar_orden(partner, "S00001", dias_atras=1, amount_total=50.0,
                       client_order_ref="VISTA PREVIA rvargas")
    odoo.agregar_orden(partner, "S00002", dias_atras=1, amount_total=50.0,
                       client_order_ref="MUESTRA-PDF")

    resultado = cot_lead.candidatas_del_lead(lead, texto_libre="s000")
    assert resultado["candidatas"] == []


# ---------------------------------------------------------------------------
# Odoo caído: "no sé" nunca se confunde con "no hay"
# ---------------------------------------------------------------------------

def test_odoo_caido_no_es_lista_vacia(odoo):
    lead = {"ref": "LEAD-1", "pp": "PP-11111", "nombre": "x", "celular": "", "url": ""}
    odoo.fallar = True
    resultado = cot_lead.ordenes_del_lead(lead)
    assert resultado["ok"] is False
    assert resultado["error"]
    assert resultado["ordenes"] == []

    resultado_candidatas = cot_lead.candidatas_del_lead(lead)
    assert resultado_candidatas["ok"] is False
    assert resultado_candidatas["candidatas"] == []

    resultado_plata = cot_lead.plata_de_la_real(lead)
    assert resultado_plata["ok"] is False

    resultado_estado = cot_lead.estado_sugerido(lead)
    assert resultado_estado["ok"] is False


def test_sin_pp_no_es_un_error_es_no_hay(odoo):
    lead = {"ref": "LEAD-1", "pp": "", "nombre": "x", "celular": "", "url": ""}
    resultado = cot_lead.ordenes_del_lead(lead)
    assert resultado == {"ok": True, "error": None, "ordenes": []}


# ---------------------------------------------------------------------------
# La plata de la real, y el estado que sugiere para el embudo
# ---------------------------------------------------------------------------

def test_plata_de_la_real_sin_conexion(odoo):
    lead = {"ref": "LEAD-1", "pp": "PP-11111", "nombre": "x", "celular": "", "url": ""}
    resultado = cot_lead.plata_de_la_real(lead)
    assert resultado == {"ok": True, "error": None, "hay_real": False}


def test_plata_de_la_real_calcula_el_abono(odoo):
    lead = {"ref": "LEAD-62", "pp": "PP-8HTJC", "nombre": "Ilayda Yerusalmi",
           "celular": "", "url": "https://x/issue/LEAD-62"}
    partner = odoo.agregar_partner("Ilayda Yerusalmi")
    odoo.agregar_orden(partner, "S00090", amount_total=158.75, total_pagado=0.0,
                       lead_ref="PP-8HTJC", lead_real=True)

    resultado = cot_lead.plata_de_la_real(lead)
    assert resultado["ok"] is True
    assert resultado["hay_real"] is True
    assert resultado["orden"] == "S00090"
    assert resultado["total"] == 158.75
    assert resultado["abono_50"] == 79.38
    assert resultado["pagado"] == 0.0
    assert resultado["saldo"] == 158.75


@pytest.mark.parametrize("etapa_cobro,pagado,estado_esperado,etiqueta_esperada", [
    ("cotizado", 0.0, "COTIZADO", None),
    ("abono", 50.0, "POR_AGENDAR", "Abono 50%"),
    ("pagado", 100.0, "POR_AGENDAR", "Pagado 100%"),
])
def test_estado_sugerido_por_etapa_de_pago(odoo, etapa_cobro, pagado,
                                           estado_esperado, etiqueta_esperada):
    lead = {"ref": "LEAD-1", "pp": "PP-11111", "nombre": "x",
           "celular": "", "url": "https://x/issue/LEAD-1"}
    partner = odoo.agregar_partner("Cliente")
    odoo.agregar_orden(partner, "S00100", amount_total=100.0, total_pagado=pagado,
                       etapa_cobro=etapa_cobro, lead_ref="PP-11111", lead_real=True)

    resultado = cot_lead.estado_sugerido(lead)
    assert resultado["ok"] is True
    assert resultado["estado"] == estado_esperado
    assert resultado["etiqueta_pago"] == etiqueta_esperada


def test_estado_sugerido_sin_real_no_sugiere_nada(odoo):
    lead = {"ref": "LEAD-1", "pp": "PP-11111", "nombre": "x", "celular": "", "url": ""}
    resultado = cot_lead.estado_sugerido(lead)
    assert resultado == {"ok": True, "error": None, "estado": None, "etiqueta_pago": None}


def test_las_etiquetas_de_pago_son_las_de_linear_leads():
    """Candado contra el copy-paste: si linear_leads.LABELS_PAGO cambia de
    orden o de nombre, esta prueba lo nota antes que un issue mal
    etiquetado."""
    assert cot_lead.ETIQUETA_ABONO == "Abono 50%"
    assert cot_lead.ETIQUETA_PAGADO == "Pagado 100%"
    assert cot_lead.ETIQUETA_ABONO in linear_leads.LABELS_PAGO
    assert cot_lead.ETIQUETA_PAGADO in linear_leads.LABELS_PAGO


# ---------------------------------------------------------------------------
# El PDF de una orden
# ---------------------------------------------------------------------------

def test_pdf_de_orden_pide_el_reporte_correcto(monkeypatch):
    llamadas = []

    def falso(reporte, orden_id):
        llamadas.append((reporte, orden_id))
        return b"%PDF-contenido"

    monkeypatch.setattr(ventas, "descargar_pdf", falso)
    assert cot_lead.pdf_de_orden(90) == b"%PDF-contenido"
    assert llamadas == [("sale.report_saleorder", 90)]


def test_pdf_de_orden_envuelve_el_error(monkeypatch):
    def revienta(reporte, orden_id):
        raise RuntimeError("Odoo no devolvió el PDF")

    monkeypatch.setattr(ventas, "descargar_pdf", revienta)
    with pytest.raises(RuntimeError, match="No se pudo generar el PDF"):
        cot_lead.pdf_de_orden(90)



# ---------------------------------------------------------------------------
# La marca de ambigüedad (2/10/2026, OK de Abraham): un teléfono que también
# es de OTRO lead vivo marca la candidata — avisa, no bloquea, y no toca el
# amarre automático del espejo (eso es del frontend).
# ---------------------------------------------------------------------------

def test_telefono_de_dos_leads_vivos_marca_la_candidata(odoo, monkeypatch):
    monkeypatch.delenv("LINEAR_API_KEY", raising=False)
    linear_leads.reiniciar_muestra()
    # En la muestra, el 6552-0966 es de LEAD-91 (Tamara, viva en «Por
    # agendar»). Se mira OTRO lead con ese mismo teléfono.
    lead = {"ref": "LEAD-200", "pp": "PP-XX200", "nombre": "Cliente B",
           "celular": "6552-0966", "url": ""}
    partner = odoo.agregar_partner("Cliente B", "6552-0966")
    orden = odoo.agregar_orden(partner, "S00097", dias_atras=1,
                               amount_total=80.0)
    resultado = cot_lead.candidatas_del_lead(lead)
    assert resultado["ok"] is True
    candidata = next(c for c in resultado["candidatas"]
                     if c["orden_id"] == orden)
    assert candidata["ambigua"] is True
    assert "más de un cliente" in candidata["aviso_ambigua"]


def test_telefono_unico_no_lleva_marca(odoo, monkeypatch):
    monkeypatch.delenv("LINEAR_API_KEY", raising=False)
    linear_leads.reiniciar_muestra()
    lead = {"ref": "LEAD-201", "pp": "PP-XX201", "nombre": "Cliente Solo",
           "celular": "6999-9871", "url": ""}
    partner = odoo.agregar_partner("Cliente Solo", "6999-9871")
    orden = odoo.agregar_orden(partner, "S00098", dias_atras=1,
                               amount_total=40.0)
    resultado = cot_lead.candidatas_del_lead(lead)
    candidata = next(c for c in resultado["candidatas"]
                     if c["orden_id"] == orden)
    assert candidata["ambigua"] is False
    assert candidata["aviso_ambigua"] == ""


def test_el_mismo_lead_no_se_cuenta_como_otro(odoo, monkeypatch):
    """El teléfono del PROPIO lead que se está mirando no es ambigüedad:
    solo cuenta un lead DISTINTO y vivo."""
    monkeypatch.delenv("LINEAR_API_KEY", raising=False)
    linear_leads.reiniciar_muestra()
    # LEAD-91 mirándose a sí mismo, con su propio teléfono de la muestra.
    lead = {"ref": "LEAD-91", "pp": "PP-70211", "nombre": "Tamara",
           "celular": "6552-0966", "url": ""}
    partner = odoo.agregar_partner("Tamara", "6552-0966")
    orden = odoo.agregar_orden(partner, "S00099", dias_atras=1,
                               amount_total=60.0)
    resultado = cot_lead.candidatas_del_lead(lead)
    candidata = next(c for c in resultado["candidatas"]
                     if c["orden_id"] == orden)
    assert candidata["ambigua"] is False


def test_sin_linear_las_candidatas_salen_sin_marca(odoo, monkeypatch):
    """Best-effort: la marca es un extra — Linear caído no esconde
    candidatas ni revienta la ficha."""
    monkeypatch.delenv("LINEAR_API_KEY", raising=False)
    linear_leads.reiniciar_muestra()

    def revienta(refrescar=False):
        raise linear_leads.ErrorLeads("Linear no contesta")

    monkeypatch.setattr(linear_leads, "listar", revienta)
    lead = {"ref": "LEAD-202", "pp": "PP-XX202", "nombre": "Cliente C",
           "celular": "6552-0966", "url": ""}
    partner = odoo.agregar_partner("Cliente C", "6552-0966")
    odoo.agregar_orden(partner, "S00100", dias_atras=1, amount_total=10.0)
    resultado = cot_lead.candidatas_del_lead(lead)
    assert resultado["ok"] is True
    assert resultado["candidatas"]
    assert all(not c["ambigua"] for c in resultado["candidatas"])


# ---------------------------------------------------------------------------
# La plata de MUCHOS leads en UNA consulta (BLOQUE 43): el monto de cada
# tarjeta del CRM. Lo mismo que `plata_de_la_real`, pero para el tablero
# entero — pedirlo lead por lead serían ~25 viajes XML-RPC por pintada.
# ---------------------------------------------------------------------------

def _lead(ref, pp, nombre="Cliente"):
    return {"ref": ref, "pp": pp, "nombre": nombre, "celular": "", "url": ""}


def test_la_plata_de_varios_sale_en_una_sola_consulta(odoo, monkeypatch):
    uno = _lead("LEAD-201", "PP-XX201", "Cliente A")
    dos = _lead("LEAD-202", "PP-XX202", "Cliente B")
    pa = odoo.agregar_partner("Cliente A")
    pb = odoo.agregar_partner("Cliente B")
    odoo.agregar_orden(pa, "S00111", amount_total=140.0, total_pagado=70.0,
                       lead_ref="PP-XX201", lead_real=True)
    # Una conectada que NO es la real: no cuenta para el monto.
    odoo.agregar_orden(pa, "S00112", amount_total=999.0, lead_ref="PP-XX201")
    odoo.agregar_orden(pb, "S00113", amount_total=50.0, lead_ref="PP-XX202",
                       lead_real=True)

    consultas = []
    real = odoo.ejecutar

    def contando(modelo, metodo, args, kw=None):
        consultas.append((modelo, metodo))
        return real(modelo, metodo, args, kw)

    monkeypatch.setattr(ventas, "_ejecutar", contando)
    resultado = cot_lead.plata_de_varios([uno, dos])
    assert resultado["ok"] is True
    assert len(consultas) == 1
    assert resultado["por_lead"]["LEAD-201"]["total"] == 140.0
    assert resultado["por_lead"]["LEAD-201"]["saldo"] == 70.0
    assert resultado["por_lead"]["LEAD-201"]["orden"] == "S00111"
    assert resultado["por_lead"]["LEAD-202"]["total"] == 50.0


def test_un_lead_sin_orden_real_no_aparece_y_eso_es_no_tiene(odoo):
    uno = _lead("LEAD-201", "PP-XX201")
    partner = odoo.agregar_partner("Cliente A")
    odoo.agregar_orden(partner, "S00111", amount_total=140.0,
                       lead_ref="PP-XX201")   # conectada, no real
    resultado = cot_lead.plata_de_varios([uno])
    assert resultado["ok"] is True
    assert resultado["por_lead"] == {}


def test_un_lead_sin_pp_no_se_le_pregunta_a_odoo(odoo, monkeypatch):
    def nunca(*a, **kw):
        raise AssertionError("sin PP no hay nada que buscar")

    monkeypatch.setattr(ventas, "_ejecutar", nunca)
    assert cot_lead.plata_de_varios([_lead("LEAD-201", "")])["por_lead"] == {}
    assert cot_lead.plata_de_varios([])["por_lead"] == {}


def test_odoo_caido_deja_el_monto_vacio_nunca_en_cero(odoo):
    uno = _lead("LEAD-201", "PP-XX201")
    partner = odoo.agregar_partner("Cliente A")
    odoo.agregar_orden(partner, "S00111", amount_total=140.0,
                       lead_ref="PP-XX201", lead_real=True)
    odoo.fallar = True
    resultado = cot_lead.plata_de_varios([uno])
    assert resultado["ok"] is False
    assert resultado["error"]
    assert resultado["por_lead"] == {}   # ni un 0.0 inventado


def test_la_plata_de_varios_dice_lo_mismo_que_la_de_uno(odoo):
    # La regla del abono del 50% vive en UN solo lugar
    # (`_plata_de_orden`): las dos puertas tienen que coincidir.
    uno = _lead("LEAD-201", "PP-XX201")
    partner = odoo.agregar_partner("Cliente A")
    odoo.agregar_orden(partner, "S00111", amount_total=140.0,
                       total_pagado=70.0, lead_ref="PP-XX201", lead_real=True)
    assert (cot_lead.plata_de_varios([uno])["por_lead"]["LEAD-201"]
            == cot_lead.plata_de_la_real(uno))
