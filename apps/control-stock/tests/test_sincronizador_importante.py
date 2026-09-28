"""«Importante»: la primera señal suelta de Linear que SOLO BAJA a WhatsApp
(`waha/sincronizador.py`), igual que el estado y «🔴 Responder».

Mismo mecanismo de carga que `test_sincronizador_representante.py` (ese
archivo vive en el droplet del CRM y se copia a mano; su lógica se prueba
AQUÍ): se copia el `.py` real a una carpeta temporal con su propio `.env`
de prueba, para no tocar el `waha/` real del repo ni depender de un
`~/waha/.env` que en esta Mac no existe.
"""

import importlib.util
import shutil
import sys
from pathlib import Path

import pytest

RUTA_REAL = Path(__file__).resolve().parents[3] / "waha" / "sincronizador.py"

MARCA = "‎"  # LEFT-TO-RIGHT MARK, la que llevan las etiquetas
# sugeridas de fábrica de WhatsApp Business («‎Pedido completado»).


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
        "sincronizador_prueba_importante", destino)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    sys.path[:] = camino
    return modulo


def lead(**cambios):
    base = {
        "ref": "LEAD-1", "nombre": "Cliente de prueba", "pp": "PP-TEST01",
        "estado": "Hablando", "resp": "Mary", "interes": "Plantas",
        "te_toca": False, "senales": set(), "telefono": "50760000000",
        "nombre_primero": "", "nombre_segundo": "", "cerrado": False,
    }
    base.update(cambios)
    return base


# ---------------------------------------------------------------------------
# La estructura: solo «Importante» está habilitada, y es una lista de una
# sola señal a propósito -sumar «Seguimiento»/«Cliente potencial» es una
# línea, no un rediseño-.
# ---------------------------------------------------------------------------

def test_solo_importante_esta_habilitada(sinc):
    assert sinc.SENALES_QUE_BAJAN == ("Importante",)


# ---------------------------------------------------------------------------
# (a) lead vivo con «Importante» en Linear -> el chat la lleva
# ---------------------------------------------------------------------------

def test_lead_con_importante_la_lleva(sinc):
    disponibles = {"Hablando", "Plantas", "Mary", "Importante"}
    quiere, faltan = sinc.deseadas(lead(senales={"Importante"}), disponibles)
    assert "Importante" in quiere
    assert faltan == []


def test_lead_con_importante_via_quiere_para(sinc):
    disponibles = {"Hablando", "Plantas", "Mary", "Importante"}
    un_lead = lead(senales={"Importante"})
    quiere, _faltan = sinc.quiere_para(un_lead, set(), disponibles)
    assert "Importante" in quiere


# ---------------------------------------------------------------------------
# (b) sin «Importante» en Linear -> no la lleva, y si el chat ya la tenia
#     puesta, el PUT de lista completa la saca sola (queda en `quitar`)
# ---------------------------------------------------------------------------

def test_lead_sin_importante_no_la_pide(sinc):
    disponibles = {"Hablando", "Plantas", "Mary", "Importante"}
    quiere, faltan = sinc.deseadas(lead(senales=set()), disponibles)
    assert "Importante" not in quiere
    assert faltan == []


def test_el_chat_la_pierde_si_linear_ya_no_la_tiene(sinc):
    disponibles = {"Hablando", "Plantas", "Mary", "Importante"}
    tiene = {"Hablando", "Plantas", "Mary", "Importante"}
    quiere, _faltan = sinc.quiere_para(lead(senales=set()), tiene, disponibles)
    quitar = [t for t in tiene if t not in quiere]
    assert "Importante" in quitar
    # Lo demas del chat no se toca por esto.
    assert "Hablando" not in quitar
    assert "Plantas" not in quitar
    assert "Mary" not in quitar


# ---------------------------------------------------------------------------
# (c) «Importante» todavia no existe en el catalogo de WhatsApp -> sin
#     error, con aviso (en `faltan`), el chat se queda sin ella
# ---------------------------------------------------------------------------

def test_importante_ausente_del_catalogo_no_revienta_y_avisa(sinc):
    disponibles = {"Hablando", "Plantas", "Mary"}  # sin "Importante"
    quiere, faltan = sinc.deseadas(lead(senales={"Importante"}), disponibles)
    assert "Importante" not in quiere
    assert "Importante" in faltan
    # El resto de la lista sigue viva: el hueco es solo de la senal.
    assert "Hablando" in quiere and "Plantas" in quiere and "Mary" in quiere


def test_importante_ausente_no_intenta_quitar_nada_que_no_este(sinc):
    disponibles = {"Hablando", "Plantas", "Mary"}
    tiene = {"Hablando", "Plantas", "Mary"}
    quiere, _faltan = sinc.quiere_para(
        lead(senales={"Importante"}), tiene, disponibles)
    poner = [q for q in quiere if q not in tiene]
    assert poner == []


# ---------------------------------------------------------------------------
# (d) el catalogo la devuelve con la marca invisible U+200E -> casa igual
# ---------------------------------------------------------------------------

def test_importante_con_marca_invisible_casa_igual(sinc):
    disponibles = {"Hablando", "Plantas", "Mary", MARCA + "Importante"}
    quiere, faltan = sinc.deseadas(lead(senales={"Importante"}), disponibles)
    # Se devuelve el nombre REAL del catalogo (con la marca): es el que
    # hace falta para buscar su id y para calzar con lo que el chat trae.
    assert (MARCA + "Importante") in quiere
    assert faltan == []


def test_importante_con_marca_no_se_reporta_como_faltante(sinc):
    disponibles = {MARCA + "Importante"}
    _quiere, faltan = sinc.deseadas(
        lead(estado="", interes="", resp="", senales={"Importante"}),
        disponibles)
    assert faltan == []


def test_importante_con_marca_no_se_duplica_si_el_chat_ya_la_tiene(sinc):
    disponibles = {"Hablando", "Plantas", "Mary", MARCA + "Importante"}
    tiene = {"Hablando", "Plantas", "Mary", MARCA + "Importante"}
    quiere, _faltan = sinc.quiere_para(
        lead(senales={"Importante"}), tiene, disponibles)
    poner = [q for q in quiere if q not in tiene]
    quitar = [t for t in tiene if t not in quiere]
    assert poner == [] and quitar == []


# ---------------------------------------------------------------------------
# (e) Perdido y Ganado: «Importante» no entra, aunque el chat ya la tuviera
#     puesta de cuando el lead estaba vivo. Base nueva (28/09/2026): Perdido
#     vuelve [] SIEMPRE (no mira `tiene` ni `disponibles`) y Ganado arma su
#     lista aparte ("Pedido completado" ± Mantenimiento ± Responder) sin
#     pedir señales -con no pedirlas alcanza en los dos casos-.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("tiene, disponibles", [
    (set(), set()),
    ({"Plantas", "Mary", "Importante"}, {"Plantas", "Mary", "Importante"}),
    ({"Plantas", MARCA + "Importante"}, {"Plantas", MARCA + "Importante"}),
])
def test_perdido_no_lleva_importante_aunque_ya_la_tuviera(
        sinc, tiene, disponibles):
    perdido = lead(estado="Perdido", cerrado=True, senales={"Importante"})
    quiere, faltan = sinc.quiere_para(perdido, tiene, disponibles)
    # Perdido queda LIMPIO del todo -no solo sin Importante-, sea cual sea
    # lo que el chat traiga puesto.
    assert quiere == []
    assert faltan == []


def test_ganado_no_lleva_importante_aunque_ya_la_tuviera(sinc):
    disponibles = {sinc.PEDIDO_COMPLETADO, "Plantas", "Mary", "Importante"}
    tiene = {"Plantas", "Mary", "Importante"}
    ganado = lead(estado="Ganado", cerrado=True, senales={"Importante"})
    quiere, faltan = sinc.quiere_para(ganado, tiene, disponibles)
    assert "Importante" not in quiere
    assert quiere == [sinc.PEDIDO_COMPLETADO]
    assert faltan == []


def test_ganado_con_mantenimiento_y_te_toca_tampoco_lleva_importante(sinc):
    disponibles = {sinc.PEDIDO_COMPLETADO, "Mantenimiento", sinc.RESPONDER,
                   "Importante"}
    tiene = {"Importante"}
    ganado = lead(estado="Ganado", cerrado=True, interes="Mantenimiento",
                  te_toca=True, senales={"Importante"})
    quiere, _faltan = sinc.quiere_para(ganado, tiene, disponibles)
    assert "Importante" not in quiere
    assert sorted(quiere) == sorted(
        [sinc.PEDIDO_COMPLETADO, "Mantenimiento", sinc.RESPONDER])


# ---------------------------------------------------------------------------
# (f) la lectura de vuelta (lo que sube) no toca «Importante»: solo
#     "responsable" e "interes" suben, y ninguno de los dos es una señal.
# ---------------------------------------------------------------------------

def test_importante_no_es_representante_ni_interes(sinc):
    # Si "Importante" alguna vez apareciera en estas listas, la lectura de
    # vuelta de main() -que solo recorre REPRESENTANTES e INTERESES- la
    # trataria como algo que SUBE, rompiendo la regla de que solo baja.
    assert "Importante" not in sinc.REPRESENTANTES
    assert "Importante" not in sinc.INTERESES


def test_leads_del_crm_solo_marca_senales_habilitadas(sinc, monkeypatch):
    # `leads_del_crm()` construye "senales" a partir de SENALES_QUE_BAJAN;
    # una etiqueta suelta que no este en esa lista (p.ej. "Seguimiento",
    # todavia apagada) no se cuela aunque el issue la tenga puesta.
    nodo = {
        "identifier": "LEAD-9", "title": "Cliente (PP-TEST09)",
        "description": "",
        "state": {"name": "Hablando", "type": "started"},
        "labels": {"nodes": [
            {"name": "Hablando", "parent": None},
            {"name": "Importante", "parent": None},
            {"name": "Seguimiento", "parent": None},
        ]},
    }
    monkeypatch.setattr(sinc, "linear",
                         lambda *a, **k: {"issues": {"nodes": [nodo]}})
    monkeypatch.setattr(sinc, "twenty", lambda *a, **k: {"data": {}})
    leads = sinc.leads_del_crm()
    assert len(leads) == 1
    assert leads[0]["senales"] == {"Importante"}
