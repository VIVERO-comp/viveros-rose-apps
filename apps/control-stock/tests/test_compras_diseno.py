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
    paleta.json: la piel nueva no trae hex de estado.

    Sobre las columnas de PANTALLA (`ORDEN_PANTALLA`), que desde el item 8
    son seis: «Abonado» sigue en la paleta y en Linear, pero ya no tiene
    columna que pintar."""
    texto = cliente.get("/compras").text
    for clave in compras.ORDEN_PANTALLA:
        estado = compras.POR_CLAVE[clave]
        assert f"--cpd-col:{estado['color']}" in texto, clave
        assert estado["chip"] in texto, clave


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
    # sigue ahí con sus palabras. Las de PANTALLA (item 8): «Abonado» ya
    # no tiene columna, así que tampoco pie.
    for clave in compras.ORDEN_PANTALLA:
        assert compras.POR_CLAVE[clave]["pie"] in texto, clave


def test_la_tarjeta_conserva_sus_enlaces(cliente, de_dueno):
    """Los enlaces de adentro de la tarjeta: ver productos (abre el
    panel) y el lead cruzado (va a Control)."""
    nueva = compras.crear("Pedido de octubre", autor="G")
    compras.agregar_linea(nueva["ref"], sku="IN-A", nombre="Tierra")
    texto = cliente.get("/compras").text
    assert f"/compras?abrir={nueva['ref']}" in texto
    # La tarjeta sigue siendo arrastrable (el div, con su data-ref).
    assert f'data-ref="{nueva["ref"]}"' in texto


def test_el_resumen_de_la_pantalla_12_va_apagado(cliente):
    """El `.res` del lienzo (Hay que comprar / Por llegar / Recibido este
    mes) existe, pero sus números no se calculan en ningún lado todavía:
    los tiles dicen «Todavía no» — nunca un monto inventado — y el de
    Proveedores enlaza a la vista real."""
    texto = cliente.get("/compras").text
    aside = texto.split('class="cpd-res"')[1].split("</aside>")[0]
    for titulo in ("Hay que comprar", "Por llegar", "Recibido este mes",
                   "Proveedores"):
        assert titulo in aside, titulo
    assert aside.count("Todavía no") == 3
    assert 'href="/compras/proveedores"' in aside
    # Ni un número con pinta de plata dentro del resumen apagado.
    assert "$" not in aside


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
    # El estado ya no es un renglón «Columna»: la pantalla 13 lo pone en
    # la cabecera, junto a la referencia («P00030 · Pedida, por llegar»).
    # El dato sigue ahí, solo cambió de lugar.
    titulo = compras.POR_CLAVE["POR_PEDIR"]["titulo"]  # crear() nace ahí
    assert f"{nueva['ref']} · {titulo}" in panel
    # Los renglones de siempre.
    for renglon in ("Cómo llega", "Responsable", "Productos ("):
        assert renglon in panel, renglon


def test_los_atajos_del_proveedor_van_apagados(cliente, de_dueno):
    """Pantallas 13 y 29: los dos íconos de llamar y escribirle al
    proveedor. Control no guarda su teléfono (vive en Odoo) y los
    proveedores no tocan WhatsApp, así que los dos se pintan en su lugar
    pero APAGADOS, con su «Todavía no» — nunca un número inventado."""
    nueva = compras.crear("Pedido de octubre", autor="G")
    panel = cliente.get(f"/compras?abrir={nueva['ref']}").text
    atajos = panel.split('class="cpd-ics"')[1].split("</div>")[0]
    assert atajos.count("disabled") == 2
    assert atajos.count('title="Todavía no"') == 2
    assert "cpd-ic c-tel" in atajos and "cpd-ic c-wa" in atajos
    # Ni un href: apagado es apagado, no un enlace que no marca a nadie.
    assert "href=" not in atajos
    assert "tel:" not in atajos and "wa.me" not in atajos


def test_ver_proveedor_abre_su_ficha_de_verdad(cliente, de_dueno):
    """El enlace chico del pie del lienzo (13): la ficha del proveedor ya
    existe y la compra guardó su id, así que abre SU tarjeta. Sin
    proveedor anotado no se pinta nada."""
    con = compras.crear("Macetas", proveedor_nombre="Vivero Las Cumbres",
                        proveedor_id=77, autor="G")
    panel = cliente.get(f"/compras?abrir={con['ref']}").text
    assert '/compras/proveedores?abrir=77#pv-77' in panel
    assert ">Ver proveedor</a>" in panel
    sin = compras.crear("Tierra negra", autor="G")
    assert "Ver proveedor" not in cliente.get(f"/compras?abrir={sin['ref']}").text


def test_la_tarjeta_con_orden_ofrece_recibir(cliente, de_dueno):
    """El botón del rincón de la tarjeta (lienzo 12/28). Es el MISMO
    enlace del panel —ni una ruta nueva— y solo donde tiene sentido: una
    compra sin su orden de compra en Odoo todavía no puede recibirse."""
    nueva = compras.crear("Tierra negra", autor="G")
    enlace = f'/compras/recibir?ref={nueva["ref"]}#cp-recibir'
    # El enlace lleva la ref adentro, así que basta buscarlo en el tablero:
    # no puede venir de la tarjeta de otra compra.
    assert enlace not in cliente.get("/compras").text
    compras.guardar_orden(nueva["ref"], 7, "P00007")
    tablero = cliente.get("/compras").text
    assert f'<a class="cpd-ob" draggable="false"\n               href="{enlace}"' in tablero
    assert ">Recibir</a>" in tablero
    # Y es el MISMO destino que ofrece el panel de esa compra.
    assert enlace in cliente.get(f"/compras?abrir={nueva['ref']}").text


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


# ---------------------------------------------------------------------------
# BLOQUE 59 · el ancho del tablero (7/10/2026)
# ---------------------------------------------------------------------------
#
# Medido antes de tocar nada, con el elemento renderizado: a 1024px de
# ventana el carril medía 436px —el resumen se llevaba 280 fijos— y de las
# SEIS columnas de 276px se veía UNA entera, con la segunda cortada a mitad
# de palabra y ninguna señal de que hubiera más al costado.
# ---------------------------------------------------------------------------

def test_la_decision_del_resumen_vive_en_un_solo_lugar():
    """«El resumen baja mientras no quepa» tiene que poder voltearse en un
    sitio, no en cinco: el dueño todavía no eligió entre esto y «resumen
    fijo + tablero de lado», y la opción que no se construyó tiene que
    costar tres renglones, no una tanda."""
    css = open(CSS).read()
    consultas = re.findall(r"@container\s+lienzo\s*\([^)]*\)", css)
    assert len(consultas) == 1, (
        f"la decisión está repartida en {len(consultas)} consultas: {consultas}")
    # Y el número es el escalón HOLGADO de la escala compartida, no uno nuevo.
    assert "min-width:1120px" in consultas[0].replace(" ", "")


def test_el_resumen_baja_por_lienzo_y_no_por_ventana():
    """La trampa que esto evita: un `@media` mide la VENTANA, y el sitio del
    contenido es la ventana menos el menú y los márgenes. Medido en este
    mismo archivo: ventana 1024 → 728px de lienzo."""
    css = open(CSS).read()
    bloque = css.split("@container lienzo", 1)[1].split("\n}", 1)[0]
    assert "cpd-envuelve" in bloque and "280px" in bloque


def test_el_carril_no_puede_encogerse_a_cero():
    """Con el resumen debajo, el tablero y el resumen se reparten el alto.
    Si al tablero se le deja llegar a 0 desaparece entero — es el defecto
    que ya se pagó en el calendario. La reja le pone piso."""
    css = _sin_blancos(open(CSS).read())
    assert "grid-template-rows:minmax(260px,1fr)auto" in css


def test_se_ve_que_las_columnas_siguen_al_costado():
    """La mitad del defecto que NO depende de la decisión de arriba: seis
    columnas de 276px piden 1716px de lienzo (~2010 de ventana), así que en
    una pantalla real siempre quedan columnas afuera.

    Dos señales, y ninguna puede mentir: la barra de desplazamiento del
    carril hecha visible a propósito (`scroll`, no `auto`, que en macOS se
    esconde) y la orilla en degradado, que al llegar al final cae sobre
    blanco y se apaga sola."""
    css = _sin_blancos(open(CSS).read())
    assert "overflow-x:auto" in css
    assert "::-webkit-scrollbar-thumb" in css
    assert "scroll-snap-type:xproximity" in css
    assert "scroll-snap-align:start" in css
    assert "position:sticky;right:0" in css
    assert '<b class="cpd-orilla" aria-hidden="true"></b>' in open(PLANTILLA).read()


def test_la_orilla_no_se_pinta_en_el_telefono():
    """Ahí el tablero es una PILA de secciones: no hay costado al que
    mirar, y un degradado de 40px en medio de la pila sería basura."""
    css = _sin_blancos(open(CSS).read())
    movil = css.split("@media(max-width:767.98px)")[-1]
    assert ".cpd-orilla{display:none}" in movil


def _sin_blancos(texto):
    return re.sub(r"\s+", "", texto)
