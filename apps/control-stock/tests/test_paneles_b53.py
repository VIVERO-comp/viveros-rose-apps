"""BLOQUE 53, frente PANELES Y MENÚ: A5 (el panel abre sin recargar),
A6 (el menú está siempre) y A12 (de quién es la venta y de qué lead).

La sustancia que estas pruebas amarran:

- A5: el panel lo sigue armando el SERVIDOR. Hay un endpoint que devuelve
  ESE MISMO pedazo ya armado (/control/panel, /venta/panel) para que el
  navegador solo lo muestre; la URL de siempre (?abrir=) sigue abriéndolo
  en la página completa; y los enlaces siguen siendo <a href> de verdad,
  así que sin JS nada cambia. Los CANDADOS valen igual en el pedazo que
  en la página: eso se mide, no se supone.
- A6: toda pantalla que pinta la hamburguesa tiene que traer su menú. Si
  no lo trae, el botón escribe la cookie `menu=0` y colapsa el menú de
  TODO el programa — que es exactamente lo que pasaba en /contactos.
- A12: el panel de Vender dice de qué contacto y de qué lead es, y
  cuando el dato no existe la fila LO DICE en vez de desaparecer.
"""

from datetime import datetime

import pytest

from app import contactos, control, cotizaciones, linear_leads, ventas
from app.datos import ZONA_PANAMA, _db
from test_cot_lead import OdooCotLead


# ---------------------------------------------------------------------------
# A6 — el menú está en TODAS las pantallas
# ---------------------------------------------------------------------------

# Las pantallas del menú por su ruta. Cada una tiene que traer el menú
# compartido: o el <nav> de base.html (que en computadora es el costado,
# nav{grid-area:lado}) o el aside.lado de las pantallas de tablero. Son
# dos PIELES de la misma lista de Python (request.state.menu_nav).
PANTALLAS_CON_MENU = ("/", "/?tab=stock", "/venta", "/control", "/calendario",
                      "/contactos", "/pedidos", "/compras", "/finanzas")


def _trae_menu(cuerpo):
    return "<nav>" in cuerpo or 'class="lado"' in cuerpo


def _pagina(cliente, ruta):
    """El HTML de una pantalla, o None si esta sesión no la abre (el
    rechazo por rol es texto pelado, no una pantalla): ahí no hay menú
    que exigir."""
    cuerpo = cliente.get(ruta).text
    return cuerpo if "<!DOCTYPE html>" in cuerpo else None


@pytest.mark.parametrize("ruta", PANTALLAS_CON_MENU)
def test_toda_pantalla_del_menu_trae_su_menu(cliente, ruta):
    """A6: el menú no se esconde solo — cuando «desaparece» es que no
    está en el HTML. /contactos no incluía `_nav.html` y era la única sin
    menú (medido en el 8095 el 7/10/2026: cero elementos <nav>)."""
    cuerpo = _pagina(cliente, ruta)
    if cuerpo is None:
        pytest.skip(f"{ruta} no la abre esta sesión")
    assert _trae_menu(cuerpo), ruta


@pytest.mark.parametrize("ruta", PANTALLAS_CON_MENU)
def test_la_hamburguesa_nunca_queda_sin_menu_que_colapsar(cliente, ruta):
    """LA REGLA GENERAL QUE HABRÍA ATRAPADO EL BUG DE A6, Y QUE LO VA A
    CUIDAR DE ACÁ EN ADELANTE.

    Una pantalla con hamburguesa y SIN menú no es un detalle cosmético:
    es una trampa que se contagia. La hamburguesa (menu.js) escribe la
    cookie `menu`, y esa cookie la lee el SERVIDOR en TODAS las demás
    pantallas para pintarlas con `body.menu-oculto`. Entonces, en una
    pantalla sin menú, el botón no hace nada visible —el gesto natural es
    apretarlo otra vez— y de paso deja el menú del PROGRAMA COMPLETO
    colapsado. Eso es justo lo que pasaba al entrar a /contactos y lo que
    el dueño vio como «se escondió solo y sigue escondido en Pedidos».

    Por eso la regla se verifica por pantalla y no solo en Contactos: lo
    que falla la próxima vez va a ser otra pantalla nueva que pinte su
    cabecera y se olvide del `{% include '_nav.html' %}`."""
    cuerpo = _pagina(cliente, ruta)
    if cuerpo is None:
        pytest.skip(f"{ruta} no la abre esta sesión")
    if 'class="hamb"' in cuerpo or "icono hamb" in cuerpo:
        assert _trae_menu(cuerpo), ruta


def test_contactos_marca_su_pestana_en_el_menu(cliente):
    for ruta in ("/contactos", "/contactos/lv1"):
        cuerpo = cliente.get(ruta).text
        assert 'href="/contactos" class="on"' in cuerpo, ruta


# ---------------------------------------------------------------------------
# A5 — el CRM: el panel se pide al servidor, la URL sigue valiendo
# ---------------------------------------------------------------------------

@pytest.fixture
def crm_muestra(monkeypatch, db_limpia):
    monkeypatch.delenv("LINEAR_API_KEY", raising=False)
    monkeypatch.delenv("AJUSTES_ADMINS", raising=False)
    linear_leads.reiniciar_muestra()
    control.iniciar_tablas()
    monkeypatch.setattr(control, "vista_empleado_apagada", lambda: False)


@pytest.fixture
def de_dueno(monkeypatch):
    monkeypatch.setenv("AJUSTES_ADMINS", "genesis")


def test_la_url_de_siempre_sigue_abriendo_el_panel(cliente, crm_muestra,
                                                   de_dueno):
    """A5 no puede romper la dirección: entrar directo a ?abrir= abre el
    panel en la página completa, como hasta hoy."""
    cuerpo = cliente.get("/control", params={"vista": "estado",
                                             "abrir": "LEAD-86"}).text
    assert 'class="panel-der"' in cuerpo
    assert 'class="telon"' in cuerpo


def test_el_panel_viaja_dentro_de_su_caja(cliente, crm_muestra, de_dueno):
    """La caja es el lugar donde el navegador mete el pedazo pedido. Sin
    panel abierto la caja existe y está vacía: nada de un lead viaja «por
    si acaso»."""
    sin_abrir = cliente.get("/control", params={"vista": "estado"}).text
    assert 'data-panel-fuente="/control/panel"' in sin_abrir
    assert 'class="panel-der"' not in sin_abrir
    con_abierto = cliente.get("/control", params={"vista": "estado",
                                                  "abrir": "LEAD-86"}).text
    caja = con_abierto[con_abierto.index('data-panel-fuente="/control/panel"'):]
    assert 'class="panel-der"' in caja


def test_el_pedazo_del_panel_es_el_mismo_panel_de_la_pagina(
        cliente, crm_muestra, de_dueno):
    """UNA sola fuente (main._panel_lead_contexto): lo que devuelve el
    endpoint del pedazo es exactamente lo que la página incluye."""
    params = {"vista": "estado", "abrir": "LEAD-86"}
    pagina = cliente.get("/control", params=params).text
    pedazo = cliente.get("/control/panel", params=params).text
    assert pedazo.lstrip().startswith("<a class=\"telon\"")
    assert "<!DOCTYPE html>" not in pedazo        # es un pedazo, no una página
    # El panel de la página, recortado desde el telón, es el del pedazo.
    de_la_pagina = pagina[pagina.index('<a class="telon"'):]
    de_la_pagina = de_la_pagina[:de_la_pagina.index("</aside>") + len("</aside>")]
    assert de_la_pagina.strip() == pedazo.strip()


def test_el_pedazo_sin_abrir_sale_vacio_y_asi_es_como_se_cierra(
        cliente, crm_muestra, de_dueno):
    """Cerrar es la MISMA operación que abrir: el enlace de cerrar no
    lleva ?abrir=, el servidor contesta vacío y la caja queda vacía."""
    pedazo = cliente.get("/control/panel", params={"vista": "estado"}).text
    assert pedazo.strip() == ""


def test_los_enlaces_del_panel_siguen_siendo_enlaces_de_verdad(
        cliente, crm_muestra, de_dueno):
    """Degradación: el JS solo INTERCEPTA. La tarjeta y los cerrar siguen
    llevando su href, así que sin JS la pantalla funciona como siempre."""
    cuerpo = cliente.get("/control", params={"vista": "estado",
                                             "abrir": "LEAD-86"}).text
    assert '<a class="ctl-zona" draggable="false" data-panel-liga href="/control?' in cuerpo
    assert 'class="telon" href="/control?vista=estado" data-panel-liga' in cuerpo
    assert 'dc-cerrar" href="/control?vista=estado" data-panel-liga' in cuerpo


def test_el_pedazo_arrastra_el_filtro_ver_igual_que_la_pagina(
        cliente, crm_muestra, de_dueno):
    """El «Ver a:» no se pierde al abrir ni al cerrar por el pedazo: el
    ver_query lo sigue componiendo Python."""
    pedazo = cliente.get("/control/panel", params={
        "vista": "estado", "ver": "Mary", "abrir": "LEAD-89"}).text
    assert 'href="/control?vista=estado&amp;ver=Mary"' in pedazo


def test_el_pedazo_aplica_el_mismo_candado_de_la_plata(
        cliente, crm_muestra, monkeypatch):
    """EL PUNTO DELICADO: el pedazo no es una segunda puerta. Sin permiso
    de ver la plata de ESE lead, el monto no viaja en el pedazo tampoco —
    y lo que llega es la leyenda que explica quién la ve."""
    # Sin AJUSTES_ADMINS, Génesis no es dueña y no tiene etiqueta Resp:,
    # así que no ve la plata de ningún lead ajeno.
    pedazo = cliente.get("/control/panel", params={
        "vista": "estado", "abrir": "LEAD-86"}).text
    assert pedazo.strip(), "el panel ajeno SÍ se abre: ver todos es ver"
    assert control.LEYENDA_SIN_PLATA in pedazo or "dc-monto-nota" in pedazo
    # Y los botones que escriben no se pintan para quien no puede tocar.
    assert 'action="/control/senal' not in pedazo


# ---------------------------------------------------------------------------
# A5 — Vender: el mismo trato
# ---------------------------------------------------------------------------

class OdooVender:
    """El Odoo mínimo de test_vender_diseno: solo los estados."""

    def __init__(self):
        self.ordenes = {}

    def ejecutar(self, modelo, metodo, args, kw=None):
        if modelo == "sale.order" and metodo == "search_read":
            ids = args[0][0][2]
            return [{"id": i, **self.ordenes[i]} for i in ids if i in self.ordenes]
        raise NotImplementedError(f"{modelo}.{metodo} no está simulado aquí")


@pytest.fixture
def odoo_vender(monkeypatch):
    falso = OdooVender()
    monkeypatch.setattr(ventas, "_ejecutar", falso.ejecutar)
    return falso


def _venta(cliente_nombre="Ana QA", celular="6000-0001", lead_issue=None,
           lead_ref=None, orden="S00050", orden_id=501):
    with _db() as con:
        cur = con.execute(
            "INSERT INTO ventas_locales (creado_en, empleada, cliente,"
            " celular, orden_id, orden, total, estado, lead_issue, lead_ref)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (datetime.now(ZONA_PANAMA).isoformat(), "Génesis",
             cliente_nombre, celular, orden_id, orden, 100.0, "cotizacion",
             lead_issue, lead_ref))
        return cur.lastrowid


def test_vender_la_url_de_siempre_sigue_abriendo_el_panel(cliente, odoo_vender):
    n = _venta()
    cuerpo = cliente.get(f"/venta?abrir=v{n}").text
    assert 'class="vd-panel"' in cuerpo
    assert 'data-panel-fuente="/venta/panel"' in cuerpo


def test_vender_el_pedazo_es_el_mismo_panel(cliente, odoo_vender):
    n = _venta()
    pagina = cliente.get(f"/venta?abrir=v{n}").text
    pedazo = cliente.get(f"/venta/panel?abrir=v{n}").text
    assert "<!DOCTYPE html>" not in pedazo
    assert 'class="vd-panel"' in pedazo
    recorte = pagina[pagina.index('<a class="vd-telon"'):]
    recorte = recorte[:recorte.index("</aside>") + len("</aside>")]
    assert recorte.strip() == pedazo.strip()


def test_vender_el_pedazo_sin_abrir_sale_vacio(cliente, odoo_vender):
    _venta()
    assert cliente.get("/venta/panel").text.strip() == ""
    # Y un ?abrir= manoseado tampoco rompe nada: solo no abre panel.
    assert cliente.get("/venta/panel?abrir=v999").text.strip() == ""
    assert cliente.get("/venta/panel?abrir=basura").text.strip() == ""


def test_vender_los_cerrar_siguen_siendo_enlaces(cliente, odoo_vender):
    n = _venta()
    cuerpo = cliente.get(f"/venta?abrir=v{n}").text
    assert f'class="vd-telon" href="/venta#v-{n}" data-panel-liga' in cuerpo
    assert f'class="vd-p-x" href="/venta#v-{n}" data-panel-liga' in cuerpo
    assert 'class="vd-abrir" draggable="false" data-panel-liga href=' in cuerpo


# ---------------------------------------------------------------------------
# A12 — «De quién es»: Contacto y Lead
# ---------------------------------------------------------------------------

def test_a12_el_panel_dice_de_que_contacto_y_de_que_lead_es(
        cliente, odoo_vender):
    n = _venta(celular="6000-0001", lead_issue="LEAD-62")
    panel = cliente.get(f"/venta/panel?abrir=v{n}").text
    assert "De quién es" in panel
    assert "<b>Contacto</b>" in panel
    assert "<b>Lead</b>" in panel
    assert "Ana QA" in panel
    # El contacto abre SU PÁGINA de /contactos, no un buscador.
    assert 'href="/contactos/t60000001"' in panel
    # Y el lead abre su ficha en el CRM.
    assert 'href="/control?abrir=LEAD-62"' in panel


def test_a12_el_enlace_del_contacto_es_el_id_que_usa_contactos(
        cliente, odoo_vender):
    """PINEADO CONTRA Contactos: el id del enlace es el que esa pantalla
    arma de verdad (casamiento por teléfono normalizado). Si Contactos
    cambiara su clave, esta prueba se pone roja en vez de dejar un enlace
    muerto en silencio."""
    n = _venta(celular="+507 6000-0001")
    panel = cliente.get(f"/venta/panel?abrir=v{n}").text
    ids = {c["id"] for c in contactos.lista()["contactos"]}
    puestos = [i for i in ids if f'href="/contactos/{i}"' in panel]
    assert puestos, f"ningún id real de Contactos en el panel; hay {ids}"
    # Y la página de ese contacto abre de verdad (no es un 404 ni un
    # rebote al listado con error).
    r = cliente.get(f"/contactos/{puestos[0]}", follow_redirects=False)
    assert r.status_code == 200, r.headers.get("location")


def test_a12_sin_lead_la_fila_lo_dice_en_vez_de_desaparecer(
        cliente, odoo_vender):
    n = _venta(lead_issue=None, lead_ref=None)
    panel = cliente.get(f"/venta/panel?abrir=v{n}").text
    assert "<b>Lead</b>" in panel
    assert "Sin lead" in panel
    assert "no salió de un lead del CRM" in panel
    assert 'href="/control?abrir=' not in panel


def test_a12_con_el_pp_pero_sin_el_numero_del_issue_lo_dice(
        cliente, odoo_vender):
    n = _venta(lead_issue=None, lead_ref="PP-12345")
    panel = cliente.get(f"/venta/panel?abrir=v{n}").text
    assert "PP-12345" in panel
    assert "no hay por dónde abrirlo en el CRM" in panel
    assert 'href="/control?abrir=' not in panel


def test_a12_sin_telefono_la_fila_del_contacto_sigue_estando(
        cliente, odoo_vender):
    n = _venta(celular=None)
    panel = cliente.get(f"/venta/panel?abrir=v{n}").text
    assert "<b>Contacto</b>" in panel
    assert f'href="/contactos/lv{n}"' in panel
    assert "Sin teléfono" in panel


def test_a12_tambien_en_una_cotizacion_de_servicio(cliente, odoo_vender):
    with _db() as con:
        cur = con.execute(
            "INSERT INTO cotizaciones_servicio (creado_en, empleada, tipo,"
            " cliente, celular, orden_id, orden, total, lead_issue)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (datetime.now(ZONA_PANAMA).isoformat(), "Génesis", "renta",
             "Beto QA", "6000-0002", 601, "S00049", 200.0, "LEAD-91"))
        n = cur.lastrowid
    odoo_vender.ordenes[601] = {"state": "sale", "invoice_ids": []}
    panel = cliente.get(f"/venta/panel?abrir=s{n}").text
    assert 'href="/contactos/t60000002"' in panel
    assert 'href="/control?abrir=LEAD-91"' in panel
