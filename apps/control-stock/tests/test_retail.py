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


# ---------------------------------------------------------------------------
# El amarre con Vender (pedido de Abraham, 22/09/2026): las cotizaciones
# vinculadas salen en la ficha y un lead ya cotizado cae solo en "Cotizado".
# ---------------------------------------------------------------------------

def _cotizacion_suelta(cliente_nombre="Juan Carlos Lopez", total=340.0,
                       celular=""):
    from app import cotizaciones
    return cotizaciones._guardar_local(
        {"id": "genesis", "nombre": "Génesis"}, "instalacion",
        cliente_nombre, celular, 9001, "S00083", total)


def test_vincular_una_cotizacion_mueve_el_lead_a_cotizado(cliente):
    registro = _cotizacion_suelta()
    r = cliente.post("/retail/vincular",
                     data={"ref": "LEAD-48", "clase": "servicio",
                           "n": registro["n"]},
                     follow_redirects=False)
    assert r.status_code == 303 and "abrir=LEAD-48" in r.headers["location"]
    _columnas, por_ref = retail.tablero()
    assert por_ref["LEAD-48"]["etapa"] == "facturar"
    # La ficha lista la cotización con su PDF.
    cuerpo = cliente.get("/retail?abrir=LEAD-48").text
    assert "S00083 · Juan Carlos Lopez" in cuerpo
    assert f"/venta/servicio/{registro['n']}/propuesta.pdf" in cuerpo


def test_desvincular_regresa_el_lead_a_por_cotizar(cliente):
    registro = _cotizacion_suelta()
    cliente.post("/retail/vincular",
                 data={"ref": "LEAD-48", "clase": "servicio", "n": registro["n"]})
    cliente.post("/retail/desvincular",
                 data={"ref": "LEAD-48", "clase": "servicio", "n": registro["n"]})
    _columnas, por_ref = retail.tablero()
    assert por_ref["LEAD-48"]["etapa"] == "cotizar"


def test_la_ficha_ofrece_solo_las_cotizaciones_del_cliente(cliente):
    """Corrección de Abraham (22/09/2026): la ficha solo ofrece vincular
    lo que pertenece a SU cliente (mismo celular o mismo nombre), nunca
    la lista general de cotizaciones sueltas."""
    # LEAD-48 en la muestra es Laura Porcell, cel 6512-8890.
    suya = _cotizacion_suelta("Laura Porcell", 340.0)
    por_celular = _cotizacion_suelta("Cliente Local", 25.0, celular="6512-8890")
    ajena = _cotizacion_suelta("Tamara", 787.0)
    cuerpo = cliente.get("/retail?abrir=LEAD-48").text
    assert "vincular" in cuerpo.lower()
    assert f'value="{suya["n"]}"' in cuerpo
    assert f'value="{por_celular["n"]}"' in cuerpo
    assert f'value="{ajena["n"]}"' not in cuerpo


def test_un_lead_anonimo_sin_celular_no_ofrece_candidatas():
    _cotizacion_suelta("Tamara", 787.0)
    assert retail.candidatas_para(
        {"ref": "LEAD-21", "nombre": "Plantas retail", "cel": ""}) == []


def test_el_celular_sale_de_la_tarjeta_del_issue():
    assert retail._cel_de(
        "💬 [Responder por WhatsApp](https://wa.me/50769999901)") == "6999-9901"
    assert retail._cel_de(
        "**Cliente**\nAna · +507 6999-9901") == "6999-9901"
    assert retail._cel_de("sin telefono aqui") == ""


def test_el_drag_hacia_adelante_le_gana_a_la_etapa_derivada(cliente):
    registro = _cotizacion_suelta()
    cliente.post("/retail/vincular",
                 data={"ref": "LEAD-48", "clase": "servicio", "n": registro["n"]})
    cliente.post("/retail/mover", data={"ref": "LEAD-48", "etapa": "entregado"})
    _columnas, por_ref = retail.tablero()
    assert por_ref["LEAD-48"]["etapa"] == "entregado"


def test_cotizar_en_vender_deja_el_lead_pendiente(cliente):
    from app import ventas
    r = cliente.get("/venta?lead=LEAD-46&cliente=Jasmin", follow_redirects=False)
    assert r.status_code == 303
    assert ventas.lead_pendiente("genesis") == {"ref": "LEAD-46", "nombre": "Jasmin"}
    # La pantalla avisa el amarre y se puede quitar.
    cuerpo = cliente.get("/venta").text
    assert "LEAD-46" in cuerpo
    cliente.post("/venta/lead/quitar")
    assert ventas.lead_pendiente("genesis") is None


def test_la_cotizacion_creada_con_lead_pendiente_nace_vinculada(cliente):
    from app import ventas
    ventas.poner_lead_pendiente("genesis", "LEAD-46", "Jasmin")
    registro = _cotizacion_suelta("Jasmin", 100.0)
    assert registro["lead_issue"] == "LEAD-46"
    assert ventas.lead_pendiente("genesis") is None  # se consumió
    _columnas, por_ref = retail.tablero()
    assert por_ref["LEAD-46"]["etapa"] == "facturar"
