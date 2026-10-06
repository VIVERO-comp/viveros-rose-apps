"""La piel nueva de Proveedores (Diseño Orquesta, pantalla 14) no cambia
NADA de lo que la pantalla hace.

El rediseño es solo aspecto: un CSS propio (`diseno-proveedores.css`,
prendido con la clase `pvd` del body) encima de la misma plantilla. Lo
que estas pruebas amarran:

- que cada acción que existía siga en el HTML (Preferido, abrir la ficha,
  el catálogo editable con Guardar/Quitar/+ Agregar, el buscador con su
  botón, los días del proveedor, el segmento con su copia del teléfono);
- que la piel sea SOLO de Proveedores: cada selector del CSS nuevo va
  bajo `body.pvd`, para no pisarle nada a Compras (body.cpd), Control ni
  al Calendario, que comparten calendario.css;
- que la pantalla siga SIN botón negro (los dos del lienzo — «+ Nuevo
  proveedor» y «ANOTAR COMPRA» — son funciones que hoy no existen y la
  piel no las inventa);
- que las iniciales del avatar se calculen en Python (regla 10), nunca
  inventadas cuando el nombre no tiene letras;
- y que los campos de escritura midan 16px en el teléfono.
"""

import re

import pytest

from app import proveedores, ventas

CSS = "app/static/diseno-proveedores.css"
PLANTILLA = "app/plantillas/proveedores.html"

PROV = {"id": 11, "name": "Vivero Las Cumbres", "phone": "6204-5511",
        "email": "lc@vivero.com", "city": "Las Cumbres"}


@pytest.fixture
def odoo_configurado(monkeypatch):
    for variable in ("ODOO_URL", "ODOO_DB", "ODOO_USER", "ODOO_PASSWORD"):
        monkeypatch.setenv(variable, "de-prueba")


@pytest.fixture
def de_dueno(monkeypatch):
    monkeypatch.setenv("AJUSTES_ADMINS", "genesis")


def _ejecutar_fake(partners=(), ordenes=(), supplierinfo=()):
    def ejecutar(modelo, metodo, args, kw=None):
        if modelo == "res.partner":
            return list(partners)
        if modelo == "purchase.order":
            return list(ordenes)
        if modelo == "product.supplierinfo":
            return list(supplierinfo)
        if modelo == "res.company":
            return [{"id": 1, "currency_id": [2, "USD"]}]
        raise AssertionError(f"modelo inesperado: {modelo}")
    return ejecutar


@pytest.fixture
def con_un_proveedor(monkeypatch, odoo_configurado, db_limpia):
    proveedores.iniciar_tablas()
    proveedores.reiniciar_cache_moneda()
    monkeypatch.setattr(ventas, "_ejecutar",
                        _ejecutar_fake(partners=[PROV]))


# ---------------------------------------------------------------------------
# La piel se prende solo en Proveedores
# ---------------------------------------------------------------------------

def test_proveedores_carga_la_piel_nueva(cliente, con_un_proveedor):
    texto = cliente.get("/compras/proveedores").text
    assert "diseno-proveedores.css" in texto
    assert '<body class="pvd' in texto


def test_la_piel_no_se_cuela_en_compras(cliente, db_limpia, monkeypatch):
    from app import compras, control, linear_leads
    monkeypatch.delenv("LINEAR_API_KEY", raising=False)
    linear_leads.reiniciar_muestra()
    compras.reiniciar_muestra()
    compras.iniciar_tablas()
    control.iniciar_tablas()
    texto = cliente.get("/compras").text
    assert "diseno-proveedores.css" not in texto
    assert 'class="pvd' not in texto


def test_cada_selector_del_css_vive_bajo_pvd():
    """El archivo es de Proveedores y de nadie más: cualquier selector
    suelto le cambiaría el aspecto a Compras, Control o al Calendario sin
    que nada avise (comparten calendario.css y diseno-base.css)."""
    css = open(CSS).read()
    sin_comentarios = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    for regla in re.findall(r"([^{}]+)\{", sin_comentarios):
        for selector in regla.split(","):
            selector = selector.strip()
            if not selector or selector.startswith("@"):
                continue
            assert selector.startswith("body.pvd"), selector


def test_los_css_compartidos_no_se_tocaron():
    css = open(CSS).read()
    assert ":root" not in css
    assert not re.search(r"--d-[a-z-]+\s*:", css), "define un token nuevo"
    for compartido in ("app/static/calendario.css",
                       "app/static/diseno-base.css",
                       "app/static/styles.css"):
        assert "pvd" not in open(compartido).read(), compartido


# ---------------------------------------------------------------------------
# Las iniciales del avatar: Python decide, la plantilla pinta (regla 10)
# ---------------------------------------------------------------------------

def test_iniciales_las_dos_primeras_palabras_con_letra():
    assert proveedores.iniciales("Vivero Las Cumbres") == "VL"
    assert proveedores.iniciales("Agro Insumos") == "AI"
    assert proveedores.iniciales("Macetas") == "M"


def test_iniciales_sin_letras_nunca_se_inventan():
    assert proveedores.iniciales("") == "?"
    assert proveedores.iniciales(None) == "?"
    assert proveedores.iniciales("6204-5511") == "?"
    # Una palabra sin letra al frente no aporta inicial.
    assert proveedores.iniciales("123 Vivero") == "V"


def test_la_ficha_trae_sus_iniciales(con_un_proveedor):
    [p] = proveedores.listar()["proveedores"]
    assert p["iniciales"] == "VL"


# ---------------------------------------------------------------------------
# La pantalla conserva todas sus acciones
# ---------------------------------------------------------------------------

def test_la_tarjeta_conserva_sus_acciones(cliente, con_un_proveedor,
                                          de_dueno):
    texto = cliente.get("/compras/proveedores").text
    # El avatar nuevo, con las iniciales calculadas en Python.
    assert 'class="pvd-av"' in texto and ">VL<" in texto
    # Abrir la ficha y marcar Preferido (admin), mismos destinos.
    assert 'href="/compras/proveedores?abrir=11#pv-11"' in texto
    assert 'action="/compras/proveedores/preferido"' in texto
    assert "Marcar «Preferido»" in texto
    # Los datos imposibles siguen en «—», nunca inventados.
    assert "A tiempo" in texto and "Dañado" in texto
    # El segmento con su copia del teléfono.
    assert texto.count('href="/compras"') >= 2
    # La hamburguesa.
    assert "Mostrar u ocultar el menú" in texto


def test_el_panel_conserva_su_catalogo_editable(cliente, con_un_proveedor,
                                                de_dueno, monkeypatch):
    linea = {"id": 7, "product_tmpl_id": [3, "Romero"], "price": 2.5,
             "min_qty": 1, "delay": 2, "product_name": "", "product_code": "",
             "partner_id": [11, "Vivero Las Cumbres"],
             "product_id": False, "currency_id": [2, "USD"]}
    monkeypatch.setattr(ventas, "_ejecutar",
                        _ejecutar_fake(partners=[PROV], supplierinfo=[linea]))
    panel = cliente.get("/compras/proveedores?abrir=11").text
    # La X nueva es el mismo enlace de cerrar: vuelve a SU tarjeta.
    assert 'class="pvd-x"' in panel
    assert 'aria-label="Cerrar"' in panel
    assert 'href="/compras/proveedores#pv-11"' in panel
    # Las cuatro escrituras del catálogo, intactas.
    assert 'action="/compras/proveedores/producto"' in panel
    for accion in ('name="guardar"', 'name="quitar"',
                   'name="accion" value="dias"'):
        assert accion in panel, accion
    # El buscador SIGUE pidiendo el botón (la búsqueda en vivo del lienzo
    # es comportamiento y va en otra tanda), y sigue siendo un GET.
    assert ">Buscar<" in panel
    assert 'method="get" action="/compras/proveedores"' in panel


def test_la_pantalla_sigue_sin_boton_negro():
    """Los dos negros del lienzo («+ Nuevo proveedor», «ANOTAR COMPRA»)
    no existen hoy: la piel no los inventa ni asciende otro botón."""
    plantilla = open(PLANTILLA).read()
    assert "Nuevo proveedor" not in plantilla
    assert "ANOTAR COMPRA" not in plantilla
    assert "btn oro" not in plantilla
    css = open(CSS).read()
    assert ".oro" not in css


# ---------------------------------------------------------------------------
# El teléfono
# ---------------------------------------------------------------------------

def test_los_campos_escriben_a_16px_en_el_telefono():
    css = open(CSS).read()
    movil = css.split("@media (max-width:767.98px)", 1)[1]
    assert "font-size:16px" in movil
    assert "input.campo" in movil and "select.sel" in movil


def test_la_piel_respeta_el_corte_del_telefono_y_no_toca_la_tablet():
    """calendario.css parte en ≥861 (computadora) y ≤767.98 (teléfono):
    la piel usa solo el corte del teléfono — la banda de tablet
    (768–860) queda con lo que las reglas generales ya le daban, sin un
    corte propio inventado."""
    css = open(CSS).read()
    cortes = set(re.findall(r"@media *\(([^)]+)\)", css))
    assert cortes == {"max-width:767.98px"}, cortes
