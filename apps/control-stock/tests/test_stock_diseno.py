"""La piel nueva de la pestaña Stock (Diseño Orquesta, pantallas 03/04 y
19/20) no cambia NADA de lo que la pestaña hace.

El rediseño es solo aspecto: una hoja propia (`diseno-stock.css`) que
carga únicamente app.html, con todos sus selectores anclados a los ids de
esa pantalla. Lo que estas pruebas amarran:

- que cada acción que existía siga en el HTML (las dos vistas del stock,
  la campana, los chips, el buscador, la bitácora, el detalle con su
  pincel de foto / Modificar stock / interruptor de la tienda, y los
  modales con sus botones);
- que la piel sea SOLO de esta pantalla: cada selector del CSS nuevo va
  bajo un ancla propia (#tab-stock, #tab-detalle, los modales, #panel y
  #fab-agregar) — nada puede ensuciar Inicio, Ajustes, Vender ni a nadie
  que comparta styles.css;
- que la VISTA PLANA del rol Inventario (stock_plano.html, propuesta de
  Omar) quede intacta: no carga la hoja nueva y sigue con su cara;
- que app.js no ganó lógica nueva (la lista la sigue pintando el mismo
  `pintar()` con las mismas clases);
- y que los campos de escritura midan 16px en el teléfono.
"""

import re

import pytest
from fastapi.testclient import TestClient

from app import datos, datos_roles, seguridad
from app.main import app

CSS = "app/static/diseno-stock.css"
PLANTILLA = "app/plantillas/app.html"
PLANA = "app/plantillas/stock_plano.html"

# Las únicas anclas permitidas: todas viven solo en app.html.
ANCLAS = ("#tab-stock", "#tab-detalle", "#modal-editar", "#modal-foto",
          "#modal-agregar", "#panel", "#fab-agregar")


@pytest.fixture
def de_dueno(monkeypatch):
    monkeypatch.setenv("AJUSTES_ADMINS", "genesis")


# ---------------------------------------------------------------------------
# La piel se prende solo en la pestaña Stock
# ---------------------------------------------------------------------------

def test_la_pantalla_carga_la_piel_nueva(cliente, con_inventario):
    # La raíz sin pestaña redirige al Calendario: la pantalla del stock
    # es /?tab=stock.
    assert "diseno-stock.css" in cliente.get("/?tab=stock").text


def test_cada_selector_vive_bajo_un_ancla_de_la_pantalla():
    """El archivo es de la pestaña Stock y de nadie más: un selector
    suelto le cambiaría el aspecto a Inicio, Ajustes o Vender sin que
    nada avise (todos comparten styles.css)."""
    css = open(CSS).read()
    sin_comentarios = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    for regla in re.findall(r"([^{}]+)\{", sin_comentarios):
        for selector in regla.split(","):
            selector = selector.strip()
            if not selector or selector.startswith("@"):
                continue
            assert selector.startswith(ANCLAS), selector


def test_los_css_compartidos_no_se_tocaron():
    """Ni styles.css ni diseno-base.css ni calendario.css: un token nuevo
    se reporta, no se mete. Y la hoja nueva no define tokens propios —
    usa los --d-* que ya existen."""
    css = open(CSS).read()
    assert ":root" not in css
    assert not re.search(r"--d-[a-z-]+\s*:", css), "define un token nuevo"
    for compartido in ("app/static/styles.css", "app/static/calendario.css",
                       "app/static/diseno-base.css"):
        assert "diseno-stock" not in open(compartido).read(), compartido


def test_la_vista_plana_del_rol_inventario_queda_intacta(db_limpia, monkeypatch):
    """La cara de Omar (propuesta aparte) no es de esta tanda: su
    plantilla no carga la hoja nueva y su pantalla sigue siendo la lista
    plana de siempre."""
    assert "diseno-stock" not in open(PLANA).read()

    productos = [{"sku": "PL-ROMERO", "nombre": "Romero",
                  "categoria": "Exterior", "disponible": 2, "fisico": 2,
                  "precio_centavos": 350}]
    monkeypatch.setattr(datos, "obtener_inventario",
                        lambda refrescar=False: (productos, 1756800000.0))
    seguridad.crear_empleada("omar", "Omar", "clave-de-prueba")
    with datos._db() as con:
        rol = con.execute("SELECT n FROM roles WHERE slug=?",
                          (datos_roles.SLUG_INVENTARIO,)).fetchone()["n"]
    assert datos_roles.poner_persona(rol, "omar", "korto") is None
    c = TestClient(app)
    r = c.post("/login", data={"usuario": "omar",
                               "contrasena": "clave-de-prueba"},
               follow_redirects=False)
    assert r.status_code == 303
    pantalla = c.get("/stock").text
    assert "diseno-stock" not in pantalla
    assert 'class="plano' in pantalla


def test_app_js_no_gano_logica_nueva():
    """La piel restila las clases que pintar() ya emite; el JS heredado no
    se tocó (regla 5 de la tanda): las clases de la tarjeta siguen siendo
    las mismas y no aparece ninguna clase de la piel nueva."""
    js = open("app/static/app.js").read()
    for clase in ('class="planta', 'class="foto"', 'class="card-pie solo-pc"'):
        assert clase in js, clase
    assert "diseno-stock" not in js


# ---------------------------------------------------------------------------
# La cara general conserva todas sus acciones (pantallas 03 y 19)
# ---------------------------------------------------------------------------

def test_la_cara_general_conserva_sus_acciones(cliente, con_inventario,
                                               de_dueno):
    texto = cliente.get("/?tab=stock").text
    # Las dos vistas del stock.
    assert "Stock online" in texto and "Stock global" in texto
    # La campana de alertas y su panel.
    assert "togglePanel()" in texto
    assert 'action="/alertas/atender"' in texto
    assert 'action="/umbral"' in texto
    # El buscador en vivo y los tres chips de siempre (ni uno más).
    assert 'oninput="filtrar()"' in texto
    for chip in ('data-cat="Todas"', 'data-cat="__alerta__"',
                 'data-cat="__cero__"'):
        assert chip in texto, chip
    # Actualizar y la bitácora del admin.
    assert 'href="/?refrescar=1"' in texto
    assert 'href="/stock/cambios"' in texto
    # La lista la sigue pintando app.js en el mismo nodo.
    assert 'id="lista"' in texto
    # Crear producto: el único botón negro, mismo destino de siempre.
    assert 'href="/productos/crear"' in texto


def test_sin_admin_la_bitacora_no_aparece(cliente, con_inventario,
                                          monkeypatch):
    monkeypatch.delenv("AJUSTES_ADMINS", raising=False)
    assert 'href="/stock/cambios"' not in cliente.get("/?tab=stock").text


# ---------------------------------------------------------------------------
# El producto abierto conserva todas sus acciones (pantallas 04 y 20)
# ---------------------------------------------------------------------------

def test_el_detalle_conserva_sus_acciones():
    plantilla = open(PLANTILLA).read()
    # Volver, el pincel de la foto, Modificar stock y Guardar ficha.
    assert "cerrarDetalle()" in plantilla
    assert 'id="det-btn-foto"' in plantilla
    assert 'id="det-ajustar"' in plantilla
    assert "guardarFicha()" in plantilla
    # El interruptor de la tienda, con su nota de que no borra nada.
    assert 'id="det-publicada"' in plantilla
    assert "Sacarla del sitio no borra nada" in plantilla
    # Los ganchitos de vehículo y la altura (van a Odoo).
    for campo in ("ficha-veh-moto", "ficha-veh-carro", "ficha-veh-pickup",
                  "ficha-altura-min", "ficha-altura-max"):
        assert campo in plantilla, campo


def test_los_modales_conservan_sus_acciones():
    plantilla = open(PLANTILLA).read()
    # Modificar stock: − / +, Guardar en Odoo, cancelar.
    assert "cambiarQty(-1)" in plantilla and "cambiarQty(1)" in plantilla
    assert "guardarStock()" in plantilla
    # La foto: pincel, descargar y el compartir que nace oculto.
    assert "subirFoto(this)" in plantilla
    assert 'id="btn-descargar"' in plantilla
    assert 'id="btn-compartir-foto" hidden' in plantilla
    # Quitar foto no existe hoy y la piel no lo inventa.
    assert "Quitar foto" not in plantilla
    # Crear planta.
    assert "crearPlanta()" in plantilla


# ---------------------------------------------------------------------------
# El teléfono
# ---------------------------------------------------------------------------

def test_los_campos_escriben_a_16px_en_el_telefono():
    css = open(CSS).read()
    movil = css.split("@media (max-width:899px)", 1)[1]
    assert "font-size:16px" in movil
    assert ".buscador input" in movil


def test_la_piel_respeta_los_cortes_de_la_pantalla():
    """app.html solo tiene dos caras (computadora ≥900 y teléfono ≤899):
    la piel usa exactamente esos cortes — no inventa una banda de tablet
    que esta pantalla no tiene."""
    css = open(CSS).read()
    cortes = set(re.findall(r"@media *\(([^)]+)\)", css))
    assert cortes == {"min-width:900px", "max-width:899px"}, cortes
