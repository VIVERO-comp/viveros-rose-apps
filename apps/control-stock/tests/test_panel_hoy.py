"""El panel «Hoy» de /calendario — esqueleto del BLOQUE 20 (Calendario).

Lo que se prueba, por regla:

- El panel aparece con las actividades del día SELECCIONADO (los mismos
  datos de `movil`): al pedir otro día, el panel muestra ese día — el
  comportamiento existente del calendario no se rompe.
- Las secciones «Por responder» y «Seguimientos de hoy» van con su
  estado hueco honesto (sin Twenty, lo dice).
- El aviso de peticiones es 0 fijo con su verdad y enlaza a /mi-crm.
- Cero POST nuevos: el panel es pintura sobre datos que ya estaban.
"""

import pytest

from app import calendario, linear_leads, mi_crm


@pytest.fixture(autouse=True)
def modo_muestra(monkeypatch, db_limpia):
    """Sin Linear ni Twenty, como el 8095."""
    monkeypatch.delenv("LINEAR_API_KEY", raising=False)
    monkeypatch.delenv("TWENTY_API_KEY", raising=False)
    linear_leads.reiniciar_muestra()


def test_el_panel_aparece_con_sus_tres_secciones(cliente):
    r = cliente.get("/calendario")
    assert r.status_code == 200
    assert "panel-hoy" in r.text
    assert "Por responder" in r.text
    assert "Seguimientos de hoy" in r.text


def test_el_panel_sigue_al_dia_seleccionado(cliente):
    # Un día cualquiera: el título del panel es el del día pedido (los
    # mismos datos de `movil`, que ya siguen al ?dia=).
    texto = cliente.get("/calendario", params={"dia": "2026-01-15"}).text
    assert "15/01/2026" in texto


def test_las_cajas_dicen_su_hueco_sin_twenty(cliente):
    texto = cliente.get("/calendario").text
    assert mi_crm.AVISO_TWENTY_PRUEBAS in texto
    assert mi_crm.AVISO_SEGUIMIENTOS in texto


def test_el_aviso_de_peticiones_es_cero_fijo_y_enlaza_al_crm(cliente):
    texto = cliente.get("/calendario").text
    assert "0 peticiones por aceptar" in texto
    assert 'href="/mi-crm"' in texto
    assert "llegan después" in texto


def test_panel_hoy_no_inventa_filas():
    panel = mi_crm.panel_hoy()
    assert panel["peticiones"] == 0
    assert panel["por_responder"]["filas"] == []
    assert panel["seguimientos"]["filas"] == []


def test_el_calendario_no_gano_rutas_post_nuevas():
    """El panel es pintura: las rutas POST de /calendario son las que ya
    existían (crear, mover, estado, agendar, reprogramar, detalle, nota).
    Si esta lista crece, alguien metió escritura por el esqueleto."""
    from app.main import app
    posts = sorted(
        str(r.path) for r in app.routes
        if str(getattr(r, "path", "")).startswith("/calendario")
        and "POST" in (getattr(r, "methods", None) or set()))
    assert posts == [
        "/calendario/actividad",
        "/calendario/actividad/{id_actividad}/detalle",
        "/calendario/actividad/{id_actividad}/estado",
        "/calendario/actividad/{id_actividad}/mover",
        "/calendario/actividad/{id_actividad}/nota",
        "/calendario/actividad/{id_actividad}/reprogramar",
        "/calendario/agendar",
        "/calendario/google/desconectar",
        "/calendario/suscripcion/regenerar",
    ]


def test_hoy_panama_sigue_siendo_la_fuente_del_dia(cliente):
    """El panel por defecto pinta el día de hoy del calendario (hora de
    Panamá), no el reloj de otra máquina."""
    hoy = calendario.hoy().isoformat()
    texto = cliente.get("/calendario").text
    assert calendario.dmy(hoy) in texto
