"""El flujo completo de la empleada, de punta a punta contra las rutas."""

import re

from fastapi.testclient import TestClient

from app.main import app

cliente = TestClient(app)


def test_recepcion_con_diferencia_y_regreso(con_ordenes):
    # Abrir la orden: todo viene aceptado y los nombres van sin " VR".
    r = cliente.get("/orden/S00774")
    assert r.status_code == 200
    assert "Pedido 00774" in r.text and "S00774" not in r.text.replace("/orden/S00774", "")
    assert "Ref. súper: FAC-774" in r.text
    assert "Novio Chino" in r.text and "Novio Chino VR" not in r.text

    # Revisar con 1 Novio Chino rechazado: el caso real de la spec.
    r = cliente.post("/orden/S00774/revisar", data={"a_VR-015": "3"})
    assert "Revisa las diferencias" in r.text
    assert "B/.2.25" in r.text

    # Confirmar: éxito con los montos y la entrega sale de pendientes.
    r = cliente.post("/orden/S00774/confirmar", data={"a_VR-015": "3"})
    assert "Entrega registrada" in r.text
    assert "B/.114.74" in r.text
    assert "S00774" not in cliente.get("/").text

    # La devolución quedó pendiente y el historial la marca así.
    r = cliente.get("/devoluciones")
    assert "Novio Chino VR" in r.text and "1 unidad" in r.text
    assert "Pedido 00774" in r.text
    assert "Regreso pendiente" in cliente.get("/historial").text

    # Confirmar el regreso al vivero: se limpia y el historial cierra.
    cliente.post("/devoluciones/S00774/regreso")
    assert "Nada por regresar" in cliente.get("/devoluciones").text
    assert "Completada" in cliente.get("/historial").text


def test_recepcion_confirmada_sobrevive_reinicio(con_ordenes):
    cliente.post("/orden/S00770/confirmar", data={})
    # Otra petición (nuevo request = como reabrir la app): sigue confirmada.
    assert "S00770" not in cliente.get("/").text
    assert cliente.get("/orden/S00770", follow_redirects=False).status_code == 303


def test_orden_inexistente_redirige(con_ordenes):
    assert cliente.get("/orden/S99999", follow_redirects=False).status_code == 303


def test_intercambio_completo(db_limpia):
    r = cliente.get("/intercambios/nuevo")
    assert "¿En qué súper estás?" in r.text and "Supermercados Rey" in r.text

    r = cliente.get("/intercambios/nuevo/plantas", params={"cliente": "Super Xtra", "sucursal": "Villalobos"})
    assert "¿Qué plantas se dañaron?" in r.text

    r = cliente.post("/intercambios/nuevo/revisar", data={
        "cliente": "Super Xtra", "sucursal": "Villalobos", "d_VR-007": "2",
    })
    assert "Confirmar intercambio" in r.text and "B/.6.40" in r.text

    r = cliente.post("/intercambios/crear", data={
        "cliente": "Super Xtra", "sucursal": "Villalobos", "d_VR-007": "2",
    })
    assert "Intercambio registrado" in r.text

    r = cliente.get("/intercambios")
    assert "Pendiente de devolver" in r.text and "Jade VR" in r.text

    # Completarlo: pasa a Completado y aparece en el historial con su valor.
    intercambio_id = re.search(r"/intercambios/(I-\d+)/completar", r.text).group(1)
    cliente.post(f"/intercambios/{intercambio_id}/completar")
    assert "Completado" in cliente.get("/intercambios").text
    texto_historial = cliente.get("/historial").text
    assert "Reemplazado" in texto_historial and "B/.6.40" in texto_historial
    assert "2 unidades reemplazadas" in texto_historial

    # Concordancia en singular: un intercambio de 1 unidad dice "reemplazada".
    cliente.post("/intercambios/crear", data={
        "cliente": "Super Xtra", "sucursal": "Villalobos", "d_VR-003": "1",
    })
    r = cliente.get("/intercambios")
    otro_id = re.search(r"/intercambios/(I-\d+)/completar", r.text).group(1)
    cliente.post(f"/intercambios/{otro_id}/completar")
    assert "1 unidad reemplazada<" in cliente.get("/historial").text


def test_sin_plantas_no_hay_revision(db_limpia):
    r = cliente.post("/intercambios/nuevo/revisar", data={
        "cliente": "Super Xtra", "sucursal": "Villalobos",
    }, follow_redirects=False)
    assert r.status_code == 303
