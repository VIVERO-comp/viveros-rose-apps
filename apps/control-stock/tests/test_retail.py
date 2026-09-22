"""La pestaña Retail en modo muestra (sin Linear)."""

import pytest

from app import calendario, retail


@pytest.fixture(autouse=True)
def muestra_limpia(monkeypatch):
    monkeypatch.delenv("LINEAR_API_KEY", raising=False)
    monkeypatch.delenv("LINEAR_PROJECT_CALENDARIO_ID", raising=False)
    calendario.reiniciar_muestra()
    calendario.invalidar_cache()


def test_el_tablero_pinta_las_cuatro_columnas(cliente):
    cuerpo = cliente.get("/retail").text
    assert "Por cotizar" in cuerpo
    assert "Cotizado · por facturar" in cuerpo
    assert "Facturado · por entregar" in cuerpo
    assert "Entregado" in cuerpo
    assert "Laura Porcell" in cuerpo  # lead de muestra
    assert "Diego Armando" in cuerpo  # mayorista


def test_mover_una_tarjeta_persiste(cliente):
    r = cliente.post("/retail/mover", data={"ref": "LEAD-48", "etapa": "facturar"},
                     follow_redirects=False)
    assert r.status_code == 303
    _columnas, por_ref = retail.tablero()
    assert por_ref["LEAD-48"]["etapa"] == "facturar"


def test_poner_fecha_crea_la_entrega_en_el_calendario(cliente):
    dia = (calendario.hoy()).isoformat()
    cliente.post("/retail/mover", data={"ref": "LEAD-44", "etapa": "entregar"})
    r = cliente.post("/retail/fecha",
                     data={"ref": "LEAD-44", "nombre": "Soledad", "entrega": dia},
                     follow_redirects=False)
    assert r.status_code == 303 and "aviso=" in r.headers["location"]
    # Quedo la fecha y la actividad Entrega existe en el calendario.
    _columnas, por_ref = retail.tablero()
    assert por_ref["LEAD-44"]["entrega"] == dia
    entregas = [a for a in calendario.listar(dia, dia)
                if a["tipo"] == "entrega" and a["cliente"] == "Soledad"]
    assert len(entregas) == 1
    # Cambiar la fecha MUEVE la misma actividad, no crea otra.
    manana = (calendario.hoy().replace(day=28)).isoformat()
    cliente.post("/retail/fecha",
                 data={"ref": "LEAD-44", "nombre": "Soledad", "entrega": manana})
    assert not [a for a in calendario.listar(dia, dia)
                if a["tipo"] == "entrega" and a["cliente"] == "Soledad"]
    assert [a for a in calendario.listar(manana, manana)
            if a["tipo"] == "entrega" and a["cliente"] == "Soledad"]


def test_el_bloque_por_entregar_sale_en_el_calendario(cliente):
    cliente.post("/retail/mover", data={"ref": "LEAD-43", "etapa": "entregar"})
    cuerpo = cliente.get("/calendario").text
    assert "Por entregar" in cuerpo
    assert "Diana Caballero" in cuerpo
    assert "sin fecha · ponla" in cuerpo


def test_los_de_servicio_no_entran_a_retail():
    _columnas, por_ref = retail.tablero()
    assert "LEAD-41" not in por_ref  # Jordan W. es Eventos · Alquiler
