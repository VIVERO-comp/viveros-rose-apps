"""La cola local hacia el order-api: nada se pierde, nada se duplica."""

import json

import httpx
import pytest

from app import datos


class RespuestaFalsa:
    def __init__(self, estado, texto="ok"):
        self.status_code = estado
        self.text = texto


@pytest.fixture
def orden_api(monkeypatch):
    """Order-api falso: registra los POST y se puede 'apagar'."""
    estado = {"caido": False, "respuesta": 200, "llamadas": []}

    def post(url, json=None, headers=None, timeout=None):
        if estado["caido"]:
            raise httpx.ConnectError("caido")
        estado["llamadas"].append({"url": url, "payload": json, "headers": headers})
        return RespuestaFalsa(estado["respuesta"])

    monkeypatch.setenv("ORDER_API_URL", "https://pedidos.ejemplo")
    monkeypatch.setenv("ORDER_API_KEY", "clave-interna")
    monkeypatch.setattr(httpx, "post", post)
    return estado


def _confirmar(cliente, pedido="S00774", datos_form=None):
    return cliente.post(f"/orden/{pedido}/confirmar", data=datos_form or {"a_VR-015": "3"})


def test_confirmar_sincroniza_al_instante(cliente, con_ordenes, orden_api):
    _confirmar(cliente)
    llamada = orden_api["llamadas"][0]
    assert llamada["url"] == "https://pedidos.ejemplo/api/supermercado/recepciones"
    assert llamada["headers"]["X-API-Key"] == "clave-interna"
    assert llamada["payload"]["pedido"] == "S00774"
    novio = next(l for l in llamada["payload"]["lineas"] if l["sku"] == "VR-015")
    assert (novio["aceptado"], novio["devuelto"]) == (3, 1)
    assert datos.pedidos_sin_sincronizar() == set()
    assert "Sin sincronizar" not in cliente.get("/historial").text


def test_order_api_caido_no_pierde_la_confirmacion(cliente, con_ordenes, orden_api):
    orden_api["caido"] = True
    r = _confirmar(cliente)
    assert "Entrega registrada" in r.text          # la empleada ni se entera
    assert datos.pedidos_sin_sincronizar() == {"S00774"}
    assert "Sin sincronizar" in cliente.get("/historial").text

    # El servidor vuelve: el reintento vacía la cola y la pill desaparece.
    orden_api["caido"] = False
    assert datos.sincronizar_pendientes() == 1
    assert datos.pedidos_sin_sincronizar() == set()
    assert "Sin sincronizar" not in cliente.get("/historial").text


def test_rechazo_4xx_no_se_martilla(cliente, con_ordenes, orden_api):
    orden_api["respuesta"] = 409
    _confirmar(cliente)
    llamadas = len(orden_api["llamadas"])
    datos.sincronizar_pendientes()
    assert len(orden_api["llamadas"]) == llamadas  # quedó en error, no reintenta
    assert datos.pedidos_sin_sincronizar() == {"S00774"}  # visible, no invisible


def test_sin_configuracion_solo_encola(cliente, con_ordenes, monkeypatch):
    monkeypatch.delenv("ORDER_API_URL", raising=False)
    _confirmar(cliente)
    with datos._db() as con:
        fila = con.execute("SELECT estado, payload FROM pendientes_odoo").fetchone()
    assert fila["estado"] == "pendiente"
    assert json.loads(fila["payload"])["odooId"] == 10774
    # Sin order-api configurado no hay pill: no se promete una sincronización
    # que no existe.
    assert "Sin sincronizar" not in cliente.get("/historial").text
