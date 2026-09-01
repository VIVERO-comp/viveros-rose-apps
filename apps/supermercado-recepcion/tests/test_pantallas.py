"""Las pantallas responden y muestran lo que deben (contra una DB temporal)."""

from fastapi.testclient import TestClient

from app.main import app

cliente = TestClient(app)


def test_entregas_muestra_las_tres_facturas(db_limpia):
    r = cliente.get("/")
    assert r.status_code == 200
    for factura in ("#774", "#781", "#770"):
        assert factura in r.text
    assert "B/.116.99" in r.text
    assert "Hola, Génesis" in r.text


def test_orden_por_fecha_mas_reciente_primero(db_limpia):
    texto = cliente.get("/").text
    assert texto.index("#774") < texto.index("#770")


def test_tabs_vacias_con_sus_mensajes(db_limpia):
    assert "Nada por regresar" in cliente.get("/devoluciones").text
    assert "plantas dañadas" in cliente.get("/intercambios").text
    assert "Aún no hay movimientos hoy" in cliente.get("/historial").text
