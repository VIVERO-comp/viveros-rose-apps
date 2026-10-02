"""El rediseño Orquesta de la pestaña Control (2/10/2026) no borra nada.

El lienzo (docs/diseno-orquesta, pantallas 05/06/21/22) cambia SOLO el
aspecto: estas pruebas inventarían cada acción y botón del tablero y de la
ficha en el HTML nuevo, para que un retoque de piel futuro no se lleve una
función por delante sin que nada avise. Reglas de la casa verificadas
aparte: la pestaña se sigue llamando Control (no CRM), el color del
interés sale de paleta.json vía Python (nunca un hex en la plantilla), y
hay UN solo botón negro en la ficha.

Mismo arreglo que tests/test_control.py: modo muestra, `genesis` como
dueño con AJUSTES_ADMINS.
"""

import pytest

from app import colores, control, linear_leads, ventas
from test_cot_lead import OdooCotLead


@pytest.fixture(autouse=True)
def muestra_limpia(monkeypatch, db_limpia):
    monkeypatch.delenv("LINEAR_API_KEY", raising=False)
    monkeypatch.delenv("CALENDARIO_ESCRITURA", raising=False)
    monkeypatch.delenv("AJUSTES_ADMINS", raising=False)
    linear_leads.reiniciar_muestra()
    control.iniciar_tablas()
    monkeypatch.setattr(control, "vista_empleado_apagada", lambda: False)


@pytest.fixture
def de_dueno(monkeypatch):
    monkeypatch.setenv("AJUSTES_ADMINS", "genesis")


def _panel(cuerpo):
    return cuerpo[cuerpo.index('class="panel-der"'):]


# ---------------------------------------------------------------------------
# El tablero: todo lo de hoy sigue en el HTML nuevo
# ---------------------------------------------------------------------------

def test_la_pestana_sigue_llamandose_control(cliente, de_dueno):
    # El lienzo la llama «CRM», pero la pestaña NO se renombra (decisión
    # vigente hasta que Korto diga): el título y el menú dicen Control.
    cuerpo = cliente.get("/control", params={"vista": "estado"}).text
    assert "<h3>Control</h3>" in cuerpo
    assert "<title>Control — Control Viverorose</title>" in cuerpo


def test_la_piel_nueva_se_carga_despues_de_la_base(cliente, de_dueno):
    cuerpo = cliente.get("/control", params={"vista": "estado"}).text
    assert "/static/diseno-control.css" in cuerpo
    assert cuerpo.index("/static/diseno-base.css") < cuerpo.index(
        "/static/diseno-control.css")


def test_el_tablero_conserva_sus_piezas(cliente, de_dueno):
    cuerpo = cliente.get("/control", params={"vista": "estado"}).text
    # La hamburguesa y el segmento de vistas.
    assert 'class="btn icono hamb"' in cuerpo
    assert "Por estado" in cuerpo and "Por empleado" in cuerpo
    # Las 9 columnas con su conteo y su pie de quién las mueve.
    for estado in linear_leads.ESTADOS:
        assert estado["nombre"] in cuerpo
        assert estado["auto"] in cuerpo
    assert cuerpo.count('class="ret-cuenta"') == len(linear_leads.ESTADOS)
    # El pie del tablero sigue explicando la corrección a mano.
    assert "Arrastrar aquí es una corrección a mano" in cuerpo


def test_la_tarjeta_conserva_sus_piezas(cliente, de_dueno):
    cuerpo = cliente.get("/control", params={"vista": "estado"}).text
    t91 = cuerpo[cuerpo.index('data-ref="LEAD-91"') - 300:][:2000]
    # El arrastre de siempre (el dueño mueve todo).
    assert 'draggable="true"' in t91
    # El enlace a la ficha y el chat, sin arrastre propio.
    assert '<a class="ctl-zona" draggable="false"' in t91
    assert '<a class="ctl-wa" draggable="false"' in t91
    # El chip del interés y la etiqueta de pago.
    assert 'class="dc-chip-interes"' in t91
    assert "Abono 50%" in t91
    # El 🔴 de «Te toca» en los que esperan respuesta.
    t87 = cuerpo[cuerpo.index('data-ref="LEAD-87"'):][:900]
    assert 'class="ctl-alerta"' in t87


def test_el_color_del_interes_sale_de_la_paleta(cliente, de_dueno):
    cuerpo = cliente.get("/control", params={"vista": "estado"}).text
    tarjeta = cuerpo[cuerpo.index('data-ref="LEAD-91"') - 300:][:600]
    esperado = colores.color_interes("Plantas")
    assert esperado  # Plantas tiene su familia en paleta.json
    assert f"--dc-interes:{esperado}" in tarjeta
    # Un interés que la paleta no conoce no inventa color ni revienta.
    assert colores.color_interes("Cohetes") == ""
    assert colores.color_interes("") == ""


def test_el_chip_de_pago_distingue_debe_de_pagado(cliente, de_dueno):
    # Rojo solo para plata que se debe (regla del lienzo); la clase la
    # decide Python, nunca la plantilla comparando textos.
    cuerpo = cliente.get("/control", params={"vista": "estado"}).text
    t91 = cuerpo[cuerpo.index('data-ref="LEAD-91"'):][:1400]   # Abono 50%
    assert "etq-pago dc-debe" in t91
    t90 = cuerpo[cuerpo.index('data-ref="LEAD-90"'):][:1400]   # Pagado 100%
    assert "etq-pago dc-ok" in t90
    t88 = cuerpo[cuerpo.index('data-ref="LEAD-88"'):][:1400]   # Cobrar saldo
    assert "etq-pago dc-debe" in t88
    assert control._clase_pago("") == ""


def test_ya_llego_sigue_en_la_columna_recordatorio(cliente, de_dueno):
    control.mover_a_estado("LEAD-86", "RECORDATORIO",
                           nota="espera Monstera grande", autor="Génesis")
    cuerpo = cliente.get("/control", params={"vista": "estado"}).text
    tarjeta = cuerpo[cuerpo.index('data-ref="LEAD-86"'):][:2000]
    assert "Ya llegó" in tarjeta
    assert "espera Monstera grande" in tarjeta
    assert "6114-9077" in tarjeta


def test_los_banners_siguen_con_su_x(cliente, de_dueno):
    cuerpo = cliente.get("/control", params={
        "vista": "estado", "error": "algo salió mal",
        "aviso": "todo quedó guardado"}).text
    assert cuerpo.count('class="cierra-banner"') == 2


# ---------------------------------------------------------------------------
# La ficha: cada acción y botón sigue en el HTML nuevo
# ---------------------------------------------------------------------------

def test_la_ficha_conserva_todas_sus_acciones(cliente, de_dueno, monkeypatch):
    # Con el Odoo fingido: sin él la sección de cotizaciones dice «No se
    # pudo leer Odoo» (correcto) y no ofrece conectar.
    monkeypatch.setattr(ventas, "_ejecutar", OdooCotLead().ejecutar)
    cuerpo = cliente.get("/control", params={"abrir": "LEAD-86",
                                             "vista": "estado"}).text
    panel = _panel(cuerpo)
    # Acciones.
    assert "💬 WhatsApp" in panel
    assert 'action="/control/responder' in panel and "🔴 Responder" in panel
    assert ">Cotizar</a>" in panel
    assert "&a=RECORDATORIO\">Recordar</a>" in panel
    # Señales (las dos de la muestra).
    assert 'action="/control/senal' in panel
    assert "Seguimiento" in panel and "Importante" in panel
    # Cotización.
    assert "Conectar cotización" in panel and ">Buscar</button>" in panel
    # Conversación y notas.
    assert "Conversación" in panel
    assert "Notas internas · solo el equipo" in panel
    assert 'action="/control/nota' in panel and "Guardar nota" in panel
    # Datos.
    for dato in ("Responsable", "Origen", "Llegó", "Teléfono", "Issue"):
        assert f"<b>{dato}</b>" in panel
    # Más opciones pliega lo delicado sin borrarlo.
    assert "<summary>Más opciones</summary>" in panel
    assert "Se lo doy a" in panel
    assert "Corregir el estado" in panel
    assert 'action="/control/responsable' in panel
    assert panel.index("<summary>Más opciones</summary>") < panel.index("Se lo doy a")


def test_un_solo_boton_negro_en_la_ficha(cliente, de_dueno):
    # El lienzo: un botón negro por pantalla — en la ficha, abrir el chat.
    cuerpo = cliente.get("/control", params={"abrir": "LEAD-86",
                                             "vista": "estado"}).text
    panel = _panel(cuerpo)
    assert panel.count('class="btn oro"') == 1
    trozo = panel[panel.index('class="btn oro"'):][:200]
    assert "WhatsApp" in trozo


def test_la_ficha_cierra_con_x_en_mac_y_atras_en_celular(cliente, de_dueno):
    cuerpo = cliente.get("/control", params={"abrir": "LEAD-86",
                                             "vista": "estado"}).text
    panel = _panel(cuerpo)
    # Un solo enlace de cerrar con sus dos caras (el CSS muestra una u
    # otra según el ancho), más el telón de atrás.
    assert 'class="btn chico dc-cerrar"' in panel
    assert '<span class="dc-cerrar-x" aria-hidden="true">✕</span>' in panel
    assert '<span class="dc-cerrar-atras">← Atrás</span>' in panel
    assert 'class="telon"' in cuerpo


def test_la_ficha_lleva_el_color_del_interes(cliente, de_dueno):
    cuerpo = cliente.get("/control", params={"abrir": "LEAD-86",
                                             "vista": "estado"}).text
    aside = cuerpo[cuerpo.index('aria-label="Ficha del lead"'):][:200]
    assert f"--dc-interes:{colores.color_interes('Plantas')}" in aside


def test_parar_mantenimiento_vive_en_mas_opciones(cliente, de_dueno,
                                                  monkeypatch):
    monkeypatch.setattr(control.mantenimiento, "activo", lambda ref: True)
    cuerpo = cliente.get("/control", params={"abrir": "LEAD-88",
                                             "vista": "estado"}).text
    panel = _panel(cuerpo)
    assert "Parar mantenimiento" in panel
    assert panel.index("<summary>Más opciones</summary>") < panel.index(
        "Parar mantenimiento")


def test_la_cotizacion_conectada_conserva_sus_acciones(cliente, de_dueno,
                                                       monkeypatch):
    falso = OdooCotLead()
    monkeypatch.setattr(ventas, "_ejecutar", falso.ejecutar)
    lead = linear_leads.uno("LEAD-91")
    partner = falso.agregar_partner(lead["nombre"], lead.get("celular") or "")
    orden = falso.agregar_orden(partner, "S00100", amount_total=140.0,
                                total_pagado=70.0, etapa_cobro="abono")
    control.conectar_cotizacion("LEAD-91", orden, autor="Abraham")
    cuerpo = cliente.get("/control", params={"abrir": "LEAD-91",
                                             "vista": "estado"}).text
    panel = _panel(cuerpo)
    # La real con su plata, y las acciones por orden conectada.
    assert "La real" in panel and "S00100" in panel
    assert "Descargar / Compartir (PDF)" in panel
    assert "Quitar real" in panel        # la S00100 ya es la real
    assert "Desconectar" in panel


def test_el_modal_del_motivo_sigue_entero(cliente, de_dueno):
    cuerpo = cliente.get("/control", params={
        "vista": "estado", "mover": "LEAD-86", "a": "COTIZADO"}).text
    assert "¿Por qué la movés?" in cuerpo
    assert "Mover y anotar" in cuerpo and "Mejor no" in cuerpo
    perdido = cliente.get("/control", params={
        "vista": "estado", "mover": "LEAD-86", "a": "PERDIDO"}).text
    assert "Motivo de la pérdida" in perdido


def test_el_lead_ajeno_sigue_sin_botones_con_la_piel_nueva(cliente):
    # Sin AJUSTES_ADMINS la sesión no es dueña y LEAD-89 es de Mary.
    cuerpo = cliente.get("/control", params={"abrir": "LEAD-89"}).text
    panel = _panel(cuerpo)
    assert 'action="/control/responder' not in panel
    assert "<summary>Más opciones</summary>" not in panel
    assert "No es tuyo." in panel
