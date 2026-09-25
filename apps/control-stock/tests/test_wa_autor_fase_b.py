"""Fase B: el autor viaja a Twenty y el bot lo comenta en Linear.

Twenty y Linear se sustituyen por dobles: aquí se prueba la decisión (a qué
mensaje se le pone autor, cuándo se comenta y cuándo NO), no la red.
"""

import pytest

from app import crm_twenty, linear_leads, wa_autor


class TwentyFalso:
    """Un Twenty de mentira con los tres mensajes de un chat."""

    def __init__(self, mensajes=None, issue="ISSUE-1"):
        self.mensajes = mensajes or []
        self.issue = issue
        self.autores = {}          # id de mensaje -> autor escrito
        self.consultas_previo = []

    def mensaje_por_wa_id(self, wa_id):
        for m in self.mensajes:
            if m.get("waMessageId") == wa_id:
                return m
        return None

    def poner_autor(self, id_mensaje, autor):
        self.autores[id_mensaje] = autor
        return True

    def mensaje_previo(self, persona_id, fecha):
        self.consultas_previo.append((persona_id, fecha))
        # Lo que de verdad hace Twenty con un `and(...)` bien armado: el
        # anterior DE ESA PERSONA, no el último de todo el sistema.
        de_ella = [m for m in self.mensajes
                   if m.get("personaId") == persona_id and m.get("fecha") < fecha]
        return max(de_ella, key=lambda m: m["fecha"]) if de_ella else None

    def issue_de_persona(self, persona_id):
        return self.issue if persona_id else ""


def mensaje(wa_id, direccion, fecha, persona="PER-1", texto="hola", id_=None):
    return {"id": id_ or ("MSG-" + wa_id), "waMessageId": wa_id,
            "direccion": direccion, "fecha": fecha, "personaId": persona,
            "texto": texto}


@pytest.fixture
def twenty(monkeypatch):
    doble = TwentyFalso()
    monkeypatch.setattr(crm_twenty, "twenty_configurado", lambda: True)
    for nombre in ("mensaje_por_wa_id", "poner_autor", "mensaje_previo",
                   "issue_de_persona"):
        monkeypatch.setattr(crm_twenty, nombre, getattr(doble, nombre))
    return doble


@pytest.fixture
def comentarios(monkeypatch):
    puestos = []
    monkeypatch.setattr(linear_leads, "comentar",
                        lambda issue, texto, autor="": puestos.append((issue, texto)))
    wa_autor.olvidar_avisos()
    return puestos


# ---------------------------------------------------------------------------
# El extracto
# ---------------------------------------------------------------------------

def test_el_extracto_cabe_en_una_linea():
    assert wa_autor.extracto("  hola   qué   tal  ") == "hola qué tal"
    assert wa_autor.extracto("línea uno\nlínea dos") == "línea uno línea dos"


def test_el_extracto_largo_se_corta_con_puntos():
    largo = "a" * 200
    corto = wa_autor.extracto(largo)
    assert len(corto) <= wa_autor.LARGO_EXTRACTO + 1
    assert corto.endswith("…")


# ---------------------------------------------------------------------------
# Escribir el autor
# ---------------------------------------------------------------------------

def test_el_autor_se_escribe_en_el_mensaje_de_twenty(db_limpia, twenty, comentarios):
    twenty.mensajes = [mensaje("WA-1", "SALIENTE", "2026-09-25T10:00:00Z")]
    wa_autor.nombrar("3", "Mary")
    wa_autor.anotar("WA-1", "3", "app")
    cuenta = wa_autor.aplicar_pendientes()
    assert cuenta["aplicados"] == 1
    assert twenty.autores == {"MSG-WA-1": "Mary"}


def test_un_dispositivo_sin_nombre_no_se_disfraza_de_persona(db_limpia, twenty, comentarios):
    twenty.mensajes = [mensaje("WA-1", "SALIENTE", "2026-09-25T10:00:00Z")]
    wa_autor.anotar("WA-1", "4", "app")
    wa_autor.aplicar_pendientes()
    assert twenty.autores == {"MSG-WA-1": "Equipo · dispositivo 4"}


def test_lo_que_manda_nuestro_sistema_sale_como_sistema(db_limpia, twenty, comentarios):
    twenty.mensajes = [mensaje("WA-1", "SALIENTE", "2026-09-25T10:00:00Z")]
    wa_autor.anotar("WA-1", "2", "api")
    wa_autor.aplicar_pendientes()
    assert twenty.autores == {"MSG-WA-1": "Sistema"}


def test_el_pendiente_se_borra_al_aplicarse(db_limpia, twenty, comentarios):
    twenty.mensajes = [mensaje("WA-1", "SALIENTE", "2026-09-25T10:00:00Z")]
    wa_autor.anotar("WA-1", "3")
    wa_autor.aplicar_pendientes()
    with wa_autor._db() as con:
        assert con.execute("SELECT COUNT(*) FROM wa_pendiente").fetchone()[0] == 0


# ---------------------------------------------------------------------------
# El candado de privacidad: solo encima de lo que Twenty YA tenía
# ---------------------------------------------------------------------------

def test_un_mensaje_que_twenty_no_tiene_no_se_crea(db_limpia, twenty, comentarios):
    """El chat que el CRM no sigue no entra por esta puerta: el pendiente se
    queda esperando y se borra solo a las 24 horas."""
    twenty.mensajes = []
    wa_autor.anotar("WA-DESCONOCIDO", "3")
    cuenta = wa_autor.aplicar_pendientes()
    assert cuenta == {"aplicados": 0, "comentados": 0, "esperando": 1, "fallaron": 0}
    assert twenty.autores == {}
    assert comentarios == []
    # Y sigue en la cola: si OpenWA lo guarda en un minuto, se aplica.
    with wa_autor._db() as con:
        assert con.execute("SELECT COUNT(*) FROM wa_pendiente").fetchone()[0] == 1


def test_el_que_esperaba_se_aplica_cuando_el_mensaje_llega(db_limpia, twenty, comentarios):
    """La carrera real: los dos webhooks llegan casi juntos y el orden no
    está garantizado."""
    wa_autor.nombrar("3", "Mary")
    wa_autor.anotar("WA-1", "3")
    assert wa_autor.aplicar_pendientes()["esperando"] == 1
    twenty.mensajes = [mensaje("WA-1", "SALIENTE", "2026-09-25T10:00:00Z")]
    assert wa_autor.aplicar_pendientes()["aplicados"] == 1
    assert twenty.autores == {"MSG-WA-1": "Mary"}


def test_sin_twenty_no_se_intenta_nada(db_limpia, monkeypatch, comentarios):
    monkeypatch.setattr(crm_twenty, "twenty_configurado", lambda: False)
    wa_autor.anotar("WA-1", "3")
    assert wa_autor.aplicar_pendientes()["aplicados"] == 0


# ---------------------------------------------------------------------------
# «Mary respondió»: solo la PRIMERA de cada tanda
# ---------------------------------------------------------------------------

def test_la_primera_respuesta_tras_el_cliente_se_comenta(db_limpia, twenty, comentarios):
    twenty.mensajes = [
        mensaje("WA-0", "ENTRANTE", "2026-09-25T09:00:00Z", texto="¿tienen monstera?"),
        mensaje("WA-1", "SALIENTE", "2026-09-25T09:20:00Z",
                texto="¡Hola! Sí, tenemos en maceta de 3 galones."),
    ]
    wa_autor.nombrar("3", "Mary")
    wa_autor.anotar("WA-1", "3")
    cuenta = wa_autor.aplicar_pendientes()
    assert cuenta["comentados"] == 1
    assert comentarios == [
        ("ISSUE-1", "Mary respondió: «¡Hola! Sí, tenemos en maceta de 3 galones.»")]


def test_la_segunda_seguida_no_comenta(db_limpia, twenty, comentarios):
    """Un chat de veinte mensajes dejaría veinte comentarios y el issue se
    volvería ilegible. La conversación entera se ve en Control."""
    twenty.mensajes = [
        mensaje("WA-0", "ENTRANTE", "2026-09-25T09:00:00Z"),
        mensaje("WA-1", "SALIENTE", "2026-09-25T09:20:00Z"),
        mensaje("WA-2", "SALIENTE", "2026-09-25T09:21:00Z", texto="te paso el precio"),
    ]
    wa_autor.nombrar("3", "Mary")
    wa_autor.anotar("WA-2", "3")
    cuenta = wa_autor.aplicar_pendientes()
    assert cuenta["aplicados"] == 1      # el autor SÍ se escribe siempre
    assert cuenta["comentados"] == 0     # el comentario no
    assert comentarios == []


def test_nuestra_primera_palabra_en_el_chat_tambien_comenta(db_limpia, twenty, comentarios):
    twenty.mensajes = [mensaje("WA-1", "SALIENTE", "2026-09-25T09:20:00Z",
                               texto="Hola, le escribo del vivero")]
    wa_autor.anotar("WA-1", "0")
    assert wa_autor.aplicar_pendientes()["comentados"] == 1
    assert comentarios[0][1].startswith("Teléfono respondió:")


def test_el_relevo_de_otro_empleado_no_comenta_de_nuevo(db_limpia, twenty, comentarios):
    """Si Rubén contesta justo después de Mary, la tanda ya estaba abierta."""
    twenty.mensajes = [
        mensaje("WA-0", "ENTRANTE", "2026-09-25T09:00:00Z"),
        mensaje("WA-1", "SALIENTE", "2026-09-25T09:20:00Z"),
        mensaje("WA-2", "SALIENTE", "2026-09-25T09:25:00Z"),
    ]
    wa_autor.nombrar("5", "Ruben")
    wa_autor.anotar("WA-2", "5")
    assert wa_autor.aplicar_pendientes()["comentados"] == 0


def test_un_chat_sin_lead_no_tiene_donde_anotarse(db_limpia, twenty, comentarios):
    twenty.mensajes = [mensaje("WA-1", "SALIENTE", "2026-09-25T09:20:00Z")]
    twenty.issue = ""
    wa_autor.anotar("WA-1", "3")
    cuenta = wa_autor.aplicar_pendientes()
    assert cuenta["aplicados"] == 1 and cuenta["comentados"] == 0
    assert comentarios == []


def test_un_mensaje_sin_texto_comenta_sin_comillas_vacias(db_limpia, twenty, comentarios):
    twenty.mensajes = [mensaje("WA-1", "SALIENTE", "2026-09-25T09:20:00Z", texto="")]
    wa_autor.nombrar("3", "Mary")
    wa_autor.anotar("WA-1", "3")
    wa_autor.aplicar_pendientes()
    assert comentarios == [("ISSUE-1", "Mary respondió.")]


# ---------------------------------------------------------------------------
# La trampa del 25/09: dos `filter=` no se suman en Twenty
# ---------------------------------------------------------------------------

def test_el_previo_se_pide_de_esa_persona_y_antes_de_esa_fecha(db_limpia, twenty, comentarios):
    """La consulta tiene que llevar LAS DOS condiciones. Con dos `filter=`
    sueltos el segundo pisa al primero y Twenty devuelve el último mensaje de
    todo el sistema — el de un desconocido."""
    twenty.mensajes = [mensaje("WA-1", "SALIENTE", "2026-09-25T09:20:00Z",
                               persona="PER-7")]
    wa_autor.anotar("WA-1", "3")
    wa_autor.aplicar_pendientes()
    assert twenty.consultas_previo == [("PER-7", "2026-09-25T09:20:00Z")]


def test_la_url_del_previo_lleva_un_solo_filter_con_and(monkeypatch):
    """La prueba de la URL de verdad, sin salir a la red."""
    pedidas = []

    def espiar(ruta):
        pedidas.append(ruta)
        return {"data": {"mensajesWhatsapp": []}}

    monkeypatch.setattr(crm_twenty, "twenty_configurado", lambda: True)
    monkeypatch.setattr(crm_twenty, "_twenty", espiar)
    crm_twenty.mensaje_previo("PER-7", "2026-09-25T09:20:00Z")
    assert len(pedidas) == 1
    ruta = pedidas[0]
    assert ruta.count("filter=") == 1
    assert "and(" in ruta and "personaId" in ruta and "fecha" in ruta


# ---------------------------------------------------------------------------
# Fail-soft: un tropiezo nunca tumba la pantalla
# ---------------------------------------------------------------------------

def test_si_twenty_no_responde_el_pendiente_se_queda(db_limpia, twenty, comentarios, monkeypatch):
    def explota(_):
        raise RuntimeError("Twenty caído")

    monkeypatch.setattr(crm_twenty, "mensaje_por_wa_id", explota)
    wa_autor.anotar("WA-1", "3")
    cuenta = wa_autor.aplicar_pendientes()
    assert cuenta["fallaron"] == 1 and cuenta["aplicados"] == 0
    with wa_autor._db() as con:
        assert con.execute("SELECT COUNT(*) FROM wa_pendiente").fetchone()[0] == 1


def test_si_falta_el_campo_autor_en_twenty_se_avisa_una_sola_vez(
        db_limpia, twenty, comentarios, monkeypatch):
    """El aplicador corre en cada pintada de Control: sin el candado del
    aviso, un campo que falta llenaría el log."""
    avisos = []
    monkeypatch.setattr(linear_leads, "registro_aviso", avisos.append)

    def explota(*_):
        raise RuntimeError("400 field autor does not exist")

    monkeypatch.setattr(crm_twenty, "poner_autor", explota)
    twenty.mensajes = [mensaje("WA-1", "SALIENTE", "2026-09-25T09:20:00Z"),
                       mensaje("WA-2", "SALIENTE", "2026-09-25T09:21:00Z")]
    wa_autor.anotar("WA-1", "3")
    wa_autor.anotar("WA-2", "3")
    wa_autor.aplicar_pendientes()
    wa_autor.aplicar_pendientes()
    assert len(avisos) == 1
    assert "autor" in avisos[0]


def test_si_linear_falla_el_autor_igual_queda_escrito(db_limpia, twenty, monkeypatch):
    """Lo importante es el autor; el comentario es el extra."""
    wa_autor.olvidar_avisos()
    monkeypatch.setattr(linear_leads, "registro_aviso", lambda _: None)

    def explota(*_, **__):
        raise RuntimeError("Linear caído")

    monkeypatch.setattr(linear_leads, "comentar", explota)
    twenty.mensajes = [mensaje("WA-1", "SALIENTE", "2026-09-25T09:20:00Z")]
    wa_autor.anotar("WA-1", "3")
    cuenta = wa_autor.aplicar_pendientes()
    assert cuenta["aplicados"] == 1 and cuenta["comentados"] == 0
    assert twenty.autores == {"MSG-WA-1": "Equipo · dispositivo 3"}


def test_ante_la_duda_no_se_comenta(db_limpia, twenty, comentarios, monkeypatch):
    """Sin persona no se puede saber si rompe el silencio: mejor que falte
    un comentario a llenar el issue de ruido."""
    twenty.mensajes = [mensaje("WA-1", "SALIENTE", "2026-09-25T09:20:00Z",
                               persona="")]
    wa_autor.anotar("WA-1", "3")
    cuenta = wa_autor.aplicar_pendientes()
    assert cuenta["aplicados"] == 1 and cuenta["comentados"] == 0
