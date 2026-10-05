"""Pruebas de las dos cifras (items 5-7 de Jay, para el item 10).

Lo que se clava: pending revenue es lo confirmado SIN entregar (el
depósito de un evento vive ahí), delivered revenue va POR FECHA DE
ENTREGA —nunca la del pago ni la de la factura—, los montos sin dato se
cuentan aparte (jamás un cero callado) y todo sale rotulado provisional.
"""

import pytest

from app import cifras, datos_roles, seguridad, venta_estado


@pytest.fixture
def manager(db_limpia):
    seguridad.crear_empleada("sam", "Sam", "clave")
    rol = next(r for r in datos_roles.listar_roles()
               if r["deber"] == "system_manager")
    datos_roles.poner_persona(rol["n"], "sam", "prueba")
    return "sam"


def test_pending_es_lo_confirmado_sin_entregar(manager):
    # Pagada y sin entregar: cuenta.
    venta_estado.abrir("venta", 1, "plant retail", "g")
    venta_estado.registrar_pago("venta", 1, "g", monto=100.0)
    # Depósito de evento sin entregar: cuenta (para eso existe la cifra).
    venta_estado.abrir("servicio", 2, "rental event", "g")
    venta_estado.registrar_pago("servicio", 2, "g", monto=750.0)
    # Pagada Y entregada: ya no es pending.
    venta_estado.abrir("venta", 3, "plant retail", "g")
    venta_estado.registrar_pago("venta", 3, "g", monto=40.0)
    venta_estado.marcar_entregada("venta", 3, manager, "Sam", monto=40.0)
    # Sin pago: no entra.
    venta_estado.abrir("venta", 4, "plant retail", "g")
    cifra = cifras.pending_revenue()
    assert cifra["monto"] == 850.0 and cifra["n"] == 2
    assert cifra["sin_monto"] == 0
    assert cifra["rotulo"].startswith("provisional")


def test_delivered_usa_la_fecha_de_entrega_no_la_del_pago(manager):
    """Pagada en septiembre, entregada el 9/10: cuenta en la semana de
    OCTUBRE de la entrega, y no aparece en la del pago."""
    venta_estado.abrir("venta", 5, "plant retail", "g")
    venta_estado.registrar_pago("venta", 5, "g", monto=120.0)
    venta_estado.marcar_entregada("venta", 5, manager, "Sam",
                                  fecha="2026-10-09", monto=120.0)
    octubre = cifras.delivered_revenue("2026-10-06", "2026-10-12")
    assert octubre["monto"] == 120.0 and octubre["n"] == 1
    septiembre = cifras.delivered_revenue("2026-09-01", "2026-09-30")
    assert septiembre["monto"] == 0.0 and septiembre["n"] == 0


def test_delivered_respeta_los_bordes_del_rango(manager):
    for n, fecha in ((6, "2026-10-01"), (7, "2026-10-07"), (8, "2026-10-08")):
        venta_estado.abrir("venta", n, "plant retail", "g")
        venta_estado.marcar_entregada("venta", n, manager, "Sam",
                                      fecha=fecha, monto=10.0)
    cifra = cifras.delivered_revenue("2026-10-01", "2026-10-07")
    assert cifra["n"] == 2 and cifra["monto"] == 20.0


def test_la_fecha_corregida_mueve_la_cifra(manager):
    """La fuente de la fecha es «Marcar entregada», corregible por el
    system manager: la corrección mueve la venta a la semana real."""
    venta_estado.abrir("venta", 9, "plant retail", "g")
    venta_estado.marcar_entregada("venta", 9, manager, "Sam",
                                  fecha="2026-10-09", monto=55.0)
    venta_estado.corregir_fecha_entrega("venta", 9, "2026-10-02", manager)
    assert cifras.delivered_revenue("2026-10-01", "2026-10-03")["monto"] == 55.0
    assert cifras.delivered_revenue("2026-10-06", "2026-10-12")["monto"] == 0.0


def test_el_monto_desconocido_se_dice_nunca_cero_callado(manager):
    venta_estado.abrir("venta", 10, "plant retail", "g")
    venta_estado.registrar_pago("venta", 10, "g")  # sin monto
    cifra = cifras.pending_revenue()
    assert cifra["n"] == 1 and cifra["monto"] == 0.0
    assert cifra["sin_monto"] == 1  # el hueco se dice
    venta_estado.marcar_entregada("venta", 10, manager, "Sam",
                                  fecha="2026-10-05")
    entregadas = cifras.delivered_revenue("2026-10-05", "2026-10-05")
    assert entregadas["n"] == 1 and entregadas["sin_monto"] == 1


def test_acepta_dates_ademas_de_texto(manager):
    from datetime import date
    venta_estado.abrir("venta", 11, "plant retail", "g")
    venta_estado.marcar_entregada("venta", 11, manager, "Sam",
                                  fecha="2026-10-05", monto=9.0)
    cifra = cifras.delivered_revenue(date(2026, 10, 1), date(2026, 10, 31))
    assert cifra["monto"] == 9.0
