"""Control <-> cot_lead: la cotización conectada en la ficha del lead
(28/09/2026, pedido de Abraham). El botón que decía «Adjuntar venta» se
renombró a «Conectar cotización» y quedó cableado.

Con un Odoo fingido (el MISMO doble de tests/test_cot_lead.py,
`OdooCotLead`): acá se prueba el cableado — conectar, marcar real,
desconectar y el PDF — y que el embudo avanza según lo que sugiera la
orden real, NUNCA hacia atrás. La capa de datos (cot_lead.py) ya está
probada aparte; estas pruebas cubren lo que le agrega este módulo.

Los leads de muestra (ver también tests/test_control.py):

    LEAD-91  Tamara              Por agendar  Resp: Ruben
    LEAD-90  Juan Carlos Lopez   Por agendar  sin Resp:
    LEAD-89  Boda Las Nubes      Agendado     Resp: Mary
    LEAD-88  Hotel Bristol       Entregado    Resp: Ruben
    LEAD-87  Ximena Dávila       Cotizado     sin Resp:, Te toca
    LEAD-86  Nedjaira            Hablando     Resp: Salomón, Te toca
    LEAD-85  Diego Armando       Nuevo        sin Resp:
"""

import pytest

from app import control, cot_lead, cotizaciones, linear_leads, ventas
from test_cot_lead import OdooCotLead


@pytest.fixture(autouse=True)
def muestra_limpia(monkeypatch, db_limpia):
    monkeypatch.delenv("LINEAR_API_KEY", raising=False)
    monkeypatch.delenv("CALENDARIO_ESCRITURA", raising=False)
    monkeypatch.delenv("AJUSTES_ADMINS", raising=False)
    linear_leads.reiniciar_muestra()
    control.iniciar_tablas()


@pytest.fixture
def de_dueno(monkeypatch):
    """La sesión de las pruebas ('genesis') es el dueño."""
    monkeypatch.setenv("AJUSTES_ADMINS", "genesis")


@pytest.fixture
def odoo(monkeypatch):
    falso = OdooCotLead()
    monkeypatch.setattr(ventas, "_ejecutar", falso.ejecutar)
    return falso


@pytest.fixture
def odoo_con_estados(odoo, monkeypatch):
    """El mismo Odoo fingido, que ADEMÁS entiende `cotizaciones.
    estados_en_odoo` — pide con el operador "in", que `OdooCotLead` no
    soporta (su doble solo entiende `=ilike` y `!=`, las formas que arma
    cot_lead.py). Acá se envuelve: todo sale editable (sin facturas, no
    cancelada) salvo que la prueba cambie el estado a mano."""
    original = odoo.ejecutar

    def envuelto(modelo, metodo, args, kw=None):
        kw = kw or {}
        if modelo == "sale.order" and metodo == "search_read" \
                and kw.get("fields") == ["state", "invoice_ids"]:
            ids = args[0][0][2] if args and args[0] else []
            return [{"id": oid, "state": odoo.ordenes[oid]["state"],
                     "invoice_ids": []} for oid in ids if oid in odoo.ordenes]
        return original(modelo, metodo, args, kw)

    monkeypatch.setattr(ventas, "_ejecutar", envuelto)
    return odoo


def _lead_y_partner(odoo, ref, nombre=None):
    lead = linear_leads.uno(ref)
    partner = odoo.agregar_partner(nombre or lead["nombre"],
                                   lead.get("celular") or "")
    return lead, partner


# ---------------------------------------------------------------------------
# Conectar avanza el embudo según lo que sugiera la orden — nunca atrás
# ---------------------------------------------------------------------------

def test_conectar_sin_pago_avanza_a_cotizado(odoo):
    lead, partner = _lead_y_partner(odoo, "LEAD-85")  # Nuevo
    orden = odoo.agregar_orden(partner, "S00100", amount_total=100.0,
                               etapa_cobro="cotizado")
    aviso, error = control.conectar_cotizacion("LEAD-85", orden, autor="Abraham")
    assert error == ""
    assert "S00100" in aviso
    assert linear_leads.uno("LEAD-85")["estado"] == "COTIZADO"


def test_conectar_con_abono_avanza_a_por_agendar_con_etiqueta(odoo):
    lead, partner = _lead_y_partner(odoo, "LEAD-85")
    orden = odoo.agregar_orden(partner, "S00101", amount_total=200.0,
                               total_pagado=100.0, etapa_cobro="abono")
    aviso, error = control.conectar_cotizacion("LEAD-85", orden, autor="Abraham")
    assert error == ""
    lead = linear_leads.uno("LEAD-85")
    assert lead["estado"] == "POR_AGENDAR"
    assert "Abono 50%" in lead["etiquetas"]


def test_conectar_con_pago_completo_pone_pagado_100(odoo):
    lead, partner = _lead_y_partner(odoo, "LEAD-85")
    orden = odoo.agregar_orden(partner, "S00102", amount_total=300.0,
                               total_pagado=300.0, etapa_cobro="pagado")
    control.conectar_cotizacion("LEAD-85", orden, autor="Abraham")
    lead = linear_leads.uno("LEAD-85")
    assert lead["estado"] == "POR_AGENDAR"
    assert "Pagado 100%" in lead["etiquetas"]


@pytest.mark.parametrize("ref, estado", [
    ("LEAD-89", "AGENDADO"), ("LEAD-88", "ENTREGADO")])
def test_conectar_no_retrocede_un_lead_mas_avanzado(odoo, ref, estado):
    # Una orden pagada del todo sugeriría "Por agendar" — que es HACIA
    # ATRÁS para un lead que ya está en Agendado o Entregado.
    lead, partner = _lead_y_partner(odoo, ref)
    orden = odoo.agregar_orden(partner, "S00103", amount_total=50.0,
                               total_pagado=50.0, etapa_cobro="pagado")
    aviso, error = control.conectar_cotizacion(ref, orden, autor="Abraham")
    assert error == ""
    assert linear_leads.uno(ref)["estado"] == estado


def test_conectar_deja_el_comentario_firmado(odoo):
    lead, partner = _lead_y_partner(odoo, "LEAD-85")
    orden = odoo.agregar_orden(partner, "S00104", amount_total=10.0)
    control.conectar_cotizacion("LEAD-85", orden, autor="Ruben")
    nota = linear_leads.comentarios(lead["id"])[0]["texto"]
    assert "Venta S00104 conectada." in nota
    assert "Ruben" in nota


def test_conectar_una_orden_de_otro_lead_se_rechaza(odoo):
    lead_a, partner = _lead_y_partner(odoo, "LEAD-85")
    lead_b = linear_leads.uno("LEAD-86")
    orden = odoo.agregar_orden(partner, "S00105", amount_total=10.0,
                               lead_ref=lead_b["pp"])
    aviso, error = control.conectar_cotizacion("LEAD-85", orden, autor="Abraham")
    assert aviso == ""
    assert "ya está conectada a otro lead" in error


# ---------------------------------------------------------------------------
# Marcar real cambia la plata que se muestra, y tampoco retrocede
# ---------------------------------------------------------------------------

def test_marcar_real_cambia_la_plata_que_se_muestra(odoo):
    lead, partner = _lead_y_partner(odoo, "LEAD-85")
    odoo.agregar_orden(partner, "S00200", amount_total=100.0,
                       lead_ref=lead["pp"], lead_real=True)
    o2 = odoo.agregar_orden(partner, "S00201", amount_total=250.0,
                            lead_ref=lead["pp"])

    antes = control.ficha("LEAD-85")["cot"]["plata"]
    assert antes["orden"] == "S00200"
    assert antes["total"] == 100.0

    aviso, error = control.marcar_real_cotizacion("LEAD-85", o2, autor="Abraham")
    assert error == ""

    despues = control.ficha("LEAD-85")["cot"]["plata"]
    assert despues["orden"] == "S00201"
    assert despues["total"] == 250.0


@pytest.mark.parametrize("ref, estado", [
    ("LEAD-89", "AGENDADO"), ("LEAD-88", "ENTREGADO")])
def test_marcar_real_tampoco_retrocede(odoo, ref, estado):
    lead, partner = _lead_y_partner(odoo, ref)
    odoo.agregar_orden(partner, "S00300", amount_total=10.0,
                       lead_ref=lead["pp"], lead_real=True)
    o2 = odoo.agregar_orden(partner, "S00301", amount_total=20.0,
                            total_pagado=20.0, etapa_cobro="pagado",
                            lead_ref=lead["pp"])
    aviso, error = control.marcar_real_cotizacion(ref, o2, autor="Abraham")
    assert error == ""
    assert linear_leads.uno(ref)["estado"] == estado


def test_marcar_real_exige_estar_conectada(odoo):
    lead, partner = _lead_y_partner(odoo, "LEAD-85")
    suelta = odoo.agregar_orden(partner, "S00302", amount_total=10.0)
    aviso, error = control.marcar_real_cotizacion("LEAD-85", suelta, autor="Abraham")
    assert aviso == ""
    assert "conéctala primero" in error


# ---------------------------------------------------------------------------
# Desconectar no toca el estado del embudo
# ---------------------------------------------------------------------------

def test_desconectar_quita_de_la_lista_sin_tocar_el_estado(odoo):
    lead, partner = _lead_y_partner(odoo, "LEAD-86")  # Hablando
    orden = odoo.agregar_orden(partner, "S00400", amount_total=10.0,
                               lead_ref=lead["pp"])
    aviso, error = control.desconectar_cotizacion("LEAD-86", orden, autor="Abraham")
    assert error == ""
    assert control.ficha("LEAD-86")["cot"]["ordenes"] == []
    assert linear_leads.uno("LEAD-86")["estado"] == "HABLANDO"


# ---------------------------------------------------------------------------
# Odoo caído: "no sé" nunca se confunde con "no hay"
# ---------------------------------------------------------------------------

def test_odoo_caido_dice_no_se_pudo_leer_no_lista_vacia(odoo):
    odoo.fallar = True
    cot = control.ficha("LEAD-85")["cot"]
    assert cot["ok"] is False
    assert cot["error"]
    assert cot["ordenes"] == []
    assert cot["candidatas"] == []
    assert cot["plata"] is None


def test_odoo_caido_en_pantalla(cliente, de_dueno, odoo):
    odoo.fallar = True
    cuerpo = cliente.get("/control", params={"abrir": "LEAD-91"}).text
    panel = cuerpo[cuerpo.index('class="panel-der"'):]
    assert "No se pudo leer Odoo" in panel
    assert "Sin cotización conectada" not in panel


# ---------------------------------------------------------------------------
# El PDF pide la orden correcta
# ---------------------------------------------------------------------------

def test_el_pdf_pide_la_orden_correcta(cliente, de_dueno, monkeypatch):
    llamadas = []

    def falso(orden_id):
        llamadas.append(orden_id)
        return b"%PDF-1.4 contenido"

    monkeypatch.setattr(cot_lead, "pdf_de_orden", falso)
    respuesta = cliente.get("/control/cotizacion/321.pdf",
                            params={"nombre": "S00089"})
    assert respuesta.status_code == 200
    assert respuesta.headers["content-type"] == "application/pdf"
    assert respuesta.content == b"%PDF-1.4 contenido"
    assert llamadas == [321]


def test_el_pdf_avisa_si_odoo_no_lo_pudo_generar(cliente, de_dueno, monkeypatch):
    def revienta(orden_id):
        raise RuntimeError("No se pudo generar el PDF de la orden: Odoo no contestó")

    monkeypatch.setattr(cot_lead, "pdf_de_orden", revienta)
    respuesta = cliente.get("/control/cotizacion/321.pdf", follow_redirects=False)
    assert respuesta.status_code == 303
    assert "error=" in respuesta.headers["location"]


# ---------------------------------------------------------------------------
# La pantalla: conectar y el candado del servidor
# ---------------------------------------------------------------------------

def test_conectar_desde_la_pantalla(cliente, de_dueno, odoo):
    partner = odoo.agregar_partner("Diego Armando")
    orden = odoo.agregar_orden(partner, "S00500", amount_total=10.0)
    respuesta = cliente.post(
        "/control/cotizacion/conectar", params={"vista": "estado"},
        data={"ref": "LEAD-85", "orden_id": str(orden)},
        follow_redirects=False)
    assert respuesta.status_code == 303
    assert "aviso=" in respuesta.headers["location"]
    assert linear_leads.uno("LEAD-85")["estado"] == "COTIZADO"


def test_conectar_no_lo_puede_un_empleado_de_otro(cliente, odoo):
    # Sesión sin AJUSTES_ADMINS: es "genesis", mapeada a Ruben — LEAD-89
    # es de Mary.
    partner = odoo.agregar_partner("Boda Las Nubes")
    orden = odoo.agregar_orden(partner, "S00501", amount_total=10.0)
    respuesta = cliente.post(
        "/control/cotizacion/conectar",
        data={"ref": "LEAD-89", "orden_id": str(orden)},
        follow_redirects=False)
    assert respuesta.status_code == 303
    assert "error=" in respuesta.headers["location"]
    assert control.ficha("LEAD-89")["cot"]["ordenes"] == []


def test_desconectar_desde_la_pantalla(cliente, de_dueno, odoo):
    lead, partner = _lead_y_partner(odoo, "LEAD-91")
    orden = odoo.agregar_orden(partner, "S00502", amount_total=10.0,
                               lead_ref=lead["pp"])
    respuesta = cliente.post(
        "/control/cotizacion/desconectar", params={"vista": "estado"},
        data={"ref": "LEAD-91", "orden_id": str(orden)},
        follow_redirects=False)
    assert respuesta.status_code == 303
    assert control.ficha("LEAD-91")["cot"]["ordenes"] == []


def test_marcar_real_desde_la_pantalla(cliente, de_dueno, odoo):
    lead, partner = _lead_y_partner(odoo, "LEAD-91")
    o1 = odoo.agregar_orden(partner, "S00503", amount_total=10.0,
                            lead_ref=lead["pp"], lead_real=True)
    o2 = odoo.agregar_orden(partner, "S00504", amount_total=20.0,
                            lead_ref=lead["pp"])
    respuesta = cliente.post(
        "/control/cotizacion/marcar-real", params={"vista": "estado"},
        data={"ref": "LEAD-91", "orden_id": str(o2)},
        follow_redirects=False)
    assert respuesta.status_code == 303
    assert control.ficha("LEAD-91")["cot"]["plata"]["orden"] == "S00504"


def test_buscar_por_numero_llega_a_la_ficha(cliente, de_dueno, odoo):
    partner = odoo.agregar_partner("Cliente a otro nombre")
    odoo.agregar_orden(partner, "S00600", amount_total=10.0)
    cuerpo = cliente.get("/control", params={
        "vista": "estado", "abrir": "LEAD-85", "buscar": "s00600"}).text
    panel = cuerpo[cuerpo.index('class="panel-der"'):]
    assert "S00600" in panel


# ---------------------------------------------------------------------------
# El retoque de la ficha (28/09/2026): compacto como Ventas, y «Editar»
# solo para lo que de verdad se puede editar en Vender.
# ---------------------------------------------------------------------------

def test_una_cotizacion_de_servicio_local_editable_trae_el_enlace_editar(
        odoo_con_estados):
    lead, partner = _lead_y_partner(odoo_con_estados, "LEAD-90")
    orden = odoo_con_estados.agregar_orden(
        partner, "S00700", amount_total=100.0, lead_ref=lead["pp"])
    registro = cotizaciones._guardar_local(
        {"nombre": "Prueba", "id": "prueba"}, "boda", "Juan Carlos Lopez",
        "", orden, "S00700", 100.0)

    ficha = control.ficha("LEAD-90")
    conectada = next(o for o in ficha["cot"]["ordenes"] if o["orden_id"] == orden)
    assert conectada["editar_n"] == registro["n"]


def test_una_orden_sin_registro_local_no_trae_editar(odoo):
    lead, partner = _lead_y_partner(odoo, "LEAD-90")
    orden = odoo.agregar_orden(partner, "S00701", amount_total=50.0,
                               lead_ref=lead["pp"])
    ficha = control.ficha("LEAD-90")
    conectada = next(o for o in ficha["cot"]["ordenes"] if o["orden_id"] == orden)
    assert conectada["editar_n"] is None


def test_una_cotizacion_local_ya_facturada_no_trae_editar(
        odoo_con_estados, monkeypatch):
    lead, partner = _lead_y_partner(odoo_con_estados, "LEAD-90")
    orden = odoo_con_estados.agregar_orden(
        partner, "S00702", amount_total=100.0, lead_ref=lead["pp"])
    cotizaciones._guardar_local(
        {"nombre": "Prueba", "id": "prueba"}, "boda", "Juan Carlos Lopez",
        "", orden, "S00702", 100.0)

    # La factura ya existe: el doble responde "facturada", nada editable.
    def con_factura(modelo, metodo, args, kw=None):
        kw = kw or {}
        if modelo == "sale.order" and metodo == "search_read" \
                and kw.get("fields") == ["state", "invoice_ids"]:
            return [{"id": orden, "state": "sale", "invoice_ids": [999]}]
        return odoo_con_estados.ejecutar(modelo, metodo, args, kw)

    monkeypatch.setattr(ventas, "_ejecutar", con_factura)
    ficha = control.ficha("LEAD-90")
    conectada = next(o for o in ficha["cot"]["ordenes"] if o["orden_id"] == orden)
    assert conectada["editar_n"] is None


def test_conectadas_usa_el_patron_compacto_sin_botones_grandes(
        cliente, de_dueno, odoo):
    lead, partner = _lead_y_partner(odoo, "LEAD-91")
    odoo.agregar_orden(partner, "S00091", amount_total=68.0,
                       lead_ref=lead["pp"], lead_real=True,
                       etapa_cobro="cotizado")
    cuerpo = cliente.get("/control", params={"abrir": "LEAD-91"}).text
    panel = cuerpo[cuerpo.index('class="panel-der"'):]
    # Solo la tarjeta de la orden conectada (la sección de más abajo,
    # "Conectar cotización", tiene su propio botón de Buscar — eso no es
    # lo que este retoque cambió).
    seccion = panel[panel.index('class="ficha-cot-item'):
                    panel.index("Conectar cotización")]
    assert 'class="badge' in seccion
    assert '<button class="btn' not in seccion
    assert '<a class="btn' not in seccion
    assert "PDF" in seccion and "Desconectar" in seccion


def test_el_badge_de_cobro_muestra_el_texto_correcto(odoo):
    lead, partner = _lead_y_partner(odoo, "LEAD-91")
    odoo.agregar_orden(partner, "S00800", amount_total=50.0,
                       lead_ref=lead["pp"], etapa_cobro="abono")
    ficha = control.ficha("LEAD-91")
    conectada = ficha["cot"]["ordenes"][0]
    assert conectada["badge"]["texto"] == "Abono 50%"
    assert conectada["badge"]["clase"] == "b-critico"


# ---------------------------------------------------------------------------
# El PDF conectado baja con nombre orden+cliente, pestaña nueva y descarga
# (28/09/2026)
# ---------------------------------------------------------------------------

def test_el_pdf_conectado_baja_con_target_blank_download_y_cliente(
        cliente, de_dueno, odoo):
    import urllib.parse

    lead, partner = _lead_y_partner(odoo, "LEAD-85")  # Diego Armando
    odoo.agregar_orden(partner, "S00500", amount_total=10.0,
                       lead_ref=lead["pp"], lead_real=True)
    pagina = cliente.get("/control", params={"abrir": "LEAD-85"}).text
    encontrado = False
    for trozo in pagina.split("<a ")[1:]:
        enlace = trozo.split(">")[0]
        if "/control/cotizacion/" in enlace and ".pdf" in enlace:
            encontrado = True
            assert 'target="_blank"' in enlace
            assert 'rel="noopener"' in enlace
            assert "download" in enlace
            href = enlace.split('href="', 1)[1].split('"', 1)[0]
            parametros = urllib.parse.parse_qs(urllib.parse.urlparse(href).query)
            assert parametros["cliente"][0] == lead["nombre"]
    assert encontrado, "no se encontró el enlace del PDF conectado"


def test_el_pdf_conectado_tiene_boton_compartir_oculto_con_su_nombre(
        cliente, de_dueno, odoo):
    """Junto al «PDF» va «Compartir» (28/09/2026): mismo archivo, mismo
    nombre orden+cliente que calcula `control._con_edicion`, oculto por
    defecto (el `hidden` va en el envoltorio que también trae el "·")."""
    lead, partner = _lead_y_partner(odoo, "LEAD-85")  # Diego Armando
    orden_id = odoo.agregar_orden(partner, "S00500", amount_total=10.0,
                                  lead_ref=lead["pp"], lead_real=True)
    esperado = ventas.nombre_de_pdf("S00500", lead["nombre"])
    pagina = cliente.get("/control", params={"abrir": "LEAD-85"}).text
    marca = f'data-compartir="/control/cotizacion/{orden_id}.pdf'
    assert marca in pagina
    pos = pagina.index(marca)
    inicio = pagina.rindex("<", 0, pos)
    fin = pagina.index(">", pos)
    tag = pagina[inicio:fin + 1]
    assert "hidden" in tag
    assert f'data-nombre="{esperado}"' in tag
