"""La fórmula de oro y sus bordes: devuelto = enviado − aceptado."""

from app.calculos import acotar_aceptado, calcular_orden, dinero, unidades

LINEAS = [
    {"sku": "VR-015", "nombre": "Novio Chino VR", "precio": 2.25, "enviado": 4},
    {"sku": "VR-005", "nombre": "Chavelitas VR", "precio": 1.5, "enviado": 6},
]


def test_todo_aceptado_por_defecto():
    r = calcular_orden(LINEAS, {})
    assert (r["env"], r["acep"], r["dev"]) == (10, 10, 0)
    assert r["dif"] == []
    assert r["t_orig"] == r["t_acep"] == 18.0


def test_devuelto_se_calcula_solo():
    r = calcular_orden(LINEAS, {"VR-015": 3})
    assert (r["acep"], r["dev"]) == (9, 1)
    assert r["dif"][0]["devuelto"] == 1
    assert r["t_dev"] == 2.25


def test_acotado_al_rango_valido():
    assert acotar_aceptado(-3, 4) == 0
    assert acotar_aceptado(99, 4) == 4
    r = calcular_orden(LINEAS, {"VR-015": 99, "VR-005": -1})
    assert r["dif"][0]["sku"] == "VR-005"
    assert (r["acep"], r["dev"]) == (4, 6)


def test_caso_real_factura_774():
    """El ejemplo de la spec: #774 con 1 Novio Chino rechazado."""
    from app.datos import obtener_orden

    orden = obtener_orden("774")
    completo = calcular_orden(orden["lineas"], {})
    assert completo["t_orig"] == 116.99
    con_rechazo = calcular_orden(orden["lineas"], {"VR-015": 3})
    assert con_rechazo["t_acep"] == 114.74
    assert con_rechazo["t_dev"] == 2.25


def test_formatos():
    assert dinero(114.74) == "B/.114.74"
    assert unidades(1) == "1 unidad"
    assert unidades(3) == "3 unidades"
