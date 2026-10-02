"""El reloj de «Responder a mano» (2/10/2026, spec B2).

Linear no guarda cuándo se puso una etiqueta, así que el botón 🔴
Responder anota el momento en `responder_a_mano_desde` y el resumen de
las 7 p.m. avisa de las que pasaron del umbral. Corren en modo muestra:
lo que se cuida es el ciclo de la fila (prender escribe, apagar borra),
la reconciliación (una etiqueta quitada a mano en Linear borra la fila),
el umbral editable en config, la siembra de estreno (una sola vez) y que
el renglón del resumen solo exista cuando hay algo que decir.
"""

from datetime import datetime, timedelta

import pytest

from app import (control, datos, linear_leads, mapas, almacen_waha,
                 responder_a_mano, resumen)
from app.datos import ZONA_PANAMA, _db


@pytest.fixture(autouse=True)
def muestra_limpia(monkeypatch, db_limpia):
    monkeypatch.delenv("LINEAR_API_KEY", raising=False)
    monkeypatch.delenv("CALENDARIO_ESCRITURA", raising=False)
    monkeypatch.delenv("TWENTY_API_KEY", raising=False)
    monkeypatch.delenv("ODOO_URL", raising=False)
    monkeypatch.delenv("SINCRO_URL", raising=False)
    monkeypatch.delenv("SINCRO_SECRET", raising=False)
    monkeypatch.delenv("VAPID_CLAVE_PUBLICA", raising=False)
    monkeypatch.delenv("VAPID_CLAVE_PRIVADA", raising=False)
    linear_leads.reiniciar_muestra()
    control.iniciar_tablas()
    responder_a_mano.iniciar_tablas()


def _fila(ref):
    with _db() as con:
        return con.execute(
            "SELECT * FROM responder_a_mano_desde WHERE lead_ref=?",
            (ref,)).fetchone()


def _envejecer(ref, horas):
    """Mueve `puesta_en` hacia atrás, como si el botón se hubiera tocado
    hace `horas`."""
    cuando = datetime.now(ZONA_PANAMA) - timedelta(hours=horas)
    with _db() as con:
        con.execute("UPDATE responder_a_mano_desde SET puesta_en=? "
                    "WHERE lead_ref=?", (cuando.isoformat(), ref))


def _sin_siembra():
    """La siembra de estreno ya corrió (en vacío): las pruebas que no son
    de la siembra no quieren que les meta filas de los leads de muestra."""
    datos.fijar_config(responder_a_mano.CLAVE_SEMBRADA, "prueba")


# ---------------------------------------------------------------------------
# El ciclo de la fila: prender escribe, apagar borra
# ---------------------------------------------------------------------------

def test_prender_responder_escribe_la_fila_con_hora_de_panama():
    _sin_siembra()
    aviso, error = control.alternar_responder("LEAD-85", True, autor="Mary")
    assert error == ""
    fila = _fila("LEAD-85")
    assert fila is not None
    cuando = datetime.fromisoformat(fila["puesta_en"])
    assert cuando.tzinfo is not None  # ISO con zona, nunca naive
    assert abs((datetime.now(ZONA_PANAMA) - cuando).total_seconds()) < 60


def test_apagar_responder_borra_la_fila():
    _sin_siembra()
    control.alternar_responder("LEAD-85", True, autor="Mary")
    assert _fila("LEAD-85") is not None

    control.alternar_responder("LEAD-85", False, autor="Mary")
    assert _fila("LEAD-85") is None


def test_sin_la_etiqueta_en_linear_no_se_anota_reloj(monkeypatch):
    """Si «Responder a mano» no existe en Linear, el botón solo toca
    «Te toca» (comportamiento de siempre) y no hay nada que medir: una
    fila sin etiqueta detrás sería un aviso falso a las 48 h."""
    _sin_siembra()
    monkeypatch.setattr(linear_leads, "responder_a_mano_disponible",
                        lambda: False)
    aviso, error = control.alternar_responder("LEAD-85", True, autor="Mary")
    assert error == ""
    assert "todavía no existe" in aviso
    assert _fila("LEAD-85") is None


def test_volver_a_prender_reinicia_el_reloj():
    _sin_siembra()
    control.alternar_responder("LEAD-85", True)
    _envejecer("LEAD-85", 100)
    control.alternar_responder("LEAD-85", True)  # idempotente, pero decide HOY
    horas = responder_a_mano._horas_desde(_fila("LEAD-85")["puesta_en"])
    assert horas < 1


# ---------------------------------------------------------------------------
# La reconciliación: la tabla nunca le gana a Linear
# ---------------------------------------------------------------------------

def test_quitar_la_etiqueta_directo_en_linear_borra_la_fila():
    _sin_siembra()
    control.alternar_responder("LEAD-85", True)
    assert _fila("LEAD-85") is not None

    # Alguien la quita en la UI de Linear, no por el botón:
    lead = linear_leads.uno("LEAD-85")
    linear_leads._muestra_label(
        lead["id"], linear_leads.LABEL_RESPONDER_A_MANO, poner=False)

    borrados = responder_a_mano.reconciliar(linear_leads.listar())
    assert borrados == ["LEAD-85"]
    assert _fila("LEAD-85") is None


def test_reconciliar_borra_la_fila_de_un_lead_que_ya_no_existe():
    _sin_siembra()
    responder_a_mano.anotar("LEAD-999")
    assert responder_a_mano.reconciliar(linear_leads.listar()) == ["LEAD-999"]
    assert _fila("LEAD-999") is None


def test_reconciliar_respeta_la_fila_que_sigue_prendida():
    _sin_siembra()
    control.alternar_responder("LEAD-85", True)
    assert responder_a_mano.reconciliar(linear_leads.listar()) == []
    assert _fila("LEAD-85") is not None


# ---------------------------------------------------------------------------
# El umbral editable en config
# ---------------------------------------------------------------------------

def test_umbral_por_defecto_es_48():
    assert responder_a_mano.umbral_horas() == 48


def test_umbral_se_lee_de_config_sin_desplegar():
    datos.fijar_config(responder_a_mano.CLAVE_UMBRAL, "6")
    assert responder_a_mano.umbral_horas() == 6


def test_umbral_roto_vuelve_al_de_siempre():
    datos.fijar_config(responder_a_mano.CLAVE_UMBRAL, "mañana")
    assert responder_a_mano.umbral_horas() == 48
    datos.fijar_config(responder_a_mano.CLAVE_UMBRAL, "-3")
    assert responder_a_mano.umbral_horas() == 48


# ---------------------------------------------------------------------------
# Los vencidos y el renglón del resumen
# ---------------------------------------------------------------------------

def test_vencidos_solo_los_que_pasaron_el_umbral_con_cliente_y_resp():
    _sin_siembra()
    control.alternar_responder("LEAD-91", True)   # Tamara · Resp: Ruben
    control.alternar_responder("LEAD-85", True)   # Diego Armando · sin Resp:
    _envejecer("LEAD-91", 50)  # pasó las 48
    # LEAD-85 recién prendida: no sale.

    filas = responder_a_mano.vencidos(linear_leads.listar())
    assert [f["ref"] for f in filas] == ["LEAD-91"]
    assert filas[0]["nombre"] == "Tamara"
    assert filas[0]["resp"] == "Ruben"
    assert filas[0]["horas"] > 48


@pytest.fixture
def resumen_mudo(monkeypatch):
    """El resumen sin salir a la red: ni Odoo, ni el almacén, ni Google."""
    monkeypatch.setattr(almacen_waha, "configurado", lambda: False)
    monkeypatch.setattr(mapas, "configurado", lambda: False)


def test_el_renglon_del_resumen_trae_cliente_y_responsable(resumen_mudo):
    _sin_siembra()
    control.alternar_responder("LEAD-91", True)
    _envejecer("LEAD-91", 72)

    r = resumen.del_dia()
    bloque = r["responder_a_mano"]
    assert bloque["total"] == 1
    assert bloque["horas"] == 48
    assert bloque["filas"][0]["nombre"] == "Tamara"
    assert bloque["filas"][0]["resp"] == "Ruben"
    assert bloque["filas"][0]["hace"].startswith("hace")
    assert "1 responder a mano +48 h" in resumen.titular(r)


def test_sin_vencidas_el_renglon_no_aparece(resumen_mudo):
    _sin_siembra()
    control.alternar_responder("LEAD-91", True)  # recién prendida

    r = resumen.del_dia()
    assert r["responder_a_mano"]["total"] == 0
    assert "responder a mano" not in resumen.titular(r)


def test_el_resumen_reconcilia_lo_quitado_a_mano(resumen_mudo):
    _sin_siembra()
    control.alternar_responder("LEAD-91", True)
    _envejecer("LEAD-91", 72)
    lead = linear_leads.uno("LEAD-91")
    linear_leads._muestra_label(
        lead["id"], linear_leads.LABEL_RESPONDER_A_MANO, poner=False)

    r = resumen.del_dia()
    assert r["responder_a_mano"]["total"] == 0
    assert _fila("LEAD-91") is None


# ---------------------------------------------------------------------------
# La siembra de estreno: una sola vez
# ---------------------------------------------------------------------------

def test_la_siembra_anota_hoy_para_las_ya_prendidas():
    lead = linear_leads.uno("LEAD-90")
    linear_leads._muestra_label(
        lead["id"], linear_leads.LABEL_RESPONDER_A_MANO, poner=True)

    sembrados = responder_a_mano.sembrar(linear_leads.listar())
    assert sembrados == ["LEAD-90"]
    fila = _fila("LEAD-90")
    assert fila is not None
    assert responder_a_mano._horas_desde(fila["puesta_en"]) < 1
    assert datos.leer_config(responder_a_mano.CLAVE_SEMBRADA, "") != ""


def test_la_siembra_corre_una_sola_vez():
    lead = linear_leads.uno("LEAD-90")
    linear_leads._muestra_label(
        lead["id"], linear_leads.LABEL_RESPONDER_A_MANO, poner=True)
    responder_a_mano.sembrar(linear_leads.listar())

    # El empleado la apaga: la fila se va…
    control.alternar_responder("LEAD-90", False)
    assert _fila("LEAD-90") is None
    # …pero la etiqueta de muestra quedó quitada; la vuelve a prender a
    # mano en Linear (simulado) y la siembra NO la re-anota: la marca de
    # config ya está puesta.
    linear_leads._muestra_label(
        lead["id"], linear_leads.LABEL_RESPONDER_A_MANO, poner=True)
    assert responder_a_mano.sembrar(linear_leads.listar()) == []
    assert _fila("LEAD-90") is None


def test_con_filas_previas_no_se_siembra_aunque_falte_la_marca():
    """Una base restaurada con filas pero sin la marca: no se pisa nada —
    solo se deja la marca puesta para no volver a mirar."""
    responder_a_mano.anotar("LEAD-91")
    _envejecer("LEAD-91", 100)
    lead = linear_leads.uno("LEAD-90")
    linear_leads._muestra_label(
        lead["id"], linear_leads.LABEL_RESPONDER_A_MANO, poner=True)

    assert responder_a_mano.sembrar(linear_leads.listar()) == []
    assert _fila("LEAD-90") is None
    assert responder_a_mano._horas_desde(_fila("LEAD-91")["puesta_en"]) > 99
