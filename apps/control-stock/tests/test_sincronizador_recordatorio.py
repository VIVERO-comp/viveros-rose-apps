"""El estado «Recordatorio» en el sincronizador de WhatsApp (29/09/2026):
el cliente espera que LLEGUE un producto — su chat lleva la etiqueta de
estado como cualquier otro estado vivo.

La etiqueta del teléfono la creó Abraham como «Recordar» (30/09/2026), y
lo que él puso a mano GANA: el código se adapta a su nombre vía
`ETIQUETA_DE_ESTADO` / `etiqueta_de_estado()`. El estado en Linear/Twenty
sigue llamándose «Recordatorio» — la traducción es SOLO del lado WhatsApp.
Y si el catálogo trajera el U+200E invisible (la trampa de las sugeridas
de WhatsApp Business), `_nombre_en_catalogo` lo tolera igual.

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


def test_en_whatsapp_el_estado_se_llama_recordar(sinc):
    # El nombre que Abraham escribió a mano en el teléfono manda: el
    # traductor lleva Recordatorio -> Recordar y deja los demás tal cual.
    assert sinc.etiqueta_de_estado("Recordatorio") == "Recordar"
    assert sinc.etiqueta_de_estado("Hablando") == "Hablando"
    assert "Recordar" in sinc.ETIQUETAS_ESTADO_WHATSAPP
    # Nada del lado WhatsApp espera una etiqueta llamada «Recordatorio».
    assert "Recordatorio" not in sinc.ETIQUETAS_ESTADO_WHATSAPP
    assert "Recordatorio" not in sinc.ETIQUETA_DE_ESTADO.values()


def test_la_etiqueta_baja_cuando_existe_en_el_telefono(sinc):
    disponibles = {"Recordar", "Plantas"}
    quiere, faltan = sinc.deseadas(lead(), disponibles)
    assert "Recordar" in quiere
    assert "Recordatorio" not in quiere
    assert faltan == []


def test_si_abraham_no_la_creo_todavia_se_reporta_y_sigue(sinc):
    # La regla 3: el código no la crea — el chat queda sin ella y el
    # reporte dice que falta, CON el nombre del teléfono («Recordar»),
    # sin reventar la pasada.
    disponibles = {"Plantas"}
    quiere, faltan = sinc.deseadas(lead(), disponibles)
    assert "Recordar" not in quiere
    assert "Recordar" in faltan
    assert "Recordatorio" not in faltan      # el nombre viejo no vuelve
    assert "Plantas" in quiere               # lo demás baja igual


def test_una_etiqueta_recordatorio_del_catalogo_ya_no_calza(sinc):
    # El código dejó de esperar «Recordatorio» como etiqueta de WhatsApp:
    # aunque existiera una con ese nombre, lo que se pide es «Recordar».
    disponibles = {"Recordatorio", "Plantas"}
    quiere, faltan = sinc.deseadas(lead(), disponibles)
    assert "Recordatorio" not in quiere
    assert "Recordar" in faltan


def test_casa_aunque_el_catalogo_la_traiga_con_la_marca_invisible(sinc):
    # La trampa conocida: una etiqueta creada desde las sugeridas de
    # WhatsApp trae U+200E al inicio y el nombre «a ojo» es idéntico.
    disponibles = {MARCA + "Recordar", "Plantas"}
    quiere, faltan = sinc.deseadas(lead(), disponibles)
    assert (MARCA + "Recordar") in quiere   # el nombre TAL CUAL del catálogo
    assert faltan == []


def test_al_salir_de_recordatorio_la_etiqueta_se_va_sola(sinc):
    # El PUT manda la lista completa: un lead que volvió a Hablando pide
    # «Hablando» y ya no pide «Recordar» — no hace falta camino de
    # quitar aparte.
    disponibles = {"Recordar", "Hablando", "Plantas", "🔴 Responder"}
    quiere, faltan = sinc.deseadas(lead(estado="Hablando", te_toca=True),
                                   disponibles)
    assert "Recordar" not in quiere
    assert "Hablando" in quiere and "🔴 Responder" in quiere
    assert faltan == []


def test_la_resta_de_un_cerrado_quita_recordar_del_chat(sinc):
    # La red de seguridad de un cerrado que no calza con Perdido/Ganado
    # compara contra lo que el CHAT tiene puesto, así que resta el nombre
    # de WhatsApp («Recordar»), no el del estado de Linear.
    tiene = {"Recordar", "Plantas"}
    cerrado = lead(estado="Cancelado", cerrado=True)
    quiere, _faltan = sinc.quiere_para(cerrado, tiene, {"Plantas"})
    assert "Recordar" not in quiere
    assert "Plantas" in quiere
