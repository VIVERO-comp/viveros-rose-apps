"""La vista «Respuestas» (/conversaciones/respuestas) — esqueleto del
BLOQUE 21.

Lo que se prueba, por regla:

- Nace cerrada: sin sesión al login; sin admin, 403 (el mismo candado
  de /conversaciones).
- La estructura abre y dice QUÉ va a mostrar, sin un solo número
  fabricado: las métricas salen «—» y el aviso del cálculo está.
- El umbral es el dato de config (respuestas.umbral_min, default 10) y
  la pantalla lo dice.
- Los 3 huecos del dato `autor` van DECLARADOS en pantalla.
- Sin Twenty (token neutralizado) la pantalla lo dice.
- CERO rutas POST (la prueba vieja de /conversaciones ya recorre el
  prefijo y cubre esta ruta; acá va la propia igual) y ni un <form>.
"""

import pytest

from app import datos, respuestas


@pytest.fixture(autouse=True)
def sin_twenty(monkeypatch, db_limpia):
    monkeypatch.delenv("TWENTY_API_KEY", raising=False)


@pytest.fixture
def admin(cliente, monkeypatch):
    monkeypatch.setenv("AJUSTES_ADMINS", "genesis")
    return cliente


# ---------------------------------------------------------------------------
# La puerta
# ---------------------------------------------------------------------------

def test_sin_sesion_redirige_al_login(db_limpia):
    from fastapi.testclient import TestClient
    from app.main import app
    c = TestClient(app)
    r = c.get("/conversaciones/respuestas", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"


def test_no_admin_recibe_403(cliente, monkeypatch):
    monkeypatch.delenv("AJUSTES_ADMINS", raising=False)
    assert cliente.get("/conversaciones/respuestas").status_code == 403


# ---------------------------------------------------------------------------
# La estructura honesta: nada inventado
# ---------------------------------------------------------------------------

def test_abre_con_la_estructura_y_sin_numeros_fabricados(admin):
    r = admin.get("/conversaciones/respuestas")
    assert r.status_code == 200
    for titulo in ("Respuesta típica", "Esperando ahora", "Nadie contestó",
                   "Por persona", "Para leer"):
        assert titulo in r.text
    # Las métricas van en «—»: el cálculo llega en la parte (d).
    assert "—" in r.text
    assert "El cálculo llega en la siguiente parte" in r.text
    # Y ni un dígito disfrazado de medición: la mediana no existe aún.
    assert "Sin filas todavía" in r.text


def test_sin_twenty_lo_dice(admin):
    assert respuestas.AVISO_SIN_TWENTY in \
        admin.get("/conversaciones/respuestas").text


def test_con_twenty_no_sale_el_aviso_de_pruebas(admin, monkeypatch):
    monkeypatch.setenv("TWENTY_API_KEY", "clave-de-prueba")
    assert respuestas.AVISO_SIN_TWENTY not in \
        admin.get("/conversaciones/respuestas").text


def test_los_tres_huecos_del_autor_van_declarados(admin):
    texto = admin.get("/conversaciones/respuestas").text
    assert "no tienen autor" in texto
    assert "«Teléfono»" in texto
    assert "Rubén no está mapeado" in texto


# ---------------------------------------------------------------------------
# El umbral: dato de config, default 10
# ---------------------------------------------------------------------------

def test_el_umbral_default_es_10():
    assert respuestas.umbral_min() == 10


def test_el_umbral_se_cambia_en_config_sin_desplegar(admin):
    datos.fijar_config("respuestas.umbral_min", "7")
    texto = admin.get("/conversaciones/respuestas").text
    assert "Contestados en 7 min" in texto
    assert "Menos de 7 min" in texto
    assert "hoy 7 min" in texto


def test_un_umbral_roto_cae_al_default():
    datos.fijar_config("respuestas.umbral_min", "no-es-numero")
    assert respuestas.umbral_min() == 10
    datos.fijar_config("respuestas.umbral_min", "-3")
    assert respuestas.umbral_min() == 10


# ---------------------------------------------------------------------------
# Solo lectura
# ---------------------------------------------------------------------------

def test_ni_un_form_en_la_pantalla(admin):
    assert "<form" not in admin.get("/conversaciones/respuestas").text


def test_cero_rutas_post_bajo_conversaciones():
    from app.main import app
    for ruta in app.routes:
        if str(getattr(ruta, "path", "")).startswith("/conversaciones"):
            assert "POST" not in (getattr(ruta, "methods", None) or set())


def test_el_periodo_y_ver_chat_van_apagados(admin):
    texto = admin.get("/conversaciones/respuestas").text
    assert "Elegir período — Todavía no" in texto
    # Hoy la lista «Para leer» está vacía: el markup del «Ver chat»
    # apagado (BLOQUE 29) vive en el loop y se prueba en test_mi_crm.
    assert "La lista llega con el cálculo" in texto
