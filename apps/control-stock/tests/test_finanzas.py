"""Finanzas (/finanzas) — esqueleto navegable de los BLOQUES 20 y 22.

Lo que se prueba, por regla:

- Nace cerrada (BLOQUE 22.7): sin sesión al login; sin admin ni deber de
  la cola, 403 — por request directa.
- Los números salen de los MISMOS motores (informe + cola + tabla de
  confirmaciones): con datos cuadran, y si no cierran el descuadre SE
  DICE en pantalla.
- Con huecos (Odoo caído) los números dependientes salen «sin dato» —
  jamás un $0 fingido.
- El botón «Confirmar» va DISABLED con «Todavía no» A SECAS (item 5,
  7/10/2026: confirmar es del asiento System manager, no un permiso
  pendiente); reportes y «Ver», apagados.
- Cada fila de la cola dice DE CUÁNDO es, CÓMO llegó la plata y QUIÉN
  la marcó (item 4), y lo que no se sabe lo DICE.
- Ni una palabra técnica en la cara del lector (item 7): ni rutas, ni
  nombres de archivo, ni nombres de sistemas que él no administra.
- CERO rutas POST bajo /finanzas y ni un <form>.

Datos QA solamente.
"""

import re

import pytest

from app import finanzas, pagos_confirmar


VENTAS_QA = [
    {"orden_id": 9001, "nombre": "S09001", "cliente": "Cliente QA Uno",
     "total": 100.0, "pagado": 40.0, "debe": 60.0, "clase": "C",
     "confirmada": True, "motivo": ""},
    {"orden_id": 9002, "nombre": "S09002", "cliente": "Cliente QA Dos",
     "total": 50.0, "pagado": 0.0, "debe": 50.0, "clase": "CANCELADA",
     "confirmada": False, "motivo": "cancelada"},  # fuera de todo
]


@pytest.fixture
def admin(cliente, monkeypatch):
    monkeypatch.setenv("AJUSTES_ADMINS", "genesis")
    return cliente


@pytest.fixture
def con_informe_qa(monkeypatch):
    monkeypatch.setattr(
        pagos_confirmar, "_informe",
        lambda: {"ventas": [dict(v) for v in VENTAS_QA], "huecos": []})


@pytest.fixture
def con_odoo_caido(monkeypatch):
    monkeypatch.setattr(
        pagos_confirmar, "_informe",
        lambda: {"ventas": [], "huecos": ["Odoo no contestó (QA)"]})


# ---------------------------------------------------------------------------
# La puerta: nace cerrada, server-side
# ---------------------------------------------------------------------------

def test_sin_sesion_redirige_al_login(db_limpia):
    from fastapi.testclient import TestClient
    from app.main import app
    c = TestClient(app)
    r = c.get("/finanzas", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"


def test_sin_admin_ni_deber_recibe_403(cliente, monkeypatch):
    monkeypatch.delenv("AJUSTES_ADMINS", raising=False)
    r = cliente.get("/finanzas")
    assert r.status_code == 403


def test_el_admin_entra(admin, con_informe_qa):
    r = admin.get("/finanzas")
    assert r.status_code == 200
    assert "Finanzas" in r.text


# ---------------------------------------------------------------------------
# Los números: mismos motores, y cuadran (o el descuadre se dice)
# ---------------------------------------------------------------------------

def test_los_cuatro_numeros_salen_de_los_motores(admin, con_informe_qa):
    texto = admin.get("/finanzas").text
    assert "$100.00" in texto      # vendido (la cancelada queda fuera)
    assert "$40.00" in texto       # por confirmar (monto_nuevo de la cola)
    assert "$60.00" in texto       # por cobrar
    assert "S09001" in texto       # la fila de la cola, la MISMA cola
    assert "S09002" not in texto   # la cancelada no existe para nadie
    # Con 100 = 0 + 40 + 60 no hay descuadre que avisar.
    assert "no cierran entre sí" not in texto


def test_el_descuadre_se_dice_no_se_esconde(admin, con_informe_qa,
                                            monkeypatch):
    """BLOQUE 59.2: con el universo de ventas confirmadas el resto es 0 por
    construcción, así que la ÚNICA forma de descuadrar es haber dado por
    buena más plata de la que Odoo tiene en esa venta. Eso se dice."""
    # S09001 tiene $40 pagados en Odoo y alguien dio por buenos $90:
    # 100 ≠ 90 + 0 (la cola ya no la trae) + 60 → se va por -50.
    monkeypatch.setattr(pagos_confirmar, "sumas_confirmadas",
                        lambda: {9001: 90.0})
    texto = admin.get("/finanzas").text
    assert "se van por $50.00" in texto


def test_lo_confirmado_fuera_del_universo_se_muestra_aparte(
        admin, con_informe_qa, monkeypatch):
    """Una confirmación sobre una venta que esta foto no cuenta (una
    cotización, una cancelada, una orden ya borrada) NO se suma a ninguno
    de los cuatro ni descuadra nada: se muestra con todas las letras.
    Repartirla sería inventar; esconderla, peor."""
    monkeypatch.setattr(pagos_confirmar, "sumas_confirmadas",
                        lambda: {8888: 50.0})
    datos = finanzas.resumen()
    assert datos["descuadre"] is None
    assert datos["confirmado_fuera"] == {"n": 1, "monto": 50.0}
    assert _tarjeta(datos, "Cobrado y confirmado")["monto"] == 0.0
    texto = admin.get("/finanzas").text
    assert "$50.00 ya dados por buenos en 1 venta" in texto


def test_con_odoo_caido_sin_dato_jamas_cero(admin, con_odoo_caido):
    texto = admin.get("/finanzas").text
    assert "Odoo no contestó (QA)" in texto
    # Vendido, por confirmar y por cobrar: sin dato, nunca $0 fingido.
    assert texto.count(">sin dato<") >= 3
    datos = finanzas.resumen()
    assert datos["con_datos"] is False
    assert [t["monto"] for t in datos["tarjetas"]][0] is None


# ---------------------------------------------------------------------------
# BLOQUE 59.2 · el universo son las VENTAS CONFIRMADAS, y el invariante
#
# Vendido = Cobrado y confirmado + Pagos por confirmar + Por cobrar.
# No es una coincidencia: es `total = pagado + debe` de cada venta
# confirmada, con lo pagado partido en «ya lo dieron por bueno» y «nadie lo
# ha revisado». Si alguien cambia la fórmula de una sola tarjeta, estas
# pruebas caen.
# ---------------------------------------------------------------------------

def _tarjeta(datos, titulo):
    for t in datos["tarjetas"]:
        if t["titulo"] == titulo:
            return t
    raise AssertionError(f"no hay tarjeta «{titulo}»")


def _marcar(orden_id, monto, evidencia="yappy", por="Rubén"):
    """Una confirmación humana de verdad en la tabla local: así
    `sumas_confirmadas` y el desglose «Cómo pagaron» leen la MISMA fila y
    la prueba de cuadre vale para los dos."""
    from app.datos import _db, ahora_iso
    with _db() as con:
        con.execute(
            "INSERT INTO pago_confirmado (orden_id, orden, cliente, monto,"
            " evidencia, nota, por, en) VALUES (?,?,?,?,?,?,?,?)",
            (orden_id, f"S{orden_id}", "Cliente QA", monto, evidencia, "",
             por, ahora_iso()))


# Un mundo con TODOS los casos que mueven los cuatro números a la vez: una
# cotización gorda (no cuenta), una confirmada con abono, una pagada
# completa, una confirmada sin un peso, una clase F confirmada (plata
# informada que Odoo no tiene) y una clase F que todavía es cotización.
MUNDO = [
    {"orden_id": 1, "nombre": "S00001", "cliente": "Cotización gorda",
     "total": 9000.0, "pagado": 0.0, "debe": 9000.0, "clase": "A",
     "confirmada": False, "motivo": ""},
    {"orden_id": 2, "nombre": "S00002", "cliente": "Con abono",
     "total": 1000.0, "pagado": 400.0, "debe": 600.0, "clase": "C",
     "confirmada": True, "motivo": ""},
    {"orden_id": 3, "nombre": "S00003", "cliente": "Pagada completa",
     "total": 250.0, "pagado": 250.0, "debe": 0.0, "clase": "D",
     "confirmada": True, "motivo": ""},
    {"orden_id": 4, "nombre": "S00004", "cliente": "Confirmada sin pago",
     "total": 80.0, "pagado": 0.0, "debe": 80.0, "clase": "C",
     "confirmada": True, "motivo": ""},
    {"orden_id": 5, "nombre": "S00005", "cliente": "Informada confirmada",
     "total": 120.0, "pagado": 0.0, "debe": 120.0, "clase": "F",
     "confirmada": True, "motivo": ""},
    {"orden_id": 6, "nombre": "S00006", "cliente": "Informada cotización",
     "total": 70.0, "pagado": 0.0, "debe": 70.0, "clase": "F",
     "confirmada": False, "motivo": ""},
    {"orden_id": 7, "nombre": "S00007", "cliente": "Cancelada",
     "total": 55.0, "pagado": 0.0, "debe": 55.0, "clase": "CANCELADA",
     "confirmada": False, "motivo": ""},
]


@pytest.fixture
def mundo(db_limpia, monkeypatch):
    monkeypatch.setattr(
        pagos_confirmar, "_informe",
        lambda: {"ventas": [dict(v) for v in MUNDO], "huecos": []})


def _cuadra(datos):
    """El invariante, leído de las TARJETAS (no de variables internas):
    así la prueba falla si cambia la fórmula de cualquiera de las cuatro."""
    vendido = _tarjeta(datos, "Vendido")["monto"]
    partes = sum(_tarjeta(datos, t)["monto"] or 0.0 for t in
                 ("Cobrado y confirmado", "Pagos por confirmar",
                  "Por cobrar"))
    return vendido, round(partes, 2)


def test_el_invariante_cuadra_sin_ninguna_confirmacion(mundo):
    datos = finanzas.resumen()
    vendido, partes = _cuadra(datos)
    # Solo las confirmadas: 1000 + 250 + 80 + 120. La cotización de $9000
    # y la clase F sin confirmar NO entran.
    assert vendido == 1450.0
    assert partes == vendido, datos["tarjetas"]
    assert datos["descuadre"] is None


def test_el_invariante_cuadra_con_confirmaciones_parciales_y_totales(mundo):
    _marcar(2, 400.0, "transferencia")   # el abono entero, ya dado por bueno
    _marcar(3, 100.0, "efectivo_qa")     # media pagada completa
    datos = finanzas.resumen()
    vendido, partes = _cuadra(datos)
    assert vendido == 1450.0
    assert partes == vendido, datos["tarjetas"]
    assert datos["descuadre"] is None
    assert _tarjeta(datos, "Cobrado y confirmado")["monto"] == 500.0
    # Lo que queda por confirmar es SOLO la plata nueva: los $150 que
    # faltan de la pagada completa. El abono ya confirmado no se repite.
    assert _tarjeta(datos, "Pagos por confirmar")["monto"] == 150.0


def test_el_invariante_falla_si_una_tarjeta_cambia_de_universo(mundo,
                                                               monkeypatch):
    """La prueba que pidió Abraham: si alguien devuelve «Por cobrar» al
    universo viejo (todas las órdenes no canceladas, borradores incluidos),
    los cuatro dejan de cuadrar y la pantalla lo DICE. Se simula el error
    sumando el `debe` de todo lo no cancelado, como hacía hasta el 7/10."""
    datos = finanzas.resumen()
    por_cobrar_viejo = round(sum(v["debe"] for v in MUNDO
                                 if v["clase"] != "CANCELADA"), 2)
    vendido = _tarjeta(datos, "Vendido")["monto"]
    confirmado = _tarjeta(datos, "Cobrado y confirmado")["monto"]
    por_confirmar = _tarjeta(datos, "Pagos por confirmar")["monto"]
    assert por_cobrar_viejo == 9870.0          # lo que mostraba antes
    assert _tarjeta(datos, "Por cobrar")["monto"] == 800.0   # lo que suma hoy
    # Con el universo viejo en una sola tarjeta el invariante se rompe:
    assert round(confirmado + por_confirmar + por_cobrar_viejo, 2) != vendido


def test_una_cotizacion_no_cuenta_en_ninguno_de_los_cuatro(mundo):
    datos = finanzas.resumen()
    for titulo in ("Vendido", "Cobrado y confirmado", "Pagos por confirmar",
                   "Por cobrar"):
        assert "9,000" not in _tarjeta(datos, titulo)["texto"]
    assert datos["sin_confirmar"] == 2        # la de $9000 y la F cotizada


def test_cada_numero_dice_que_suma(mundo):
    """Era el problema de fondo: los cuatro eran correctos y ninguno decía
    de qué universo hablaba."""
    datos = finanzas.resumen()
    for t in datos["tarjetas"]:
        assert t["hint"].strip(), t["titulo"]
    assert "4 ventas que el cliente ya confirmó" in \
        _tarjeta(datos, "Vendido")["hint"]
    assert "no cuenta" in _tarjeta(datos, "Por cobrar")["hint"]
    assert datos["regla"] == finanzas.TEXTO_REGLA


def test_con_el_boton_apagado_la_linea_dice_que_nadie_confirmo(admin, mundo):
    """Condición textual de Abraham: el $0.00 no puede leerse como «no
    cobré nada»."""
    datos = finanzas.resumen()
    assert _tarjeta(datos, "Cobrado y confirmado")["monto"] == 0.0
    assert _tarjeta(datos, "Cobrado y confirmado")["hint"] == \
        finanzas.TEXTO_NADIE_CONFIRMO
    assert finanzas.TEXTO_NADIE_CONFIRMO in admin.get("/finanzas").text


def test_la_plata_informada_sobre_una_cotizacion_se_ve_pero_no_suma(
        admin, mundo):
    """S00006 es clase F y todavía cotización: no entra en los cuatro, y
    la pantalla la muestra con su aviso — es justo la plata mal puesta."""
    datos = finanzas.resumen()
    assert datos["cola_fuera"] == {"n": 1, "monto": 70.0}
    assert [p["orden"] for p in datos["otros_pendientes"]] == ["S00006"]
    assert [p["orden"] for p in datos["pendientes"]] == ["S00002", "S00003",
                                                         "S00005"]
    texto = admin.get("/finanzas").text
    assert "1 pago informado sobre cotizaciones" in texto
    assert "S00006" in texto


# ---------------------------------------------------------------------------
# BLOQUE 59.4 · «Cómo pagaron» se muda acá, y suma la MISMA tarjeta
# ---------------------------------------------------------------------------

def test_como_pagaron_vacio_dice_que_nadie_confirmo(admin, mundo):
    datos = finanzas.resumen()
    assert datos["desglose"] == []
    assert datos["nadie_confirmo"] is True
    assert "Cómo pagaron" in admin.get("/finanzas").text


def test_como_pagaron_suma_exactamente_cobrado_y_confirmado(mundo):
    """Condición de Abraham: si el desglose sumara otra cosa se crearía
    una quinta verdad justo cuando estamos matando la segunda."""
    _marcar(2, 400.0, "transferencia")
    _marcar(3, 100.0, "yappy")
    _marcar(3, 50.0, "tarjeta")
    datos = finanzas.resumen()
    assert round(sum(d["monto"] for d in datos["desglose"]), 2) == \
        _tarjeta(datos, "Cobrado y confirmado")["monto"] == 550.0
    assert {d["clave"] for d in datos["desglose"]} == {"transferencia",
                                                       "yappy", "tarjeta"}
    # Ordenado de mayor a menor, para que lo grande se lea primero.
    assert [d["monto"] for d in datos["desglose"]] == [400.0, 100.0, 50.0]


def test_como_pagaron_no_cuenta_lo_de_fuera_del_universo(mundo):
    """Una confirmación sobre una cotización o una cancelada no entra al
    desglose: si entrara, dejaría de sumar la tarjeta."""
    _marcar(2, 400.0, "transferencia")
    _marcar(1, 9000.0, "yappy")     # la cotización de $9000
    _marcar(7, 55.0, "tarjeta")     # la cancelada
    datos = finanzas.resumen()
    assert round(sum(d["monto"] for d in datos["desglose"]), 2) == 400.0
    assert _tarjeta(datos, "Cobrado y confirmado")["monto"] == 400.0
    assert datos["confirmado_fuera"]["monto"] == 9055.0


def test_un_metodo_que_la_casa_no_conoce_se_ve_no_se_reparte(mundo):
    _marcar(2, 400.0, "cripto_marciana")
    datos = finanzas.resumen()
    assert len(datos["desglose"]) == 1
    assert datos["desglose"][0]["texto"] == \
        pagos_confirmar.TEXTO_EVIDENCIA_DESCONOCIDA
    assert round(sum(d["monto"] for d in datos["desglose"]), 2) == \
        _tarjeta(datos, "Cobrado y confirmado")["monto"] == 400.0


def test_las_cifras_de_cifras_py_viajan_con_su_rotulo(admin, con_informe_qa):
    texto = admin.get("/finanzas").text
    assert "Plata cobrada de trabajos sin entregar" in texto
    assert "solo cuenta lo que la app registró desde el 5/10/2026" in texto


# ---------------------------------------------------------------------------
# Botones apagados y cero escritura
# ---------------------------------------------------------------------------

def test_confirmar_va_apagado_y_sin_el_si_de_jay(admin, con_informe_qa):
    """Item 5 (7/10/2026): el botón decía «falta el sí de Jay» y ese
    permiso ya no es el motivo — confirmar es del asiento System manager.
    Queda apagado, y nada más."""
    texto = admin.get("/finanzas").text
    assert "Confirmar — Todavía no" in texto
    assert "Jay" not in texto
    for boton in re.findall(r"<button[^>]*>[^<]*Todavía no[^<]*</button>",
                            texto):
        assert "disabled" in boton, boton


def test_los_reportes_van_apagados(admin, con_informe_qa):
    texto = admin.get("/finanzas").text
    for nombre in finanzas.REPORTES:
        assert f"{nombre} — Todavía no" in texto
    assert "Descargar reporte — Todavía no" in texto


def test_ni_un_form_en_la_pantalla(admin, con_informe_qa):
    assert "<form" not in admin.get("/finanzas").text


def test_cero_rutas_post_bajo_finanzas():
    from app.main import app
    for ruta in app.routes:
        if str(getattr(ruta, "path", "")).startswith("/finanzas"):
            assert "POST" not in (getattr(ruta, "methods", None) or set())


# ---------------------------------------------------------------------------
# Item 4 · cada fila de la cola dice cuándo, cómo y quién
# ---------------------------------------------------------------------------

VENTA_CON_FECHA = [
    {"orden_id": 9101, "nombre": "S09101", "cliente": "Cliente QA Fecha",
     "fecha": "2026-10-02 15:04:33", "total": 300.0, "pagado": 300.0,
     "debe": 0.0, "clase": "D", "motivo": ""},
]


@pytest.fixture
def con_venta_fechada(monkeypatch):
    monkeypatch.setattr(
        pagos_confirmar, "_informe",
        lambda: {"ventas": [dict(v) for v in VENTA_CON_FECHA], "huecos": []})


def test_la_fila_dice_la_fecha_de_la_venta(admin, con_venta_fechada):
    """Era el reclamo: la cola no decía de cuándo era cada fila."""
    texto = admin.get("/finanzas").text
    assert "Venta del 02/10/2026" in texto


def test_sin_confirmar_todavia_el_metodo_y_el_quien_se_DICEN(
        admin, con_venta_fechada):
    """Nada se inventa: una fila que nadie tocó no tiene método (se elige
    al confirmar) ni quién la marcó, y las dos cosas se escriben."""
    texto = admin.get("/finanzas").text
    assert pagos_confirmar.SIN_METODO in texto
    assert pagos_confirmar.SIN_MARCA in texto


def test_con_una_confirmacion_previa_sale_el_metodo_y_el_nombre(
        admin, con_venta_fechada, monkeypatch):
    """Lo que SÍ existe sale: el libro de confirmaciones guarda qué se vio
    y quién lo vio, y la fila lo cuenta."""
    monkeypatch.setattr(pagos_confirmar, "confirmados", lambda: {
        9101: {"orden_id": 9101, "evidencia": "yappy", "por": "Rubén",
               "en": "2026-10-05 09:12:00", "monto": 100.0}})
    # Con un abono previo de 100 y 300 pagados, la fila RE-ENTRA por los 200
    # nuevos — que es justo el caso en el que el método y el quién existen.
    monkeypatch.setattr(pagos_confirmar, "sumas_confirmadas",
                        lambda: {9101: 100.0})
    texto = admin.get("/finanzas").text
    assert "Voucher de Yappy" in texto
    assert "marcó: Rubén, el 05/10/2026" in texto


def test_una_fecha_que_no_se_entiende_no_se_inventa():
    assert pagos_confirmar.fecha_de_venta("") == pagos_confirmar.SIN_FECHA
    assert pagos_confirmar.fecha_de_venta(None) == pagos_confirmar.SIN_FECHA
    assert pagos_confirmar.fecha_de_venta("ayer") == pagos_confirmar.SIN_FECHA
    assert pagos_confirmar.fecha_de_venta("2026-10-02") == "02/10/2026"


# ---------------------------------------------------------------------------
# Item 7 · ni una palabra técnica en la cara del lector
# ---------------------------------------------------------------------------

# Lo que esta pantalla NO puede decir: rutas internas, nombres de archivo y
# nombres de sistemas que quien lee Finanzas no administra.
JERGA_PROHIBIDA = ("/revisar", "/pagos-por-confirmar", "cifras.py",
                   "hechos locales", "FUERA de Odoo", "informe de",
                   "confirmaciones humanas", "droplet", ".env", "BLOQUE")


@pytest.mark.parametrize("palabra", JERGA_PROHIBIDA)
def test_la_pantalla_no_habla_en_tecnico(admin, con_informe_qa, palabra):
    cuerpo = admin.get("/finanzas").text
    # Solo lo que se VE: los comentarios de Jinja no llegan al HTML, pero
    # las clases y los href sí, así que se mira el texto entre etiquetas.
    visible = " ".join(re.sub(r"<[^>]+>", " ", cuerpo).split())
    assert palabra not in visible, f"«{palabra}» se ve en la pantalla"
