"""G1 · EL MARCO ES UNA SOLA PIEZA (BLOQUE 54, 7/10/2026).

El dueño lo dijo así: «la app parece cinco apps distintas». La causa no
era el diseño de cada pestaña — era que el MARCO (el menú, el encabezado,
los márgenes) estaba escrito una vez por pantalla, y lo escrito once veces
nunca mide igual.

Estas pruebas son el candado que evita que vuelva. Dos capas:

1. **Una sola fuente del HTML**: las once pestañas pintan su encabezado
   con el MISMO macro (`_cabecera.html`) y su menú con los MISMOS dos
   parciales (`_nav.html` / `_lado.html`). Si alguien vuelve a escribir un
   `<header>` propio o copia la hamburguesa a mano, acá se cae.

2. **Una sola fuente de las MEDIDAS**: los números del marco —el ancho del
   menú, el alto del encabezado, el tamaño del título, el margen lateral—
   viven en `diseno-base.css` y en ninguna otra hoja. Si otra hoja vuelve
   a escribirlos, acá se cae. Es la forma de garantizar que MIDEN lo
   mismo sin abrir un navegador en cada corrida: con un solo juego de
   números no hay dos medidas posibles.

Y la medición de verdad, con el navegador, vive en
`scratchpad/medir_marco.py` (Playwright): abre cada pestaña a 1280 y a 390
y compara posición y tamaño reales del menú, de la hamburguesa y del
título. No corre acá porque Playwright no está en el venv de la app.
"""

import pathlib
import re

import pytest

RAIZ = pathlib.Path(__file__).resolve().parent.parent
PLANTILLAS = RAIZ / "app" / "plantillas"
ESTATICOS = RAIZ / "app" / "static"

# Las once pestañas del menú, por su plantilla.
PESTANAS = [
    "calendario.html", "app.html", "control.html", "contactos.html",
    "pedidos.html", "venta.html", "compras.html", "proveedores.html",
    "conversaciones.html", "finanzas.html", "mi_crm.html",
]
# …y las pantallas de adentro que también llevan marco.
DE_ADENTRO = ["contactos_ficha.html", "respuestas.html",
              "compras_recibir.html", "revisar_ventas.html"]

TODAS = PESTANAS + DE_ADENTRO


def _texto(nombre):
    return (PLANTILLAS / nombre).read_text()


def _sin_comentarios(css):
    return re.sub(r"/\*.*?\*/", "", css, flags=re.S)


# ---------------------------------------------------------------------------
# 1. Una sola pieza de HTML
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("plantilla", TODAS)
def test_cada_pestana_usa_el_encabezado_compartido(plantilla):
    """Nadie vuelve a escribir su propio <header>."""
    t = _texto(plantilla)
    assert "_cabecera.html" in t, (
        f"{plantilla} no importa el encabezado compartido")
    assert "cab.cabecera(" in t, (
        f"{plantilla} no llama al macro del encabezado")


@pytest.mark.parametrize("plantilla", TODAS)
def test_nadie_copia_la_hamburguesa_a_mano(plantilla):
    """La hamburguesa se pinta UNA vez, en `_cabecera.html`.

    Antes estaba copiada en nueve plantillas con tres tamaños de SVG
    distintos (17, 20 y 24 px de trazo) — tres hamburguesas para un mismo
    botón. La del teléfono (`.hamb-movil`, en `_barra_movil.html`) es otra
    pieza y tampoco se copia.
    """
    t = _texto(plantilla)
    assert 'class="hamb"' not in t, (
        f"{plantilla} escribe su propia hamburguesa; va por cab.cabecera()")


@pytest.mark.parametrize("plantilla", TODAS)
def test_el_titulo_de_la_pestana_es_el_del_marco(plantilla):
    """Un `<h1>`/`<h3>` propio al lado de la hamburguesa es un encabezado
    paralelo: el título lo pone el macro."""
    t = _texto(plantilla)
    cuerpo = t[t.find("{% call cab.cabecera("):] if "cab.cabecera(" in t else ""
    # dentro del encabezado no puede haber otro titular
    fin = cuerpo.find("{% endcall %}")
    assert fin > 0, f"{plantilla}: el call del encabezado no cierra"
    dentro = cuerpo[:fin]
    for etiqueta in ("<h1", "<h2", "<h3"):
        assert etiqueta not in dentro, (
            f"{plantilla} pone un {etiqueta} dentro del encabezado único")


def test_el_pie_del_menu_es_solo_nombre_y_rol():
    """G1: el pie del menú es parte del marco, así que dice lo mismo en las
    once pestañas. Los avisos por pantalla se mudaron a la pantalla."""
    lado = _texto("_lado.html")
    assert "lado_pie_extra" not in lado.replace(
        "`lado_pie_extra`", "")  # solo puede quedar nombrado en el comentario
    for plantilla in TODAS:
        assert "lado_pie_extra" not in _texto(plantilla), (
            f"{plantilla} le mete un renglón propio al pie del menú")


def test_el_menu_sale_de_los_dos_parciales_de_siempre():
    """Ninguna pantalla arma su lista de pestañas a mano."""
    for plantilla in TODAS:
        t = _texto(plantilla)
        assert "_nav.html" in t or "_lado.html" in t, (
            f"{plantilla} no incluye ningún menú compartido")
        # la lista de pestañas la decide Python y la recorre el parcial:
        # ninguna plantilla arma su propio `for` sobre menu_nav
        sin_comentarios = re.sub(r"\{#.*?#\}", "", t, flags=re.S)
        assert "menu_nav" not in sin_comentarios, (
            f"{plantilla} recorre el menú por su cuenta en vez de incluirlo")


# ---------------------------------------------------------------------------
# 2. Una sola fuente de las MEDIDAS
# ---------------------------------------------------------------------------

# Los números del marco. Cada uno vive en `diseno-base.css` y en ninguna
# otra hoja; el valor está acá escrito para que un cambio a ojo también
# tenga que pasar por esta prueba.
MEDIDAS = {
    "--marco-menu": "240px",
    "--marco-pil": "48px",
    "--marco-pil-letra": "15px",
    "--marco-cab-alto": "72px",
    "--marco-margen": "28px",
    "--marco-cab-letra": "22px",
    "--marco-hamb": "44px",
}


def test_las_medidas_del_marco_viven_en_la_hoja_base():
    base = _sin_comentarios((ESTATICOS / "diseno-base.css").read_text())
    for token, valor in MEDIDAS.items():
        assert re.search(re.escape(token) + r"\s*:\s*" + re.escape(valor),
                         base), f"falta {token}:{valor} en diseno-base.css"


def test_ninguna_otra_hoja_vuelve_a_escribir_el_marco():
    """El candado de verdad: si otra hoja define el ancho del menú o el
    tamaño del título, el marco vuelve a medir distinto por pestaña.

    Lo que se busca son DEFINICIONES de los selectores del marco
    (`.cab`, `.cab-titulo`, `nav`/`.lado` como costado), no usos.
    """
    prohibidos = (
        # el título del encabezado
        r"\.cab-titulo\s*\{",
        # las tres rayas, fuera de la hoja base
        r"\.cab\s+\.hamb\s*\{",
        # el ancho del costado escrito a mano
        r"grid-template-columns\s*:\s*2[0-9]{2}px\s+1fr",
    )
    for hoja in sorted(ESTATICOS.glob("*.css")):
        if hoja.name == "diseno-base.css":
            continue
        css = _sin_comentarios(hoja.read_text())
        for patron in prohibidos:
            assert not re.search(patron, css), (
                f"{hoja.name} vuelve a escribir una medida del marco "
                f"({patron}); el marco es una sola pieza: diseno-base.css")


def test_la_escala_de_anchos_es_una_sola():
    """A1: las decisiones de layout se toman sobre el LIENZO (@container)
    y con los escalones de la escala compartida — 560, 860, 940 y 1180.
    Un número nuevo ahí es una escala paralela."""
    escalones = {"560px", "860px", "940px", "939.98px", "1180px",
                 "699.98px", "559.98px"}
    sueltos = {}
    for hoja in sorted(ESTATICOS.glob("*.css")):
        css = _sin_comentarios(hoja.read_text())
        for m in re.finditer(r"@container\s+lienzo\s*\(\s*(?:min|max)-width"
                             r"\s*:\s*([0-9.]+px)\s*\)", css):
            if m.group(1) not in escalones:
                sueltos.setdefault(hoja.name, set()).add(m.group(1))
    assert not sueltos, (
        f"escalones fuera de la escala compartida: {sueltos}")


def test_los_colores_de_contacto_son_tokens():
    """Los tintes de llamar / WhatsApp / mapa estaban copiados a mano en
    cuatro hojas. Ahora son tokens de `diseno-base.css`."""
    base = _sin_comentarios((ESTATICOS / "diseno-base.css").read_text())
    for token in ("--d-tel-fondo", "--d-tel-tinta",
                  "--d-map-fondo", "--d-map-tinta"):
        assert token + ":" in base.replace(" ", ""), f"falta {token}"
    for hoja in sorted(ESTATICOS.glob("*.css")):
        if hoja.name == "diseno-base.css":
            continue
        css = _sin_comentarios(hoja.read_text()).upper()
        for hexa in ("#EDF2FE", "#3A5BC7", "#F7EDFD", "#8145B5"):
            assert hexa not in css, (
                f"{hoja.name} escribe {hexa} a mano; usá el token")


def test_todo_var_usado_esta_definido_en_una_hoja_que_la_pantalla_carga():
    """Un `var()` sin valor NO da error: invalida la propiedad y se calla.

    Así se perdieron los bordes de los campos de Vender y de Ajustes y el
    botón «Guardar» de la vista plana del Stock, sin un renglón en ningún
    log. Esta prueba recorre cada plantilla, junta las hojas que carga y
    comprueba que cada token que esas hojas usan esté definido en alguna
    de ellas.
    """
    hojas = {p.name: p.read_text() for p in ESTATICOS.glob("*.css")}
    define = {n: set(re.findall(r"(--[A-Za-z0-9_-]+)\s*:", t))
              for n, t in hojas.items()}
    usa = {n: set(m.group(1) for m in
                  re.finditer(r"var\(\s*(--[A-Za-z0-9_-]+)\s*\)", t))
           for n, t in hojas.items()}
    # los que pinta Python en un style="" de la plantilla
    EN_LINEA = {"--c", "--exp-izq", "--exp-ancho"}
    faltan = {}
    for plantilla in PLANTILLAS.glob("*.html"):
        t = plantilla.read_text()
        cargadas = re.findall(r"/static/([a-z0-9_.-]+\.css)", t)
        if '{% extends "base.html" %}' in t:
            cargadas = ["styles.css", "diseno-base.css"] + cargadas
        if not cargadas:
            continue
        disponibles = set(EN_LINEA)
        disponibles |= set(re.findall(r"(--[A-Za-z0-9_-]+)\s*:", t))
        for h in cargadas:
            disponibles |= define.get(h, set())
        for h in cargadas:
            for token in usa.get(h, set()):
                if token not in disponibles:
                    faltan.setdefault(plantilla.name, set()).add(
                        f"{token} (usado en {h})")
    assert not faltan, (
        "tokens usados sin definir en ninguna hoja que la pantalla "
        f"cargue: {faltan}")
