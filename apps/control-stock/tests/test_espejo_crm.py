"""El espejo de las ventas de Vender en el CRM (decisión del 22/09/2026).

Toda venta o cotización de Vender vive también en Linear/Twenty como un
lead normal (label "Hecha desde inventario") vía el puente de Vercel
(/api/crm/lead-inventario), y su oportunidad del Flujo de Odoo queda
amarrada por lead_ref, en etapa Cotizado, con la label "Odoo" avisada al
puente. Aquí se prueba la costura de control-stock con el puente fingido
(crm_leads.espejar_venta parcheado); el puente real vive en el frontend.

Regla del sync (Abraham, 22/09/2026): entre el kanban Retail y el Flujo
solo se espejan Cotizado y Facturado — cotizar pone Odoo "Cotizado" ↔
kanban "Cotizado · por facturar", y cobrar pone Odoo "Facturado" ↔ kanban
"Facturado · por entregar". Nada más se mueve solo.
"""
import pytest

from app import cotizaciones, crm_leads, retail, ventas
from test_cotizaciones import OdooServicios
from test_ventas import OdooFalso

EMPLEADA = {"id": "genesis", "nombre": "Génesis"}


@pytest.fixture
def odoo_servicios(monkeypatch, tmp_path, db_limpia):
    falso = OdooServicios()
    for variable, valor in {
        "ODOO_URL": "http://odoo-de-prueba:8069", "ODOO_DB": "pruebas",
        "ODOO_USER": "prueba", "ODOO_PASSWORD": "prueba",
        "VENTA_FOTOS_DIR": str(tmp_path / "fotos"),
    }.items():
        monkeypatch.setenv(variable, valor)
    monkeypatch.setattr(ventas, "_ejecutar", falso.ejecutar)
    return falso


@pytest.fixture
def odoo_venta(monkeypatch, tmp_path, db_limpia):
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


def _espejo_fijo(monkeypatch, respuesta):
    """Enchufa un puente fingido y devuelve la lista de llamadas."""
    llamadas = []

    def fingido(nombre, celular, tipo, orden, total, empleada, issue=""):
        llamadas.append({"nombre": nombre, "celular": celular, "tipo": tipo,
                         "orden": orden, "total": total, "empleada": empleada,
                         "issue": issue})
        return dict(respuesta) if respuesta else None

    monkeypatch.setattr(crm_leads, "espejar_venta", fingido)
    return llamadas


def _marcas_odoo(monkeypatch):
    marcas = []
    monkeypatch.setattr(crm_leads, "marcar_odoo", marcas.append)
    return marcas


def _cotizacion_de_prueba(celular="6001-2233"):
    return cotizaciones.crear_cotizacion(
        EMPLEADA, "boda", "Ana", celular,
        [{"texto": "Ambientación de la ceremonia", "monto": "300"}], [])


# --- cotizaciones de servicio ------------------------------------------------

def test_espejo_reusa_la_oportunidad_por_lead_ref(odoo_servicios, monkeypatch):
    """Cliente ya conocido (llegó por WhatsApp): la cotización cuelga de SU
    oportunidad, no nace otra tarjeta en el Flujo."""
    odoo = odoo_servicios
    odoo.oportunidades[500] = {"name": "PP-AAAAA", "lead_ref": "PP-AAAAA",
                               "tag_ids": [], "expected_revenue": 0.0}
    _espejo_fijo(monkeypatch, {
        "ok": True, "existente": True, "codigoRef": "PP-AAAAA",
        "identifier": "LEAD-9", "url": "https://linear.app/x/LEAD-9"})
    marcas = _marcas_odoo(monkeypatch)

    registro = _cotizacion_de_prueba()

    orden = odoo.ordenes[registro["orden_id"]]
    assert orden["opportunity_id"] == 500
    assert len(odoo.oportunidades) == 1  # ninguna oportunidad nueva
    oportunidad = odoo.oportunidades[500]
    assert oportunidad["stage_id"] == 7001  # Cotizado
    assert oportunidad["partner_id"] == orden["vals"]["partner_id"]
    assert odoo.tags[oportunidad["tag_ids"][0]] == "BODA"
    assert oportunidad["expected_revenue"] == 300.0
    assert marcas == ["PP-AAAAA"]  # la label "Odoo" se avisa tras el amarre
    assert registro["lead_ref"] == "PP-AAAAA"
    assert registro["lead_issue"] == "LEAD-9"
    assert registro["lead_url"].endswith("LEAD-9")


def test_espejo_sin_oportunidad_crea_y_graba_lead_ref(odoo_servicios, monkeypatch):
    """El puente abrió un lead nuevo pero su aviso a Odoo no llegó: la
    oportunidad se crea aquí y queda amarrada por lead_ref igual."""
    odoo = odoo_servicios
    _espejo_fijo(monkeypatch, {
        "ok": True, "existente": False, "codigoRef": "PP-BBBBB",
        "identifier": "LEAD-12", "url": "u"})
    marcas = _marcas_odoo(monkeypatch)

    registro = _cotizacion_de_prueba(celular="")

    orden = odoo.ordenes[registro["orden_id"]]
    oportunidad = odoo.oportunidades[orden["opportunity_id"]]
    assert oportunidad["lead_ref"] == "PP-BBBBB"
    assert oportunidad["stage_id"] == 7001
    assert marcas == ["PP-BBBBB"]
    assert registro["lead_ref"] == "PP-BBBBB"


def test_espejo_caido_no_rompe_la_cotizacion(odoo_servicios, monkeypatch):
    """Sin puente (o caído) la venta sale exactamente como antes."""
    odoo = odoo_servicios
    llamadas = _espejo_fijo(monkeypatch, None)

    registro = _cotizacion_de_prueba()

    assert llamadas, "el espejo sí se intentó"
    orden = odoo.ordenes[registro["orden_id"]]
    oportunidad = odoo.oportunidades[orden["opportunity_id"]]
    assert oportunidad["stage_id"] == 7001
    assert "lead_ref" not in oportunidad
    assert registro["lead_ref"] is None


def test_lead_pendiente_viaja_al_puente_y_queda_de_respaldo(odoo_servicios, monkeypatch):
    """La empleada vino de la ficha de un lead del kanban ("Cotizar en
    Vender"): ese issue viaja al puente para reutilizarse, se consume una
    sola vez, y si el puente está caído la cotización queda vinculada
    igual (fallback local)."""
    ventas.poner_lead_pendiente(EMPLEADA["id"], "LEAD-5", "Ana")
    llamadas = _espejo_fijo(monkeypatch, None)  # puente caído

    registro = _cotizacion_de_prueba()

    assert llamadas[0]["issue"] == "LEAD-5"
    assert registro["lead_issue"] == "LEAD-5"
    assert ventas.lead_pendiente(EMPLEADA["id"]) is None  # consumido


# --- ventas rápidas (Nueva Venta) --------------------------------------------

def test_venta_anonima_tambien_se_espeja(odoo_venta, monkeypatch):
    """Decisión de Abraham: TODAS las ventas van al puente, aun las de
    mostrador sin nombre ni celular (nacen como lead provisional). Pero si
    el puente está caído Y no hay datos, no se inventa tarjeta en el
    Flujo: no habría a quién dar seguimiento."""
    llamadas = _espejo_fijo(monkeypatch, None)
    ventas.agregar_al_carrito(EMPLEADA["id"], 501, 2)

    registro = ventas.crear_cotizacion(EMPLEADA, "", "")

    assert llamadas[0]["nombre"] == "" and llamadas[0]["celular"] == ""
    assert llamadas[0]["tipo"] == "venta"
    assert registro["lead_ref"] is None
    assert registro["oportunidad_id"] is None


def test_venta_espejada_marca_el_kanban_y_guarda_el_vinculo(odoo_venta, monkeypatch):
    """Con el puente vivo, la venta queda amarrada al lead (PP-XXXXX y
    LEAD-NN), a su oportunidad, y la tarjeta del kanban Retail cae en
    "Cotizado · por facturar" al instante."""
    _espejo_fijo(monkeypatch, {
        "ok": True, "existente": False, "codigoRef": "PP-CCCCC",
        "identifier": "LEAD-7", "url": "u"})
    monkeypatch.setattr(cotizaciones, "_oportunidad_espejada",
                        lambda partner, nombre, etiqueta, espejo: 321)
    odoo_venta.sale_order_write = lambda args, kw: True
    odoo_venta.crm_lead_write = lambda args, kw: True
    ventas.agregar_al_carrito(EMPLEADA["id"], 501, 1)

    registro = ventas.crear_cotizacion(EMPLEADA, "Rosa", "6011-2233")

    assert registro["lead_ref"] == "PP-CCCCC"
    assert registro["lead_issue"] == "LEAD-7"
    assert registro["oportunidad_id"] == 321
    assert retail._estados()["LEAD-7"]["etapa"] == "facturar"


def test_cobrar_avanza_flujo_a_facturado_y_kanban_a_entregar(odoo_venta, monkeypatch):
    """El sync solo vive en Cotizado y Facturado: al cobrar, la oportunidad
    pasa a Facturado (no a Pagado — Abono y Pagado los mueve Abraham) y la
    tarjeta del kanban a "Facturado · por entregar"."""
    _espejo_fijo(monkeypatch, {
        "ok": True, "existente": False, "codigoRef": "PP-DDDDD",
        "identifier": "LEAD-8", "url": "u"})
    monkeypatch.setattr(cotizaciones, "_oportunidad_espejada",
                        lambda *argumentos: 400)
    monkeypatch.setattr(
        cotizaciones, "_id_ref",
        lambda ref: {"vivero_rose_pedidos.etapa_flujo_facturado": 7003}.get(ref, 7001))
    escrituras_lead = []
    odoo_venta.sale_order_write = lambda args, kw: True
    odoo_venta.crm_lead_write = (
        lambda args, kw: escrituras_lead.append(args) or True)
    ventas.agregar_al_carrito(EMPLEADA["id"], 501, 1)
    registro = ventas.crear_cotizacion(EMPLEADA, "Rosa", "")

    venta = ventas.cobrar(registro["n"], "efectivo")

    assert venta["estado"] == "pagado"
    assert any(args[1].get("stage_id") == 7003 for args in escrituras_lead)
    assert retail._estados()["LEAD-8"]["etapa"] == "entregar"
