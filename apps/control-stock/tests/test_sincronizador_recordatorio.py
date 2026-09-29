"""El estado «Recordatorio» en el sincronizador de WhatsApp (29/09/2026):
el cliente espera que LLEGUE un producto — su chat lleva la etiqueta de
estado como cualquier otro estado vivo. La etiqueta del teléfono la crea
Abraham (regla 3: el código nunca crea etiquetas); mientras no exista,
sale en los «faltan» del reporte y la pasada sigue. Y si al crearla trae
el U+200E invisible (la trampa de las sugeridas de WhatsApp Business),
`_nombre_en_catalogo` la tolera igual.

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
        "sincronizador_prueba_recordatorio", destino)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    sys.path[:] = camino
    return modulo


def lead(**cambios):
    base = {
        "ref": "LEAD-1", "nombre": "Cliente de prueba", "pp": "PP-TEST01",
        "estado": "Recordatorio", "resp": "", "interes": "Plantas",
        "te_toca": False, "senales": set(), "telefono": "50760000000",
        "nombre_primero": "", "nombre_segundo": "", "cerrado": False,
    }
    base.update(cambios)
    return base


def test_recordatorio_es_un_estado_con_etiqueta(sinc):
    assert "Recordatorio" in sinc.ESTADOS_CON_ETIQUETA
    # Y los seis de siempre siguen: siete estados con etiqueta en total
    # (Ganado y Perdido siguen fuera, un cerrado no anuncia estado).
    assert len(sinc.ESTADOS_CON_ETIQUETA) == 7
    assert "Ganado" not in sinc.ESTADOS_CON_ETIQUETA
    assert "Perdido" not in sinc.ESTADOS_CON_ETIQUETA


def test_la_etiqueta_baja_cuando_existe_en_el_telefono(sinc):
    disponibles = {"Recordatorio", "Plantas"}
    quiere, faltan = sinc.deseadas(lead(), disponibles)
    assert "Recordatorio" in quiere
    assert faltan == []


def test_si_abraham_no_la_creo_todavia_se_reporta_y_sigue(sinc):
    # La regla 3: el código no la crea — el chat queda sin ella y el
    # reporte dice que falta, sin reventar la pasada.
    disponibles = {"Plantas"}
    quiere, faltan = sinc.deseadas(lead(), disponibles)
    assert "Recordatorio" not in quiere
    assert "Recordatorio" in faltan
    assert "Plantas" in quiere               # lo demás baja igual


def test_casa_aunque_el_catalogo_la_traiga_con_la_marca_invisible(sinc):
    # La trampa conocida: una etiqueta creada desde las sugeridas de
    # WhatsApp trae U+200E al inicio y el nombre «a ojo» es idéntico.
    disponibles = {MARCA + "Recordatorio", "Plantas"}
    quiere, faltan = sinc.deseadas(lead(), disponibles)
    assert (MARCA + "Recordatorio") in quiere   # el nombre TAL CUAL del catálogo
    assert faltan == []


def test_al_salir_de_recordatorio_la_etiqueta_se_va_sola(sinc):
    # El PUT manda la lista completa: un lead que volvió a Hablando pide
    # «Hablando» y ya no pide «Recordatorio» — no hace falta camino de
    # quitar aparte.
    disponibles = {"Recordatorio", "Hablando", "Plantas", "🔴 Responder"}
    quiere, faltan = sinc.deseadas(lead(estado="Hablando", te_toca=True),
                                   disponibles)
    assert "Recordatorio" not in quiere
    assert "Hablando" in quiere and "🔴 Responder" in quiere
    assert faltan == []
