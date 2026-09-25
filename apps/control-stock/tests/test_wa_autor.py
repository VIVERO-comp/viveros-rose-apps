"""Quién respondió (Fase A): el dispositivo que escribió cada saliente.

Los payloads de estas pruebas tienen la forma real de WAHA (`message.any`
con `_data.Info`), copiada de lo que manda el 6099-1459. El número y el LID
van cambiados: aquí no hay datos de nadie.
"""

import hashlib
import hmac
import json

import pytest

from app import wa_autor

SECRETO = "secreto-de-prueba-solo-para-pytest"
LID = "123456789012345"


def evento(wa_id="A5C42A8BD022E9B5324198D2800380DF", dispositivo="3",
           source="app", from_me=True, chat=None, grupo=False):
    """Un `message.any` de WAHA como el que llega de verdad."""
    remitente = LID + (f":{dispositivo}" if dispositivo else "") + "@lid"
    chat = chat or "987654321098765@lid"
    return {
        "event": "message.any",
        "session": "vivero",
        "payload": {
            "id": f"true_{chat}_{wa_id}",
            "timestamp": 1790351424,
            "from": chat,
            "fromMe": from_me,
            "source": source,
            "body": "texto que a este modulo no le importa",
            "_data": {
                "Info": {
                    "ID": wa_id,
                    "Chat": chat,
                    "Sender": remitente,
                    "IsFromMe": str(from_me),
                    "IsGroup": str(grupo),
                },
            },
        },
    }


def firmar(cuerpo, secreto=SECRETO):
    return hmac.new(secreto.encode(), cuerpo, hashlib.sha512).hexdigest()


@pytest.fixture
def con_secreto(monkeypatch):
    monkeypatch.setenv("WAHA_WEBHOOK_SECRET", SECRETO)


# ---------------------------------------------------------------------------
# Leer el dispositivo del JID
# ---------------------------------------------------------------------------

def test_el_sufijo_del_jid_es_el_dispositivo():
    assert wa_autor.dispositivo_de(f"{LID}:3@lid") == "3"
    assert wa_autor.dispositivo_de(f"{LID}:25@lid") == "25"
    # Los tres dominios que WhatsApp usa para lo mismo.
    assert wa_autor.dispositivo_de("50760991459:7@s.whatsapp.net") == "7"
    assert wa_autor.dispositivo_de("50760991459:7@c.us") == "7"


def test_sin_sufijo_es_el_telefono_y_no_un_error():
    """El celular del negocio es el dispositivo 0 y llega pelado.

    Confundirlo con "no se sabe" era el error fácil: haría que la mayoría de
    las respuestas, que salen del teléfono, se vieran como desconocidas."""
    assert wa_autor.dispositivo_de(f"{LID}@lid") == wa_autor.TELEFONO == "0"
    assert wa_autor.dispositivo_de("50760991459@c.us") == "0"


def test_lo_que_no_es_un_jid_no_inventa_dispositivo():
    for basura in ("", None, "Mary", "@lid", "hola@ejemplo.com"):
        assert wa_autor.dispositivo_de(basura) == ""


# ---------------------------------------------------------------------------
# La escalera de nombres: nunca se inventa una persona
# ---------------------------------------------------------------------------

def test_los_nombres_de_fabrica(db_limpia):
    assert wa_autor.nombre_de("0") == "Teléfono"
    # Lo que manda nuestro propio codigo por la API de WAHA no es nadie del
    # equipo, aunque salga del mismo numero.
    assert wa_autor.nombre_de("4", source="api") == "Sistema"
    assert wa_autor.nombre_de("0", source="api") == "Sistema"


def test_un_dispositivo_sin_mapear_no_recibe_nombre_de_persona(db_limpia):
    assert wa_autor.nombre_de("4") == "Equipo · dispositivo 4"
    assert wa_autor.nombre_de("12") == "Equipo · dispositivo 12"


def test_nombrar_y_reasignar_un_dispositivo(db_limpia):
    wa_autor.nombrar("3", "Mary")
    assert wa_autor.nombre_de("3") == "Mary"
    # Cuando alguien vuelve a vincular su computadora, el numero cambia de
    # dueno: reasignar tiene que ser escribir, no desplegar.
    wa_autor.nombrar("3", "Ruben")
    assert wa_autor.nombre_de("3") == "Ruben"
    # Y vaciarlo lo devuelve al nombre honesto, no a un nombre viejo.
    wa_autor.nombrar("3", "")
    assert wa_autor.nombre_de("3") == "Equipo · dispositivo 3"


def test_el_nombre_puesto_le_gana_al_telefono(db_limpia):
    """Si el dueño decide llamar «Abraham» al celular del negocio, manda él."""
    wa_autor.nombrar("0", "Abraham")
    assert wa_autor.nombre_de("0") == "Abraham"


# ---------------------------------------------------------------------------
# Anotar
# ---------------------------------------------------------------------------

def test_anotar_cuenta_el_dispositivo_y_deja_el_pendiente(db_limpia):
    assert wa_autor.anotar("WA-1", "3", "app") is True
    vistos = wa_autor.vistos()
    assert [(v["dispositivo"], v["mensajes"]) for v in vistos] == [("3", 1)]
    assert vistos[0]["se_ve"] == "Equipo · dispositivo 3"
    assert vistos[0]["sin_nombre"] is True


def test_un_reintento_de_waha_no_infla_el_contador(db_limpia):
    """WAHA reintenta sus webhooks: el mismo mensaje no puede contar dos
    veces ni duplicar el pendiente."""
    assert wa_autor.anotar("WA-1", "3") is True
    assert wa_autor.anotar("WA-1", "3") is False
    assert wa_autor.vistos()[0]["mensajes"] == 1


def test_el_telefono_va_primero_y_despues_los_mas_activos(db_limpia):
    wa_autor.anotar("WA-1", "5")
    wa_autor.anotar("WA-2", "5")
    wa_autor.anotar("WA-3", "5")
    wa_autor.anotar("WA-4", "3")
    wa_autor.anotar("WA-5", "0")
    assert [v["dispositivo"] for v in wa_autor.vistos()] == ["0", "5", "3"]


def test_anotar_exige_mensaje_y_dispositivo(db_limpia):
    assert wa_autor.anotar("", "3") is False
    assert wa_autor.anotar("WA-1", "") is False
    assert wa_autor.vistos() == []


def test_los_pendientes_viejos_se_borran(db_limpia):
    """La promesa de privacidad, hecha código: un id que en 24 horas no
    encontró su mensaje en Twenty es de un chat que el CRM no sigue."""
    from datetime import datetime, timedelta

    from app.datos import ZONA_PANAMA

    ahora = datetime.now(ZONA_PANAMA)
    wa_autor.anotar("VIEJO", "3", cuando=(ahora - timedelta(hours=30)).isoformat())
    wa_autor.anotar("NUEVO", "3", cuando=ahora.isoformat())
    assert wa_autor.limpiar_pendientes(ahora=ahora) == 1
    with wa_autor._db() as con:
        quedan = [f[0] for f in con.execute(
            "SELECT wa_message_id FROM wa_pendiente").fetchall()]
    assert quedan == ["NUEVO"]
    # El contador del dispositivo NO se borra: es lo que sostiene la tabla
    # de Ajustes, y no dice nada de ningún chat.
    assert wa_autor.vistos()[0]["mensajes"] == 2


# ---------------------------------------------------------------------------
# Leer el evento de WAHA
# ---------------------------------------------------------------------------

def test_leer_un_saliente_de_verdad():
    leido = wa_autor.leer_evento(evento())
    assert leido == {
        "wa_message_id": "A5C42A8BD022E9B5324198D2800380DF",
        "dispositivo": "3",
        "source": "app",
    }


def test_el_telefono_principal_llega_sin_sufijo():
    leido = wa_autor.leer_evento(evento(dispositivo=""))
    assert leido["dispositivo"] == "0"


def test_lo_que_no_es_una_respuesta_nuestra_se_descarta():
    # Un mensaje del cliente: de ese no hay autor que buscar.
    assert wa_autor.leer_evento(evento(from_me=False)) is None
    # Un grupo no es la conversación con un cliente.
    assert wa_autor.leer_evento(evento(grupo=True)) is None
    assert wa_autor.leer_evento(evento(chat="12345@g.us")) is None
    # Otros eventos de WAHA (acuses, estado de sesión) no dicen nada de esto.
    otro = evento()
    otro["event"] = "message.ack"
    assert wa_autor.leer_evento(otro) is None
    # Y la basura no tumba nada.
    for basura in (None, {}, [], "hola", {"event": "message.any"}):
        assert wa_autor.leer_evento(basura) is None


def test_sin_el_id_crudo_sirve_el_id_compuesto_de_waha():
    """WAHA compone su id como `true_<chat>_<ID>`; el último tramo es el
    mismo waMessageId que OpenWA ya guardó en Twenty."""
    e = evento()
    e["payload"]["_data"]["Info"]["ID"] = ""
    assert wa_autor.leer_evento(e)["wa_message_id"] == "A5C42A8BD022E9B5324198D2800380DF"


def test_sin_remitente_no_se_adivina_el_dispositivo():
    e = evento()
    e["payload"]["_data"]["Info"]["Sender"] = ""
    assert wa_autor.leer_evento(e) is None


# ---------------------------------------------------------------------------
# La firma
# ---------------------------------------------------------------------------

def test_la_firma_es_del_cuerpo_crudo(con_secreto):
    cuerpo = json.dumps(evento()).encode()
    assert wa_autor.firma_valida(cuerpo, firmar(cuerpo)) is True
    # Mayúsculas o minúsculas del hex dan igual.
    assert wa_autor.firma_valida(cuerpo, firmar(cuerpo).upper()) is True


def test_la_firma_mala_no_pasa(con_secreto):
    cuerpo = json.dumps(evento()).encode()
    assert wa_autor.firma_valida(cuerpo, firmar(cuerpo, "otro-secreto")) is False
    assert wa_autor.firma_valida(cuerpo, "") is False
    assert wa_autor.firma_valida(cuerpo, None) is False
    # Un byte distinto en el cuerpo invalida la firma.
    assert wa_autor.firma_valida(cuerpo + b" ", firmar(cuerpo)) is False


def test_sin_secreto_no_se_valida_nada(monkeypatch):
    monkeypatch.delenv("WAHA_WEBHOOK_SECRET", raising=False)
    cuerpo = b"{}"
    assert wa_autor.configurado() is False
    assert wa_autor.firma_valida(cuerpo, firmar(cuerpo)) is False


# ---------------------------------------------------------------------------
# El endpoint
# ---------------------------------------------------------------------------

def _postear(cliente_sin_sesion, e, secreto=SECRETO):
    cuerpo = json.dumps(e).encode()
    return cliente_sin_sesion.post(
        "/wa/autor", content=cuerpo,
        headers={"Content-Type": "application/json",
                 "X-Webhook-Hmac": firmar(cuerpo, secreto)})


@pytest.fixture
def sin_sesion(db_limpia):
    """El webhook lo llama una máquina: no hay cookie ni la necesita."""
    from fastapi.testclient import TestClient

    from app.main import app

    return TestClient(app)


def test_el_webhook_anota_el_dispositivo(sin_sesion, con_secreto):
    r = _postear(sin_sesion, evento())
    assert r.status_code == 200
    assert r.json() == {"ok": True, "anotado": True, "dispositivo": "3"}
    assert wa_autor.vistos()[0]["dispositivo"] == "3"


def test_el_webhook_sin_firma_no_entra(sin_sesion, con_secreto):
    r = sin_sesion.post("/wa/autor", json=evento())
    assert r.status_code == 401
    assert wa_autor.vistos() == []


def test_el_webhook_con_firma_de_otro_secreto_no_entra(sin_sesion, con_secreto):
    r = _postear(sin_sesion, evento(), secreto="el-secreto-equivocado")
    assert r.status_code == 401
    assert wa_autor.vistos() == []


def test_sin_secreto_configurado_el_webhook_no_acepta_nada(sin_sesion, monkeypatch):
    monkeypatch.delenv("WAHA_WEBHOOK_SECRET", raising=False)
    r = _postear(sin_sesion, evento())
    assert r.status_code == 503
    assert wa_autor.vistos() == []


def test_un_evento_que_no_aplica_contesta_200(sin_sesion, con_secreto):
    """200 y no 4xx: WAHA reintenta lo que no sea 2xx, y un entrante no debe
    volver quince veces."""
    r = _postear(sin_sesion, evento(from_me=False))
    assert r.status_code == 200
    assert r.json()["anotado"] is False
    assert wa_autor.vistos() == []


def test_el_webhook_no_pide_sesion(sin_sesion, con_secreto):
    """La prueba de que el middleware lo deja pasar: sin cookie, la
    respuesta es del endpoint (401 por firma) y no un redirect al login."""
    r = sin_sesion.post("/wa/autor", json=evento(), follow_redirects=False)
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# La tabla en Ajustes
# ---------------------------------------------------------------------------

def test_ajustes_muestra_los_dispositivos_al_dueno(cliente, monkeypatch):
    monkeypatch.setenv("AJUSTES_ADMINS", "genesis")
    wa_autor.anotar("WA-1", "0")
    wa_autor.anotar("WA-2", "3")
    wa_autor.nombrar("3", "Mary")
    html = cliente.get("/?tab=ajustes").text
    assert "Dispositivos de WhatsApp" in html
    assert "Teléfono" in html
    assert "Mary" in html


def test_solo_el_dueno_nombra_dispositivos(cliente, monkeypatch):
    monkeypatch.setenv("AJUSTES_ADMINS", "otra@persona.com")
    wa_autor.anotar("WA-1", "3")
    r = cliente.post("/ajustes/dispositivos/nombrar",
                     data={"dispositivo": "3", "nombre": "Mary"},
                     follow_redirects=False)
    assert r.status_code in (303, 403)
    assert wa_autor.nombre_de("3") == "Equipo · dispositivo 3"


def test_el_dueno_nombra_desde_ajustes(cliente, monkeypatch):
    monkeypatch.setenv("AJUSTES_ADMINS", "genesis")
    wa_autor.anotar("WA-1", "3")
    r = cliente.post("/ajustes/dispositivos/nombrar",
                     data={"dispositivo": "3", "nombre": "Mary"},
                     follow_redirects=False)
    assert r.status_code == 303
    assert wa_autor.nombre_de("3") == "Mary"


def test_un_dispositivo_que_no_es_numero_no_entra(cliente, monkeypatch):
    monkeypatch.setenv("AJUSTES_ADMINS", "genesis")
    r = cliente.post("/ajustes/dispositivos/nombrar",
                     data={"dispositivo": "; DROP TABLE", "nombre": "Mary"},
                     follow_redirects=False)
    assert r.status_code == 303
    assert "dispositivo-invalido" in r.headers["location"]
    assert wa_autor.vistos() == []
