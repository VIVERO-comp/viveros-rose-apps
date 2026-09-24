"""Los avisos Web Push: suscripción del celular y el aviso de un chat que
cae en "Esperando respuesta" (pedido de Abraham, 23/09/2026)."""

import pytest

from app import avisos, control, crm_flujo


@pytest.fixture(autouse=True)
def muestra_limpia(db_limpia, monkeypatch):
    monkeypatch.delenv("TWENTY_API_KEY", raising=False)
    monkeypatch.delenv("LINEAR_API_KEY", raising=False)
    monkeypatch.delenv("VAPID_CLAVE_PUBLICA", raising=False)
    monkeypatch.delenv("VAPID_CLAVE_PRIVADA", raising=False)
    control.refrescar()
    crm_flujo.refrescar()
    control.iniciar_tablas()
    avisos.iniciar_tablas()


@pytest.fixture
def con_claves(monkeypatch):
    """Las claves VAPID puestas, pero sin salir a la red: el envío real se
    reemplaza por una lista."""
    monkeypatch.setenv("VAPID_CLAVE_PUBLICA", "publica-de-prueba")
    monkeypatch.setenv("VAPID_CLAVE_PRIVADA", "privada-de-prueba")
    mandados = []
    monkeypatch.setattr(avisos, "avisar",
                        lambda usuario, titulo, cuerpo, url="/":
                        mandados.append((usuario, titulo, cuerpo, url)))
    return mandados


def test_sin_claves_no_hay_boton_ni_aviso(cliente):
    cuerpo = cliente.get("/?tab=ajustes").text
    assert "Avisos en este celular" in cuerpo
    assert 'id="activar-avisos"' not in cuerpo       # sin claves no se ofrece
    assert "faltan las claves VAPID" in cuerpo


def test_con_claves_el_boton_lleva_la_clave_publica(cliente, monkeypatch):
    monkeypatch.setenv("VAPID_CLAVE_PUBLICA", "publica-de-prueba")
    monkeypatch.setenv("VAPID_CLAVE_PRIVADA", "privada-de-prueba")
    cuerpo = cliente.get("/?tab=ajustes").text
    assert 'id="activar-avisos"' in cuerpo
    assert 'data-clave="publica-de-prueba"' in cuerpo


def test_el_service_worker_vive_en_la_raiz(cliente):
    # Servido desde /static solo podría atender /static: los avisos son de
    # toda la app, así que va en la raíz y con permiso de alcance.
    r = cliente.get("/sw-avisos.js")
    assert r.status_code == 200
    assert r.headers["service-worker-allowed"] == "/"
    assert "showNotification" in r.text
    assert cliente.get("/manifest.webmanifest").status_code == 200


def test_suscribir_guarda_el_celular_y_la_baja_lo_quita(cliente):
    suscripcion = {"endpoint": "https://push.example/abc",
                   "keys": {"p256dh": "llave", "auth": "secreto"}}
    assert cliente.post("/avisos/suscribir", json=suscripcion).status_code == 204
    assert avisos.cuantos("genesis") == 1
    # Re-suscribirse no duplica la fila (el endpoint es la llave).
    cliente.post("/avisos/suscribir", json=suscripcion)
    assert avisos.cuantos("genesis") == 1
    cliente.post("/avisos/baja", follow_redirects=False)
    assert avisos.cuantos("genesis") == 0


def test_una_suscripcion_a_medias_se_rechaza(cliente):
    r = cliente.post("/avisos/suscribir", json={"endpoint": "https://push/x"})
    assert r.status_code == 400


def test_la_primera_corrida_solo_toma_nota(cliente, con_claves):
    # Si no, el encargado recibiría un aviso por cada conversación abierta
    # el día que se despliega.
    control.tablero()
    assert con_claves == []


def test_mover_a_esperando_a_mano_avisa_al_encargado(cliente, con_claves):
    control.tablero()                       # la corrida que toma nota
    con_claves.clear()
    # Kev (m2) está En curso; un compañero lo devuelve a Esperando.
    cliente.post("/control/mover", data={"chat": "m2", "columna": "esperando"})
    usuario, titulo, _cuerpo, url = con_claves[0]
    assert usuario == avisos.USUARIO_CHATS
    assert titulo == "Esperando respuesta · Kev"
    assert url == "/control?abrir=m2"
    # Y no vuelve a sonar cada vez que alguien abre la pantalla.
    con_claves.clear()
    cliente.get("/control")
    assert con_claves == []


def test_un_mensaje_nuevo_del_cliente_tambien_avisa(cliente, con_claves):
    control.tablero()
    con_claves.clear()
    # Soledad (m4) estaba en Terminado; escribe de nuevo y su chat cae en
    # Esperando respuesta.
    crm_flujo._MUESTRA[3]["estado"] = "EN_CONVERSACION"
    control._MUESTRA[3].update({"dir": "ENTRANTE", "texto": "Otra consulta",
                                "fecha": "2099-01-01T12:00:00+00:00"})
    try:
        control.tablero()
        assert [t for _u, t, _c, _url in con_claves] == [
            "Esperando respuesta · Soledad"]
    finally:
        control._MUESTRA[3].update({"dir": "SALIENTE",
                                    "texto": "¡Que las disfrute! Cualquier cosa me escribe 🌿",
                                    "fecha": "2026-09-21T16:20:00+00:00"})
        crm_flujo._MUESTRA[3]["estado"] = "GANADO"


def test_el_aviso_de_prueba_necesita_un_celular_activado(cliente, monkeypatch):
    monkeypatch.setenv("VAPID_CLAVE_PUBLICA", "publica-de-prueba")
    monkeypatch.setenv("VAPID_CLAVE_PRIVADA", "privada-de-prueba")
    r = cliente.post("/avisos/prueba", follow_redirects=False)
    assert "avisos-sin-celular" in r.headers["location"]
    cliente.post("/avisos/suscribir",
                 json={"endpoint": "https://push.example/xyz",
                       "keys": {"p256dh": "llave", "auth": "secreto"}})
    mandados = []
    monkeypatch.setattr(avisos, "avisar",
                        lambda *a, **k: mandados.append(a))
    r = cliente.post("/avisos/prueba", follow_redirects=False)
    assert "avisos-prueba" in r.headers["location"] and len(mandados) == 1
