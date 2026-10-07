"""Mi CRM (/mi-crm) — esqueleto navegable del BLOQUE 20.

Lo que se prueba, por regla:

- La vista renderiza CON y SIN datos; sin Linear la pantalla dice que es
  la muestra; sin `Resp:` que case, dice por qué el tablero sale vacío.
- Las 4 columnas del CRM chico y NUNCA Nuevo ni Hablando.
- TODOS los botones apagados presentes, disabled y con «Todavía no» —
  regla dura de Abraham: nada que parezca funcionar y no guarde.
- CERO rutas POST bajo /mi-crm (es un módulo de solo lectura).
- El estado-hueco honesto cuando el token de Twenty está neutralizado.
- BLOQUE 29: el «Ver chat» de una fila apunta a /chat/<ref> en data-href
  y va apagado (la ruta no existe todavía).

Datos QA solamente — acá no entra ningún nombre de cliente real.
"""

import re

import pytest

from app import agenda, linear_leads, mi_crm


@pytest.fixture(autouse=True)
def tablero_de_muestra(monkeypatch, db_limpia):
    """Sin Linear (modo muestra) y sin Twenty, como el 8095."""
    monkeypatch.delenv("LINEAR_API_KEY", raising=False)
    monkeypatch.delenv("TWENTY_API_KEY", raising=False)
    linear_leads.reiniciar_muestra()


def _leads_qa():
    """Un tablero QA chiquito: dos de «Flor QA», uno de otra persona."""
    base = {
        "id": "qa-1", "ref": "LEAD-901", "url": "", "titulo": "",
        "nombre": "Cliente QA Uno", "pp": "PP-90001", "descripcion": "",
        "celular": "6000-0001", "wa": "", "estado": "COTIZADO",
        "estado_nombre": "Cotizado", "estado_ficha": None, "cerrado": False,
        "etiquetas": [], "origen": "WhatsApp", "interes": "Plantas",
        "pago": "", "motivo": "", "motivo_clave": "", "resp": "Flor QA",
        "te_toca": False, "creado": "", "dias": 1, "hace": "hace 1 día",
    }
    return [
        dict(base),
        dict(base, id="qa-2", ref="LEAD-902", nombre="Cliente QA Dos",
             estado="POR_AGENDAR", pago="Abono 50%"),
        dict(base, id="qa-3", ref="LEAD-903", nombre="Cliente QA Ajeno",
             resp="Otra QA", estado="COTIZADO"),
        dict(base, id="qa-4", ref="LEAD-904", nombre="Cliente QA Nuevo",
             estado="NUEVO"),
    ]


@pytest.fixture
def con_leads_qa(monkeypatch):
    monkeypatch.setattr(linear_leads, "listar",
                        lambda refrescar=False: _leads_qa())
    monkeypatch.setattr(agenda, "responsable_de_empleada",
                        lambda empleada: "Flor QA")


# ---------------------------------------------------------------------------
# Renderiza con y sin datos, y dice la verdad de la fuente
# ---------------------------------------------------------------------------

def test_abre_y_dice_que_es_la_muestra(cliente):
    r = cliente.get("/mi-crm")
    assert r.status_code == 200
    assert "Mi CRM" in r.text
    assert mi_crm.AVISO_MUESTRA in r.text


def test_sin_resp_que_case_lo_dice_y_no_inventa(cliente):
    # Génesis no casa con ninguna Resp: de la muestra.
    texto = cliente.get("/mi-crm").text
    assert mi_crm.AVISO_SIN_RESP in texto
    assert "Sin datos en pruebas." in texto


def test_con_datos_pinta_solo_lo_del_responsable(cliente, con_leads_qa):
    texto = cliente.get("/mi-crm").text
    assert "Cliente QA Uno" in texto          # COTIZADO suyo
    assert "Cliente QA Dos" in texto          # POR_AGENDAR suyo
    assert "Cliente QA Ajeno" not in texto    # de otra persona
    assert "Cliente QA Nuevo" not in texto    # NUEVO no es columna del chico
    # La tarjeta abre la ficha EXISTENTE de Control.
    assert "/control?abrir=LEAD-901" in texto


def test_las_columnas_son_las_4_del_crm_chico(cliente):
    texto = cliente.get("/mi-crm").text
    for titulo in ("Cotizado", "Por agendar", "Agendado", "Ganado"):
        assert f'aria-label="{titulo}"' in texto
    assert 'aria-label="Nuevo"' not in texto
    assert 'aria-label="Hablando"' not in texto


def test_linear_caido_lo_dice_en_vez_de_inventar(cliente, monkeypatch):
    def revienta(refrescar=False):
        raise RuntimeError("Linear no contesta")
    monkeypatch.setattr(linear_leads, "listar", revienta)
    texto = cliente.get("/mi-crm").text
    assert "Linear no contestó" in texto
    assert "Nada se inventa" in texto


# ---------------------------------------------------------------------------
# Los huecos honestos de las cajas
# ---------------------------------------------------------------------------

def test_sin_twenty_la_caja_por_responder_lo_dice(cliente):
    assert mi_crm.AVISO_TWENTY_PRUEBAS in cliente.get("/mi-crm").text


def test_con_twenty_la_caja_dice_que_el_calculo_llega_despues(
        cliente, monkeypatch):
    monkeypatch.setenv("TWENTY_API_KEY", "clave-de-prueba")
    texto = cliente.get("/mi-crm").text
    assert mi_crm.AVISO_RESPONDER_LUEGO in texto
    assert mi_crm.AVISO_TWENTY_PRUEBAS not in texto


def test_peticiones_y_seguimientos_declaran_su_hueco(cliente):
    texto = cliente.get("/mi-crm").text
    assert mi_crm.AVISO_PETICIONES in texto
    assert mi_crm.AVISO_SEGUIMIENTOS in texto
    assert mi_crm.AVISO_FALTA_COTIZAR in texto


# ---------------------------------------------------------------------------
# Botones apagados: disabled y con «Todavía no» — nada que finja guardar
# ---------------------------------------------------------------------------

def test_todos_los_botones_de_accion_van_apagados(cliente, con_leads_qa):
    texto = cliente.get("/mi-crm").text
    for rotulo in ("Pedir compra — Todavía no", "+ Nuevo lead — Todavía no",
                   "Agendar — Todavía no"):
        assert rotulo in texto
    # Cada botón que dice «Todavía no» lleva el atributo disabled.
    for boton in re.findall(r"<button[^>]*>[^<]*Todavía no[^<]*</button>",
                            texto):
        assert "disabled" in boton, boton


def test_no_hay_ni_un_form_en_la_pantalla(cliente, con_leads_qa):
    assert "<form" not in cliente.get("/mi-crm").text


def test_cero_rutas_post_bajo_mi_crm():
    from app.main import app
    for ruta in app.routes:
        if str(getattr(ruta, "path", "")).startswith("/mi-crm"):
            assert "POST" not in (getattr(ruta, "methods", None) or set())


# ---------------------------------------------------------------------------
# El celular: dos pestañas por GET, una etapa a la vez
# ---------------------------------------------------------------------------

def test_las_dos_pestanas_son_enlaces_get(cliente):
    texto = cliente.get("/mi-crm").text
    assert 'href="/mi-crm"' in texto
    assert 'href="/mi-crm?pestana=tablero"' in texto


def test_la_pestana_tablero_muestra_una_etapa_a_la_vez(cliente, con_leads_qa):
    texto = cliente.get("/mi-crm",
                        params={"pestana": "tablero",
                                "etapa": "POR_AGENDAR"}).text
    # El selector de etapas viaja como enlaces GET.
    assert "/mi-crm?pestana=tablero&etapa=COTIZADO" in texto
    v = mi_crm.vista({"id": "genesis", "nombre": "Génesis"}, False,
                     pestana="tablero", etapa="POR_AGENDAR")
    assert v["columna_movil"]["clave"] == "POR_AGENDAR"
    assert [t["nombre"] for t in v["columna_movil"]["tarjetas"]] == \
        ["Cliente QA Dos"]


def test_una_etapa_invalida_cae_a_cotizado(cliente):
    v = mi_crm.vista({"id": "genesis"}, False, pestana="tablero",
                     etapa="NUEVO")
    assert v["etapa"] == "COTIZADO"


# ---------------------------------------------------------------------------
# BLOQUE 29: el destino canónico del chat ya viaja en el markup
# ---------------------------------------------------------------------------

def test_url_chat_es_la_forma_canonica():
    assert mi_crm.url_chat("LEAD-901") == "/chat/LEAD-901"


def test_una_fila_de_por_responder_lleva_ver_chat_apagado_con_data_href(
        cliente, monkeypatch):
    monkeypatch.setattr(mi_crm, "caja_por_responder", lambda: {
        "filas": [{"nombre": "Cliente QA Uno",
                   "detalle": "«¿Tienen romero?»",
                   "chat_href": mi_crm.url_chat("LEAD-901")}],
        "aviso": ""})
    texto = cliente.get("/mi-crm").text
    assert 'data-href="/chat/LEAD-901"' in texto
    assert "Ver chat — Todavía no" in texto
    # Y no existe un enlace VIVO a esa ruta futura (solo el data-href
    # del botón apagado).
    assert '<a href="/chat/' not in texto


# ---------------------------------------------------------------------------
# BLOQUE 59 · que se lea como tablero (7/10/2026)
# ---------------------------------------------------------------------------
#
# Medido antes de tocar nada, con el elemento renderizado: a 1024 y a 1280
# de ventana las CUATRO etapas salían en DOS FILAS de dos cuadros, porque
# la reja solo se ponía en fila desde 1120px de lienzo (≈1420 de ventana).
# Y «Seguimientos» era una caja de 152px de alto para decir que no hay
# ninguno, al lado de «Por responder».
# ---------------------------------------------------------------------------

_CSS_MC = "app/static/diseno-mi-crm.css"
_HTML_MC = "app/plantillas/mi_crm.html"


def _apretado(ruta):
    return re.sub(r"\s+", "", open(ruta).read())


def test_las_cuatro_etapas_van_una_al_lado_de_la_otra():
    """Un carril de kanban, no una reja que se parte: columnas de ancho
    fijo que crecen si sobra sitio y se corren de lado si no caben. Una
    sola regla, no tres escalones."""
    css = _apretado(_CSS_MC)
    assert ".mc-board{display:flex" in css
    assert "flex:10200px" in css          # ni encoge por debajo de 200, ni deja de crecer
    # Y ya no quedan escalones de reja para el tablero.
    assert "grid-template-columns:repeat(4" not in css.split(".mc-board")[1][:400]


def test_seguimientos_es_una_tira_y_por_responder_sigue_siendo_caja():
    """Lo primero lo pidió Abraham; lo segundo está ABIERTO con él, así
    que no se decide acá. La tira ya existe: el día que diga que sí, es
    cambiar una clase."""
    html = open(_HTML_MC).read()
    tira = html.split('class="mc-tira"', 1)[1].split("</section>", 1)[0]
    assert "Seguimientos" in tira
    assert '<p class="mc-hueco">' not in tira      # el párrafo dentro de la caja se fue
    caja = html.split('class="mc-bx" aria-label="Por responder"', 1)[1]
    assert "Por responder" in caja.split("</section>", 1)[0]


def test_el_aviso_de_seguimientos_lo_sigue_diciendo_python():
    """La tira cambia la FORMA, no el dato: el texto sigue saliendo de
    `mi_crm`, no escrito en la plantilla."""
    html = open(_HTML_MC).read()
    assert "v.seguimientos.aviso" in html


def test_se_ve_que_las_columnas_siguen_al_costado_en_mi_crm():
    """Misma señal que Compras y por la misma razón. La barra se pinta a
    mano (en macOS la de fábrica es flotante y se esconde) y la orilla en
    degradado se apaga sola al llegar al final, así que no puede mentir."""
    css = _apretado(_CSS_MC)
    assert "overflow-x:auto" in css
    assert "::-webkit-scrollbar-thumb" in css
    assert "scroll-snap-align:start" in css
    assert "position:sticky;right:0" in css
    assert '<b class="mc-orilla" aria-hidden="true"></b>' in open(_HTML_MC).read()


def test_la_orilla_no_dice_nada_y_no_se_lee():
    """Es paint: ni texto, ni enlace, ni foco. Un lector de pantalla no
    tiene por qué tropezarse con un degradado."""
    html = open(_HTML_MC).read()
    assert 'class="mc-orilla" aria-hidden="true"></b>' in html
