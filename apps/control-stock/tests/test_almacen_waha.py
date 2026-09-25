"""El tamaño del almacén de WAHA, preguntado al otro droplet (25/09/2026).

Lo que se cuida aquí es lo que duele si se rompe:

1. Que la URL salga de `SINCRO_URL` y no de una variable nueva: dos nombres
   para la misma puerta ya apagaron el enganche de WhatsApp una vez.
2. Que el número mostrado sea el **«después» del limpiador** y no el disco de
   este instante: el almacén sube durante la hora y el limpiador lo pliega
   cada hora a los :17, así que el instantáneo daría falsas alarmas.
3. Que cuando el limpiador DEJA de correr no se repita su último número
   tranquilizador — ese es el caso que de otro modo pasa desapercibido.
   Y que la EDAD salga del `mtime` del diario y nunca de la fecha escrita en
   su última línea: el droplet escribe ese texto en UTC y el proceso que lo
   lee corre en hora de Panamá, así que compararlos daba −4 horas y apagaba
   esta detección en silencio (bug real del 25/09/2026).
4. Que cuando no se puede saber, se lance: el renglón queda en blanco y lo
   dice, nunca en 0 MB.

Ninguna prueba sale a la red: `armar()` es pura y `leer()` se prueba con una
puerta falsa.
"""

from datetime import datetime

import pytest

from app import almacen_waha
from app.datos import ZONA_PANAMA

# Las 6:17 pm del 25/09/2026 en Panamá, como epoch. Es lo que el endpoint
# manda: un instante absoluto, sin zona que interpretar.
MTIME_6_17_PM = datetime(2026, 9, 25, 18, 17, 3, tzinfo=ZONA_PANAMA).timestamp()


@pytest.fixture(autouse=True)
def puente_puesto(monkeypatch):
    monkeypatch.setenv("SINCRO_URL", "http://10.116.0.3:3002/sincro/lead")
    monkeypatch.setenv("SINCRO_SECRET", "no-importa-el-valor")


def crudo(**cambios):
    """Lo que contesta el endpoint del droplet del CRM en un día sano."""
    base = {"ok": True, "mtime": MTIME_6_17_PM, "cola": "corrida",
            "antes_mb": 5, "mb": 2, "mensajes": 132, "edad_horas": 0.7,
            "ahora_mb": 4}
    base.update(cambios)
    return base


# ---------------------------------------------------------------------------
# El puente: la misma puerta, el mismo secreto
# ---------------------------------------------------------------------------

def test_la_url_sale_de_sincro_url_sin_pedir_variable_nueva():
    assert almacen_waha._endpoint() == "http://10.116.0.3:3002/almacen"
    assert almacen_waha.configurado() is True


def test_hace_falta_la_url_Y_el_secreto(monkeypatch):
    monkeypatch.delenv("SINCRO_SECRET")
    assert almacen_waha.configurado() is False
    monkeypatch.setenv("SINCRO_SECRET", "x")
    monkeypatch.delenv("SINCRO_URL")
    assert almacen_waha.configurado() is False
    assert almacen_waha._endpoint() == ""


def test_una_sincro_url_rota_no_arma_endpoint(monkeypatch):
    monkeypatch.setenv("SINCRO_URL", "10.116.0.3:3002")   # sin esquema
    assert almacen_waha._endpoint() == ""
    assert almacen_waha.configurado() is False


def test_leer_manda_el_bearer_y_pega_en_la_ruta_del_almacen(monkeypatch):
    visto = {}

    class Respuesta:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return crudo()

    def falso_get(url, headers=None, timeout=None):
        visto.update({"url": url, "headers": headers or {}})
        return Respuesta()

    monkeypatch.setattr(almacen_waha.httpx, "get", falso_get)
    assert almacen_waha.leer()["mb"] == 2
    assert visto["url"] == "http://10.116.0.3:3002/almacen"
    assert visto["headers"]["Authorization"].startswith("Bearer ")


def test_si_el_endpoint_contesta_que_no_se_lanza(monkeypatch):
    class Respuesta:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"ok": False, "motivo": "falta SINCRO_SECRET"}

    monkeypatch.setattr(almacen_waha.httpx, "get",
                        lambda *a, **k: Respuesta())
    with pytest.raises(RuntimeError, match="SINCRO_SECRET"):
        almacen_waha.leer()


# ---------------------------------------------------------------------------
# El número honesto: el «después» del limpiador
# ---------------------------------------------------------------------------

def test_el_numero_es_el_despues_del_limpiador_no_el_disco_de_ahora():
    a = almacen_waha.armar(crudo(mb=2, antes_mb=5, ahora_mb=9),
                           hoy_iso="2026-09-25")
    assert a["mb"] == 2, "el instantáneo (9 MB) daría una falsa alarma"
    assert a["alerta"] is False and a["vencido"] is False
    assert a["corto"] == "", "sano no gasta espacio del titular"
    # El salto se nombra: es la señal de que está plegando bien.
    assert "lo bajó de 5 MB a 2 MB" in a["frase"]
    assert "9 MB" not in a["frase"]
    assert "a las 6:17 pm" in a["frase"]
    assert "132 mensajes" in a["frase"]


def test_sin_salto_no_se_repite_el_mismo_numero_dos_veces():
    a = almacen_waha.armar(crudo(mb=2, antes_mb=2), hoy_iso="2026-09-25")
    assert "lo dejó en 2 MB" in a["frase"]
    assert "de 2 MB a 2 MB" not in a["frase"]


def test_los_miles_de_mensajes_van_con_espacio():
    a = almacen_waha.armar(crudo(mensajes=20584), hoy_iso="2026-09-25")
    assert "20 584 mensajes" in a["frase"]


def test_sin_cuenta_de_mensajes_la_frase_no_la_inventa():
    # `limpiar_almacen.sh` escribe «quedan ? mensajes» si sqlite falló.
    a = almacen_waha.armar(crudo(mensajes=None), hoy_iso="2026-09-25")
    assert "mensajes" not in a["frase"]
    assert a["mb"] == 2


def test_un_almacen_grande_es_alerta_y_entra_al_titular():
    a = almacen_waha.armar(crudo(mb=68, antes_mb=71, mensajes=20584),
                           hoy_iso="2026-09-25")
    assert a["alerta"] is True
    assert a["corto"] == "almacén 68 MB"
    assert "OJO" in a["frase"] and "20 MB" in a["frase"]
    assert "límites de sincronización de historial" in a["frase"]


def test_el_tope_es_el_mismo_con_el_que_grita_el_limpiador():
    # `limpiar_almacen.sh` alerta con `-gt 20`: el mismo número, para que la
    # pantalla y el diario del droplet no digan cosas distintas.
    assert almacen_waha.TOPE_MB == 20
    assert almacen_waha.armar(crudo(mb=20))["alerta"] is False
    assert almacen_waha.armar(crudo(mb=21))["alerta"] is True


# ---------------------------------------------------------------------------
# El caso que de otro modo pasa desapercibido: el limpiador muerto
# ---------------------------------------------------------------------------

def test_si_el_limpiador_no_corre_no_se_repite_su_numero_viejo():
    ayer = datetime(2026, 9, 24, 12, 17, 1, tzinfo=ZONA_PANAMA).timestamp()
    a = almacen_waha.armar(
        crudo(mb=2, antes_mb=5, edad_horas=30.0, ahora_mb=64, mtime=ayer),
        hoy_iso="2026-09-25")
    assert a["vencido"] is True and a["alerta"] is True
    assert a["mb"] == 64, "el disco de ahora, no el 2 MB congelado de ayer"
    assert a["corto"] == "almacén sin limpiar"
    assert "no ha corrido" in a["frase"]
    assert "24/09/2026" in a["frase"], "la fecha aparece porque no es de hoy"
    assert "2 MB" not in a["frase"]


def test_sin_ninguna_corrida_en_el_diario_pero_con_disco():
    a = almacen_waha.armar({"ok": True, "mtime": None, "mb": None,
                            "antes_mb": None, "mensajes": None,
                            "edad_horas": None, "ahora_mb": 3})
    assert a["vencido"] is True and a["mb"] == 3
    assert "ninguna pasada" in a["frase"]


def test_si_el_diario_termina_en_algo_raro_no_se_sostiene_su_fecha():
    """El `mtime` es del ARCHIVO: si alguien le escribió otra cosa al final,
    es fresco aunque la última corrida sea vieja. Ahí se dice que no se sabe,
    no «todo bien»."""
    a = almacen_waha.armar(crudo(cola="desconocida", edad_horas=None,
                                 ahora_mb=31), hoy_iso="2026-09-25")
    assert a["vencido"] is True and a["alerta"] is True
    assert a["mb"] == 31, "el disco de ahora, que sí se puede medir"
    assert "no reconozco" in a["frase"]
    assert "no ha corrido desde su última pasada" not in a["frase"]


def test_un_mtime_en_el_futuro_no_cuenta_como_vencido():
    """Relojes desalineados: una edad negativa es fresca, no vencida."""
    a = almacen_waha.armar(crudo(edad_horas=-1.5), hoy_iso="2026-09-25")
    assert a["vencido"] is False and a["mb"] == 2


def test_si_no_hay_diario_ni_disco_se_lanza_para_que_quede_el_hueco():
    """Nunca 0 MB: un cero falso parece una buena noticia."""
    with pytest.raises(RuntimeError):
        almacen_waha.armar({"ok": True, "mtime": None, "mb": None,
                            "ahora_mb": None})


# ---------------------------------------------------------------------------
# La hora, a la panameña
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("cuando,hoy,texto", [
    ((2026, 9, 25, 18, 17), "2026-09-25", "a las 6:17 pm"),
    ((2026, 9, 25, 8, 17), "2026-09-25", "a las 8:17 am"),
    ((2026, 9, 24, 12, 17), "2026-09-25", "el 24/09/2026 a las 12:17 pm"),
])
def test_la_hora_de_la_ultima_corrida_sale_del_mtime(cuando, hoy, texto):
    mtime = datetime(*cuando, tzinfo=ZONA_PANAMA).timestamp()
    assert almacen_waha._cuando_texto(mtime, hoy) == texto


@pytest.mark.parametrize("mtime", [None, 0, "", "cualquier cosa"])
def test_sin_mtime_no_se_inventa_una_hora(mtime):
    assert almacen_waha._cuando_texto(mtime, "2026-09-25") == ""


def test_la_hora_se_muestra_en_panama_no_en_la_zona_del_droplet():
    """El droplet del CRM corre en UTC. Si la hora se tomara de allá, el
    dueño leería «8:17 pm» cuando en su reloj son las 4:17 pm."""
    en_utc = datetime.fromisoformat("2026-09-25T21:17:00+00:00").timestamp()
    assert almacen_waha._cuando_texto(en_utc, "2026-09-25") == "a las 4:17 pm"
