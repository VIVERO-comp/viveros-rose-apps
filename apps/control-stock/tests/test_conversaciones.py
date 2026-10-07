"""La pantalla /conversaciones (ITEM 11 del plan de Jay) y el reintento
del casamiento de la ficha (Nº12 de la lista de bugs).

Parte A — la pantalla: solo admin, agrupado por chat con el más reciente
primero, el autor visible en los salientes, el aviso honesto con Twenty
caído o sin key, y CERO rutas POST nuevas (es lectura pura).

Parte B — el reintento: una ficha que no casó al entrar el mensaje vuelve
a buscar por teléfono al abrirse; «Sin chat disponible» queda solo para
cuando de verdad no hay mensajes; y el caché de 60 s evita la doble
consulta a Twenty.

Todo con dobles de Twenty: ninguna prueba sale a la red.
"""

import pytest

from app import control, conversaciones, crm_twenty, linear_leads


@pytest.fixture(autouse=True)
def tablero_de_muestra(monkeypatch, db_limpia):
    """Sin Linear (tablero de muestra) y con el caché de fichas limpio."""
    monkeypatch.delenv("LINEAR_API_KEY", raising=False)
    linear_leads.reiniciar_muestra()
    control.iniciar_tablas()
    crm_twenty._fichas.clear()
    yield
    crm_twenty._fichas.clear()


@pytest.fixture
def admin(cliente, monkeypatch):
    """El mismo cliente de la casa (Génesis), vuelta admin."""
    monkeypatch.setenv("AJUSTES_ADMINS", "genesis")
    return cliente


def msg(fecha, chat="50761112222@c.us", nombre="Jenny Londoño",
        texto="Hola", direccion="ENTRANTE", autor="", telefono="50761112222",
        persona=""):
    """Un mensaje crudo como lo devuelve /rest/mensajesWhatsapp."""
    return {"fecha": fecha, "chatId": chat, "chatNombre": nombre,
            "texto": texto, "direccion": direccion, "autor": autor,
            "telefono": telefono, "personaId": persona,
            "waMessageId": "wa-" + fecha}


# Dos chats: el de Jenny es el más reciente y cierra con un saliente de
# ruben; el de Pablo es más viejo y cierra con un entrante. Vienen del
# más nuevo al más viejo, como los sirve Twenty con fecha[DescNullsLast].
RECIENTES = [
    msg("2026-10-04T15:00:00Z", texto="Perfecto, mañana se la llevamos",
        direccion="SALIENTE", autor="ruben"),
    msg("2026-10-04T14:50:00Z", texto="¿Tienen alocasia?"),
    msg("2026-10-03T10:00:00Z", chat="50769998888@c.us", nombre="Pablo",
        telefono="50769998888", texto="Buenas, ¿precio de la veranera?"),
    msg("2026-10-03T09:00:00Z", chat="50769998888@c.us", nombre="Pablo",
        telefono="50769998888", texto="Con gusto, $45",
        direccion="SALIENTE", autor="Mary"),
]


@pytest.fixture
def twenty_con_chats(monkeypatch):
    """Twenty fingido para la pantalla: recientes y el hilo de un chat."""
    monkeypatch.setenv("TWENTY_API_KEY", "clave-de-prueba")
    monkeypatch.setattr(crm_twenty, "mensajes_recientes",
                        lambda limite=300: (list(RECIENTES), False))
    monkeypatch.setattr(
        crm_twenty, "mensajes_de_chat",
        lambda chat_id, limite=400: sorted(
            [m for m in RECIENTES if m["chatId"] == chat_id],
            key=lambda m: m["fecha"]))


# ---------------------------------------------------------------------------
# Parte A · el candado: es supervisión, solo el admin entra
# ---------------------------------------------------------------------------

def test_sin_sesion_redirige_al_login(db_limpia):
    from fastapi.testclient import TestClient
    from app.main import app
    c = TestClient(app)
    r = c.get("/conversaciones", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"


def test_sin_rol_de_supervision_recibe_403(cliente, monkeypatch):
    """V2 (BLOQUE 29): Conversaciones dejó de ser «solo admin» y pasó a
    ser de los ROLES Director y Finanzas. Una empleada sin ninguno de los
    dos —y sin admin— recibe 403 con el texto que lo dice."""
    monkeypatch.delenv("AJUSTES_ADMINS", raising=False)
    r = cliente.get("/conversaciones")
    assert r.status_code == 403
    assert "Director y Finanzas" in r.text


def test_cero_rutas_post_nuevas():
    """Lectura pura: ninguna ruta de /conversaciones acepta POST."""
    from app.main import app
    for ruta in app.routes:
        if str(getattr(ruta, "path", "")).startswith("/conversaciones"):
            assert "POST" not in (getattr(ruta, "methods", None) or set())


def test_conversaciones_es_una_entrada_del_menu(admin):
    """BLOQUE 36.1: el enlace suelto de la barra de /control murió —
    Conversaciones es una pestaña del menú por rol, y el menú lo decide
    Python (request.state.menu_nav) para todas las pantallas."""
    cuerpo = admin.get("/control").text
    assert 'href="/conversaciones"' in cuerpo
    # Y vive en el costado, no colgando de la barra del tablero.
    # (BLOQUE 54, G1: el título de la pestaña ya no es un <h3> propio de
    # cada plantilla, es el <h1 class="cab-titulo"> del encabezado único.)
    assert cuerpo.index('href="/conversaciones"') < cuerpo.index(">CRM</h1>")


def test_la_capa_de_adentro_lleva_a_respuestas(admin, twenty_con_chats):
    """«Respuestas» ya no es pestaña del menú: es la otra vista DE
    Conversaciones, y se llega por la capa de arriba (dos enlaces)."""
    cuerpo = admin.get("/conversaciones").text
    assert 'href="/conversaciones/respuestas"' in cuerpo
    assert ">Todos los chats<" in cuerpo


# ---------------------------------------------------------------------------
# Parte A · la lista: agrupado, orden, autor
# ---------------------------------------------------------------------------

def test_agrupa_por_chat_y_cuenta_los_mensajes(admin, twenty_con_chats):
    lista = conversaciones.listar()
    assert lista["ok"] is True
    assert [c["titulo"] for c in lista["conversaciones"]] == [
        "Jenny Londoño", "Pablo"]
    assert [c["cant"] for c in lista["conversaciones"]] == [2, 2]


def test_la_mas_reciente_va_primero_en_pantalla(admin, twenty_con_chats):
    texto = admin.get("/conversaciones").text
    assert texto.index("Jenny Londoño") < texto.index("Pablo")


def test_el_ultimo_saliente_muestra_su_autor(admin, twenty_con_chats):
    texto = admin.get("/conversaciones").text
    assert "→ ruben:" in texto
    assert "Perfecto, mañana se la llevamos" in texto
    # El chat de Pablo cerró con un entrante: habla el cliente.
    assert "← Pablo:" in texto


def test_el_corte_es_honesto_y_el_ver_mas_solo_si_hay_mas(admin, monkeypatch):
    monkeypatch.setenv("TWENTY_API_KEY", "clave-de-prueba")
    monkeypatch.setattr(crm_twenty, "mensajes_recientes",
                        lambda limite=300: (list(RECIENTES), True))
    texto = admin.get("/conversaciones").text
    assert "Agrupadas de los últimos 4 mensajes" in texto
    assert "Ver más (600 mensajes)" in texto
    # Y sin más atrás, el botón no se finge.
    monkeypatch.setattr(crm_twenty, "mensajes_recientes",
                        lambda limite=300: (list(RECIENTES), False))
    assert "Ver más" not in admin.get("/conversaciones").text


# ---------------------------------------------------------------------------
# Parte A · abrir una conversación: el mismo hilo de la ficha
# ---------------------------------------------------------------------------

def test_abrir_muestra_el_hilo_con_el_cliente_y_el_equipo(admin, twenty_con_chats):
    texto = admin.get("/conversaciones",
                      params={"abrir": "50761112222@c.us"}).text
    assert "ficha-hilo" in texto
    assert "hilo-grupo mio" in texto          # el equipo, a la derecha
    assert ">ruben<" in texto                 # con el nombre de quien respondió
    assert "¿Tienen alocasia?" in texto


def test_abrir_reusa_el_armado_de_control_hilo(twenty_con_chats):
    abierta = conversaciones.abrir("50761112222@c.us")
    grupos = [b for b in abierta["hilo"] if b["tipo"] == "grupo"]
    assert [g["nombre"] for g in grupos] == ["Jenny Londoño", "ruben"]
    assert [g["mio"] for g in grupos] == [False, True]


def test_el_chat_que_casa_con_un_lead_enlaza_a_control(admin, twenty_con_chats, monkeypatch):
    # El teléfono del chat es el celular de LEAD-86 (Nedjaira) del tablero
    # de muestra: 6114-9077 -> 50761149077.
    monkeypatch.setattr(
        crm_twenty, "mensajes_de_chat",
        lambda chat_id, limite=400: [
            msg("2026-10-04T14:50:00Z", chat=chat_id, nombre="Nedjaira",
                telefono="50761149077", texto="Hola")])
    texto = admin.get("/conversaciones",
                      params={"abrir": "50761149077@c.us"}).text
    assert "/control?abrir=LEAD-86" in texto


def test_el_chat_sin_lead_se_muestra_igual(admin, twenty_con_chats):
    """Esa es la gracia de la pantalla: un chat sin lead no se esconde."""
    texto = admin.get("/conversaciones",
                      params={"abrir": "50761112222@c.us"}).text
    assert "Sin lead en el tablero" in texto
    assert "ficha-hilo" in texto


# ---------------------------------------------------------------------------
# Parte A · honestidad: Twenty caído o sin key
# ---------------------------------------------------------------------------

def test_twenty_caido_lo_dice_y_la_pantalla_carga_igual(admin, monkeypatch):
    monkeypatch.setenv("TWENTY_API_KEY", "clave-de-prueba")

    def revienta(limite=300):
        raise RuntimeError("Twenty no contesta")
    monkeypatch.setattr(crm_twenty, "mensajes_recientes", revienta)
    r = admin.get("/conversaciones")
    assert r.status_code == 200
    assert ("Twenty no contesta; no se pudieron cargar las "
            "conversaciones.") in r.text


def test_sin_key_de_twenty_degrada_al_aviso_honesto(admin, monkeypatch):
    """La instancia de pruebas tiene la key neutralizada: la pantalla no
    puede fingir una lista vacía."""
    monkeypatch.delenv("TWENTY_API_KEY", raising=False)
    r = admin.get("/conversaciones")
    assert r.status_code == 200
    assert "no se pudieron cargar las conversaciones" in r.text


def test_abrir_con_twenty_caido_lo_dice_en_el_panel(admin, monkeypatch):
    monkeypatch.setenv("TWENTY_API_KEY", "clave-de-prueba")
    monkeypatch.setattr(crm_twenty, "mensajes_recientes",
                        lambda limite=300: (list(RECIENTES), False))

    def revienta(chat_id, limite=400):
        raise RuntimeError("timeout")
    monkeypatch.setattr(crm_twenty, "mensajes_de_chat", revienta)
    r = admin.get("/conversaciones", params={"abrir": "50761112222@c.us"})
    assert r.status_code == 200
    assert "no se pudo cargar esta conversación" in r.text


# ---------------------------------------------------------------------------
# Parte B · el reintento del casamiento (Nº12)
# ---------------------------------------------------------------------------

class TwentyFalso:
    """El REST de Twenty, fingido: leadsWeb sin casamiento, y los
    mensajes solo si la consulta viene POR TELÉFONO."""

    def __init__(self, mensajes_tel=None):
        self.llamadas = []
        self.mensajes_tel = mensajes_tel or []

    def __call__(self, ruta):
        self.llamadas.append(ruta)
        if ruta.startswith("leadsWeb"):
            return {"data": {"leadsWeb": []}}
        if ruta.startswith("people/"):
            return {"data": {"person": {}}}
        if ruta.startswith("mensajesWhatsapp") and "telefono[eq]" in ruta:
            return {"data": {"mensajesWhatsapp": list(self.mensajes_tel)}}
        return {"data": {"mensajesWhatsapp": []}}


LEAD_SIN_CASAR = {"id": "muestra-LEAD-86", "ref": "LEAD-86",
                  "pp": "PP-70202", "celular": "6114-9077"}


@pytest.fixture
def con_key(monkeypatch):
    monkeypatch.setenv("TWENTY_API_KEY", "clave-de-prueba")


def test_la_ficha_que_no_casaba_ahora_encuentra_el_chat_por_telefono(
        con_key, monkeypatch):
    falso = TwentyFalso(mensajes_tel=[
        msg("2026-10-04T14:50:00Z", nombre="Nedjaira",
            telefono="50761149077", texto="¿Llegó mi pedido?"),
        msg("2026-10-04T15:00:00Z", nombre="Nedjaira",
            telefono="50761149077", texto="Sí, va en camino",
            direccion="SALIENTE", autor="Mary"),
    ])
    monkeypatch.setattr(crm_twenty, "_twenty", falso)
    ficha = crm_twenty.ficha_de_lead(LEAD_SIN_CASAR)
    assert ficha is not None
    assert [m["texto"] for m in ficha["mensajes"]] == [
        "¿Llegó mi pedido?", "Sí, va en camino"]
    assert ficha["mensajes"][1]["autor"] == "Mary"
    # Las dos grafías del número van en UN solo filter con or(): dos
    # `filter=` en la misma URL no se suman (trampa de la casa).
    reintento = next(r for r in falso.llamadas if "telefono[eq]" in r)
    assert reintento.count("filter=") == 1
    assert "or(" in reintento
    assert "50761149077" in reintento and "61149077" in reintento


def test_el_reintento_llega_hasta_la_pantalla(con_key, cliente, monkeypatch):
    monkeypatch.setenv("AJUSTES_ADMINS", "genesis")
    falso = TwentyFalso(mensajes_tel=[
        msg("2026-10-04T14:50:00Z", nombre="Nedjaira",
            telefono="50761149077", texto="¿Llegó mi pedido?")])
    monkeypatch.setattr(crm_twenty, "_twenty", falso)
    texto = cliente.get("/control", params={"abrir": "LEAD-86"}).text
    assert "¿Llegó mi pedido?" in texto
    assert "Sin chat disponible." not in texto


def test_sin_mensajes_de_verdad_sigue_diciendo_sin_chat(con_key, cliente, monkeypatch):
    monkeypatch.setenv("AJUSTES_ADMINS", "genesis")
    falso = TwentyFalso(mensajes_tel=[])
    monkeypatch.setattr(crm_twenty, "_twenty", falso)
    assert crm_twenty.ficha_de_lead(LEAD_SIN_CASAR) is None
    crm_twenty._fichas.clear()
    texto = cliente.get("/control", params={"abrir": "LEAD-86"}).text
    assert "Sin chat disponible." in texto


def test_el_cache_de_60s_evita_la_doble_consulta(con_key, monkeypatch):
    falso = TwentyFalso(mensajes_tel=[])
    monkeypatch.setattr(crm_twenty, "_twenty", falso)
    crm_twenty.ficha_de_lead(LEAD_SIN_CASAR)
    consultas = len(falso.llamadas)
    assert consultas > 0
    crm_twenty.ficha_de_lead(LEAD_SIN_CASAR)
    assert len(falso.llamadas) == consultas  # no martilla a Twenty


def test_un_lead_sin_celular_y_sin_casar_no_inventa_nada(con_key, monkeypatch):
    falso = TwentyFalso(mensajes_tel=[
        msg("2026-10-04T14:50:00Z", texto="no debería salir")])
    monkeypatch.setattr(crm_twenty, "_twenty", falso)
    ficha = crm_twenty.ficha_de_lead(
        {"id": "muestra-LEAD-83", "ref": "LEAD-83", "pp": "PP-70190",
         "celular": ""})
    assert ficha is None
    assert not any("telefono[eq]" in r for r in falso.llamadas)
