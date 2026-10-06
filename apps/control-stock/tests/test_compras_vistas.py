"""Las DOS vistas de la pestaña Compras, y cómo se pasa de una a la otra.

El agujero que estas pruebas tapan: la vista de proveedores se construyó
en una tanda y el formulario de compra en otra, y **ninguna de las dos
puso el enlace entre las dos pantallas**. La vista existía y no había
forma de llegar a ella; las 32 pruebas de `test_proveedores.py` pasaban
igual, porque ninguna miraba la navegación.

Lo que queda amarrado acá:

- que desde el tablero se llegue a Proveedores Y de vuelta, en los DOS
  tamaños de pantalla (en el teléfono la `.barra` está escondida, así que
  la copia de la barra no alcanza);
- que las dos pantallas usen EL MISMO mecanismo —el `.segmento` de
  Control— y no dos inventos distintos;
- que el enlace no se coma el único botón negro, que en Compras es
  «+ Anotar compra» (el texto del Diseño Orquesta, pantalla 12; antes
  decía «+ Compra» — mismo enlace, mismo destino);
- y que las dos rutas contesten de verdad, no solo que el `href` esté
  escrito.
"""

import pytest

from app import compras


@pytest.fixture(autouse=True)
def muestra_limpia(monkeypatch, db_limpia):
    monkeypatch.delenv("LINEAR_API_KEY", raising=False)
    monkeypatch.delenv("CALENDARIO_ESCRITURA", raising=False)
    monkeypatch.delenv("AJUSTES_ADMINS", raising=False)
    from app import control, linear_leads, proveedores
    linear_leads.reiniciar_muestra()
    compras.reiniciar_muestra()
    compras.iniciar_tablas()
    control.iniciar_tablas()
    proveedores.iniciar_tablas()


@pytest.fixture
def de_dueno(monkeypatch):
    monkeypatch.setenv("AJUSTES_ADMINS", "genesis")


def _segmentos(texto):
    """Los dos `.segmento` de la pantalla (el de la barra y el del
    teléfono), como trozos de HTML."""
    return [t.split("</div>")[0]
            for t in texto.split('<div class="segmento')[1:]]


# ---------------------------------------------------------------------------
# Ir y volver: el agujero
# ---------------------------------------------------------------------------

def test_desde_compras_se_llega_a_proveedores(cliente):
    texto = cliente.get("/compras").text
    assert 'href="/compras/proveedores"' in texto, \
        "el tablero no tiene cómo llegar a Proveedores"
    # Y la ruta contesta de verdad, no es un enlace a la nada.
    respuesta = cliente.get("/compras/proveedores")
    assert respuesta.status_code == 200
    assert "Proveedores" in respuesta.text


def test_desde_proveedores_se_vuelve_a_compras(cliente):
    texto = cliente.get("/compras/proveedores").text
    assert 'href="/compras"' in texto, \
        "la vista de proveedores no tiene cómo volver al tablero"
    respuesta = cliente.get("/compras")
    assert respuesta.status_code == 200
    assert 'id="cp-tablero"' in respuesta.text


def test_el_camino_completo_de_ida_y_vuelta(cliente, de_dueno):
    """La vuelta entera, siguiendo los enlaces que la pantalla pinta — no
    URLs escritas a mano en la prueba."""
    tablero = cliente.get("/compras").text
    assert 'href="/compras/proveedores"' in tablero

    proveedores_html = cliente.get("/compras/proveedores").text
    assert proveedores_html.count('href="/compras"') >= 1

    de_vuelta = cliente.get("/compras")
    assert de_vuelta.status_code == 200
    assert 'id="cp-tablero"' in de_vuelta.text


# ---------------------------------------------------------------------------
# El mismo mecanismo en las dos, y en los dos tamaños
# ---------------------------------------------------------------------------

def test_las_dos_pantallas_usan_el_segmento_de_control(cliente):
    """Dos vistas de la misma pestaña se cambian con el `.segmento` que ya
    existe en Control, no con un lenguaje nuevo por pantalla."""
    for ruta in ("/compras", "/compras/proveedores"):
        texto = cliente.get(ruta).text
        segmentos = _segmentos(texto)
        assert len(segmentos) == 2, (ruta, len(segmentos))
        for seg in segmentos:
            assert 'href="/compras"' in seg, ruta
            assert 'href="/compras/proveedores"' in seg, ruta


def test_cada_pantalla_marca_su_propia_vista(cliente):
    # El lienzo fresco (12/13/14) llama «Compras» a la primera cara del
    # segmento (antes decía «Tablero» — mismo enlace, misma vista).
    for seg in _segmentos(cliente.get("/compras").text):
        activo = seg.split('class="on"')[1].split("</a>")[0]
        assert "Compras" in activo
    for seg in _segmentos(cliente.get("/compras/proveedores").text):
        activo = seg.split('class="on"')[1].split("</a>")[0]
        assert "Proveedores" in activo


def test_en_el_telefono_tambien_se_puede_cambiar_de_vista(cliente):
    """Bajo 768px la `.barra` entera está `display:none`, así que la copia
    que vive dentro de ella NO sirve en el celular. Por eso el segmento se
    pinta dos veces y una copia lleva `seg-movil`."""
    for ruta in ("/compras", "/compras/proveedores"):
        texto = cliente.get(ruta).text
        assert "seg-movil" in texto, ruta
        # La copia del teléfono está FUERA de la barra: si estuviera dentro
        # se esconderían las dos juntas y el celular quedaría sin salida.
        antes_de_la_barra = texto.split('<div class="barra">')[0]
        assert "seg-movil" in antes_de_la_barra, ruta


def test_el_css_reparte_las_dos_copias():
    """Una se ve en computadora y la otra en el teléfono: si las dos se
    vieran a la vez, la pantalla tendría el selector repetido."""
    css = open("app/static/calendario.css").read()
    assert ".segmento.seg-movil{display:none}" in css
    movil = css.split("@media (max-width:767.98px){", 1)[1]
    assert ".segmento.seg-movil{display:flex" in movil
    # Y la barra que lleva la otra copia sigue escondida en el teléfono.
    assert ".barra,.chips,a.franja,.zona,.lado{display:none}" in movil


# ---------------------------------------------------------------------------
# Lo que el enlace NO se lleva
# ---------------------------------------------------------------------------

def test_el_unico_boton_negro_de_compras_sigue_siendo_mas_compra(cliente,
                                                                 de_dueno):
    """Un solo botón negro por pantalla, y el de Compras ya estaba dado."""
    texto = cliente.get("/compras").text
    assert texto.count('class="btn oro"') == 1
    negro = texto.split('class="btn oro"')[1].split("</a>")[0]
    assert "+ Anotar compra" in negro
    # El segmento no es un botón: son enlaces dentro del segmento.
    for seg in _segmentos(texto):
        assert "btn oro" not in seg


def test_proveedores_no_estrena_un_boton_negro(cliente):
    assert 'class="btn oro"' not in cliente.get("/compras/proveedores").text


def test_proveedores_ya_no_tiene_su_propio_volver(cliente):
    """Eran dos inventos para la misma cosa: el «← Compras» de la barra y
    el «← Volver» del teléfono se reemplazaron por el segmento, que es el
    mecanismo que ya existía en la app."""
    texto = cliente.get("/compras/proveedores").text
    assert "← Compras" not in texto
    assert "bm-volver" not in texto
    # Y al no usar `bm_volver`, el teléfono conserva su hamburguesa.
    assert "hamb-movil" in texto


def test_proveedores_sigue_siendo_parte_de_la_pestana_compras(cliente):
    """No es una pestaña propia del menú: el pill de Compras queda «on» y
    el navbar no estrena una entrada (decisión de la otra tanda, que se
    respeta)."""
    texto = cliente.get("/compras/proveedores").text
    assert 'class="pil on" href="/compras"' in texto
    nav = texto.split("<nav>")[1].split("</nav>")[0]
    assert 'href="/compras/proveedores"' not in nav


def test_ninguna_de_las_dos_pantallas_estrena_javascript(cliente):
    """Navegación por enlaces: el segmento son dos `<a>` y nada más."""
    for ruta, cuantos in (("/compras", 3), ("/compras/proveedores", 1)):
        texto = cliente.get(ruta).text
        assert texto.count("<script") == cuantos, ruta
        for sospechoso in ("onclick", "onchange", "_vistas_compras.js"):
            assert sospechoso not in texto, (ruta, sospechoso)
