"""El rediseño Orquesta de la pestaña Control (2/10/2026) no borra nada.

El lienzo (docs/diseno-orquesta, pantallas 05/06/21/22) cambia SOLO el
aspecto: estas pruebas inventarían cada acción y botón del tablero y de la
ficha en el HTML nuevo, para que un retoque de piel futuro no se lleve una
función por delante sin que nada avise. Reglas de la casa verificadas
aparte: la pestaña se sigue llamando Control (no CRM), el color del
interés sale de paleta.json vía Python (nunca un hex en la plantilla), y
hay UN solo botón negro en la ficha. (Lo del nombre cambió con el lienzo
de ROLES del BLOQUE 43: ESTA pantalla se titula «CRM»; el programa se
sigue llamando Control Viverorose.)

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

def test_la_pantalla_se_titula_crm_y_el_programa_sigue_siendo_control(
        cliente, de_dueno):
    # El lienzo de PESTAÑAS decía «la pestaña se llama Control»; el de
    # ROLES (BLOQUE 43) titula ESTA pantalla «CRM», con la marca «Todos»
    # al lado para el Director. Manda el de Roles para lo que él muestra.
    # Lo que NO cambió: el programa se sigue llamando Control Viverorose
    # (la pestaña del navegador y el menú lateral, congelado por otra
    # tanda, lo siguen diciendo).
    cuerpo = cliente.get("/control", params={"vista": "estado"}).text
    assert "<h3>CRM</h3>" in cuerpo
    assert '<span class="dc-marca">Todos</span>' in cuerpo
    assert "<title>Control — Control Viverorose</title>" in cuerpo


def test_la_marca_todos_es_solo_del_director(cliente):
    # Sin AJUSTES_ADMINS la sesión no es dueña: ve el tablero completo (lo
    # de siempre), pero la marca «Todos» es del Director.
    cuerpo = cliente.get("/control", params={"vista": "estado"}).text
    assert "<h3>CRM</h3>" in cuerpo
    assert "dc-marca" not in cuerpo


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


def test_el_responsable_va_de_inicial_en_la_tarjeta(cliente, de_dueno):
    # Fidelidad P37 (pantalla 05): el responsable es el círculo con su
    # inicial, no un chip con su nombre. El chip se queda SOLO para el
    # hueco «Sin asignar», que es lo que el dueño necesita ver para
    # repartir. (Con el lienzo de ROLES —BLOQUE 43— ese círculo se mudó
    # a la fila de ABAJO, porque el rincón de arriba es ahora la plata;
    # sigue siendo el mismo círculo y lo verifica la prueba de la
    # tarjeta reordenada.)
    cuerpo = cliente.get("/control", params={"vista": "estado"}).text
    desde = cuerpo.index('data-ref="LEAD-91"')
    tarjeta = cuerpo[desde:desde + 10 + cuerpo[desde + 10:].index("data-ref=")]
    resp = linear_leads.uno("LEAD-91")["resp"]
    assert resp  # la muestra lo trae con su Resp:
    assert f'aria-label="Responsable: {resp}"' in tarjeta
    assert 'class="ctl-av"' in tarjeta
    assert "chip-nadie" not in tarjeta


def test_sin_responsable_el_hueco_sigue_diciendose(cliente, de_dueno,
                                                   monkeypatch):
    # Sin `Resp:` no hay círculo (no se inventa una inicial): queda el chip
    # «Sin asignar», que es el que le dice al dueño qué falta repartir.
    real = linear_leads.listar

    def sin_resp(*a, **kw):
        return [dict(l, resp="") for l in real(*a, **kw)]

    monkeypatch.setattr(linear_leads, "listar", sin_resp)
    cuerpo = cliente.get("/control", params={"vista": "estado"}).text
    assert 'class="ctl-av"' not in cuerpo
    assert "chip-nadie" in cuerpo


def test_la_cabecera_de_la_ficha_lleva_interes_ref_y_origen(cliente, de_dueno):
    # Pantallas 06/22: nombre arriba y, debajo, el chip del interés con
    # «LEAD-NN · llegó por Origen». El origen dejó de ser renglón de datos.
    panel = _panel(cliente.get("/control", params={"abrir": "LEAD-86",
                                                   "vista": "estado"}).text)
    cabecera = panel[:panel.index("</div>", panel.index("dc-cab-sub"))]
    assert "dc-chip-interes" in cabecera
    assert "LEAD-86" in cabecera and "llegó por" in cabecera


def test_los_atajos_de_llamar_y_chatear_son_los_enlaces_de_siempre(
        cliente, de_dueno):
    # Los dos íconos pastel del lienzo (06/22) no inventan nada: el verde
    # es el wa.me de siempre y el azul, el teléfono que ya estaba en los
    # datos. Sin teléfono no se pinta ninguno de los dos.
    panel = _panel(cliente.get("/control", params={"abrir": "LEAD-86",
                                                   "vista": "estado"}).text)
    celular = linear_leads.uno("LEAD-86")["celular"]
    assert celular
    assert f'href="tel:{celular.replace("-", "")}"' in panel
    assert 'href="https://wa.me/507' in panel


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
    # Acciones. Abrir el chat sigue en la ficha: con la fidelidad P37 es el
    # ícono verde de arriba (pantallas 06/22) en vez del botón «💬
    # WhatsApp» — mismo enlace de wa.me, otro lugar.
    assert 'class="dc-ic dc-wa"' in panel
    assert 'href="https://wa.me/507' in panel
    assert 'action="/control/responder' in panel and "🔴 Responder" in panel
    # Cotizar es formulario POST desde el punto 1 de roles (precisión 2:
    # el GET /venta?lead= mutaba).
    assert 'action="/venta/lead"' in panel and ">Cotizar</button>" in panel
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
    # Datos. El origen dejó de ser renglón: la fidelidad P37 lo subió a la
    # cabecera, junto al LEAD-NN («LEAD-86 · llegó por …», pantallas
    # 06/22). El dato sigue ahí, solo cambió de lugar.
    for dato in ("Llegó", "Teléfono", "Issue"):
        assert f"<b>{dato}</b>" in panel
    # El responsable tampoco se perdió: con el lienzo de Roles (BLOQUE 43)
    # dejó de ser renglón de datos y es la PRIMERA de las cinco filas, «Lo
    # atiende» — que además abre el cuadro de asignar.
    assert ">Lo atiende</span>" in panel
    assert "Sin asignar" in panel          # LEAD-86 no tiene Resp:
    assert "llegó por" in panel
    assert panel.index("llegó por") < panel.index(">Lo atiende</span>")
    # Más opciones pliega lo delicado sin borrarlo.
    assert "<summary>Más opciones</summary>" in panel
    assert "Se lo doy a" in panel
    assert "Corregir el estado" in panel
    assert 'action="/control/responsable' in panel
    assert panel.index("<summary>Más opciones</summary>") < panel.index("Se lo doy a")


def test_ningun_boton_negro_en_la_ficha(cliente, de_dueno):
    # El lienzo pone UN botón negro por pantalla, y en la ficha (06/22) ese
    # negro es «COBRAR SALDO $X» — una acción que Control no hace: el cobro
    # se registra en Odoo. Así que acá no queda ninguno: el chat se fue al
    # ícono verde y el cobro va apagado en su lugar (ver la prueba de
    # abajo). Cero negros es la lectura correcta del lienzo, no un olvido.
    cuerpo = cliente.get("/control", params={"abrir": "LEAD-86",
                                             "vista": "estado"}).text
    panel = _panel(cuerpo)
    assert 'class="btn oro"' not in panel
    assert 'class="dc-ic dc-wa"' in panel


def test_cobrar_saldo_va_apagado_con_el_saldo_real(cliente, de_dueno,
                                                   monkeypatch):
    # El botón negro del lienzo, en su lugar y APAGADO: el monto es el
    # saldo REAL de la orden real (cot_lead), nunca uno de ejemplo.
    falso = OdooCotLead()
    monkeypatch.setattr(ventas, "_ejecutar", falso.ejecutar)
    lead = linear_leads.uno("LEAD-91")
    partner = falso.agregar_partner(lead["nombre"], lead.get("celular") or "")
    orden = falso.agregar_orden(partner, "S00100", amount_total=140.0,
                                total_pagado=70.0, etapa_cobro="abono")
    control.conectar_cotizacion("LEAD-91", orden, autor="Abraham")
    panel = _panel(cliente.get("/control", params={"abrir": "LEAD-91",
                                                   "vista": "estado"}).text)
    assert "Cobrar saldo $70.00 — Todavía no" in panel
    boton = panel[panel.index("Cobrar saldo $70.00") - 220:]
    assert "disabled" in boton[:260]


def test_sin_orden_real_no_se_pinta_ningun_cobro(cliente, de_dueno,
                                                 monkeypatch):
    # Sin plata conocida no hay botón: ni apagado ni con un $0.00 inventado.
    monkeypatch.setattr(ventas, "_ejecutar", OdooCotLead().ejecutar)
    panel = _panel(cliente.get("/control", params={"abrir": "LEAD-86",
                                                   "vista": "estado"}).text)
    assert "Cobrar saldo" not in panel


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


# ---------------------------------------------------------------------------
# BLOQUE 43 — las tres pantallas del lienzo de ROLES:
#   A. el panel del lead (monto grande + cinco filas),
#   B. el CRM reordenado (nombre y monto arriba, interés y persona abajo),
#   C. el cuadro de asignar / reasignar.
# Lo que ninguna de las tres hace: inventar un dato o aflojar un candado.
# ---------------------------------------------------------------------------

def _con_plata(falso, ref, total=1150.0, pagado=70.0, etapa="abono"):
    """Le conecta a un lead su orden REAL en el Odoo fingido."""
    lead = linear_leads.uno(ref)
    partner = falso.agregar_partner(lead["nombre"], lead.get("celular") or "")
    orden = falso.agregar_orden(partner, "S00100", amount_total=total,
                                total_pagado=pagado, etapa_cobro=etapa)
    control.conectar_cotizacion(ref, orden, autor="Abraham")
    return orden


def test_el_panel_lleva_las_cinco_filas_en_su_orden(cliente, de_dueno):
    panel = _panel(cliente.get("/control", params={"abrir": "LEAD-91",
                                                   "vista": "estado"}).text)
    etiquetas = ["Lo atiende", "Seguimiento", "Último mensaje",
                 "Cotización", "Historial"]
    donde = [panel.index(">%s</span>" % e) for e in etiquetas]
    assert donde == sorted(donde)   # en el orden del lienzo
    assert panel.count('class="dc-fila-l">') == 5


def test_se_toca_la_fila_entera_no_un_enlacito_al_final(cliente, de_dueno):
    # El <a> ES la fila: abre en la etiqueta y cierra después de la
    # flecha, así que todo el renglón es área activa.
    panel = _panel(cliente.get("/control", params={"abrir": "LEAD-91",
                                                   "vista": "estado"}).text)
    fila = panel[panel.index('<a class="dc-fila"'):]
    fila = fila[:fila.index("</a>")]
    assert ">Lo atiende</span>" in fila          # la etiqueta, adentro
    assert 'class="dc-fila-go"' in fila          # y la flecha, también


def test_lo_atiende_sale_de_la_etiqueta_resp_y_abre_el_cuadro(cliente, de_dueno):
    panel = _panel(cliente.get("/control", params={"abrir": "LEAD-91",
                                                   "vista": "estado"}).text)
    assert linear_leads.uno("LEAD-91")["resp"] == "Ruben"
    assert "Ruben" in panel
    assert "abrir=LEAD-91&amp;asignar=1" in panel


def test_sin_responsable_la_fila_lo_dice_y_pide_ojo(cliente, de_dueno):
    panel = _panel(cliente.get("/control", params={"abrir": "LEAD-86",
                                                   "vista": "estado"}).text)
    assert "Nadie lo tiene todavía" in panel
    assert "dc-fila-ojo" in panel


def test_el_monto_grande_es_el_total_de_la_orden_real(cliente, de_dueno,
                                                       monkeypatch):
    falso = OdooCotLead()
    monkeypatch.setattr(ventas, "_ejecutar", falso.ejecutar)
    _con_plata(falso, "LEAD-91")
    panel = _panel(cliente.get("/control", params={"abrir": "LEAD-91",
                                                   "vista": "estado"}).text)
    assert '<span class="dc-monto-amt num">$1,150.00</span>' in panel
    assert "S00100 · debe $1,080.00" in panel


def test_sin_orden_real_no_hay_numero_y_el_panel_dice_por_que(cliente,
                                                               de_dueno,
                                                               monkeypatch):
    monkeypatch.setattr(ventas, "_ejecutar", OdooCotLead().ejecutar)
    panel = _panel(cliente.get("/control", params={"abrir": "LEAD-86",
                                                   "vista": "estado"}).text)
    assert "dc-monto-amt" not in panel       # ni un $0.00 de relleno
    assert "Todavía no hay monto: falta la cotización real." in panel


def test_odoo_caido_no_se_confunde_con_sin_cotizacion(cliente, de_dueno,
                                                       monkeypatch):
    falso = OdooCotLead()
    falso.fallar = True
    monkeypatch.setattr(ventas, "_ejecutar", falso.ejecutar)
    panel = _panel(cliente.get("/control", params={"abrir": "LEAD-91",
                                                   "vista": "estado"}).text)
    assert "dc-monto-amt" not in panel
    assert "No se pudo leer Odoo" in panel


def test_la_fila_del_seguimiento_va_apagada_con_su_todavia_no(cliente,
                                                               de_dueno):
    # La señal existe en Linear (está en la muestra), pero poner o cambiar
    # un seguimiento CON fecha y nota todavía no existe como dato: la fila
    # lo dice y no es un enlace.
    panel = _panel(cliente.get("/control", params={"abrir": "LEAD-91",
                                                   "vista": "estado"}).text)
    fila = panel[panel.index(">Seguimiento</span>"):]
    fila = fila[:fila.index("</div>", fila.index("dc-fila-no"))]
    assert "Sin seguimiento" in fila
    assert "Todavía no" in fila
    assert "La fecha y la nota todavía no existen como dato." in fila


def test_sin_twenty_la_fila_del_ultimo_mensaje_lo_dice(cliente, de_dueno):
    # En el 8095 (y en modo muestra) Twenty no está conectado: la fila lo
    # DICE, en vez de pasar por «sin mensajes» — que sería inventar una
    # respuesta sobre una conversación que no se pudo mirar.
    panel = _panel(cliente.get("/control", params={"abrir": "LEAD-91",
                                                   "vista": "estado"}).text)
    assert "Twenty no está conectado en esta instancia" in panel
    assert "Sin mensajes todavía" not in panel


def test_twenty_caido_y_twenty_sin_conectar_no_se_confunden(monkeypatch):
    # Tres cosas distintas, tres textos distintos.
    lead = linear_leads.uno("LEAD-91")
    monkeypatch.setattr(control.crm_twenty, "ficha_de_lead",
                        lambda l: {"fallo": "Twenty no contesta; la conversación…"})
    caido = control.ficha("LEAD-91")["filas"][2]
    assert "no contesta" in caido["valor"]
    monkeypatch.setattr(control.crm_twenty, "ficha_de_lead", lambda l: None)
    sin_conectar = control.ficha("LEAD-91")["filas"][2]
    assert "no está conectado" in sin_conectar["valor"]
    monkeypatch.setattr(control.crm_twenty, "ficha_de_lead",
                        lambda l: {"mensajes": []})
    vacio = control.ficha("LEAD-91")["filas"][2]
    assert vacio["valor"] == "Sin mensajes todavía"
    assert lead["ref"] == "LEAD-91"


def test_el_ultimo_mensaje_es_el_mas_nuevo_con_quien_lo_escribio(monkeypatch):
    monkeypatch.setattr(control.crm_twenty, "ficha_de_lead", lambda l: {
        "mensajes": [
            {"fecha": "2026-10-01T10:00:00Z", "texto": "Hola",
             "salida": False, "cuando": "1 oct · 10:00", "autor": ""},
            {"fecha": "2026-10-02T17:14:00Z",
             "texto": "Perfecto, espero la versión nueva.",
             "salida": False, "cuando": "ayer · 17:14", "autor": ""},
        ]})
    fila = control.ficha("LEAD-91")["filas"][2]
    assert fila["valor"] == "«Perfecto, espero la versión nueva.»"
    assert fila["detalle"] == "Tamara · ayer · 17:14"


# ---- el candado de la plata (E del encargo: ninguno se afloja) ----

def test_quien_ve_la_plata_de_un_lead():
    director = {"admin": True, "resp_propio": "", "plata_todo": True}
    finanzas = {"admin": False, "resp_propio": "", "plata_todo": True}
    ruben = {"admin": False, "resp_propio": "Ruben", "plata_todo": False}
    nadie = {"admin": False, "resp_propio": "", "plata_todo": False}
    mio = linear_leads.uno("LEAD-91")      # Resp: Ruben
    ajeno = linear_leads.uno("LEAD-89")    # Resp: Mary
    for alc in (director, finanzas):
        assert control.puede_ver_plata(mio, alc) is True
        assert control.puede_ver_plata(ajeno, alc) is True
    assert control.puede_ver_plata(mio, ruben) is True
    assert control.puede_ver_plata(ajeno, ruben) is False
    assert control.puede_ver_plata(mio, nadie) is False


def test_un_lead_ajeno_no_ensena_ni_un_numero_de_plata(cliente, monkeypatch):
    # Sin AJUSTES_ADMINS la sesión no es dueña y no le toca ningún lead:
    # ve el tablero completo (28/09/2026) pero NO la plata de nadie.
    falso = OdooCotLead()
    monkeypatch.setattr(ventas, "_ejecutar", falso.ejecutar)
    _con_plata(falso, "LEAD-91")
    cuerpo = cliente.get("/control", params={"abrir": "LEAD-91",
                                             "vista": "estado"}).text
    panel = _panel(cuerpo)
    assert "1,150" not in cuerpo and "1150" not in cuerpo
    assert "Cobrar saldo" not in panel
    assert "ficha-cot-monto" not in panel
    # Y se dice quién la ve, en vez de dejar el hueco mudo.
    assert control.LEYENDA_SIN_PLATA in panel


def test_el_tablero_no_le_pide_a_odoo_la_plata_que_no_va_a_ensenar(monkeypatch):
    # El candado no es solo de pintado: de un lead que esta sesión no
    # puede ver no se le pregunta nada a Odoo.
    pedidos = []
    monkeypatch.setattr(control.cot_lead, "plata_de_varios",
                        lambda leads: pedidos.append([l["ref"] for l in leads])
                        or {"ok": True, "por_lead": {}})
    control.tablero_por_estado(
        alcance_actual={"admin": False, "resp_propio": "Ruben",
                        "plata_todo": False})
    assert pedidos and set(pedidos[0]) == {"LEAD-91", "LEAD-88"}  # los de Ruben


def test_sin_alcance_el_tablero_no_toca_odoo(monkeypatch):
    # `tablero_por_estado()` a secas (pruebas, avisos de fondo) no paga un
    # viaje a Odoo: el monto solo se lee cuando la pantalla lo pide.
    def nunca(leads):
        raise AssertionError("no se le debe preguntar a Odoo")

    monkeypatch.setattr(control.cot_lead, "plata_de_varios", nunca)
    columnas = control.tablero_por_estado()
    assert all(l["monto"] is None for c in columnas for l in c["leads"])


# ---- B. el tablero reordenado ----

def test_la_tarjeta_lleva_el_monto_arriba_y_la_persona_abajo(cliente,
                                                              de_dueno,
                                                              monkeypatch):
    falso = OdooCotLead()
    monkeypatch.setattr(ventas, "_ejecutar", falso.ejecutar)
    _con_plata(falso, "LEAD-91")
    cuerpo = cliente.get("/control", params={"vista": "estado"}).text
    desde = cuerpo.index('data-ref="LEAD-91"')
    resto = cuerpo[desde + 10:]
    corta = resto.index("data-ref=") if "data-ref=" in resto else len(resto)
    tarjeta = cuerpo[desde:desde + 10 + corta]
    arriba = tarjeta[:tarjeta.index("ctl-pie-tarjeta")]
    abajo = tarjeta[tarjeta.index("ctl-pie-tarjeta"):]
    # Arriba: el nombre y la plata.
    assert "Tamara" in arriba
    assert '<b class="ctl-monto num">$1,150.00</b>' in arriba
    # Abajo: el color del interés y la persona.
    assert "dc-chip-interes" in abajo and ">Plantas</span>" in abajo
    assert 'aria-label="Responsable: Ruben"' in abajo
    assert "ctl-av" not in arriba
    # El «hace» no se pierde ni se repite: bajó a la fila de abajo.
    assert "hace 1 día" in abajo and "hace 1 día" not in arriba


def test_sin_monto_el_hace_se_queda_arriba_como_en_el_lienzo(cliente,
                                                              de_dueno):
    # Las tarjetas sin plata del lienzo llevan el tiempo en ese rincón.
    cuerpo = cliente.get("/control", params={"vista": "estado"}).text
    desde = cuerpo.index('data-ref="LEAD-88"')
    resto = cuerpo[desde + 10:]
    corta = resto.index("data-ref=") if "data-ref=" in resto else len(resto)
    tarjeta = cuerpo[desde:desde + 10 + corta]
    arriba = tarjeta[:tarjeta.index("ctl-pie-tarjeta")]
    assert "ctl-monto" not in arriba
    # Y con él su resaltado de los 5 días (T2, 29/09/2026), que no se
    # perdió al mudarse de fila.
    assert 'class="ctl-hace hace-alerta"' in arriba


def test_el_total_de_la_columna_es_solo_de_quien_ve_toda_la_plata(
        cliente, de_dueno, monkeypatch):
    falso = OdooCotLead()
    monkeypatch.setattr(ventas, "_ejecutar", falso.ejecutar)
    _con_plata(falso, "LEAD-91")
    del_dueno = cliente.get("/control", params={"vista": "estado"}).text
    assert '<span class="ret-total num">$1,150.00</span>' in del_dueno
    # Sin un solo monto conocido no hay total: un $0.00 diría «no hay
    # plata» cuando lo cierto es «no se sabe».
    assert del_dueno.count('class="ret-total') == 1


def test_asignar_desde_la_tarjeta_sin_dueno_es_del_dueno(cliente, de_dueno):
    cuerpo = cliente.get("/control", params={"vista": "estado"}).text
    desde = cuerpo.index('data-ref="LEAD-86"')   # sin Resp:
    resto = cuerpo[desde + 10:]
    corta = resto.index("data-ref=") if "data-ref=" in resto else len(resto)
    tarjeta = cuerpo[desde:desde + 10 + corta]
    assert 'class="ctl-asignar"' in tarjeta
    assert "abrir=LEAD-86&asignar=1" in tarjeta
    # El hueco se sigue diciendo con todas sus letras.
    assert "chip-nadie" in tarjeta


def test_un_empleado_no_ve_el_enlace_de_asignar(cliente):
    cuerpo = cliente.get("/control", params={"vista": "estado"}).text
    assert "ctl-asignar" not in cuerpo


# ---- C. el cuadro de asignar / reasignar ----

def test_el_cuadro_de_asignar_se_abre_con_un_enlace_get(cliente, de_dueno):
    cuerpo = cliente.get("/control", params={
        "vista": "estado", "abrir": "LEAD-86", "asignar": "1"}).text
    assert 'class="modal dc-asignar"' in cuerpo
    assert "¿A quién se lo mandas?" in cuerpo
    # Las tres etiquetas `Resp:` de Linear, con su carga REAL de hoy.
    for nombre in linear_leads.responsables():
        assert f'value="{nombre}"' in cuerpo
    assert "2 leads abiertos" in cuerpo        # los de Ruben


def test_en_el_cuadro_no_viene_nadie_premarcado(cliente, de_dueno):
    cuerpo = cliente.get("/control", params={
        "vista": "estado", "abrir": "LEAD-91", "asignar": "1"}).text
    assert "checked" not in cuerpo
    # Ni siquiera quien lo tiene hoy: se dice, no se marca.
    assert "lo tiene hoy" in cuerpo


def test_la_nota_y_la_fecha_del_cuadro_son_opcionales(cliente, de_dueno):
    cuerpo = cliente.get("/control", params={
        "vista": "estado", "abrir": "LEAD-86", "asignar": "1"}).text
    assert "Nota para la persona (opcional)" in cuerpo
    assert "Seguimiento (opcional)" in cuerpo
    caja = cuerpo[cuerpo.index("dc-asignar-dos"):]
    caja = caja[:caja.index("</div>\n\n  <div class=\"acciones\">")]
    assert "required" not in caja


def test_mandar_peticion_va_apagado_y_el_cuadro_no_escribe_nada(cliente,
                                                                 de_dueno):
    cuerpo = cliente.get("/control", params={
        "vista": "estado", "abrir": "LEAD-86", "asignar": "1"}).text
    cuadro = cuerpo[cuerpo.index('class="modal dc-asignar"'):]
    cuadro = cuadro[:cuadro.index("</div>\n{% endif %}") if "{% endif %}" in cuadro
                    else len(cuadro)]
    assert "Mandar petición — Todavía no" in cuadro
    assert "disabled" in cuadro
    # Es una capa, no un formulario: no hay a dónde mandar nada todavía.
    assert "<form" not in cuadro[:cuadro.index("Mandar petición")]
    # Y lo que SÍ reparte hoy sigue nombrado.
    assert "Se lo doy a" in cuadro


def test_con_dueno_el_mismo_cuadro_dice_reasignar(cliente, de_dueno):
    cuerpo = cliente.get("/control", params={
        "vista": "estado", "abrir": "LEAD-91", "asignar": "1"}).text
    assert "<h4>Reasignar lead</h4>" in cuerpo
    assert "Reasignar — Todavía no" in cuerpo


def test_repartir_sigue_siendo_del_dueno_tambien_en_el_cuadro(cliente):
    # Un empleado puede pedir ?asignar=1 a mano: el servidor no le arma el
    # cuadro. El candado no es que el enlace no se pinte.
    cuerpo = cliente.get("/control", params={
        "vista": "estado", "abrir": "LEAD-86", "asignar": "1"}).text
    assert "dc-asignar" not in cuerpo


def test_el_cuadro_pliega_el_panel_una_capa_a_la_vez(cliente, de_dueno):
    # Los dos viven en el mismo z-index: dejarlos juntos pondría el panel
    # SOBRE el telón del cuadro, sin atenuar. Se abre uno u otro, y la X
    # del cuadro vuelve al panel.
    cuerpo = cliente.get("/control", params={
        "vista": "estado", "abrir": "LEAD-91", "asignar": "1"}).text
    assert 'class="panel-der"' not in cuerpo
    assert cuerpo.count('class="telon"') == 1
    assert 'href="/control?vista=estado&amp;abrir=LEAD-91"' in cuerpo


# ---------------------------------------------------------------------------
# Fidelidad al lienzo de Roles: NINGÚN elemento se omite. Lo que todavía
# no tiene dato ni flujo se pinta IGUAL, en su lugar, apagado con su
# «Todavía no» — nunca se borra de la pantalla ni se reacomoda.
# ---------------------------------------------------------------------------

def test_nuevo_lead_esta_en_su_esquina_apagado(cliente, de_dueno):
    # El lienzo lo pone arriba a la derecha. Los leads nacen del mensaje
    # del cliente, así que no hay camino: apagado, pero presente.
    cuerpo = cliente.get("/control", params={"vista": "estado"}).text
    assert "+ Nuevo lead — Todavía no" in cuerpo
    boton = cuerpo[cuerpo.index("+ Nuevo lead") - 260:cuerpo.index("+ Nuevo lead")]
    assert "disabled" in boton


def test_la_tira_ver_a_trae_a_todos_y_el_hueco_con_su_cuenta(cliente, de_dueno):
    cuerpo = cliente.get("/control", params={"vista": "estado"}).text
    tira = cuerpo[cuerpo.index('class="dc-fl"'):]
    tira = tira[:tira.index("</div>")]
    assert "Ver a:" in tira
    assert ">Todos</a>" in tira
    for nombre in linear_leads.responsables():
        assert f">{nombre}</a>" in tira
    # El conteo de «Sin asignar» es real: los vivos sin `Resp:`.
    vivos = [c for c in control.tablero_por_estado() for c in c["leads"]]
    sin_dueno = len([l for l in control._vivos(vivos) if not l["resp"]])
    assert f">Sin asignar {sin_dueno}</a>" in tira


def test_ver_a_filtra_lo_que_se_pinta_y_nada_mas(cliente, de_dueno):
    cuerpo = cliente.get("/control", params={"vista": "estado",
                                             "ver": "Mary"}).text
    # LEAD-89 es de Mary; LEAD-91, de Ruben.
    assert 'data-ref="LEAD-89"' in cuerpo
    assert 'data-ref="LEAD-91"' not in cuerpo
    # El filtro se queda puesto al abrir una tarjeta y al cerrar el panel
    # (el &amp; es Jinja escapando el ampersand del query, como debe).
    assert "/control?vista=estado&amp;ver=Mary&abrir=LEAD-89" in cuerpo
    panel = cliente.get("/control", params={"vista": "estado", "ver": "Mary",
                                            "abrir": "LEAD-89"}).text
    assert 'class="telon" href="/control?vista=estado&amp;ver=Mary"' in panel


def test_un_filtro_inventado_no_deja_la_pantalla_en_blanco(cliente, de_dueno):
    cuerpo = cliente.get("/control", params={"vista": "estado",
                                             "ver": "Fulano"}).text
    assert 'data-ref="LEAD-91"' in cuerpo
    assert 'data-ref="LEAD-89"' in cuerpo


def test_ver_a_no_toca_ningun_permiso():
    # Es una vista, no un candado: filtrar no cambia lo que se puede
    # mover ni lo que se puede ver de plata.
    leads = linear_leads.listar()
    solo_mary = control.filtrar_por_ver(leads, "Mary")
    assert solo_mary and all(l["resp"] == "Mary" for l in solo_mary)
    assert control.filtrar_por_ver(leads, "") == leads
    sin_dueno = control.filtrar_por_ver(leads, control.VER_SIN_ASIGNAR)
    assert sin_dueno and all(not l["resp"] for l in sin_dueno)


def test_las_dos_cajas_de_peticiones_estan_en_su_lugar_apagadas(cliente,
                                                                 de_dueno):
    # El lienzo las pone entre los filtros y el tablero. Las peticiones
    # llegan en el punto 2 del plan de roles: las cajas se pintan igual,
    # apagadas, y SIN una sola fila de ejemplo.
    cuerpo = cliente.get("/control", params={"vista": "estado"}).text
    tira = cuerpo[cuerpo.index('class="dc-strip"'):cuerpo.index('class="ret-tablero"')]
    assert "Peticiones que mandaste · esperando" in tira
    assert "Dijeron que no pueden" in tira
    assert tira.count("Todavía no") == 3      # las dos cajas y Reasignar
    assert "Reasignar — Todavía no" in tira
    # Ni un nombre ni un número inventado: no hay datos de peticiones.
    for nombre in linear_leads.responsables():
        assert nombre not in tira


def test_las_cajas_de_peticiones_son_del_director(cliente):
    # Es él quien manda las peticiones; a un empleado no le dicen nada.
    cuerpo = cliente.get("/control", params={"vista": "estado"}).text
    assert "dc-strip" not in cuerpo


def test_cobrar_se_pinta_siempre_aunque_no_haya_saldo(cliente, de_dueno,
                                                       monkeypatch):
    # El botón grande del pie es del lienzo: esconderlo dejaba el pie
    # vacío. Sin saldo conocido dice «Cobrar» a secas — nunca un $0.00.
    monkeypatch.setattr(ventas, "_ejecutar", OdooCotLead().ejecutar)
    panel = _panel(cliente.get("/control", params={"abrir": "LEAD-86",
                                                   "vista": "estado"}).text)
    assert "Cobrar — Todavía no" in panel
    assert "$0.00" not in panel


def test_ver_contacto_es_el_enlace_del_lienzo(cliente, de_dueno):
    # La ficha de Contactos casa por teléfono EN LECTURA, así que el
    # camino honesto es su buscador con el celular puesto: siempre
    # resuelve y cae en el contacto si existe.
    panel = _panel(cliente.get("/control", params={"abrir": "LEAD-91",
                                                   "vista": "estado"}).text)
    celular = linear_leads.uno("LEAD-91")["celular"]
    assert celular == "6552-0966"
    assert 'href="/contactos?q=6552-0966"' in panel
    assert ">Ver contacto</a>" in panel


def test_sin_telefono_ver_contacto_va_apagado(cliente, de_dueno):
    # LEAD-83 (Monica Gama) no tiene celular: no hay por dónde buscarlo.
    panel = _panel(cliente.get("/control", params={"abrir": "LEAD-83",
                                                   "vista": "estado"}).text)
    assert "Ver contacto — Todavía no" in panel
    assert 'href="/contactos?q="' not in panel
