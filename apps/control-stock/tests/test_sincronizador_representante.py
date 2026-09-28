"""`ETIQUETAS_REPRESENTANTE`: el interruptor para apagar por un rato los
nombres de empleados en WhatsApp (`waha/sincronizador.py`).

Ese archivo vive en el droplet del CRM y se copia a mano, pero su lógica se
prueba AQUÍ, igual que `waha/endpoint.py` en `test_waha_endpoint.py` — es el
único lugar donde alguien la corre antes de instalarla.

**Por qué la carga es distinta a la de `endpoint.py`.** Al importar el
sincronizador corre `ENV = leer_env()`, que abre un `.env` en el MISMO
directorio del archivo (`RUTA = dirname(__file__)`). En esta Mac no hay
`~/waha/.env`, así que en vez de tocar `leer_env()` (regla del encargo: no
cambiarla) se copia el `.py` a una carpeta temporal con su propio `.env` de
prueba — el `waha/` real del repo no se toca.
"""

import importlib.util
import shutil
import sys
from pathlib import Path

import pytest

RUTA_REAL = Path(__file__).resolve().parents[3] / "waha" / "sincronizador.py"


@pytest.fixture
def sinc(tmp_path):
    """El sincronizador real, cargado desde una copia con su propio `.env`."""
    if not RUTA_REAL.exists():
        pytest.skip("este checkout no trae la carpeta waha/")
    destino = tmp_path / "sincronizador.py"
    shutil.copy(RUTA_REAL, destino)
    (tmp_path / ".env").write_text(
        "WAHA_API_KEY=clave-de-prueba\nLINEAR_API_KEY=clave-de-prueba\n")
    camino = list(sys.path)
    spec = importlib.util.spec_from_file_location(
        "sincronizador_prueba_representante", destino)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    sys.path[:] = camino
    return modulo


def lead(**cambios):
    base = {
        "ref": "LEAD-1", "nombre": "Cliente de prueba", "pp": "PP-TEST01",
        "estado": "Hablando", "resp": "Mary", "interes": "Plantas",
        "te_toca": False, "telefono": "50760000000",
        "nombre_primero": "", "nombre_segundo": "", "cerrado": False,
    }
    base.update(cambios)
    return base


# ---------------------------------------------------------------------------
# (a) representantes_activos()
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("valor", ["off", "OFF", "0", "no", "No", " off "])
def test_apagado_con_off_0_no(sinc, monkeypatch, valor):
    monkeypatch.setitem(sinc.ENV, "ETIQUETAS_REPRESENTANTE", valor)
    assert sinc.representantes_activos() is False


@pytest.mark.parametrize("valor", ["on", "ON", "1", "si", "cualquier-cosa"])
def test_prendido_con_on_y_cualquier_otro_valor(sinc, monkeypatch, valor):
    monkeypatch.setitem(sinc.ENV, "ETIQUETAS_REPRESENTANTE", valor)
    assert sinc.representantes_activos() is True


def test_prendido_por_ausencia(sinc, monkeypatch):
    monkeypatch.delitem(sinc.ENV, "ETIQUETAS_REPRESENTANTE", raising=False)
    assert sinc.representantes_activos() is True


# ---------------------------------------------------------------------------
# (b) deseadas() con `resp` puesto
# ---------------------------------------------------------------------------

def test_deseadas_incluye_el_representante_prendido(sinc, monkeypatch):
    monkeypatch.delitem(sinc.ENV, "ETIQUETAS_REPRESENTANTE", raising=False)
    quiere, faltan = sinc.deseadas(lead(), {"Hablando", "Plantas", "Mary"})
    assert "Mary" in quiere


def test_deseadas_no_incluye_el_representante_apagado(sinc, monkeypatch):
    monkeypatch.setitem(sinc.ENV, "ETIQUETAS_REPRESENTANTE", "off")
    quiere, faltan = sinc.deseadas(lead(), {"Hablando", "Plantas", "Mary"})
    assert "Mary" not in quiere
    # El estado y el interes siguen igual: el interruptor es solo del
    # representante.
    assert "Hablando" in quiere and "Plantas" in quiere


# ---------------------------------------------------------------------------
# (c) apagado: un lead vivo con el nombre puesto termina en `quitar`
# ---------------------------------------------------------------------------

def test_apagado_saca_el_nombre_de_un_chat_que_ya_lo_tenia(sinc, monkeypatch):
    monkeypatch.setitem(sinc.ENV, "ETIQUETAS_REPRESENTANTE", "off")
    disponibles = {"Hablando", "Plantas", "Mary"}
    tiene = {"Hablando", "Plantas", "Mary"}
    quiere, _faltan = sinc.quiere_para(lead(), tiene, disponibles)
    quitar = [t for t in tiene if t not in quiere]
    assert "Mary" in quitar
    assert "Hablando" not in quitar and "Plantas" not in quitar


def test_prendido_no_quita_el_nombre_del_chat(sinc, monkeypatch):
    monkeypatch.delitem(sinc.ENV, "ETIQUETAS_REPRESENTANTE", raising=False)
    disponibles = {"Hablando", "Plantas", "Mary"}
    tiene = {"Hablando", "Plantas", "Mary"}
    quiere, _faltan = sinc.quiere_para(lead(), tiene, disponibles)
    quitar = [t for t in tiene if t not in quiere]
    assert "Mary" not in quitar


# ---------------------------------------------------------------------------
# (d) Perdido y Ganado: el interruptor no cambia nada de esos caminos
# ---------------------------------------------------------------------------

def test_perdido_igual_prendido_o_apagado(sinc, monkeypatch):
    disponibles = {"Hablando", "Plantas", "Mary", sinc.PEDIDO_COMPLETADO}
    tiene = {"Plantas", "Mary"}
    perdido = lead(estado="Perdido", cerrado=True)

    monkeypatch.delitem(sinc.ENV, "ETIQUETAS_REPRESENTANTE", raising=False)
    quiere_on, faltan_on = sinc.quiere_para(perdido, tiene, disponibles)

    monkeypatch.setitem(sinc.ENV, "ETIQUETAS_REPRESENTANTE", "off")
    quiere_off, faltan_off = sinc.quiere_para(perdido, tiene, disponibles)

    assert quiere_on == quiere_off == []
    assert faltan_on == faltan_off == []


def test_ganado_igual_prendido_o_apagado(sinc, monkeypatch):
    disponibles = {"Hablando", "Plantas", "Mary", sinc.PEDIDO_COMPLETADO}
    tiene = {"Plantas", "Mary"}
    ganado = lead(estado="Ganado", cerrado=True)

    monkeypatch.delitem(sinc.ENV, "ETIQUETAS_REPRESENTANTE", raising=False)
    quiere_on, faltan_on = sinc.quiere_para(ganado, tiene, disponibles)

    monkeypatch.setitem(sinc.ENV, "ETIQUETAS_REPRESENTANTE", "off")
    quiere_off, faltan_off = sinc.quiere_para(ganado, tiene, disponibles)

    assert quiere_on == quiere_off == [sinc.PEDIDO_COMPLETADO]
    assert faltan_on == faltan_off == []


def test_ganado_con_mantenimiento_igual_prendido_o_apagado(sinc, monkeypatch):
    disponibles = {"Mantenimiento", sinc.PEDIDO_COMPLETADO}
    tiene = {"Mantenimiento"}
    ganado = lead(estado="Ganado", cerrado=True, interes="Mantenimiento")

    monkeypatch.delitem(sinc.ENV, "ETIQUETAS_REPRESENTANTE", raising=False)
    quiere_on, _f = sinc.quiere_para(ganado, tiene, disponibles)

    monkeypatch.setitem(sinc.ENV, "ETIQUETAS_REPRESENTANTE", "off")
    quiere_off, _f = sinc.quiere_para(ganado, tiene, disponibles)

    assert sorted(quiere_on) == sorted(quiere_off) == \
        sorted(["Mantenimiento", sinc.PEDIDO_COMPLETADO])


# ---------------------------------------------------------------------------
# (e) apagado no cambia interes, estado ni Responder en la lista deseada
# ---------------------------------------------------------------------------

def test_apagado_no_toca_interes_estado_ni_responder(sinc, monkeypatch):
    disponibles = {"Cotizado", "Eventos", "Mary", sinc.RESPONDER}
    un_lead = lead(estado="Cotizado", interes="Eventos", resp="Mary",
                    te_toca=True)

    monkeypatch.delitem(sinc.ENV, "ETIQUETAS_REPRESENTANTE", raising=False)
    quiere_on, faltan_on = sinc.deseadas(un_lead, disponibles)

    monkeypatch.setitem(sinc.ENV, "ETIQUETAS_REPRESENTANTE", "off")
    quiere_off, faltan_off = sinc.deseadas(un_lead, disponibles)

    # Solo el representante cambia; el resto de la lista es identica.
    assert set(quiere_on) - set(quiere_off) == {"Mary"}
    assert "Cotizado" in quiere_off
    assert "Eventos" in quiere_off
    assert sinc.RESPONDER in quiere_off
    assert faltan_on == faltan_off == []


# ---------------------------------------------------------------------------
# El interruptor mismo no revienta si `.env` no trae la variable
# ---------------------------------------------------------------------------

def test_el_modulo_carga_sin_la_variable_en_el_env(sinc):
    # El fixture ya cargo el modulo con un .env sin ETIQUETAS_REPRESENTANTE:
    # si esto no revento, el interruptor tolera su ausencia (el caso real de
    # cualquier .env de hoy, que todavia no la tiene).
    assert sinc.representantes_activos() is True
