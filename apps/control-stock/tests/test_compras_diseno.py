"""La piel nueva de Compras (Diseño Orquesta, pantallas 12/13/28/29) no
cambia NADA de lo que la pestaña hace.

El rediseño del 2/10/2026 es solo aspecto: un CSS propio
(`diseno-compras.css`, prendido con la clase `cpd` del body) encima de la
misma plantilla. Lo que estas pruebas amarran:

- que cada acción que existía siga en el HTML (el tablero, el panel, el
  formulario de anotar y la hoja de Recibir no pierden ni un botón);
- que la piel sea SOLO de Compras: cada selector del CSS nuevo va bajo
  `body.cpd`, para no pisarle nada a Control, Calendario ni a las otras
  ramas que comparten calendario.css;
- que el color de columnas y contadores siga saliendo de paleta.json por
  `compras.ESTADOS` (ni un hex de estado a mano en la piel nueva);
- y que los campos de escritura midan 16px en el teléfono (la regla de
  siempre: así iOS no hace zoom al enfocar).
"""

import re

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


CSS = "app/static/diseno-compras.css"
PLANTILLA = "app/plantillas/compras.html"
RECIBIR = "app/plantillas/compras_recibir.html"


# ---------------------------------------------------------------------------
# La piel se prende solo en Compras
# ---------------------------------------------------------------------------

def test_compras_carga_la_piel_nueva(cliente):
    texto = cliente.get("/compras").text
    assert "diseno-compras.css" in texto
    assert '<body class="cpd' in texto


def test_recibir_tambien_lleva_la_piel(cliente):
    plantilla = open(RECIBIR).read()
    assert "diseno-compras.css" in plantilla
    assert '<body class="cpd' in plantilla


def test_la_piel_no_se_cuela_en_otras_pantallas(cliente):
    """Proveedores (pantalla 14, otra tanda) sigue con su piel de antes:
    el CSS nuevo no se carga fuera de las pantallas rediseñadas."""
    texto = cliente.get("/compras/proveedores").text
    assert "diseno-compras.css" not in texto
    assert 'class="cpd' not in texto


def test_cada_selector_del_css_vive_bajo_cpd():
    """El archivo es de Compras y de nadie más: cualquier selector suelto
    le cambiaría el aspecto a Control o al Calendario sin que nada avise
    (comparten calendario.css y diseno-base.css)."""
    css = open(CSS).read()
    sin_comentarios = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    for regla in re.findall(r"([^{}]+)\{", sin_comentarios):
        for selector in regla.split(","):
            selector = selector.strip()
            if not selector or selector.startswith("@"):
                continue
            assert selector.startswith("body.cpd"), selector


def test_los_css_compartidos_no_se_tocaron():
    """La regla de la tanda: ni styles.css, ni calendario.css, ni
    diseno-base.css — un token nuevo se reporta, no se mete. Acá se amarra
    lo medible: la piel de Compras no depende de una clase nueva en los
    compartidos (sus selectores son los de siempre más el prefijo cpd)."""
    css = open(CSS).read()
    assert "cpd-x" in css and "--cpd-col" in css
    for compartido in ("app/static/calendario.css",
                       "app/static/diseno-base.css",
                       "app/static/styles.css"):
        assert "cpd" not in open(compartido).read(), compartido


# ---------------------------------------------------------------------------
# El color sigue saliendo de la paleta
# ---------------------------------------------------------------------------

def test_el_color_de_cada_columna_viaja_desde_la_paleta(cliente):
    """`--cpd-col` y el chip del contador los pone el servidor con
    compras.ESTADOS (paleta.json): la piel nueva no trae hex de estado."""
    texto = cliente.get("/compras").text
    for estado in compras.ESTADOS:
        assert f"--cpd-col:{estado['color']}" in texto, estado["clave"]
        assert estado["chip"] in texto, estado["clave"]


def test_la_piel_nueva_no_trae_hex_de_estado_a_mano():
    """Los únicos colores del CSS nuevo son tokens --d-* / --cpd-col y
    neutros del lienzo; ninguno de los sólidos de estado_compra."""
    css = open(CSS).read().lower()
    for estado in compras.ESTADOS:
        assert estado["color"].lower() not in css, estado["clave"]


# ---------------------------------------------------------------------------
# El tablero conserva todas sus acciones (pantallas 12 y 28)
# ---------------------------------------------------------------------------

def test_el_tablero_conserva_sus_acciones(cliente, de_dueno):
    texto = cliente.get("/compras").text
    # El único botón negro, mismo destino de siempre.
    assert 'href="/compras?nueva=1"' in texto
    assert "+ Anotar compra" in texto
    # Las dos vistas (y la copia del teléfono).
    assert texto.count('href="/compras/proveedores"') >= 2
    # El arrastre de Control, intacto.
    assert 'data-destino="/compras/estado"' in texto
    assert 'data-campo="estado"' in texto
    # La hamburguesa.
    assert "Mostrar u ocultar el menú" in texto
    # El pie de cada columna subió a ser el hint bajo el título, pero
    # sigue ahí con sus palabras.
    for estado in compras.ESTADOS:
        assert estado["pie"] in texto, estado["clave"]


def test_la_tarjeta_conserva_sus_enlaces(cliente, de_dueno):
    """Los enlaces de adentro de la tarjeta: ver productos (abre el
    panel) y el lead cruzado (va a Control)."""
    nueva = compras.crear("Pedido de octubre", autor="G")
    compras.agregar_linea(nueva["ref"], sku="IN-A", nombre="Tierra")
    texto = cliente.get("/compras").text
    assert f"/compras?abrir={nueva['ref']}" in texto
    # La tarjeta sigue siendo arrastrable (el div, con su data-ref).
    assert f'data-ref="{nueva["ref"]}"' in texto


# ---------------------------------------------------------------------------
# El panel de la compra abierta (pantallas 13 y 29)
# ---------------------------------------------------------------------------

def test_el_panel_conserva_sus_renglones_y_su_cerrar(cliente, de_dueno):
    nueva = compras.crear("Pedido de octubre", autor="G")
    compras.agregar_linea(nueva["ref"], sku="IN-A", nombre="Tierra")
    panel = cliente.get(f"/compras?abrir={nueva['ref']}").text
    # La X nueva es el mismo enlace de cerrar: vuelve a SU tarjeta.
    assert 'class="cpd-x"' in panel
    assert 'aria-label="Cerrar"' in panel
    assert f'href="/compras#c-{nueva["ref"]}"' in panel
    # Los renglones de siempre.
    for renglon in ("Columna", "Cómo llega", "Responsable", "Productos ("):
        assert renglon in panel, renglon


def test_las_acciones_del_panel_van_en_negro():
    """La regla del lienzo (pantalla 13): al abrir una tarjeta, un solo
    botón negro — el paso que toca ahora. Las dos acciones del panel
    (nunca se pintan juntas: falta la orden O se puede recibir) llevan
    `btn oro`; el POST y el href son los mismos de antes."""
    plantilla = open(PLANTILLA).read()
    crear = plantilla.split('action="/compras/orden"')[1].split("</form>")[0]
    assert 'class="btn oro"' in crear
    assert "Crear la orden en Odoo" in crear
    assert '<a class="btn oro" href="/compras/recibir?ref=' in plantilla


# ---------------------------------------------------------------------------
# El formulario de Anotar compra no cambió (el rediseño es solo piel)
# ---------------------------------------------------------------------------

def test_el_formulario_de_anotar_conserva_todas_sus_acciones():
    """Incluido el botón Buscar: el lienzo pide búsqueda en vivo, pero eso
    es COMPORTAMIENTO y va en otra tanda — hoy se busca apretando."""
    plantilla = open(PLANTILLA).read()
    for accion in ('value="buscar"', 'value="descartar"',
                   'value="crear_proveedor"', 'value="crear_producto"',
                   'value="bajos"', 'value="ocultar_bajos"',
                   'name="agregar"', 'name="quitar"',
                   'action="/compras/nueva"', 'formaction="/compras/borrador"',
                   'name="como_llega"', 'name="resp"', 'name="lead_ref"',
                   ">Anotar la compra<", ">Mejor no<", ">Buscar<"):
        assert accion in plantilla, accion


def test_recibir_conserva_sus_acciones():
    plantilla = open(RECIBIR).read()
    for accion in ('action="/compras/recibir"', "Registrar lo que llegó",
                   "llego-", "roto-", "← Compras", ">Mejor no<"):
        assert accion in plantilla, accion
    # Y su botón negro sigue siendo el que sube el stock.
    assert plantilla.count('class="btn oro"') == 1


# ---------------------------------------------------------------------------
# El teléfono
# ---------------------------------------------------------------------------

def test_los_campos_escriben_a_16px_en_el_telefono():
    """La hoja de Recibir no vive ni en .modal ni en .panel-der, así que
    la regla de los 16px de calendario.css no la cubría: la piel nueva la
    pone para TODOS los campos de Compras."""
    css = open(CSS).read()
    movil = css.split("@media (max-width:767.98px)", 1)[1]
    assert "font-size:16px" in movil
    assert "input.campo" in movil and "select.sel" in movil
