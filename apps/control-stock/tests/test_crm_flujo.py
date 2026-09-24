"""Lo que queda de crm_flujo: la trastienda de la pestaña Retail.

La pestaña CRM murió en la Fase 5 (24/09/2026) y con ella su pantalla, así
que las pruebas que la abrían se fueron. Este módulo sigue vivo porque
`retail.py` lo usa, y estas pruebas cuidan lo que se puede romper desde
ahí sin que nadie se dé cuenta: que una corrección de tipo NO se escriba a
medias, y que un fallo del puente no se dé por hecho.

El vocabulario de aquí es el VIEJO (Contactado, En conversación). Lo nuevo
vive en `linear_leads.py`; nada nuevo debería entrar por aquí.
"""

import pytest

from app import crm_flujo


@pytest.fixture(autouse=True)
def muestra_limpia(monkeypatch):
    monkeypatch.delenv("TWENTY_API_KEY", raising=False)
    monkeypatch.delenv("LINEAR_API_KEY", raising=False)
    crm_flujo.refrescar()
    for fila in crm_flujo._MUESTRA:
        fila["tipoInteres"] = ["MAYORISTA" if fila["id"] == "L3"
                              else "PLANTAS_RETAIL"]


def test_un_tipo_inventado_no_toca_nada():
    assert crm_flujo.cambiar_interes("L1", "PLANTAS_DE_MENTIRA") is None
    assert crm_flujo.detalle("L1")["interes"] == "PLANTAS_RETAIL"


def test_con_twenty_de_verdad_el_cambio_va_por_el_puente(monkeypatch):
    """Con Twenty configurado NO se escribe a medias desde aquí: la
    corrección viaja a /api/crm/lead-interes, que mueve Twenty, la label de
    Linear y la etiqueta de Odoo de una sola vez."""
    llamadas = []
    monkeypatch.setattr(crm_flujo.crm_twenty, "twenty_configurado", lambda: True)
    monkeypatch.setattr(
        crm_flujo.crm_leads, "cambiar_tipo_de_interes",
        lambda lead, interes: (llamadas.append((lead, interes))
                               or {"ok": True, "enLinear": True, "enOdoo": True}))
    monkeypatch.setattr(crm_flujo, "refrescar", lambda: None)
    monkeypatch.setattr(crm_flujo, "_refrescar_retail", lambda: None)
    assert crm_flujo.cambiar_interes("L9", "MANTENIMIENTO") == {
        "en_linear": True, "en_odoo": True}
    assert llamadas == [("L9", "MANTENIMIENTO")]


def test_si_el_puente_falla_el_cambio_no_se_da_por_hecho(monkeypatch):
    monkeypatch.setattr(crm_flujo.crm_twenty, "twenty_configurado", lambda: True)
    monkeypatch.setattr(crm_flujo.crm_leads, "cambiar_tipo_de_interes",
                        lambda lead, interes: None)
    assert crm_flujo.cambiar_interes("L9", "MANTENIMIENTO") is None


def test_la_pestana_crm_ya_no_existe(cliente):
    # La pantalla y sus cuatro POST se fueron en la Fase 5; la piel del
    # calendario dentro de Twenty (/crm/calendario) NO se tocó.
    assert cliente.get("/crm", follow_redirects=False).status_code == 404
    assert cliente.post("/crm/interes", data={}).status_code == 404
    assert cliente.get("/crm/calendario", follow_redirects=False).status_code != 404
