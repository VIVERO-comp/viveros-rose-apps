"""Los avisos Web Push: la suscripción del celular y el botón de prueba.

QUÉ dispara el aviso cambió en la Fase 5 (24/09/2026): ya no es un chat que
cae en la columna "Esperando respuesta" —esa columna murió con el kanban de
chats— sino un lead que gana la etiqueta «Te toca» en Linear. Eso se prueba
en tests/test_control.py (`test_el_aviso_suena_una_sola_vez_por_lead` y
compañía), que es donde vive esa decisión. Aquí queda la plomería: las
claves VAPID, el service worker y la suscripción.
"""

import pytest

from app import avisos, control, linear_leads


@pytest.fixture(autouse=True)
def muestra_limpia(db_limpia, monkeypatch):
    monkeypatch.delenv("TWENTY_API_KEY", raising=False)
    monkeypatch.delenv("LINEAR_API_KEY", raising=False)
    monkeypatch.delenv("VAPID_CLAVE_PUBLICA", raising=False)
    monkeypatch.delenv("VAPID_CLAVE_PRIVADA", raising=False)
    linear_leads.reiniciar_muestra()
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


def test_el_aviso_no_suena_dos_veces_por_el_mismo_lead(cliente, con_claves):
    """La plomería vista desde arriba: la pantalla se puede abrir mil veces
    y el celular suena una sola vez por lead.

    Antes esto lo cuidaba la tabla `control_visto` del kanban de chats; hoy
    lo cuida `control_acuse`, y el disparador es la etiqueta «Te toca».
    """
    # La primera corrida solo toma nota (Ximena y Nedjaira ya esperaban).
    control.avisar_a_quien_le_toca()
    assert con_claves == []
    # Ahora sí: a Tamara le entra un mensaje y le toca a alguien.
    linear_leads.poner_te_toca(linear_leads.uno("LEAD-91")["id"], True)
    control.avisar_a_quien_le_toca()
    assert [t for _u, t, _c, _url in con_claves] == ["Te toca · Tamara"]
    # Y abrir la pantalla mil veces no lo repite.
    con_claves.clear()
    cliente.get("/control")
    cliente.get("/control")
    assert con_claves == []


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
