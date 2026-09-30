"""Las cinco señales sueltas que SOLO BAJAN de Linear a WhatsApp
(`waha/sincronizador.py`, 28/09/2026; «Llamar» sumada el 30/09/2026):
«Importante», «Seguimiento», «Cliente potencial», «Entrega pendiente» y
«Llamar».

`test_sincronizador_importante.py` ya cubre a fondo el mecanismo con
«Importante» como ejemplo (vivo/cerrado/ausente/marca). Este archivo cubre
lo que agrega esta tanda: que las CINCO están habilitadas, y que
«Seguimiento» y «Cliente potencial» -las dos sugeridas de fábrica, que
llegan con el U+200E- y «Entrega pendiente» -creada a mano, sin marca-
casan igual con el mismo mecanismo de `deseadas()`.

Mismo mecanismo de carga que los otros `test_sincronizador_*`: se copia el
`.py` real a una carpeta temporal con su propio `.env` de prueba.
"""

import importlib.util
import shutil
import sys
from pathlib import Path

import pytest

RUTA_REAL = Path(__file__).resolve().parents[3] / "waha" / "sincronizador.py"

MARCA = "‎"  # LEFT-TO-RIGHT MARK


@pytest.fixture
def sinc(tmp_path):
    if not RUTA_REAL.exists():
        pytest.skip("este checkout no trae la carpeta waha/")
    destino = tmp_path / "sincronizador.py"
    shutil.copy(RUTA_REAL, destino)
    (tmp_path / ".env").write_text(
        "WAHA_API_KEY=clave-de-prueba\nLINEAR_API_KEY=clave-de-prueba\n")
    camino = list(sys.path)
    spec = importlib.util.spec_from_file_location(
        "sincronizador_prueba_senales", destino)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    sys.path[:] = camino
    return modulo


def lead(**cambios):
    base = {
        "ref": "LEAD-1", "nombre": "Cliente de prueba", "pp": "PP-TEST01",
        "estado": "Agendado", "resp": "Mary", "interes": "Plantas",
        "te_toca": False, "senales": set(), "telefono": "50760000000",
        "nombre_primero": "", "nombre_segundo": "", "cerrado": False,
    }
    base.update(cambios)
    return base


def test_las_cinco_estan_habilitadas(sinc):
    assert sinc.SENALES_QUE_BAJAN == (
        "Importante", "Seguimiento", "Cliente potencial", "Entrega pendiente",
        "Llamar")


@pytest.mark.parametrize("senal", [
    "Importante", "Seguimiento", "Cliente potencial", "Entrega pendiente",
    "Llamar"])
def test_cada_senal_baja_sin_marca_cuando_el_catalogo_no_la_tiene_marcada(
        sinc, senal):
    disponibles = {"Agendado", "Plantas", "Mary", senal}
    quiere, faltan = sinc.deseadas(lead(senales={senal}), disponibles)
    assert senal in quiere
    assert faltan == []


@pytest.mark.parametrize("senal", [
    "Importante", "Seguimiento", "Cliente potencial", "Entrega pendiente",
    "Llamar"])
def test_cada_senal_casa_igual_si_el_catalogo_la_trae_con_marca(sinc, senal):
    # No hace falta que la señal sea de verdad una sugerida de fábrica para
    # probar la tolerancia: el mecanismo es genérico y no le importa cuál
    # de las cuatro es -eso lo decide WhatsApp, no este código-.
    disponibles = {"Agendado", "Plantas", "Mary", MARCA + senal}
    quiere, faltan = sinc.deseadas(lead(senales={senal}), disponibles)
    assert (MARCA + senal) in quiere
    assert faltan == []


@pytest.mark.parametrize("senal", [
    "Importante", "Seguimiento", "Cliente potencial", "Entrega pendiente",
    "Llamar"])
def test_cada_senal_ausente_del_catalogo_avisa_sin_reventar(sinc, senal):
    disponibles = {"Agendado", "Plantas", "Mary"}
    quiere, faltan = sinc.deseadas(lead(senales={senal}), disponibles)
    assert senal not in quiere
    assert senal in faltan


def test_las_cinco_juntas_bajan_a_la_vez(sinc):
    todas = {"Importante", "Seguimiento", "Cliente potencial",
             "Entrega pendiente", "Llamar"}
    disponibles = {"Agendado", "Plantas", "Mary"} | todas
    quiere, faltan = sinc.deseadas(lead(senales=todas), disponibles)
    assert todas <= set(quiere)
    assert faltan == []


@pytest.mark.parametrize("senal", [
    "Importante", "Seguimiento", "Cliente potencial", "Entrega pendiente",
    "Llamar"])
def test_ninguna_senal_entra_a_un_perdido(sinc, senal):
    disponibles = {"Plantas", "Mary", senal}
    tiene = {"Plantas", "Mary", senal}
    perdido = lead(estado="Perdido", cerrado=True, senales={senal})
    quiere, faltan = sinc.quiere_para(perdido, tiene, disponibles)
    assert quiere == []
    assert faltan == []


@pytest.mark.parametrize("senal", [
    "Importante", "Seguimiento", "Cliente potencial", "Entrega pendiente",
    "Llamar"])
def test_ninguna_senal_entra_a_un_ganado(sinc, senal):
    disponibles = {sinc.PEDIDO_COMPLETADO, "Plantas", "Mary", senal}
    tiene = {"Plantas", "Mary", senal}
    ganado = lead(estado="Ganado", cerrado=True, senales={senal})
    quiere, _faltan = sinc.quiere_para(ganado, tiene, disponibles)
    assert senal not in quiere
