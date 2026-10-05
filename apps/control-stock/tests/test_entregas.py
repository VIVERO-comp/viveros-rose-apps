"""Pruebas de la entrega como obligación nombrada (item 7 de Jay).

Dirección + asignado puestos durante la venta, editables hasta cerrar,
con historial de qué cambió, quién y cuándo. «Marcar entregada» exige
asignado (con aviso, no mudo), es del system manager, valida la salida
en Odoo AHÍ —si Odoo dice que no, nada queda marcado— y fija la fecha
REAL de entrega.
"""

import pytest

from app import datos, datos_roles, entregas, seguridad, venta_estado, ventas


@pytest.fixture
def manager(db_limpia):
    seguridad.crear_empleada("sam", "Sam", "clave")
    seguridad.crear_empleada("genesis", "Génesis", "clave")
    rol = next(r for r in datos_roles.listar_roles()
               if r["deber"] == "system_manager")
    datos_roles.poner_persona(rol["n"], "sam", "prueba")
    return "sam"


def _venta_local(orden_id=None):
    """Una fila mínima en ventas_locales, como las que crea Vender."""
    with datos._db() as con:
        cursor = con.execute(
            "INSERT INTO ventas_locales (creado_en, empleada, cliente,"
            " orden_id, orden, total, estado)"
            " VALUES ('2026-10-05T10:00:00', 'Génesis', 'Ana', ?, 'S77',"
            " 18.0, 'pagado')", (orden_id,))
        return cursor.lastrowid


def test_historial_registra_cada_cambio(manager):
    n = _venta_local()
    assert entregas.guardar("venta", n, "Calle 50", "Sam", "Génesis") is None
    assert entregas.guardar("venta", n, "Calle 50", "Mensajero Juan",
                            "Sam") is None
    cambios = entregas.historial_de("venta", n)
    # Dos del primer guardado (dirección y asignado) + uno del segundo.
    assert [(c["campo"], c["antes"], c["despues"]) for c in cambios] == [
        ("direccion", "", "Calle 50"),
        ("asignado", "", "Sam"),
        ("asignado", "Sam", "Mensajero Juan"),
    ]
    assert cambios[2]["por"] == "Sam" and cambios[2]["en"]
    fila = entregas.obligacion_de("venta", n)
    assert fila["direccion"] == "Calle 50"
    assert fila["asignado"] == "Mensajero Juan"


def test_guardar_sin_cambios_no_ensucia_el_historial(manager):
    n = _venta_local()
    entregas.guardar("venta", n, "Calle 50", "Sam", "Génesis")
    entregas.guardar("venta", n, "Calle 50", "Sam", "Génesis")
    assert len(entregas.historial_de("venta", n)) == 2


def test_editable_hasta_cerrar_y_despues_no(manager):
    n = _venta_local()
    venta_estado.abrir("venta", n, "plant retail", "Génesis")
    entregas.guardar("venta", n, "Calle 50", "Sam", "Génesis")
    venta_estado.registrar_pago("venta", n, "Génesis")
    error, fila = entregas.marcar_entregada("venta", n, manager, "Sam")
    assert error is None and fila["estado"] == 3
    # Cerrada: la obligación es historia.
    assert entregas.guardar("venta", n, "Otra calle", "Otro",
                            "Génesis") == "cerrada"
    assert entregas.obligacion_de("venta", n)["direccion"] == "Calle 50"


def test_marcar_entregada_exige_asignado_con_aviso(manager):
    n = _venta_local()
    error, fila = entregas.marcar_entregada("venta", n, manager, "Sam")
    assert error == "falta_asignado"
    assert fila["entrega_marcada"] == 0  # nada quedó marcado


def test_marcar_entregada_es_del_system_manager(manager):
    n = _venta_local()
    entregas.guardar("venta", n, "", "Sam", "Génesis")
    error, fila = entregas.marcar_entregada("venta", n, "genesis", "Génesis")
    assert error == "solo_system_manager"
    assert fila["entrega_marcada"] == 0


def test_marcar_entregada_fija_la_fecha_real(manager):
    n = _venta_local()
    venta_estado.abrir("venta", n, "plant retail", "Génesis")
    entregas.guardar("venta", n, "Calle 50", "Sam", "Génesis")
    error, fila = entregas.marcar_entregada("venta", n, manager, "Sam",
                                            fecha="2026-10-09")
    assert error is None
    assert fila["entrega_marcada"] == 1
    assert fila["fecha_entrega"] == "2026-10-09"
    # Y el pago sigue intacto: la entrega nunca escribe plata.
    assert fila["pago_confirmado"] == 0 and fila["estado"] == 1


def test_si_odoo_rechaza_la_salida_nada_queda_marcado(manager, monkeypatch):
    n = _venta_local(orden_id=55)
    entregas.guardar("venta", n, "", "Sam", "Génesis")
    monkeypatch.setattr(ventas, "configurado", lambda: True)

    def revienta(orden_id):
        raise RuntimeError("salida bloqueada")

    monkeypatch.setattr(ventas, "validar_salida", revienta)
    error, resultado = entregas.marcar_entregada("venta", n, manager, "Sam")
    assert error == "odoo"
    assert "salida bloqueada" in resultado["detalle"]
    assert venta_estado.estado_de("venta", n)["entrega_marcada"] == 0


# ---------------------------------------------------------------------------
# La ficha por HTTP (humo): existe, pinta los candados y guarda.
# ---------------------------------------------------------------------------

def test_ficha_estado_entrega_por_http(cliente):
    n = _venta_local()
    pagina = cliente.get(f"/venta/estado/venta/{n}")
    assert pagina.status_code == 200
    assert "Estado del trato" in pagina.text
    assert "La entrega: dirección y asignado" in pagina.text
    # Génesis no es system manager: el botón de marcar no está, y se
    # dice quién la marca.
    assert "MARCAR ENTREGADA" not in pagina.text
    r = cliente.post(f"/venta/estado/venta/{n}/entrega",
                     data={"direccion": "Calle 50",
                           "asignado_sel": "",
                           "asignado_libre": "Mensajero Juan"},
                     follow_redirects=False)
    assert r.status_code == 303
    assert entregas.obligacion_de("venta", n)["asignado"] == "Mensajero Juan"


def test_ficha_de_origen_invalido_redirige(cliente):
    r = cliente.get("/venta/estado/otracosa/1", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/venta"
