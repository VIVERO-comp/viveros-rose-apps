"""Pruebas del motor de los 3 estados (items 5-6-7 de Jay, 5/10/2026).

Los candados que el decision record no negocia, clavados en los DOS
sentidos: el pago nunca escribe la entrega, la entrega nunca escribe el
pago, el 3 exige los dos hechos, un depósito de evento jamás cierra, y
marcar entregada es solo del system manager (por deber, no por nombre).
"""

import pytest

from app import datos_roles, seguridad, venta_estado


@pytest.fixture
def base(db_limpia):
    venta_estado.iniciar_tablas()
    return db_limpia


@pytest.fixture
def manager(base):
    """La empleada 'sam' ocupa el deber system_manager; 'genesis' no."""
    seguridad.crear_empleada("sam", "Sam", "clave")
    seguridad.crear_empleada("genesis", "Génesis", "clave")
    rol = next(r for r in datos_roles.listar_roles()
               if r["deber"] == "system_manager")
    assert datos_roles.poner_persona(rol["n"], "sam", "prueba") is None
    return "sam"


# ---------------------------------------------------------------------------
# Los dos candados de dirección
# ---------------------------------------------------------------------------

def test_el_pago_nunca_escribe_la_entrega(manager):
    venta_estado.abrir("venta", 1, "plant retail", "genesis")
    fila = venta_estado.registrar_pago("venta", 1, "genesis", monto=18.0)
    assert fila["pago_confirmado"] == 1
    # Ni una columna de entrega se movió.
    assert fila["entrega_marcada"] == 0
    assert fila["fecha_entrega"] is None
    assert fila["entrega_por"] is None and fila["entrega_en"] is None
    assert fila["entrega_monto"] is None
    assert fila["estado"] == 2  # plantas: pago confirma, no cierra


def test_la_entrega_nunca_escribe_el_pago(manager):
    venta_estado.abrir("venta", 2, "plant retail", "genesis")
    error, fila = venta_estado.marcar_entregada(
        "venta", 2, manager, "Sam", fecha="2026-10-05", monto=18.0)
    assert error is None
    assert fila["entrega_marcada"] == 1
    assert fila["fecha_entrega"] == "2026-10-05"
    # Ni una columna de pago se movió, y el estado NO dice plata.
    assert fila["pago_confirmado"] == 0
    assert fila["pago_completo"] == 0
    assert fila["pago_monto"] is None
    assert fila["pago_por"] is None and fila["pago_en"] is None
    assert fila["estado"] == 1


# ---------------------------------------------------------------------------
# El 3 exige los dos hechos — nunca uno solo
# ---------------------------------------------------------------------------

def test_estado_3_imposible_con_un_solo_hecho(manager):
    # Solo pago: 2.
    venta_estado.abrir("venta", 3, "plant retail", "genesis")
    fila = venta_estado.registrar_pago("venta", 3, "genesis")
    assert fila["estado"] == 2
    # Solo entrega: sigue en 1.
    venta_estado.abrir("venta", 4, "plant retail", "genesis")
    _, fila = venta_estado.marcar_entregada("venta", 4, manager)
    assert fila["estado"] == 1
    # Ni a mano: el system manager tampoco puede cerrar sin los dos.
    assert venta_estado.poner_estado_manual(
        "venta", 3, 3, manager) == "faltan_hechos"
    assert venta_estado.poner_estado_manual(
        "venta", 4, 3, manager) == "faltan_hechos"
    assert venta_estado.estado_de("venta", 3)["estado"] == 2
    assert venta_estado.estado_de("venta", 4)["estado"] == 1


def test_plantas_cierran_con_pago_y_entrega(manager):
    """Conversión de plantas: pago confirmado → 2; entrega → 3."""
    venta_estado.abrir("venta", 5, "plant retail", "genesis")
    assert venta_estado.registrar_pago("venta", 5, "genesis")["estado"] == 2
    error, fila = venta_estado.marcar_entregada("venta", 5, manager)
    assert error is None and fila["estado"] == 3


def test_el_deposito_de_un_evento_nunca_cierra(manager):
    """Conversión de evento/jardín/proyecto: el depósito deja en 2 y la
    entrega con saldo pendiente NO cierra — el 3 llega solo con el saldo
    cobrado (términos satisfechos)."""
    venta_estado.abrir("servicio", 9, "rental event", "genesis")
    fila = venta_estado.registrar_pago("servicio", 9, "genesis", monto=500.0)
    assert fila["pago_completo"] == 0  # por tipo: un pago sin más es depósito
    assert fila["estado"] == 2
    error, fila = venta_estado.marcar_entregada("servicio", 9, manager)
    assert error is None
    assert fila["estado"] == 2  # obligación cumplida, saldo pendiente: NO cierra
    assert venta_estado.poner_estado_manual(
        "servicio", 9, 3, manager) == "falta_saldo"
    # Llega el saldo: ahora sí.
    fila = venta_estado.registrar_pago("servicio", 9, "genesis",
                                       monto=1000.0, completo=True)
    assert fila["estado"] == 3


def test_tipo_desconocido_cae_al_lado_conservador(manager):
    """Un tipo renombrado o raro se trata como los eventos: el pago solo
    NO deja la venta lista para cerrar."""
    venta_estado.abrir("servicio", 10, "lo que sea", "genesis")
    fila = venta_estado.registrar_pago("servicio", 10, "genesis")
    assert fila["pago_completo"] == 0 and fila["estado"] == 2


# ---------------------------------------------------------------------------
# Quién puede qué
# ---------------------------------------------------------------------------

def test_solo_el_system_manager_marca_entregada(manager):
    venta_estado.abrir("venta", 6, "plant retail", "genesis")
    error, fila = venta_estado.marcar_entregada("venta", 6, "genesis")
    assert error == "solo_system_manager"
    # Y NO se escribió nada.
    assert fila["entrega_marcada"] == 0 and fila["fecha_entrega"] is None
    # El deber se mueve de rol y la respuesta se mueve con él (por deber,
    # no por nombre).
    rol_eventos = next(r for r in datos_roles.listar_roles()
                       if r["deber"] is None)
    datos_roles.poner_persona(rol_eventos["n"], "genesis", "prueba")
    datos_roles.asignar_deber("system_manager", rol_eventos["n"])
    error, _ = venta_estado.marcar_entregada("venta", 6, "genesis")
    assert error is None
    assert not venta_estado.es_system_manager("sam")


def test_cualquiera_sube_a_2_solo_con_pago_registrado(manager):
    venta_estado.abrir("venta", 7, "plant retail", "genesis")
    assert venta_estado.poner_estado_manual(
        "venta", 7, 2, "genesis") == "falta_pago"
    venta_estado.registrar_pago("venta", 7, "genesis")
    # Ya en 2 por el hecho; volver a ponerlo es un no-op permitido.
    assert venta_estado.poner_estado_manual("venta", 7, 2, "genesis") is None


def test_bajar_un_estado_es_del_system_manager(manager):
    venta_estado.abrir("venta", 8, "plant retail", "genesis")
    venta_estado.registrar_pago("venta", 8, "genesis")
    assert venta_estado.poner_estado_manual(
        "venta", 8, 1, "genesis") == "solo_system_manager"
    assert venta_estado.poner_estado_manual("venta", 8, 1, manager) is None
    assert venta_estado.estado_de("venta", 8)["estado"] == 1


def test_dos_pagos_acumulan_el_monto(manager):
    """Depósito $100 + saldo $400 = $500, nunca $400 (fix del review,
    5/10: el COALESCE pisaba el monto y las cifras subcontaban). El
    monto del ESTADO acumula los hechos; el historial guarda CADA pago
    con SU monto —el del hecho, no el acumulado."""
    venta_estado.abrir("servicio", 31, "rental event", "g")
    venta_estado.registrar_pago("servicio", 31, "g", monto=100.0,
                                completo=False, detalle="depósito")
    venta_estado.registrar_pago("servicio", 31, "g", monto=400.0,
                                completo=True, detalle="saldo")
    fila = venta_estado.estado_de("servicio", 31)
    assert fila["pago_monto"] == 500.0
    assert fila["pago_completo"] == 1
    pagos = [c for c in venta_estado.historial_de("servicio", 31)
             if c["hecho"] == "pago"]
    assert len(pagos) == 2
    assert "depósito" in pagos[0]["detalle"] and "100.00" in pagos[0]["detalle"]
    assert "saldo" in pagos[1]["detalle"] and "400.00" in pagos[1]["detalle"]
    assert "500" not in pagos[1]["detalle"]  # jamás el acumulado
    # Un hecho sin monto conocido no toca el acumulado.
    venta_estado.registrar_pago("servicio", 31, "g", completo=True)
    assert venta_estado.estado_de("servicio", 31)["pago_monto"] == 500.0


def test_pago_completo_nunca_baja(manager):
    """Cobrado el saldo, queda cobrado: un hecho posterior sin saldo
    (completo=False) no des-completa el pago."""
    venta_estado.abrir("servicio", 32, "rental event", "g")
    venta_estado.registrar_pago("servicio", 32, "g", monto=500.0,
                                completo=True)
    venta_estado.registrar_pago("servicio", 32, "g", monto=20.0,
                                completo=False)
    fila = venta_estado.estado_de("servicio", 32)
    assert fila["pago_completo"] == 1 and fila["pago_monto"] == 520.0


def test_historial_guarda_cada_movimiento(manager):
    venta_estado.abrir("venta", 11, "plant retail", "genesis")
    venta_estado.registrar_pago("venta", 11, "genesis")
    venta_estado.marcar_entregada("venta", 11, manager, "Sam")
    hechos = [c["hecho"] for c in venta_estado.historial_de("venta", 11)]
    assert hechos == ["nace", "pago", "entrega"]
    cierre = venta_estado.historial_de("venta", 11)[-1]
    assert cierre["de"] == 2 and cierre["a"] == 3
    assert cierre["puesto_por"] == "Sam"


# ---------------------------------------------------------------------------
# Términos: el default se guarda; el override queda registrado
# ---------------------------------------------------------------------------

def test_termino_default_se_guarda_sin_override(base):
    quedado = venta_estado.guardar_termino(
        "venta", 1, "plant retail", "", "Génesis")
    assert quedado == "100% antes de proceder"
    assert venta_estado.termino_de("venta", 1)["termino"] == quedado
    assert venta_estado.overrides_de("venta", 1) == []


def test_override_queda_registrado_con_su_default(base):
    quedado = venta_estado.guardar_termino(
        "servicio", 2, "rental event", "30% ahora, 70% al armar", "Mary")
    assert quedado == "30% ahora, 70% al armar"
    registros = venta_estado.overrides_de("servicio", 2)
    assert len(registros) == 1
    assert registros[0]["texto"] == "30% ahora, 70% al armar"
    assert registros[0]["default_que_habia"] == "50% depósito, 50% al cumplir"
    assert registros[0]["por"] == "Mary"
    assert registros[0]["en"]  # cuándo, siempre


def test_escribir_el_mismo_default_no_es_override(base):
    venta_estado.guardar_termino(
        "venta", 3, "plant retail", "100% antes de proceder", "Génesis")
    assert venta_estado.overrides_de("venta", 3) == []


# ---------------------------------------------------------------------------
# Revisión del Arquitecto (5/10): epoch UTC, historial inmutable y la
# corrección de la fecha de entrega.
# ---------------------------------------------------------------------------

def test_los_instantes_van_en_epoch_utc(manager):
    """La trampa de zonas (edad_horas negativa) no se repite: los *_en de
    venta_estado y venta_estado_cambio son NÚMEROS (epoch UTC), nunca
    texto. El texto es solo de pantalla (texto_de_epoch)."""
    venta_estado.abrir("venta", 21, "plant retail", "genesis")
    venta_estado.registrar_pago("venta", 21, "genesis")
    venta_estado.marcar_entregada("venta", 21, manager, "Sam")
    fila = venta_estado.estado_de("venta", 21)
    assert isinstance(fila["pago_en"], float) and fila["pago_en"] > 1.7e9
    assert isinstance(fila["entrega_en"], float) and fila["entrega_en"] > 1.7e9
    for cambio in venta_estado.historial_de("venta", 21):
        assert isinstance(cambio["puesto_en"], float)
        assert cambio["puesto_en"] > 1.7e9
    # El texto de pantalla existe y lo decide quien muestra.
    assert venta_estado.texto_de_epoch(fila["pago_en"])
    assert venta_estado.texto_de_epoch(None) == ""
    # La fecha de entrega es la excepción a propósito: una FECHA de
    # calendario elegida por una persona, no un instante.
    assert len(fila["fecha_entrega"]) == 10


def test_el_historial_es_inmutable_solo_insert():
    """Ninguna pieza de la app escribe UPDATE ni DELETE sobre
    venta_estado_cambio: una corrección es otra fila. Se escanea el
    código fuente completo de app/ (el mismo candado de estilo que la
    lista de métodos de reconciliacion)."""
    import glob
    import os
    import re
    carpeta = os.path.join(os.path.dirname(venta_estado.__file__), "*.py")
    for ruta in glob.glob(carpeta):
        with open(ruta, encoding="utf-8") as archivo:
            fuente = archivo.read()
        assert not re.search(r"(?i)UPDATE\s+venta_estado_cambio", fuente), ruta
        assert not re.search(r"(?i)DELETE\s+FROM\s+venta_estado_cambio",
                             fuente), ruta


def test_corregir_la_fecha_de_entrega(manager):
    """La entrega real fue otro día: solo el system manager corrige la
    fecha (la que usa delivered revenue), el historial gana su fila y el
    instante del acto no se reescribe."""
    venta_estado.abrir("venta", 22, "plant retail", "genesis")
    venta_estado.registrar_pago("venta", 22, "genesis")
    venta_estado.marcar_entregada("venta", 22, manager, "Sam",
                                  fecha="2026-10-05")
    acto = venta_estado.estado_de("venta", 22)["entrega_en"]
    # Sin el deber, no.
    assert venta_estado.corregir_fecha_entrega(
        "venta", 22, "2026-10-03", "genesis") == "solo_system_manager"
    assert venta_estado.corregir_fecha_entrega(
        "venta", 22, "2026-10-03", manager, "Sam") is None
    fila = venta_estado.estado_de("venta", 22)
    assert fila["fecha_entrega"] == "2026-10-03"
    assert fila["entrega_en"] == acto  # el acto pasó cuando pasó
    ultimo = venta_estado.historial_de("venta", 22)[-1]
    assert "fecha corregida" in ultimo["detalle"]
    assert "2026-10-05" in ultimo["detalle"] and "2026-10-03" in ultimo["detalle"]


def test_corregir_fecha_exige_una_entrega_marcada(manager):
    venta_estado.abrir("venta", 23, "plant retail", "genesis")
    assert venta_estado.corregir_fecha_entrega(
        "venta", 23, "2026-10-03", manager) == "sin_entrega"
