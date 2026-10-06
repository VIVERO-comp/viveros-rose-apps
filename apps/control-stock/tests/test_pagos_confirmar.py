"""Pruebas de la cola «Pagos por confirmar» (item 6 de Jay, 5/10/2026).

El motor de la cola es el MISMO de /revisar (reconciliacion), suplantado
acá con un informe falso: estas pruebas no simulan media Odoo. Lo que se
clava: la cola lista lo que nadie confirmó (plata en Odoo sin
confirmación humana, y la clase F), confirmar registra quién/cuándo/qué
vio, solo el system manager confirma, el acceso es por deber, y la
confirmación deja el hecho del pago en la venta local — completo SOLO
con el saldo en 0 (un depósito de evento nunca cierra).
"""

import pytest

from app import datos, datos_roles, pagos_confirmar, seguridad, venta_estado


def _informe_falso(ventas_filas, huecos=None):
    return {"contadores": {}, "ventas": ventas_filas,
            "fuera_de_alcance": {}, "creado_hoy": {},
            "huecos": huecos or []}


def _fila(orden_id, nombre, pagado, debe, clase="D", cliente="Ana",
          total=None, motivo="Pagada completa"):
    return {"orden_id": orden_id, "nombre": nombre, "cliente": cliente,
            "pagado": pagado, "debe": debe,
            "total": total if total is not None else pagado + debe,
            "clase": clase, "motivo": motivo}


@pytest.fixture
def equipo(db_limpia):
    """sam = system manager · olga = operaciones · genesis = sin deber."""
    for usuario, nombre in (("sam", "Sam"), ("olga", "Olga"),
                            ("genesis", "Génesis")):
        seguridad.crear_empleada(usuario, nombre, "clave")
    roles = {r["deber"]: r["n"] for r in datos_roles.listar_roles()}
    datos_roles.poner_persona(roles["system_manager"], "sam", "prueba")
    datos_roles.poner_persona(roles["operations"], "olga", "prueba")
    return {"manager": "sam", "operaciones": "olga", "sin_deber": "genesis"}


def test_la_cola_lista_lo_que_nadie_confirmo(equipo, monkeypatch):
    monkeypatch.setattr(pagos_confirmar, "_informe", lambda: _informe_falso([
        _fila(1, "S00001", 100.0, 0.0, clase="D"),
        _fila(2, "S00002", 0.0, 50.0, clase="C"),   # sin plata: no entra
        _fila(3, "S00003", 0.0, 80.0, clase="F",    # pago fuera de Odoo: sí
              motivo="Pago informado, no registrado en Odoo · verificar"),
        _fila(4, "S00004", 10.0, 0.0, clase="CANCELADA"),  # nunca
    ]))
    pendientes, huecos = pagos_confirmar.cola()
    assert [p["orden"] for p in pendientes] == ["S00001", "S00003"]
    assert pendientes[0]["completo"] is True       # saldo en 0
    assert pendientes[1]["fuera_de_odoo"] is True
    assert pendientes[1]["completo"] is False
    assert huecos == []


def test_los_huecos_del_informe_viajan_tal_cual(equipo, monkeypatch):
    monkeypatch.setattr(pagos_confirmar, "_informe",
                        lambda: _informe_falso([], ["Odoo no contestó: x"]))
    pendientes, huecos = pagos_confirmar.cola()
    assert pendientes == [] and huecos == ["Odoo no contestó: x"]


def test_confirmar_registra_quien_cuando_y_que_vio(equipo):
    error = pagos_confirmar.confirmar(
        7, "S00007", "Ana", 120.0, "yappy", "voucher del 4/10",
        equipo["manager"], "Sam", completo=True)
    assert error is None
    fila = pagos_confirmar.historial()[0]
    assert fila["orden"] == "S00007" and fila["monto"] == 120.0
    assert fila["evidencia"] == "yappy"
    assert fila["nota"] == "voucher del 4/10"
    assert fila["por"] == "Sam" and fila["en"]
    # Y la cola ya no la lista.
    assert 7 in pagos_confirmar.confirmados()


def test_confirmar_exige_evidencia_del_catalogo(equipo):
    assert pagos_confirmar.confirmar(
        7, "S00007", "Ana", 120.0, "palabra", "", equipo["manager"],
        "Sam") == "evidencia_invalida"
    assert pagos_confirmar.historial() == []


def test_solo_el_system_manager_confirma(equipo):
    for quien in (equipo["operaciones"], equipo["sin_deber"]):
        assert pagos_confirmar.confirmar(
            7, "S00007", "Ana", 120.0, "tarjeta", "", quien) == \
            "solo_system_manager"
    assert pagos_confirmar.historial() == []


def test_el_acceso_es_por_deber_no_por_nombre(equipo):
    assert pagos_confirmar.puede_ver(equipo["manager"])
    assert pagos_confirmar.puede_ver(equipo["operaciones"])
    assert not pagos_confirmar.puede_ver(equipo["sin_deber"])
    # El deber se muda de rol y el acceso se muda con él.
    rol_libre = next(r for r in datos_roles.listar_roles()
                     if r["deber"] is None)
    datos_roles.poner_persona(rol_libre["n"], "genesis", "prueba")
    datos_roles.asignar_deber("owner_view", rol_libre["n"])
    assert pagos_confirmar.puede_ver(equipo["sin_deber"])


def test_confirmar_deja_el_hecho_en_la_venta_local(equipo):
    with datos._db() as con:
        n = con.execute(
            "INSERT INTO ventas_locales (creado_en, empleada, cliente,"
            " orden_id, orden, total, estado) VALUES"
            " ('2026-10-05T10:00:00','Génesis','Ana',31,'S00031',200.0,"
            "'vendida')").lastrowid
    venta_estado.abrir("venta", n, "plant retail", "Génesis")
    pagos_confirmar.confirmar(31, "S00031", "Ana", 200.0, "transferencia",
                              "", equipo["manager"], "Sam", completo=True)
    hechos = venta_estado.estado_de("venta", n)
    assert hechos["pago_confirmado"] == 1 and hechos["estado"] == 2
    # Y NO tocó la entrega (el candado de siempre).
    assert hechos["entrega_marcada"] == 0


def test_el_deposito_confirmado_de_un_evento_no_cierra(equipo):
    with datos._db() as con:
        con.execute(
            "INSERT INTO cotizaciones_servicio (creado_en, empleada, tipo,"
            " cliente, celular, orden_id, orden, total) VALUES"
            " ('2026-10-05T10:00:00','Mary','renta','Ilayda','',41,"
            "'S00041',1500.0)")
        n = con.execute("SELECT n FROM cotizaciones_servicio"
                        " WHERE orden_id=41").fetchone()["n"]
    venta_estado.abrir("servicio", n, "rental event", "Mary")
    # Depósito del 50%: completo=False (lo decide la cola por el saldo).
    pagos_confirmar.confirmar(41, "S00041", "Ilayda", 750.0, "yappy",
                              "depósito", equipo["manager"], "Sam",
                              completo=False)
    hechos = venta_estado.estado_de("servicio", n)
    assert hechos["estado"] == 2 and hechos["pago_completo"] == 0
    # Ni marcando la entrega cierra: falta el saldo.
    venta_estado.marcar_entregada("servicio", n, equipo["manager"], "Sam")
    assert venta_estado.estado_de("servicio", n)["estado"] == 2


# ---------------------------------------------------------------------------
# La pantalla por HTTP: acceso por deber y el POST que relee la cola.
# ---------------------------------------------------------------------------

@pytest.fixture
def web(cliente, monkeypatch):
    """El cliente de siempre (genesis), con el informe suplantado."""
    monkeypatch.setattr(pagos_confirmar, "_informe", lambda: _informe_falso([
        _fila(9, "S00009", 60.0, 0.0, clase="D"),
    ]))
    return cliente


def test_sin_deber_la_pantalla_dice_403(web):
    assert web.get("/pagos-por-confirmar").status_code == 403


def test_con_deber_ve_la_cola_y_el_manager_confirma(web):
    # genesis gana el deber de system manager: ve y confirma.
    rol = next(r for r in datos_roles.listar_roles()
               if r["deber"] == "system_manager")
    datos_roles.poner_persona(rol["n"], "genesis", "prueba")
    pagina = web.get("/pagos-por-confirmar")
    assert pagina.status_code == 200
    assert "S00009" in pagina.text and "Confirmar que llegó" in pagina.text
    r = web.post("/pagos-por-confirmar/confirmar",
                 data={"orden_id": "9", "evidencia": "tarjeta",
                       "nota": "pos del vivero"}, follow_redirects=False)
    assert r.status_code == 303 and "aviso=" in r.headers["location"]
    fila = pagos_confirmar.historial()[0]
    assert fila["orden"] == "S00009" and fila["evidencia"] == "tarjeta"
    assert fila["monto"] == 60.0  # el monto sale del informe, no del form
    # Confirmada: la pantalla ya no la lista como pendiente.
    assert "Nada por confirmar" in web.get("/pagos-por-confirmar").text
