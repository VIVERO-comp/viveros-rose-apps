"""El lado del droplet del CRM: `waha/endpoint.py` (25/09/2026).

Ese archivo vive en el otro droplet y se copia a mano, pero su lógica se
prueba AQUÍ, con la suite que sí se corre: es el único lugar donde alguien la
va a ejecutar antes de instalarla.

**Por qué existe este archivo.** La primera versión calculaba la edad de la
última corrida parseando la fecha escrita en el diario. El host del droplet
del CRM corre en **UTC** y es él quien escribe ese texto por cron; el proceso
que lo lee corre con `TZ=America/Panama`. Resultado: la edad salía **−4
horas**, nunca pasaba de las 3, y la detección de «el limpiador murió» —la
red de seguridad entera de este renglón— quedaba apagada **sin un solo error
en ningún log**. Ahora la edad sale del `mtime` del archivo, que es un epoch
absoluto. Estas pruebas están para que nadie vuelva al parseo en seis meses.
"""

import importlib.util
import os
import sys
import time
from pathlib import Path

import pytest

RUTA = Path(__file__).resolve().parents[3] / "waha" / "endpoint.py"

# Un texto de fecha en UTC, +5 horas sobre la hora de Panamá: la trampa
# exacta. Y otro absurdo, de 2020. Ninguno de los dos debe influir en nada.
LINEA_UTC = "2026-09-25 20:17:04 · almacen 2 MB -> 2 MB · quedan 58 mensajes"
LINEA_2020 = "2020-01-01 00:00:00 · almacen 2 MB -> 2 MB · quedan 58 mensajes"
ALERTA = ("2026-09-25 20:17:04 · OJO: el almacen sigue en 68 MB "
          "(lo sano es ~2). Revisar los limites de historial.")


@pytest.fixture
def endpoint(monkeypatch, tmp_path):
    """El módulo del droplet, cargado por ruta con su `sincronizador` falso."""
    if not RUTA.exists():
        pytest.skip("este checkout no trae la carpeta waha/")

    class Falso:
        ENV = {"SINCRO_SECRET": "el-secreto-de-prueba"}

        @staticmethod
        def sincronizar_uno(ref, aplicar=False):
            return {}

    monkeypatch.setitem(sys.modules, "sincronizador", Falso)
    camino = list(sys.path)
    spec = importlib.util.spec_from_file_location("waha_endpoint", RUTA)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    sys.path[:] = camino          # el módulo se mete `/waha` en el path
    # El diario y el almacén, en la carpeta de la prueba.
    monkeypatch.setattr(modulo, "DIARIO", str(tmp_path / "almacen.log"))
    monkeypatch.setattr(modulo, "ALMACEN", str(tmp_path / ".sessions"))
    return modulo


def diario(endpoint, texto, hace_horas=0.0):
    """Escribe el diario y le pone el `mtime` que se quiera."""
    ruta = endpoint.DIARIO
    with open(ruta, "w", encoding="utf-8") as archivo:
        archivo.write(texto if texto.endswith("\n") else texto + "\n")
    cuando = time.time() - hace_horas * 3600
    os.utime(ruta, (cuando, cuando))
    return ruta


# ---------------------------------------------------------------------------
# LA prueba: la edad sale del mtime, no del texto
# ---------------------------------------------------------------------------

def test_la_edad_sale_del_mtime_y_no_del_texto_de_la_linea(endpoint):
    """El texto está en UTC —cinco horas adelante del reloj de este proceso— y
    la edad tiene que salir igual correcta y POSITIVA. Parseando el texto daba
    −4 horas, y con eso el limpiador muerto no se detectaba nunca."""
    diario(endpoint, LINEA_UTC, hace_horas=0.5)
    leido = endpoint._ultima_corrida()
    assert leido["edad_horas"] == pytest.approx(0.5, abs=0.05)
    assert leido["edad_horas"] > 0


def test_el_texto_de_la_linea_no_influye_en_la_edad(endpoint):
    """Dos fechas escritas absurdamente distintas, el mismo `mtime`: la misma
    edad. Si alguien volviera al parseo, esta prueba se cae sola."""
    diario(endpoint, LINEA_UTC, hace_horas=1.0)
    una = endpoint._ultima_corrida()["edad_horas"]
    diario(endpoint, LINEA_2020, hace_horas=1.0)
    otra = endpoint._ultima_corrida()["edad_horas"]
    assert una == pytest.approx(otra, abs=0.05)
    assert una == pytest.approx(1.0, abs=0.05)


def test_la_fecha_de_la_linea_no_viaja(endpoint):
    """No se manda: si viajara, alguien la usaría para mostrar la hora y el
    bug volvería por la puerta de atrás."""
    diario(endpoint, LINEA_UTC)
    leido = endpoint._ultima_corrida()
    assert "cuando" not in leido
    assert "2026-09-25 20:17:04" not in str(leido)
    assert leido["mtime"] == pytest.approx(time.time(), abs=5)


def test_un_diario_viejo_se_ve_viejo(endpoint):
    """El caso que esto vino a salvar: la línea congelada en 2 MB mientras el
    disco crece. 30 horas son 30 horas, en cualquier zona."""
    diario(endpoint, LINEA_UTC, hace_horas=30.0)
    leido = endpoint._ultima_corrida()
    assert leido["edad_horas"] == pytest.approx(30.0, abs=0.1)
    assert leido["mb"] == 2, "el número sigue ahí; lo que caducó es su fecha"


# ---------------------------------------------------------------------------
# La cola del diario: cuándo el mtime vale como fecha de la corrida
# ---------------------------------------------------------------------------

def test_la_alerta_del_limpiador_al_final_sigue_fechando_su_corrida(endpoint):
    """`limpiar_almacen.sh` escribe la alerta justo después de su corrida, en
    la misma pasada: segundos de diferencia, así que el `mtime` vale."""
    diario(endpoint, LINEA_UTC + "\n" + ALERTA, hace_horas=0.5)
    leido = endpoint._ultima_corrida()
    assert leido["cola"] == "alerta"
    assert leido["edad_horas"] == pytest.approx(0.5, abs=0.05)
    assert leido["mb"] == 2


def test_una_cola_desconocida_no_fecha_la_corrida(endpoint):
    """El `mtime` es del ARCHIVO: si alguien le escribió otra cosa al final,
    es fresco aunque la corrida sea vieja. Sin fecha que sostener, `None` —
    y el resumen lo trata como vencido. Mejor «no sé» que «todo bien»."""
    diario(endpoint, LINEA_UTC + "\nalguien escribió esto a mano", hace_horas=0)
    leido = endpoint._ultima_corrida()
    assert leido["cola"] == "desconocida"
    assert leido["edad_horas"] is None
    assert leido["mb"] == 2, "el último «después» conocido sigue sirviendo"


def test_las_alertas_sueltas_no_se_confunden_con_una_corrida(endpoint):
    diario(endpoint, ALERTA)
    leido = endpoint._ultima_corrida()
    assert leido["cola"] == "desconocida"
    assert leido["mb"] is None and leido["edad_horas"] is None


# ---------------------------------------------------------------------------
# Los números, el disco y el diario que no está
# ---------------------------------------------------------------------------

def test_lee_el_antes_el_despues_y_los_mensajes(endpoint):
    diario(endpoint, "2026-09-25 20:17:04 · almacen 71 MB -> 68 MB "
                     "· quedan 20584 mensajes")
    leido = endpoint._ultima_corrida()
    assert (leido["antes_mb"], leido["mb"], leido["mensajes"]) == (71, 68, 20584)


def test_si_sqlite_no_contesto_los_mensajes_no_se_inventan(endpoint):
    """El script escribe «quedan ? mensajes» cuando no pudo contarlos."""
    diario(endpoint, "2026-09-25 20:17:04 · almacen 5 MB -> 2 MB "
                     "· quedan ? mensajes")
    leido = endpoint._ultima_corrida()
    assert leido["mb"] == 2 and leido["mensajes"] is None


def test_toma_la_ultima_corrida_no_la_primera(endpoint):
    diario(endpoint, "2026-09-25 16:17:02 · almacen 71 MB -> 68 MB "
                     "· quedan 20584 mensajes\n"
                     "2026-09-25 17:17:03 · almacen 5 MB -> 2 MB "
                     "· quedan 132 mensajes")
    assert endpoint._ultima_corrida()["mb"] == 2


def test_sin_diario_no_hay_nada_que_leer(endpoint):
    assert endpoint._ultima_corrida() == {}


def test_el_disco_se_mide_y_sin_almacen_es_none(endpoint, tmp_path):
    assert endpoint._disco_mb() is None, "sin carpeta, no se inventa un 0"
    carpeta = tmp_path / ".sessions" / "gows" / "vivero"
    carpeta.mkdir(parents=True)
    (carpeta / "gows.db").write_bytes(b"x" * 3 * 1048576)
    assert endpoint._disco_mb() == 3


# ---------------------------------------------------------------------------
# El endpoint entero, por HTTP, como lo llama control-stock
# ---------------------------------------------------------------------------

@pytest.fixture
def servidor(endpoint, monkeypatch):
    """El endpoint de verdad, en un puerto libre de localhost."""
    import threading
    from http.server import ThreadingHTTPServer
    monkeypatch.setattr(endpoint, "SECRETO", "el-secreto-de-prueba")
    server = ThreadingHTTPServer(("127.0.0.1", 0), endpoint.Manejador)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield "http://127.0.0.1:%d" % server.server_address[1]
    server.shutdown()
    server.server_close()


def test_el_almacen_por_http_contesta_lo_que_espera_control_stock(
        endpoint, servidor):
    import httpx
    diario(endpoint, LINEA_UTC, hace_horas=0.5)
    r = httpx.get(servidor + "/almacen", timeout=5,
                  headers={"Authorization": "Bearer el-secreto-de-prueba"})
    assert r.status_code == 200
    cuerpo = r.json()
    assert cuerpo["ok"] is True
    assert cuerpo["mb"] == 2 and cuerpo["antes_mb"] == 2
    assert cuerpo["edad_horas"] == pytest.approx(0.5, abs=0.05)
    assert cuerpo["cola"] == "corrida"
    assert "cuando" not in cuerpo, "la fecha de la línea no viaja"
    # Y lo que control-stock arma con eso se sostiene.
    from app import almacen_waha
    assert almacen_waha.armar(cuerpo)["vencido"] is False


def test_el_almacen_pide_credencial(servidor):
    import httpx
    assert httpx.get(servidor + "/almacen", timeout=5).status_code == 401
    assert httpx.get(servidor + "/almacen", timeout=5,
                     headers={"Authorization": "Bearer no"}).status_code == 401


def test_las_rutas_viejas_siguen_iguales(servidor):
    import httpx
    assert httpx.get(servidor + "/salud", timeout=5).json()["ok"] is True
    assert httpx.get(servidor + "/lo-que-sea", timeout=5).status_code == 404
    assert httpx.post(servidor + "/sincro/lead", json={"lead": "LEAD-62"},
                      timeout=5).status_code == 401
    r = httpx.post(servidor + "/sincro/lead", json={"lead": "LEAD-62"},
                   timeout=5,
                   headers={"Authorization": "Bearer el-secreto-de-prueba"})
    assert r.status_code == 200 and r.json()["ok"] is True
    r = httpx.post(servidor + "/sincro/lead", json={"lead": "nada"}, timeout=5,
                   headers={"Authorization": "Bearer el-secreto-de-prueba"})
    assert r.status_code == 400
