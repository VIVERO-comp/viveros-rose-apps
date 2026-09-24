"""Fase 4: el calendario cierra el embudo.

Corren en modo muestra (sin LINEAR_API_KEY y sin Odoo), así que el tablero
de leads y su plata viven en memoria: se puede probar entero el camino
«Por agendar → Agendado → Entregado → Ganado» sin tocar nada real.

Los leads de muestra que importan (app/linear_leads.py):

    LEAD-91  Tamara              Por agendar   Abono 50%   saldo $762.50
    LEAD-90  Juan Carlos Lopez   Por agendar   Pagado 100% saldo $0, sin Resp:
    LEAD-89  Boda Las Nubes      Agendado      Abono 50%   saldo $1 200
    LEAD-88  Hotel Bristol       Entregado     Cobrar saldo saldo $300
"""

import pytest

from app import agenda, calendario, linear_leads


@pytest.fixture(autouse=True)
def muestra_limpia(monkeypatch):
    monkeypatch.delenv("LINEAR_API_KEY", raising=False)
    monkeypatch.delenv("LINEAR_PROJECT_CALENDARIO_ID", raising=False)
    monkeypatch.delenv("CALENDARIO_ESCRITURA", raising=False)
    monkeypatch.delenv("ODOO_URL", raising=False)
    linear_leads.reiniciar_muestra()
    calendario.reiniciar_muestra()
    calendario.invalidar_cache()
    agenda.refrescar()


@pytest.fixture
def odoo_caido(monkeypatch):
    """Odoo configurado pero sin contestar: el caso que NO puede mentir."""
    monkeypatch.setenv("ODOO_URL", "http://odoo-de-prueba")
    monkeypatch.setenv("ODOO_DB", "prueba")
    monkeypatch.setenv("ODOO_USER", "prueba")
    monkeypatch.setenv("ODOO_PASSWORD", "prueba")

    def revienta(*_a, **_k):
        raise OSError("no hay ruta al host")

    monkeypatch.setattr("app.ventas._ejecutar", revienta)
    agenda.refrescar()


# ---------------------------------------------------------------------------
# Los 5 tipos que se agendan desde un lead
# ---------------------------------------------------------------------------

def test_los_cinco_tipos_existen_en_el_calendario():
    # Ninguna etiqueta se crea: los 5 tienen que ser tipos que YA existen
    # en el grupo "Tipo de actividad" de Linear.
    for tipo in agenda.TIPOS:
        assert tipo["clave"] in calendario.POR_CLAVE
    assert [t["clave"] for t in agenda.TIPOS] == [
        "entrega", "instalacion", "mantenimiento", "visita", "recogida"]


@pytest.mark.parametrize("tipo, entrega", [
    # Las tres que dejan el trabajo hecho (regla del dueño, 24/09/2026).
    ("entrega", True),
    ("instalacion", True),
    ("mantenimiento", True),
    # Una Visita es ir a ver el sitio: el trabajo todavía no se hizo.
    ("visita", False),
    # Una Recogida es retirar las plantas del alquiler DESPUÉS del evento:
    # el lead ya se entregó.
    ("recogida", False),
    # Y un tipo que no se agenda desde un lead nunca mueve el embudo.
    ("compra", False),
    ("reunion", False),
])
def test_solo_tres_tipos_cierran_la_entrega(tipo, entrega):
    assert agenda.cierra_la_entrega(tipo) is entrega


# ---------------------------------------------------------------------------
# El bloque "Por agendar", con su saldo
# ---------------------------------------------------------------------------

def test_el_bloque_lista_los_que_ya_pagaron_con_su_saldo():
    bloque = agenda.por_agendar()
    por_ref = {l["ref"]: l for l in bloque["leads"]}
    assert set(por_ref) == {"LEAD-91", "LEAD-90"}
    assert por_ref["LEAD-91"]["pago"] == "Abono 50%"
    assert por_ref["LEAD-91"]["saldo"] == 762.5
    assert por_ref["LEAD-91"]["saldo_texto"] == "$762.50"
    assert por_ref["LEAD-90"]["saldo"] == 0.0
    assert bloque["error"] == ""


def test_el_saldo_se_escribe_con_el_espacio_de_los_miles():
    assert agenda.plata(1525) == "$1 525.00"
    assert agenda.plata(0) == "$0.00"


def test_si_odoo_no_contesta_el_saldo_no_se_inventa(odoo_caido):
    bloque = agenda.por_agendar()
    assert bloque["error"]
    for lead in bloque["leads"]:
        # Ni $0.00 ni un número cualquiera: "no se sabe".
        assert lead["saldo_conocido"] is False
        assert lead["saldo"] is None
        assert lead["saldo_texto"] == ""


def test_un_lead_sin_cotizacion_en_odoo_lo_dice():
    # LEAD-87 (Ximena) está en Cotizado y no tiene orden en la muestra.
    lead = agenda.lead_con_saldo("LEAD-87")
    assert lead["saldo_conocido"] is False
    assert "Sin cotización en Odoo" in lead["saldo_aviso"]


# ---------------------------------------------------------------------------
# Agendar: la fecha crea la actividad y el lead pasa a Agendado
# ---------------------------------------------------------------------------

def test_agendar_crea_la_actividad_y_mueve_el_lead():
    dia = calendario.hoy().isoformat()
    aviso = agenda.agendar("LEAD-91", "entrega", dia, hora="10:00",
                           resp="Ruben", autor="Abraham")
    assert "Agendado" in aviso
    assert linear_leads.uno("LEAD-91")["estado"] == "AGENDADO"
    actividad = next(a for a in calendario.listar(dia, dia)
                     if a["cliente"] == "Tamara")
    assert actividad["tipo"] == "entrega"
    assert actividad["lead"] == "LEAD-91"       # la amarra vive en la marca
    assert actividad["resp_lead"] == "Ruben"


def test_agendar_le_pone_el_responsable_al_lead_si_no_tenia():
    dia = calendario.hoy().isoformat()
    assert linear_leads.uno("LEAD-90")["resp"] == ""
    agenda.agendar("LEAD-90", "entrega", dia, resp="Mary")
    assert linear_leads.uno("LEAD-90")["resp"] == "Mary"


def test_agendar_sin_responsable_hereda_el_del_lead():
    dia = calendario.hoy().isoformat()
    agenda.agendar("LEAD-91", "entrega", dia, resp="")
    actividad = next(a for a in calendario.listar(dia, dia)
                     if a["cliente"] == "Tamara")
    assert actividad["resp_lead"] == "Ruben"  # el Resp: que ya tenía el lead


def test_agendar_un_tipo_que_no_se_agenda_se_rechaza():
    dia = calendario.hoy().isoformat()
    with pytest.raises(calendario.ErrorCalendario):
        agenda.agendar("LEAD-91", "compra", dia)
    assert linear_leads.uno("LEAD-91")["estado"] == "POR_AGENDAR"


def test_agendar_sin_fecha_se_rechaza():
    with pytest.raises(calendario.ErrorCalendario):
        agenda.agendar("LEAD-91", "entrega", "")
    assert linear_leads.uno("LEAD-91")["estado"] == "POR_AGENDAR"


def test_el_responsable_sugerido_sale_del_empleado_de_la_sesion():
    assert agenda.responsable_de_empleada(
        {"id": "ruben", "nombre": "Rubén Pérez", "email": "ruben@viverorose.com"}
    ) == "Ruben"
    assert agenda.responsable_de_empleada(
        {"id": "mary@viverorose.com", "nombre": "", "email": ""}) == "Mary"
    assert agenda.responsable_de_empleada(
        {"id": "nadie", "nombre": "Quien Sea", "email": ""}) == ""


# ---------------------------------------------------------------------------
# «Hecha» → Entregado, y Ganado solo con saldo 0
# ---------------------------------------------------------------------------

def _actividad_de(lead_ref, tipo="entrega"):
    dia = calendario.hoy().isoformat()
    agenda.agendar(lead_ref, tipo, dia, hora="10:00")
    nombre = linear_leads.uno(lead_ref)["nombre"]
    return next(a for a in calendario.listar(dia, dia)
                if a["cliente"] == nombre and a["tipo"] == tipo)


def test_hecha_con_saldo_deja_entregado_y_marca_cobrar_saldo():
    actividad = _actividad_de("LEAD-91")  # Tamara debe $762.50
    aviso = agenda.al_marcar_hecha(actividad, autor="Ruben")
    lead = linear_leads.uno("LEAD-91")
    assert lead["estado"] == "ENTREGADO"     # NO pasa a Ganado
    assert lead["pago"] == "Cobrar saldo"
    assert "762.50" in aviso and "Cobrar saldo" in aviso


def test_hecha_sin_saldo_sigue_sola_a_ganado():
    actividad = _actividad_de("LEAD-90")  # Juan Carlos, pagado completo
    aviso = agenda.al_marcar_hecha(actividad, autor="Mary")
    lead = linear_leads.uno("LEAD-90")
    assert lead["estado"] == "GANADO"
    assert lead["pago"] == "Pagado 100%"
    assert "Ganado" in aviso


@pytest.mark.parametrize("tipo", ["recogida", "visita"])
def test_lo_que_no_entrega_hecho_no_mueve_el_estado(tipo):
    actividad = _actividad_de("LEAD-91", tipo=tipo)
    # Agendar sí movió el lead a Agendado; marcar la actividad, no.
    assert linear_leads.uno("LEAD-91")["estado"] == "AGENDADO"
    assert agenda.al_marcar_hecha(actividad, autor="Ruben") == ""
    assert linear_leads.uno("LEAD-91")["estado"] == "AGENDADO"


def test_una_actividad_sin_lead_no_mueve_nada():
    actividad = {"lead": "", "tipo": "entrega", "resp_lead": ""}
    assert agenda.al_marcar_hecha(actividad, autor="Ruben") == ""


def test_un_lead_sin_resp_hereda_el_del_que_marco_hecha():
    # Decisión del dueño (24/09/2026). LEAD-90 no tiene Resp:.
    dia = calendario.hoy().isoformat()
    agenda.agendar("LEAD-90", "entrega", dia, resp="")
    assert linear_leads.uno("LEAD-90")["resp"] == ""
    actividad = next(a for a in calendario.listar(dia, dia)
                     if a["cliente"] == "Juan Carlos Lopez")
    agenda.al_marcar_hecha(actividad, autor="Salomón")
    assert linear_leads.uno("LEAD-90")["resp"] == "Salomón"


def test_sin_saldo_confiable_no_se_cierra_nada(odoo_caido):
    actividad = _actividad_de("LEAD-91")
    aviso = agenda.al_marcar_hecha(actividad, autor="Ruben")
    lead = linear_leads.uno("LEAD-91")
    # Entregado sí (la actividad se hizo), pero Ganado NO: cerrar un lead
    # que quizá debe plata es el error caro.
    assert lead["estado"] == "ENTREGADO"
    assert "no se pudo leer" in aviso


# ---------------------------------------------------------------------------
# Ganado cuando entra el pago que salda
# ---------------------------------------------------------------------------

def test_los_entregados_que_ya_pagaron_pasan_a_ganado():
    # Hotel Bristol (LEAD-88) está Entregado debiendo $300; entra el pago.
    agenda._SALDOS_MUESTRA["PP-70205"]["saldo"] = 0.0
    agenda.refrescar()
    try:
        assert agenda.cerrar_los_que_ya_pagaron() == ["Hotel Bristol"]
        lead = linear_leads.uno("LEAD-88")
        assert lead["estado"] == "GANADO"
        assert lead["pago"] == "Pagado 100%"
    finally:
        agenda._SALDOS_MUESTRA["PP-70205"]["saldo"] = 300.0
        agenda.refrescar()


def test_un_entregado_que_todavia_debe_no_se_cierra():
    assert agenda.cerrar_los_que_ya_pagaron() == []
    assert linear_leads.uno("LEAD-88")["estado"] == "ENTREGADO"


def test_sin_saldo_confiable_no_se_cierra_en_lote(odoo_caido):
    assert agenda.cerrar_los_que_ya_pagaron() == []
    assert linear_leads.uno("LEAD-88")["estado"] == "ENTREGADO"


# ---------------------------------------------------------------------------
# Reprogramar no toca el estado
# ---------------------------------------------------------------------------

def test_reprogramar_mueve_la_fecha_y_deja_el_estado():
    from datetime import timedelta
    actividad = _actividad_de("LEAD-91")
    otro_dia = (calendario.hoy() + timedelta(days=4)).isoformat()
    agenda.reprogramar(actividad["id"], otro_dia)
    assert linear_leads.uno("LEAD-91")["estado"] == "AGENDADO"
    movida = next(a for a in calendario.listar(otro_dia, otro_dia)
                  if a["id"] == actividad["id"])
    assert movida["fecha"] == otro_dia
    assert movida["lead"] == "LEAD-91"  # la amarra sobrevive al cambio


# ---------------------------------------------------------------------------
# La pantalla
# ---------------------------------------------------------------------------

def test_la_pantalla_pinta_el_bloque_por_agendar(cliente):
    cuerpo = cliente.get("/calendario").text
    assert "Por agendar" in cuerpo
    assert "Tamara" in cuerpo
    assert "$762.50" in cuerpo
    assert "Abono 50%" in cuerpo
    # El bloque viejo de la pestaña Retail ya no está.
    assert "de la pestaña Retail" not in cuerpo


def test_la_pantalla_dice_que_el_saldo_no_se_pudo_leer(cliente, odoo_caido):
    cuerpo = cliente.get("/calendario").text
    assert "No se pudo leer el saldo en Odoo" in cuerpo
    assert "$762.50" not in cuerpo


def test_el_formulario_de_agendar_trae_los_5_tipos_y_el_resp_sugerido(cliente):
    cuerpo = cliente.get("/calendario", params={"agendar": "LEAD-91"}).text
    assert "Agendar a Tamara" in cuerpo
    for nombre in ("Entrega", "Instalación", "Mantenimiento", "Visita", "Recogida"):
        assert f">{nombre}<" in cuerpo
    assert '<option value="Ruben" selected>' in cuerpo  # el Resp: del lead
    assert "Queda $762.50 por cobrar" in cuerpo


def test_agendar_desde_la_pantalla(cliente):
    dia = calendario.hoy().isoformat()
    respuesta = cliente.post(
        "/calendario/agendar", params={"volver": f"/calendario?dia={dia}"},
        data={"lead": "LEAD-91", "tipo": "entrega", "fecha": dia,
              "hora": "11:00", "resp": "Mary", "dur": "60"},
        follow_redirects=False)
    assert respuesta.status_code == 303
    assert "aviso=" in respuesta.headers["location"]
    assert linear_leads.uno("LEAD-91")["estado"] == "AGENDADO"


def test_agendar_con_una_fecha_mala_vuelve_al_formulario(cliente):
    respuesta = cliente.post(
        "/calendario/agendar",
        data={"lead": "LEAD-91", "tipo": "entrega", "fecha": "ayer"},
        follow_redirects=False)
    assert respuesta.status_code == 303
    destino = respuesta.headers["location"]
    assert "agendar=LEAD-91" in destino and "error=" in destino
    assert linear_leads.uno("LEAD-91")["estado"] == "POR_AGENDAR"


def test_la_ficha_ensena_el_saldo_arriba_del_boton(cliente):
    dia = calendario.hoy().isoformat()
    actividad = _actividad_de("LEAD-91")
    cuerpo = cliente.get("/calendario", params={"dia": dia, "abrir": actividad["id"]}).text
    # El saldo y el botón, en ese orden: el saldo va ARRIBA.
    assert cuerpo.index("Cobrar $762.50 antes de entregar") < cuerpo.index("Hecha · entregado")
    assert "Lead LEAD-91" in cuerpo


def test_marcar_hecha_desde_la_pantalla_mueve_el_embudo(cliente):
    dia = calendario.hoy().isoformat()
    actividad = _actividad_de("LEAD-90")  # pagado completo
    respuesta = cliente.post(
        f"/calendario/actividad/{actividad['id']}/estado",
        params={"volver": f"/calendario?dia={dia}"},
        data={"estado": "hecha"}, follow_redirects=False)
    assert respuesta.status_code == 303
    assert linear_leads.uno("LEAD-90")["estado"] == "GANADO"
