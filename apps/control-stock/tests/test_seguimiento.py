"""El Seguimiento con fecha (2/10/2026, spec C).

Corren en modo muestra (linear_leads + calendario en memoria), así que el
flujo completo —programar con fecha, la tarjeta que sube ese día, marcar
«Hecha»— se prueba sin tocar nada real. Lo que se cuida: que la actividad
nazca amarrada al lead y a su Resp:, que el tipo que falta en Linear NO
cree la cita (candado de la casa) pero tampoco rompa, que «Hecha» quite
la señal sin mover el estado (por campo, nunca por título), y que las
otras señales sigan siendo las banderas simples de siempre.

LEAD-91 (Tamara, Por agendar, Resp: Ruben) es el de cabecera acá.
"""

from datetime import timedelta

import pytest

from app import (agenda, calendario, control, datos, linear_leads,
                 seguimiento)
from app.datos import _db


@pytest.fixture(autouse=True)
def muestra_limpia(monkeypatch, db_limpia):
    monkeypatch.delenv("LINEAR_API_KEY", raising=False)
    monkeypatch.delenv("LINEAR_PROJECT_CALENDARIO_ID", raising=False)
    monkeypatch.delenv("CALENDARIO_ESCRITURA", raising=False)
    monkeypatch.delenv("ODOO_URL", raising=False)
    monkeypatch.delenv("SINCRO_URL", raising=False)
    monkeypatch.delenv("SINCRO_SECRET", raising=False)
    linear_leads.reiniciar_muestra()
    calendario.reiniciar_muestra()
    calendario.invalidar_cache()
    agenda.refrescar()
    control.iniciar_tablas()
    seguimiento.iniciar_tablas()


def _fila(ref):
    with _db() as con:
        return con.execute(
            "SELECT * FROM seguimiento_lead WHERE lead_ref=?", (ref,)).fetchone()


def _actividades_de(ref):
    desde = calendario.hoy().isoformat()
    hasta = (calendario.hoy() + timedelta(days=60)).isoformat()
    return [a for a in calendario.listar(desde, hasta)
            if a.get("lead") == ref and a["tipo"] == "seguimiento"]


def _manana():
    return (calendario.hoy() + timedelta(days=1)).isoformat()


# ---------------------------------------------------------------------------
# Programar: señal + actividad + ancla
# ---------------------------------------------------------------------------

def test_programar_crea_la_actividad_con_el_resp_y_ancla_la_fila():
    fecha = _manana()
    aviso, error = control.programar_seguimiento("LEAD-91", fecha, autor="Mary")
    assert error == ""
    assert "Tamara" in aviso and "Ruben" in aviso

    vivas = [a for a in _actividades_de("LEAD-91") if a["estado"] != "cancel"]
    assert len(vivas) == 1
    cita = vivas[0]
    assert cita["titulo"] == "Seguimiento — Tamara"
    assert cita["fecha"] == fecha
    assert cita["resp_lead"] == "Ruben"  # la marca, nunca assignee

    lead = linear_leads.uno("LEAD-91")
    assert "Seguimiento" in lead["etiquetas"]
    fila = _fila("LEAD-91")
    assert fila["actividad_id"] == cita["id"]
    assert fila["fecha"] == fecha


def test_sin_el_tipo_en_linear_no_hay_cita_pero_la_senal_queda(monkeypatch):
    monkeypatch.setattr(
        seguimiento, "tipo_en_linear",
        lambda: ("", "el tipo de actividad «Seguimiento» todavía no existe "
                     "en Linear (grupo Tipo de actividad del calendario): "
                     "la cita no se creó."))
    aviso, error = control.programar_seguimiento("LEAD-91", _manana())
    assert error == ""
    assert "todavía no existe en Linear" in aviso
    assert _actividades_de("LEAD-91") == []
    assert _fila("LEAD-91") is None
    # El comportamiento de hoy sobrevive: la bandera sí se pone.
    assert "Seguimiento" in linear_leads.uno("LEAD-91")["etiquetas"]


def test_el_nombre_del_tipo_vive_en_config():
    assert seguimiento.tipo_configurado() == "Seguimiento"
    datos.fijar_config(seguimiento.CLAVE_TIPO, "Seguimiento cliente")
    assert seguimiento.tipo_configurado() == "Seguimiento cliente"


def test_una_fecha_pasada_no_programa_nada():
    ayer = (calendario.hoy() - timedelta(days=1)).isoformat()
    aviso, error = control.programar_seguimiento("LEAD-91", ayer)
    assert "ya pasó" in error
    assert _fila("LEAD-91") is None


def test_reprogramar_pisa_la_cita_vieja():
    control.programar_seguimiento("LEAD-91", _manana())
    primera = _fila("LEAD-91")["actividad_id"]
    nueva_fecha = (calendario.hoy() + timedelta(days=7)).isoformat()

    control.programar_seguimiento("LEAD-91", nueva_fecha)

    fila = _fila("LEAD-91")
    assert fila["fecha"] == nueva_fecha
    assert fila["actividad_id"] != primera
    vieja = next(a for a in _actividades_de("LEAD-91") if a["id"] == primera)
    assert vieja["estado"] == "cancel"


# ---------------------------------------------------------------------------
# La tarjeta: muestra la fecha y sube el día que toca
# ---------------------------------------------------------------------------

def _columna(clave):
    return next(c for c in control.tablero_por_estado()
                if c["clave"] == clave)


def test_la_tarjeta_muestra_la_fecha_programada():
    fecha = _manana()
    control.programar_seguimiento("LEAD-91", fecha)
    tarjeta = next(l for l in _columna("POR_AGENDAR")["leads"]
                   if l["ref"] == "LEAD-91")
    assert tarjeta["seguimiento_fecha"] == fecha
    assert tarjeta["seguimiento_texto"] == calendario.dmy(fecha)
    assert tarjeta["seguimiento_hoy"] is False


def test_el_dia_del_seguimiento_la_tarjeta_sube_al_inicio():
    # LEAD-90 comparte la columna Por agendar con LEAD-91 y por defecto va
    # DESPUÉS (el orden de siempre)…
    hoy = calendario.hoy().isoformat()
    antes = [l["ref"] for l in _columna("POR_AGENDAR")["leads"]]
    assert antes.index("LEAD-90") > antes.index("LEAD-91")

    # …hasta que su seguimiento toca HOY: sube al inicio de la columna.
    control.programar_seguimiento("LEAD-90", hoy)

    despues = _columna("POR_AGENDAR")["leads"]
    assert despues[0]["ref"] == "LEAD-90"
    assert despues[0]["seguimiento_texto"] == "HOY"
    assert despues[0]["seguimiento_hoy"] is True


# ---------------------------------------------------------------------------
# «Hecha»: la señal se quita sola y el estado NO se mueve
# ---------------------------------------------------------------------------

def test_hecha_quita_la_senal_borra_la_fila_y_no_mueve_el_estado():
    control.programar_seguimiento("LEAD-91", _manana())
    cita = next(a for a in _actividades_de("LEAD-91")
                if a["estado"] != "cancel")
    assert linear_leads.uno("LEAD-91")["estado"] == "POR_AGENDAR"

    extra = agenda.al_marcar_hecha(cita, autor="Ruben")

    assert "se quitó" in extra and "se queda donde está" in extra
    lead = linear_leads.uno("LEAD-91")
    assert "Seguimiento" not in lead["etiquetas"]
    assert lead["estado"] == "POR_AGENDAR"  # un seguimiento no entrega
    assert _fila("LEAD-91") is None


def test_un_seguimiento_agendado_a_mano_no_toca_nada():
    """Una actividad de tipo seguimiento SIN fila en la tabla (creada a
    mano en el calendario) no matchea por campo y no mueve nada — nunca
    se decide por el título."""
    creada = calendario.crear(
        tipo="seguimiento", cliente="Tamara", fecha=_manana(),
        lead="LEAD-91", resp_lead="Ruben")
    lead = linear_leads.uno("LEAD-91")
    linear_leads.poner_etiqueta_suelta(lead["id"], "Seguimiento", True)

    actividad = next(a for a in _actividades_de("LEAD-91")
                     if a["id"] == creada["id"])
    assert agenda.al_marcar_hecha(actividad, autor="Ruben") == ""
    assert "Seguimiento" in linear_leads.uno("LEAD-91")["etiquetas"]


def test_la_entrega_de_siempre_sigue_entregando():
    """El gancho nuevo va ANTES del camino que entrega: una Entrega normal
    tiene que seguir moviendo el lead a Entregado como siempre."""
    agenda.agendar("LEAD-91", "entrega", _manana(), resp="Ruben")
    actividad = next(a for a in calendario.listar(
        calendario.hoy().isoformat(),
        (calendario.hoy() + timedelta(days=10)).isoformat())
        if a.get("lead") == "LEAD-91" and a["tipo"] == "entrega")

    extra = agenda.al_marcar_hecha(actividad, autor="Ruben")
    assert "Entregado" in extra
    assert linear_leads.uno("LEAD-91")["estado"] == "ENTREGADO"


# ---------------------------------------------------------------------------
# Apagar a mano y las otras señales
# ---------------------------------------------------------------------------

def test_apagar_la_senal_a_mano_cancela_la_cita_y_borra_la_fila():
    control.programar_seguimiento("LEAD-91", _manana())
    cita_id = _fila("LEAD-91")["actividad_id"]

    aviso, error = control.alternar_senal("LEAD-91", "Seguimiento", False)
    assert error == ""
    assert "canceló" in aviso
    assert _fila("LEAD-91") is None
    cita = next(a for a in _actividades_de("LEAD-91") if a["id"] == cita_id)
    assert cita["estado"] == "cancel"
    assert "Seguimiento" not in linear_leads.uno("LEAD-91")["etiquetas"]


def test_importante_sigue_siendo_una_bandera_simple():
    aviso, error = control.alternar_senal("LEAD-91", "Importante", True)
    assert error == ""
    assert "Importante" in linear_leads.uno("LEAD-91")["etiquetas"]
    assert _fila("LEAD-91") is None  # ninguna tabla, ninguna cita
    assert _actividades_de("LEAD-91") == []

    control.alternar_senal("LEAD-91", "Importante", False)
    assert "Importante" not in linear_leads.uno("LEAD-91")["etiquetas"]


# ---------------------------------------------------------------------------
# La ficha y el selector
# ---------------------------------------------------------------------------

def test_la_ficha_marca_que_seguimiento_pide_fecha():
    abierta = control.ficha("LEAD-91")
    seg = next(s for s in abierta["senales"] if s["nombre"] == "Seguimiento")
    assert seg["pide_fecha"] is True  # apagada: el botón abre el selector

    control.programar_seguimiento("LEAD-91", _manana())
    abierta = control.ficha("LEAD-91")
    seg = next(s for s in abierta["senales"] if s["nombre"] == "Seguimiento")
    assert seg["pide_fecha"] is False  # prendida: el botón apaga
    assert abierta["seguimiento_texto"] == calendario.dmy(_manana())


def test_las_opciones_del_selector_salen_calculadas_del_servidor():
    opciones = control.opciones_seguimiento(control.ficha("LEAD-91"))
    assert [o["texto"] for o in opciones["opciones"]] == [
        "Mañana", "En 3 días", "En 1 semana"]
    assert opciones["opciones"][0]["fecha"] == _manana()
    assert opciones["tipo_aviso"] == ""  # en muestra el tipo existe
