"""El gasto de Google del día, preguntado al order-api (30/09/2026).

Lo que se cuida aquí es lo que duele si se rompe:

1. Que la puerta sea la que YA existe —`ORDER_API_URL` + `ORDER_API_KEY`, con
   el header `X-API-Key`— y no una variable nueva: dos nombres para la misma
   puerta ya apagaron el enganche de WhatsApp una vez.
2. Que cuando no se puede saber, se LANCE. El renglón queda en blanco y lo
   dice, nunca en «0 consultas» — que en este renglón sería la mentira más
   cómoda de todas, porque un cero parece «no se gastó nada».
3. Que el día que se pide sea el día del resumen: el corte del tope diario lo
   decide el order-api, y las dos apps tienen que hablar del mismo día.
4. Que el tope alcanzado grite y un día normal no.

Ninguna prueba sale a la red: `armar()` es pura y `leer()` se prueba con una
puerta falsa.
"""

import pytest

from app import mapas


@pytest.fixture(autouse=True)
def puente_puesto(monkeypatch):
    monkeypatch.setenv("ORDER_API_URL", "http://order-api-order-api-1:8083")
    monkeypatch.setenv("ORDER_API_KEY", "no-importa-el-valor")


def crudo(**cambios):
    """Lo que contesta el order-api en un día normal."""
    base = {"consultas_google": 42, "respaldo_corregimiento": 3,
            "tope_alcanzado": False, "tope": 300}
    base.update(cambios)
    return base


class Respuesta:
    def __init__(self, cuerpo=None, status_code=200):
        self.status_code = status_code
        self._cuerpo = cuerpo if cuerpo is not None else crudo()

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._cuerpo


# ---------------------------------------------------------------------------
# El puente: la misma puerta del order-api, sin variable nueva
# ---------------------------------------------------------------------------

def test_hace_falta_la_url_Y_la_clave(monkeypatch):
    assert mapas.configurado() is True
    monkeypatch.delenv("ORDER_API_KEY")
    assert mapas.configurado() is False
    monkeypatch.setenv("ORDER_API_KEY", "x")
    monkeypatch.delenv("ORDER_API_URL")
    assert mapas.configurado() is False


def test_leer_pega_en_la_ruta_del_order_api_con_su_clave_y_el_dia(monkeypatch):
    visto = {}

    def falso_get(url, params=None, headers=None, timeout=None):
        visto.update({"url": url, "params": params or {},
                      "headers": headers or {}})
        return Respuesta()

    monkeypatch.setattr(mapas.httpx, "get", falso_get)
    assert mapas.leer("2026-09-30")["consultas_google"] == 42
    assert visto["url"] == ("http://order-api-order-api-1:8083"
                            "/api/admin/metricas-envio")
    # El día viaja en la query, no en la ruta: el corte del tope lo decide el
    # order-api y las dos apps tienen que hablar del MISMO día.
    assert visto["params"] == {"dia": "2026-09-30"}
    # La clave va en el header, nunca en la query: una clave en la URL queda
    # escrita en cualquier log intermedio.
    assert visto["headers"] == {"X-API-Key": "no-importa-el-valor"}


def test_una_url_con_barra_al_final_no_duplica_la_barra(monkeypatch):
    monkeypatch.setenv("ORDER_API_URL", "http://order-api:8083/")
    visto = {}

    monkeypatch.setattr(mapas.httpx, "get",
                        lambda url, **k: visto.update(url=url) or Respuesta())
    mapas.leer("2026-09-30")
    assert visto["url"] == "http://order-api:8083/api/admin/metricas-envio"


def test_un_404_se_explica_como_lo_que_es(monkeypatch):
    """El error esperado mientras las tarifas de envío no estén desplegadas.
    El crudo de httpx no lo explicaría, y esto se va a leer en un log."""
    monkeypatch.setattr(mapas.httpx, "get",
                        lambda url, **k: Respuesta(status_code=404))
    with pytest.raises(RuntimeError, match="todavía no tiene la ruta"):
        mapas.leer("2026-09-30")


def test_un_401_dice_que_la_clave_es_la_que_esta_mal(monkeypatch):
    monkeypatch.setattr(mapas.httpx, "get",
                        lambda url, **k: Respuesta(status_code=401))
    with pytest.raises(RuntimeError, match="ORDER_API_KEY"):
        mapas.leer("2026-09-30")


def test_cualquier_otro_error_sube_tal_cual(monkeypatch):
    monkeypatch.setattr(mapas.httpx, "get",
                        lambda url, **k: Respuesta(status_code=500))
    with pytest.raises(RuntimeError, match="HTTP 500"):
        mapas.leer("2026-09-30")


# ---------------------------------------------------------------------------
# La regla: nada se inventa, y un cero NO es «no sé»
# ---------------------------------------------------------------------------

def test_un_dia_normal_cuenta_las_dos_cosas_y_no_gasta_titular():
    bloque = mapas.armar(crudo())
    assert bloque["consultas"] == 42
    assert bloque["respaldo"] == 3
    assert bloque["alerta"] is False
    assert bloque["corto"] == "", "sano no se nombra en el titular"
    assert bloque["frase"] == ("Mapas: 42 consultas a Google hoy, 3 veces se "
                               "usó el tiempo de respaldo del corregimiento. "
                               "El tope del día son 300.")


def test_sin_respaldo_no_se_nombra_el_respaldo():
    frase = mapas.armar(crudo(respaldo_corregimiento=0))["frase"]
    assert frase == "Mapas: 42 consultas a Google hoy. El tope del día son 300."


def test_una_sola_vez_se_dice_en_singular():
    frase = mapas.armar(crudo(consultas_google=1,
                              respaldo_corregimiento=1))["frase"]
    assert "1 consulta a Google hoy" in frase
    assert "1 vez se usó" in frase


def test_un_dia_sin_consultas_se_escribe_cero_sin_adornarlo():
    """Un cero de verdad no es una falla ni se explica: el renglón cuenta lo
    que pasó, no por qué. Y es distinto de no saber, que ni llega hasta aquí."""
    bloque = mapas.armar(crudo(consultas_google=0, respaldo_corregimiento=0))
    assert bloque["alerta"] is False
    assert bloque["frase"] == "Mapas: 0 consultas a Google hoy. El tope del día son 300."


def test_sin_los_numeros_no_hay_renglon_se_lanza():
    """Rellenar con ceros sería inventar la mejor noticia posible («no se
    gastó nada») a partir de no saber nada."""
    with pytest.raises(RuntimeError, match="no mandó las cuentas del día"):
        mapas.armar({"tope": 300})
    with pytest.raises(RuntimeError, match="no mandó las cuentas del día"):
        mapas.armar({"consultas_google": 42})     # falta el respaldo
    with pytest.raises(RuntimeError, match="no mandó las cuentas del día"):
        mapas.armar({"consultas_google": "muchas", "respaldo_corregimiento": 3})


def test_un_cero_de_verdad_si_arma_renglon():
    """El contrapunto de la prueba anterior: 0 es un número, y no se confunde
    con la ausencia."""
    assert mapas.armar({"consultas_google": 0,
                        "respaldo_corregimiento": 0})["consultas"] == 0


# ---------------------------------------------------------------------------
# El tope: el caso que de otro modo pasa desapercibido
# ---------------------------------------------------------------------------

def test_el_tope_alcanzado_grita_y_entra_al_titular():
    bloque = mapas.armar(crudo(consultas_google=300, respaldo_corregimiento=47,
                               tope_alcanzado=True))
    assert bloque["alerta"] is True
    assert bloque["corto"] == "tope de Google"
    assert bloque["frase"].startswith("⚠️ Se alcanzó el tope diario de 300 "
                                      "consultas a Google;")
    # Los tres números que pidió el dueño siguen estando en el caso de alarma:
    # las consultas van en el título del renglón, el tope y las veces del
    # respaldo aquí. Es el número que dice cuánto del día se cobró aproximado.
    assert "tiempo de respaldo del corregimiento (47 veces)" in bloque["frase"]


def test_el_aviso_del_tope_sin_respaldo_no_pone_un_parentesis_vacio():
    frase = mapas.armar(crudo(consultas_google=300, respaldo_corregimiento=0,
                              tope_alcanzado=True))["frase"]
    assert "del corregimiento." in frase
    assert "()" not in frase


def test_el_tope_lo_manda_el_order_api_no_se_deduce_del_numero():
    """Si el order-api dice que NO se alcanzó, no se alcanzó — aunque las
    cuentas parezcan decir otra cosa. El que sabe del corte es él."""
    bloque = mapas.armar(crudo(consultas_google=300, tope=300,
                               tope_alcanzado=False))
    assert bloque["alerta"] is False


def test_sin_la_bandera_se_deduce_del_tope_que_si_mando():
    """No es un invento: es aritmética sobre lo que sí mandó."""
    sin_bandera = {"consultas_google": 300, "respaldo_corregimiento": 12,
                   "tope": 300}
    assert mapas.armar(sin_bandera)["alerta"] is True
    assert mapas.armar(dict(sin_bandera, consultas_google=299))["alerta"] is False


def test_sin_bandera_y_sin_tope_no_se_grita():
    """No hay con qué comparar. Un grito sin fundamento es peor que callarse:
    el dueño aprendería a ignorar el aviso."""
    bloque = mapas.armar({"consultas_google": 900, "respaldo_corregimiento": 0})
    assert bloque["alerta"] is False
    assert bloque["tope"] is None
    assert "El tope del día" not in bloque["frase"], "no se nombra lo que no se sabe"


def test_sin_tope_el_aviso_sigue_siendo_legible():
    frase = mapas.armar({"consultas_google": 300, "respaldo_corregimiento": 8,
                         "tope_alcanzado": True})["frase"]
    assert frase.startswith("⚠️ Se alcanzó el tope diario de consultas a "
                            "Google;")


def test_bloque_pega_leer_con_armar(monkeypatch):
    monkeypatch.setattr(mapas.httpx, "get", lambda url, **k: Respuesta())
    assert mapas.bloque("2026-09-30")["consultas"] == 42
