"""El mantenimiento mensual (28/09/2026, pedido de Abraham).

Corre en modo muestra (linear_leads + calendario), así que la serie
completa —arrancar, marcar Hecha, parar— se prueba sin tocar nada real.

LEAD-88 (Hotel Bristol) ya trae interés Mantenimiento y Resp: Ruben en el
tablero de muestra — es el que usan casi todas las pruebas de acá. Está
Entregado con saldo pendiente, así que un `mover_estado(..., "GANADO")`
directo (automático: hasta un cerrado siempre "puede avanzar") alcanza
para simular que ganó, sin tener que pelear con el saldo de Odoo.
"""

from datetime import date, timedelta

import pytest

from app import agenda, calendario, linear_leads, mantenimiento


@pytest.fixture(autouse=True)
def muestra_limpia(monkeypatch, db_limpia):
    monkeypatch.delenv("LINEAR_API_KEY", raising=False)
    monkeypatch.delenv("LINEAR_PROJECT_CALENDARIO_ID", raising=False)
    monkeypatch.delenv("CALENDARIO_ESCRITURA", raising=False)
    monkeypatch.delenv("ODOO_URL", raising=False)
    monkeypatch.delenv("AJUSTES_ADMINS", raising=False)
    linear_leads.reiniciar_muestra()
    calendario.reiniciar_muestra()
    calendario.invalidar_cache()
    agenda.refrescar()
    mantenimiento.iniciar_tablas()


@pytest.fixture
def de_dueno(monkeypatch):
    monkeypatch.setenv("AJUSTES_ADMINS", "genesis")


def _rango_largo():
    desde = calendario.hoy().isoformat()
    hasta = (calendario.hoy() + timedelta(days=400)).isoformat()
    return desde, hasta


def _cita_de(ref, tipo="mantenimiento"):
    """La actividad vigente de este lead, buscando en un rango largo (la
    cita nace ~1 mes en el futuro, fuera del mes en curso del calendario)."""
    desde, hasta = _rango_largo()
    return next(a for a in calendario.listar(desde, hasta)
                if a["lead"] == ref and a["tipo"] == tipo)


# ---------------------------------------------------------------------------
# Al ganar: arranca la serie (o no, según el interés)
# ---------------------------------------------------------------------------

def test_al_ganar_crea_la_primera_cita_con_el_dia_ancla_y_el_resp():
    lead = linear_leads.uno("LEAD-88")  # Hotel Bristol
    assert lead["interes"] == "Mantenimiento"
    assert lead["resp"] == "Ruben"
    hoy = calendario.hoy()

    mantenimiento.al_ganar(lead, autor="Ruben")

    assert mantenimiento.activo("LEAD-88") is True
    cita = _cita_de("LEAD-88")
    esperado = mantenimiento._siguiente_fecha(hoy, hoy.day)
    assert cita["fecha"] == esperado.isoformat()
    assert cita["resp_lead"] == "Ruben"
    assert cita["tipo"] == "mantenimiento"


def test_con_otro_interes_no_crea_nada():
    lead = linear_leads.uno("LEAD-90")  # Juan Carlos Lopez, interés Plantas
    assert lead["interes"] != "Mantenimiento"

    mantenimiento.al_ganar(lead, autor="Mary")

    assert mantenimiento.activo("LEAD-90") is False
    desde, hasta = _rango_largo()
    assert not any(a["lead"] == "LEAD-90" for a in calendario.listar(desde, hasta))


def test_al_ganar_es_idempotente_no_duplica_la_serie():
    lead = linear_leads.uno("LEAD-88")
    mantenimiento.al_ganar(lead, autor="Ruben")
    primera = _cita_de("LEAD-88")
    mantenimiento.al_ganar(lead, autor="Ruben")  # una segunda corrida, por lo que sea
    desde, hasta = _rango_largo()
    citas = [a for a in calendario.listar(desde, hasta)
             if a["lead"] == "LEAD-88" and a["tipo"] == "mantenimiento"]
    assert len(citas) == 1
    assert citas[0]["id"] == primera["id"]


def test_un_ganado_por_pago_tardio_tambien_arranca_la_serie():
    """El otro camino a Ganado (`cerrar_los_que_ya_pagaron`, cuando el pago
    que salda entra después) pasa por el MISMO enganche — no hay dos
    lugares que puedan desincronizarse."""
    agenda._SALDOS_MUESTRA["PP-70205"]["saldo"] = 0.0
    agenda.refrescar()
    try:
        cerrados = agenda.cerrar_los_que_ya_pagaron()
    finally:
        agenda._SALDOS_MUESTRA["PP-70205"]["saldo"] = 300.0
        agenda.refrescar()
    assert cerrados == ["Hotel Bristol"]
    assert linear_leads.uno("LEAD-88")["estado"] == "GANADO"
    assert mantenimiento.activo("LEAD-88") is True


# ---------------------------------------------------------------------------
# «Hecha» en la recurrente: nace la siguiente, el lead no se mueve
# ---------------------------------------------------------------------------

def test_hecha_en_la_recurrente_crea_la_siguiente_y_no_mueve_el_estado():
    lead = linear_leads.uno("LEAD-88")
    assert linear_leads.mover_estado(lead["id"], "GANADO") is True
    lead = linear_leads.uno("LEAD-88")
    hoy = calendario.hoy()
    mantenimiento.al_ganar(lead, autor="Ruben")
    cita = _cita_de("LEAD-88")
    # Como en la pantalla real: primero la actividad se marca Hecha en el
    # calendario, y RECIÉN DESPUÉS se le pregunta al embudo qué hacer.
    calendario.cambiar_estado(cita["id"], "hecha")

    aviso = agenda.al_marcar_hecha(cita, autor="Mary")

    assert linear_leads.uno("LEAD-88")["estado"] == "GANADO"  # sigue igual
    assert "próxima cita" in aviso
    desde, hasta = _rango_largo()
    nueva = next(a for a in calendario.listar(desde, hasta)
                if a["lead"] == "LEAD-88" and a["tipo"] == "mantenimiento"
                and a["id"] != cita["id"])
    esperado = mantenimiento._siguiente_fecha(date.fromisoformat(cita["fecha"]), hoy.day)
    assert nueva["fecha"] == esperado.isoformat()
    assert nueva["estado"] != "hecha"


def test_hecha_en_un_mantenimiento_normal_sigue_entregando_como_hoy():
    """Un Mantenimiento agendado a mano (no la recurrente de esta serie)
    entrega igual que Entrega o Instalación — se distingue por el campo,
    no por el título."""
    dia = calendario.hoy().isoformat()
    agenda.agendar("LEAD-91", "mantenimiento", dia, hora="10:00")  # Tamara debe $762.50
    actividad = _cita_de("LEAD-91")
    assert mantenimiento.es_recurrente(actividad) is False

    aviso = agenda.al_marcar_hecha(actividad, autor="Ruben")

    lead = linear_leads.uno("LEAD-91")
    assert lead["estado"] == "ENTREGADO"       # como cualquier entrega con saldo
    assert lead["pago"] == "Cobrar saldo"
    assert "Cobrar saldo" in aviso


# ---------------------------------------------------------------------------
# El ancla no deriva: un 31 en un mes corto vuelve al 31 después
# ---------------------------------------------------------------------------

def test_ancla_31_en_mes_corto_cae_al_ultimo_dia_y_vuelve_despues():
    primera = mantenimiento._siguiente_fecha(date(2027, 1, 31), 31)
    assert primera == date(2027, 2, 28)  # 2027 no es bisiesto
    segunda = mantenimiento._siguiente_fecha(primera, 31)  # el ancla, no el 28
    assert segunda == date(2027, 3, 31)


def test_ancla_31_en_febrero_bisiesto_cae_29():
    assert mantenimiento._siguiente_fecha(date(2028, 1, 31), 31) == date(2028, 2, 29)


# ---------------------------------------------------------------------------
# Parar: apaga, cancela la pendiente, comenta una vez
# ---------------------------------------------------------------------------

def test_parar_apaga_cancela_la_pendiente_y_comenta_una_vez():
    lead = linear_leads.uno("LEAD-88")
    mantenimiento.al_ganar(lead, autor="Ruben")
    cita = _cita_de("LEAD-88")
    antes = len(linear_leads.comentarios(lead["id"]))

    aviso, error = mantenimiento.parar("LEAD-88", autor="Abraham")

    assert error == ""
    assert "Mantenimiento detenido" in aviso
    assert mantenimiento.activo("LEAD-88") is False

    desde, hasta = _rango_largo()
    actualizada = next(a for a in calendario.listar(desde, hasta) if a["id"] == cita["id"])
    assert actualizada["estado"] == "cancel"

    notas = linear_leads.comentarios(lead["id"])
    assert len(notas) == antes + 1
    assert mantenimiento.TEXTO_PARAR in notas[-1]["texto"]
    assert "Abraham" in notas[-1]["texto"]


def test_parar_sin_mantenimiento_activo_avisa_error():
    aviso, error = mantenimiento.parar("LEAD-90", autor="Abraham")
    assert aviso == ""
    assert "no tiene mantenimiento activo" in error


def test_parar_sin_cita_pendiente_igual_apaga_y_comenta():
    """Si por lo que sea no había una cita pendiente (falló al crearla),
    parar igual apaga la serie y deja su comentario — no hay nada que
    cancelar, y eso no es un error."""
    lead = linear_leads.uno("LEAD-88")
    mantenimiento.al_ganar(lead, autor="Ruben")
    # Se "pierde" la cita a mano, simulando que nunca se pudo crear.
    from app.datos import _db
    with _db() as con:
        con.execute("UPDATE mantenimiento_lead SET actividad_pendiente_id=NULL WHERE ref=?",
                    ("LEAD-88",))
    aviso, error = mantenimiento.parar("LEAD-88", autor="Abraham")
    assert error == ""
    assert mantenimiento.activo("LEAD-88") is False


# ---------------------------------------------------------------------------
# El botón en la ficha
# ---------------------------------------------------------------------------

def test_el_boton_solo_se_pinta_con_mantenimiento_activo(cliente, de_dueno):
    cuerpo_sin = cliente.get("/control", params={"abrir": "LEAD-88"}).text
    assert "Parar mantenimiento" not in cuerpo_sin

    mantenimiento.al_ganar(linear_leads.uno("LEAD-88"), autor="Ruben")

    cuerpo_con = cliente.get("/control", params={"abrir": "LEAD-88"}).text
    assert "Parar mantenimiento" in cuerpo_con


def test_parar_desde_la_pantalla(cliente, de_dueno):
    mantenimiento.al_ganar(linear_leads.uno("LEAD-88"), autor="Ruben")
    respuesta = cliente.post(
        "/control/mantenimiento/parar", params={"vista": "estado"},
        data={"ref": "LEAD-88"}, follow_redirects=False)
    assert respuesta.status_code == 303
    assert "aviso=" in respuesta.headers["location"]
    assert mantenimiento.activo("LEAD-88") is False


def test_parar_no_lo_puede_un_empleado_de_otro(cliente):
    # Sesión sin AJUSTES_ADMINS: no es dueño, y "solo_resp" le sale vacío
    # (no matchea a nadie) — ningún lead es "suyo", ni el de Ruben.
    mantenimiento.al_ganar(linear_leads.uno("LEAD-88"), autor="Ruben")
    respuesta = cliente.post(
        "/control/mantenimiento/parar",
        data={"ref": "LEAD-88"}, follow_redirects=False)
    assert respuesta.status_code == 303
    assert "error=" in respuesta.headers["location"]
    assert mantenimiento.activo("LEAD-88") is True
