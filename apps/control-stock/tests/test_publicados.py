"""La lectura del catálogo publicado del sitio, que es lo que define "online".

Es un espejo de plantaspanama.com, no un interruptor: cada build del
frontend deja en /catalogo-publicado.json los SKU que salieron publicados.
Aquí se prueba la degradación, que es lo delicado: un sitio caído no puede
convertirse en "no hay nada publicado".
"""

import pytest

from app import datos
# La función de verdad, capturada al importar: el fixture autouse de conftest
# sustituye datos.obtener_publicados para que ninguna prueba salga a la red,
# y aquí es justo esa función la que se quiere probar.
from app.datos import obtener_publicados


class RespuestaFalsa:
    def __init__(self, cuerpo):
        self._cuerpo = cuerpo

    def raise_for_status(self):
        pass

    def json(self):
        return self._cuerpo


@pytest.fixture
def sin_cache():
    datos.reiniciar_cache_publicados()
    yield
    datos.reiniciar_cache_publicados()


def test_lee_los_publicados_del_sitio(sin_cache, monkeypatch):
    monkeypatch.setattr(datos.httpx, "get", lambda *a, **k: RespuestaFalsa(
        {"conocidos": ["PL-A", "PL-B", "PL-C"], "publicados": ["PL-A", "PL-B"]}))
    skus, error = obtener_publicados()
    assert skus == {"PL-A", "PL-B"}
    assert error is None


def test_no_vuelve_a_pedirlo_dentro_del_ttl(sin_cache, monkeypatch):
    llamadas = []

    def get(*a, **k):
        llamadas.append(1)
        return RespuestaFalsa({"publicados": ["PL-A"]})

    monkeypatch.setattr(datos.httpx, "get", get)
    obtener_publicados()
    obtener_publicados()
    assert len(llamadas) == 1


def test_sitio_caido_sirve_el_ultimo_valor_bueno(sin_cache, monkeypatch):
    monkeypatch.setattr(datos.httpx, "get",
                        lambda *a, **k: RespuestaFalsa({"publicados": ["PL-A"]}))
    obtener_publicados()
    # Vence el TTL y ahora el sitio no responde: vale más el valor viejo que
    # decir que no hay nada publicado (vaciaría la pestaña Stock online).
    datos._cache_publicados["skus"]["en"] = 0

    def cae(*a, **k):
        raise RuntimeError("timeout")

    monkeypatch.setattr(datos.httpx, "get", cae)
    skus, error = obtener_publicados()
    assert skus == {"PL-A"}
    assert error is None


def test_sin_valor_previo_lo_dice_en_vez_de_inventar(sin_cache, monkeypatch):
    def cae(*a, **k):
        raise RuntimeError("timeout")

    monkeypatch.setattr(datos.httpx, "get", cae)
    skus, error = obtener_publicados()
    assert skus is None
    assert "no se pudo leer" in error
