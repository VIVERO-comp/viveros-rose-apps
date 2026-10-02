"""El alta de productos: Planta · Maceta · Insumo (dueño, 30/09/2026).

Las plantas siguen por su camino de siempre (modal de Stock → order-api, ver
test_alta_planta.py); lo que se prueba acá es el camino nuevo de maceta e
insumo, que escribe DIRECTO en Odoo por ventas._ejecutar.

Ninguna prueba sale a la red: el Odoo se sustituye con un doble que anota lo
que se le manda y que puede decir "ese campo no existe" para probar la
tolerancia al addon viejo.
"""

import pytest

from app import altas, datos, ventas


class OdooFalso:
    """Lo mínimo de Odoo para un alta: categorías, impuestos, unidades,
    fields_get y el create de product.template.

    `campos` es lo que este Odoo dice tener: con los cuatro campos de la
    maceta fuera, se simula el addon de hoy (que todavía no los trajo).
    """

    def __init__(self, categorias=None, impuestos=None, campos=None,
                 unidades=None, usados=()):
        self.categorias = ({"Macetas": 13, "Insumos": 10, "Plantas": 12}
                           if categorias is None else categorias)
        # Un ITBMS de venta al 7% y uno de compra, para que se vea que solo
        # se usa el de venta.
        self.impuestos = ([{"id": 41, "type_tax_use": "sale", "amount": 7.0},
                           {"id": 42, "type_tax_use": "purchase", "amount": 7.0}]
                          if impuestos is None else impuestos)
        self.campos = ({
            "maceta_material": {"type": "selection", "selection": [
                ["fibra", "Fibra"], ["barro", "Barro"],
                ["plastico", "Plástico"], ["cemento", "Cemento"]]},
            "maceta_diametro_cm": {"type": "float"},
            "maceta_alto_cm": {"type": "float"},
            "maceta_color": {"type": "char"},
            "publicado": {"type": "boolean"},
            "uom_id": {"type": "many2one"},
            "uom_po_id": {"type": "many2one"},
        } if campos is None else campos)
        self.unidades = ([{"id": 1, "name": "Unidades"}, {"id": 11, "name": "L"},
                          {"id": 21, "name": "kg"}]
                         if unidades is None else unidades)
        self.usados = set(usados)  # default_code ya ocupados en Odoo
        self.creados = []
        self.llamadas = []

    def ejecutar(self, modelo, metodo, args, kw=None):
        self.llamadas.append((modelo, metodo))
        clave = f"{modelo}.{metodo}"
        if clave == "product.category.search_read":
            pedidos = args[0][0][2]
            return [{"id": i, "name": n} for n, i in sorted(self.categorias.items(),
                                                            key=lambda x: x[1])
                    if n in pedidos]
        if clave == "account.tax.search":
            condiciones = {c[0]: c[2] for c in args[0]}
            return [t["id"] for t in self.impuestos
                    if t["type_tax_use"] == condiciones["type_tax_use"]
                    and t["amount"] == condiciones["amount"]][:1]
        if clave == "product.template.fields_get":
            return {c: m for c, m in self.campos.items() if c in args[0]}
        if clave == "uom.uom.search_read":
            return list(self.unidades)
        if clave == "product.template.search":
            codigo = args[0][0][2]
            return [777] if codigo in self.usados else []
        if clave == "product.template.create":
            self.creados.append(args[0])
            return 900 + len(self.creados)
        raise AssertionError(f"llamada no esperada a Odoo: {clave}")


@pytest.fixture
def odoo(monkeypatch):
    """Odoo configurado y simulado; los cachés del módulo, limpios."""
    for variable in ("ODOO_URL", "ODOO_DB", "ODOO_USER", "ODOO_PASSWORD"):
        monkeypatch.setenv(variable, "de-prueba")
    falso = OdooFalso()
    monkeypatch.setattr(ventas, "_ejecutar", falso.ejecutar)
    altas.reiniciar_cache()
    yield falso
    altas.reiniciar_cache()


MACETA = {"nombre": "Maceta barro 30", "material": "barro", "diametro": "30",
          "alto": "25", "color": "Terracota", "precio": "12.50", "costo": "6"}
INSUMO = {"nombre": "Abono orgánico", "unidad": "unidad", "precio": "9",
          "costo": "4.50"}


def _crear(tipo, form):
    limpio, error, campo = altas.revisar(tipo, form)
    assert error is None, error
    return altas.crear(limpio)


# ---------------------------------------------------------------------------
# Los impuestos: la regla dura del dueño
# ---------------------------------------------------------------------------

def test_el_create_manda_los_dos_impuestos_vacios(odoo):
    """Sin mandarlos, Odoo pone el impuesto por defecto de la compañía: así
    nacieron los 123 productos con un 7% de compra que nadie decidió."""
    _crear("maceta", MACETA)
    valores = odoo.creados[0]
    assert valores["taxes_id"] == [[6, 0, []]]
    assert valores["supplier_taxes_id"] == [[6, 0, []]]


def test_la_casilla_apagada_no_pone_ningun_impuesto(odoo):
    _crear("insumo", INSUMO)
    assert odoo.creados[0]["taxes_id"] == [[6, 0, []]]
    assert odoo.creados[0]["supplier_taxes_id"] == [[6, 0, []]]
    # Ni se molestó en buscarlo.
    assert ("account.tax", "search") not in odoo.llamadas


def test_la_casilla_marcada_pone_solo_el_impuesto_de_venta(odoo):
    hecho = _crear("maceta", {**MACETA, "itbms": "1"})
    valores = odoo.creados[0]
    assert valores["taxes_id"] == [[6, 0, [41]]]
    # El de compra se queda vacío SIEMPRE (lo está viendo el contador).
    assert valores["supplier_taxes_id"] == [[6, 0, []]]
    assert hecho["avisos"] == []


def test_sin_el_impuesto_del_7_el_producto_se_crea_igual(monkeypatch, odoo):
    """El impuesto se busca, nunca se crea: si no está, el producto nace sin
    él y queda el aviso en la pantalla."""
    odoo.impuestos = []
    hecho = _crear("maceta", {**MACETA, "itbms": "1"})
    assert odoo.creados[0]["taxes_id"] == [[6, 0, []]]
    assert "sin_itbms" in hecho["avisos"]
    assert altas.texto_de_avisos(hecho["avisos"])[0].startswith("No se encontró")
    # Y nunca se creó un account.tax.
    assert ("account.tax", "create") not in odoo.llamadas


# ---------------------------------------------------------------------------
# Las categorías: por nombre, y la que falta apaga su tipo
# ---------------------------------------------------------------------------

def test_la_categoria_se_resuelve_por_nombre(odoo):
    _crear("maceta", MACETA)
    assert odoo.creados[0]["categ_id"] == 13
    _crear("insumo", INSUMO)
    assert odoo.creados[1]["categ_id"] == 10
    assert ("product.category", "search_read") in odoo.llamadas


def test_sin_la_categoria_el_tipo_se_apaga_sin_reventar(odoo):
    odoo.categorias = {"Insumos": 10}
    altas.reiniciar_cache()
    tipos = {t["clave"]: t for t in altas.tipos_para_pantalla()}
    assert tipos["maceta"]["listo"] is False
    assert "Macetas" in tipos["maceta"]["motivo"]
    assert tipos["insumo"]["listo"] is True
    # Y la planta no depende de ninguna categoría nueva: siempre está.
    assert tipos["planta"]["listo"] is True
    # Intentar crearla igual no revienta: sube con su motivo.
    limpio, _, _campo = altas.revisar("maceta", MACETA)
    with pytest.raises(datos.SinConexion) as fallo:
        altas.crear(limpio)
    assert "Macetas" in str(fallo.value)
    assert odoo.creados == []


def test_sin_odoo_los_dos_tipos_nuevos_se_apagan(monkeypatch):
    for variable in ("ODOO_URL", "ODOO_DB", "ODOO_USER", "ODOO_PASSWORD"):
        monkeypatch.delenv(variable, raising=False)
    altas.reiniciar_cache()
    tipos = {t["clave"]: t for t in altas.tipos_para_pantalla()}
    assert tipos["maceta"]["listo"] is False
    assert tipos["insumo"]["listo"] is False
    assert tipos["planta"]["listo"] is True


def test_la_categoria_plantas_no_se_ofrece_en_ningun_formulario(odoo, cliente,
                                                                con_inventario):
    """«Plantas» existe vacía en Odoo y NO se usa (dueño, 30/09/2026): la
    planta elige Exterior / Interior / Florales."""
    assert "Plantas" not in altas.CATEGORIA_DE.values()
    assert "Plantas" not in datos.CATEGORIAS_PLANTA
    html = cliente.get("/?tab=stock").text
    opciones = html.split('id="agregar-categoria"', 1)[1].split("</select>", 1)[0]
    assert ">Exterior<" in opciones and ">Interior<" in opciones
    assert ">Plantas<" not in opciones


# ---------------------------------------------------------------------------
# Una maceta nace NO publicada
# ---------------------------------------------------------------------------

def test_la_maceta_nace_sin_publicar(odoo):
    """`publicado` (el Boolean del addon) trae default=True en Odoo: hay que
    mandarlo en False a propósito o la maceta nacería lista para la tienda."""
    _crear("maceta", MACETA)
    assert odoo.creados[0]["publicado"] is False


def test_el_insumo_tampoco_nace_publicado(odoo):
    _crear("insumo", INSUMO)
    assert odoo.creados[0]["publicado"] is False


def test_sin_la_casilla_publicado_se_avisa(odoo):
    """Un Odoo sin el addon no tiene la casilla: se crea igual y se dice."""
    odoo.campos = {}
    altas.reiniciar_cache()
    hecho = _crear("maceta", MACETA)
    assert "publicado" not in odoo.creados[0]
    assert "sin_publicado" in hecho["avisos"]


# ---------------------------------------------------------------------------
# Los cuatro campos del addon: tolerancia a que todavía no existan
# ---------------------------------------------------------------------------

def test_los_cuatro_datos_de_la_maceta_van_cuando_odoo_los_tiene(odoo):
    _crear("maceta", MACETA)
    valores = odoo.creados[0]
    assert valores["maceta_material"] == "barro"
    assert valores["maceta_diametro_cm"] == 30
    assert valores["maceta_alto_cm"] == 25
    assert valores["maceta_color"] == "Terracota"


def test_sin_los_campos_del_addon_la_maceta_se_crea_igual(odoo):
    """Otra tanda los está agregando al addon; hoy no existen en el Odoo
    real. El alta guarda lo demás y lo dice, nunca revienta."""
    odoo.campos = {"publicado": {"type": "boolean"},
                   "uom_id": {"type": "many2one"}}
    altas.reiniciar_cache()
    hecho = _crear("maceta", MACETA)
    valores = odoo.creados[0]
    assert not any(c in valores for c in altas.CAMPOS_MACETA)
    assert valores["name"] == "Maceta barro 30"
    assert valores["list_price"] == 12.5
    assert "sin_campos_maceta" in hecho["avisos"]


def test_si_no_se_pudo_preguntar_por_los_campos_no_se_mandan(odoo, monkeypatch):
    """fields_get caído: mandar un campo que no existe haría fallar el
    create entero, así que se omiten (y se avisa)."""
    monkeypatch.setattr(altas, "_leer_metadatos", lambda: None)
    altas.reiniciar_cache()
    hecho = _crear("maceta", MACETA)
    assert not any(c in odoo.creados[0] for c in altas.CAMPOS_MACETA)
    assert "sin_campos_maceta" in hecho["avisos"]
    # publicado sí se manda: en producción existe y la regla del dueño manda.
    assert odoo.creados[0]["publicado"] is False


def test_el_material_se_casa_con_las_claves_del_addon(odoo):
    """Si el addon usara las etiquetas como clave, se manda la etiqueta."""
    odoo.campos = {**odoo.campos, "maceta_material": {
        "type": "selection",
        "selection": [["Barro", "Barro"], ["Plástico", "Plástico"]]}}
    altas.reiniciar_cache()
    _crear("maceta", MACETA)
    assert odoo.creados[0]["maceta_material"] == "Barro"


def test_el_diametro_y_el_alto_son_opcionales(odoo):
    _crear("maceta", {**MACETA, "diametro": "", "alto": "", "color": ""})
    valores = odoo.creados[0]
    assert "maceta_diametro_cm" not in valores
    assert "maceta_color" not in valores
    assert valores["maceta_material"] == "barro"


# ---------------------------------------------------------------------------
# El insumo y su unidad (que nunca se crea)
# ---------------------------------------------------------------------------

def test_la_unidad_se_busca_entre_las_que_odoo_ya_tiene(odoo):
    _crear("insumo", {**INSUMO, "unidad": "litro"})
    assert odoo.creados[0]["uom_id"] == 11
    assert odoo.creados[0]["uom_po_id"] == 11
    # Y la otra que ofrece el formulario también calza ("Unidades").
    _crear("insumo", {**INSUMO, "unidad": "unidad"})
    assert odoo.creados[1]["uom_id"] == 1


def test_el_formulario_solo_ofrece_litro_y_unidad(odoo):
    """«Saco» se quitó (dueño, 01/10/2026: «saco no lo pongas, ponlo como
    insumo y listo»): en este Odoo esa unidad de medida no existe y él no
    quiso crearla. Quedan las dos que Odoo sí tiene."""
    assert [u["clave"] for u in altas.UNIDADES] == ["litro", "unidad"]
    assert all(altas.id_de_unidad(u["clave"]) for u in altas.UNIDADES)
    # Y lo que ya no se ofrece tampoco se acepta si alguien lo postea.
    limpio, error, campo = altas.revisar("insumo", {**INSUMO, "unidad": "saco"})
    assert limpio is None
    assert "unidad" in error.lower()
    assert odoo.creados == []


def test_una_unidad_que_odoo_no_tiene_no_se_crea(odoo):
    """La red de seguridad: si alguien renombra o archiva una unidad en Odoo,
    el insumo se crea igual con la unidad por defecto y la pantalla lo dice.
    Nunca se crea una unidad de medida. (Hoy las dos del formulario existen,
    así que esto no se dispara por el camino normal.)"""
    odoo.unidades = [{"id": 21, "name": "kg"}]
    altas.reiniciar_cache()
    hecho = _crear("insumo", INSUMO)
    assert "uom_id" not in odoo.creados[0]
    assert "sin_unidad" in hecho["avisos"]
    assert ("uom.uom", "create") not in odoo.llamadas
    # El insumo quedó creado: una unidad que no calza no bloquea el alta.
    assert odoo.creados[0]["default_code"] == "IN-ABONO-ORGANICO"


def test_sin_unidad_elegida_no_se_crea_nada(odoo):
    limpio, error, campo = altas.revisar("insumo", {**INSUMO, "unidad": ""})
    assert limpio is None
    assert "unidad" in error.lower()
    assert odoo.creados == []


# ---------------------------------------------------------------------------
# Validación del formulario
# ---------------------------------------------------------------------------

def test_un_nombre_sin_letras_no_es_un_nombre(odoo):
    for nombre in ("", "   ", "🤍", "30"):
        limpio, error, campo = altas.revisar("maceta", {**MACETA, "nombre": nombre})
        assert limpio is None and error


def test_precio_y_costo_ilegibles_o_negativos_rebotan(odoo):
    for cambio in ({"precio": "-1"}, {"costo": "-2"}, {"precio": "mucho"}):
        limpio, error, campo = altas.revisar("maceta", {**MACETA, **cambio})
        assert limpio is None and error


def test_precio_y_costo_son_opcionales_y_valen_cero(odoo):
    limpio, error, campo = altas.revisar("insumo", {**INSUMO, "precio": "", "costo": ""})
    assert error is None
    assert limpio["precio"] == 0.0 and limpio["costo"] == 0.0


def test_el_material_es_obligatorio_y_solo_de_la_lista(odoo):
    for material in ("", "acero", "FIBRA"):
        limpio, error, campo = altas.revisar("maceta", {**MACETA, "material": material})
        assert limpio is None and "material" in error.lower()


def test_un_tipo_que_no_existe_no_crea_nada(odoo):
    limpio, error, campo = altas.revisar("planta", MACETA)
    assert limpio is None
    # La planta existe, pero su formulario es el de Stock: se dice dónde.
    assert "Stock" in error
    limpio, error, campo = altas.revisar("bicicleta", MACETA)
    assert limpio is None and error


def test_un_nombre_del_que_no_sale_referencia_rebota(odoo):
    """Tiene letras, pero ninguna sirve para el default_code: un producto sin
    referencia queda invisible para el stock y para las ventas."""
    limpio, error, campo = altas.revisar("maceta", {**MACETA, "nombre": "日本"})
    assert limpio is None
    assert "referencia" in error


# ---------------------------------------------------------------------------
# La referencia (SKU)
# ---------------------------------------------------------------------------

def test_la_referencia_se_arma_del_nombre_con_su_prefijo(odoo):
    assert altas.sku_de("MC-", "Maceta Barro 30") == "MC-MACETA-BARRO-30"
    assert altas.sku_de("IN-", "Abono orgánico") == "IN-ABONO-ORGANICO"
    assert altas.sku_de("MC-", "¿?") == ""


def test_una_referencia_ocupada_se_resuelve_con_sufijo(odoo):
    odoo.usados = {"MC-MACETA-BARRO-30"}
    hecho = _crear("maceta", MACETA)
    assert hecho["sku"] == "MC-MACETA-BARRO-30-2"
    assert odoo.creados[0]["default_code"] == "MC-MACETA-BARRO-30-2"


def test_el_producto_nace_almacenable_y_vendible(odoo):
    _crear("maceta", MACETA)
    valores = odoo.creados[0]
    assert valores["type"] == "consu" and valores["is_storable"] is True
    assert valores["sale_ok"] is True
    assert valores["standard_price"] == 6.0


# ---------------------------------------------------------------------------
# Las pantallas
# ---------------------------------------------------------------------------

def test_el_boton_de_stock_ahora_lleva_a_elegir_el_tipo(cliente, con_inventario):
    html = cliente.get("/?tab=stock").text
    assert 'href="/productos/crear"' in html
    assert "Crear producto" in html
    # El de antes (un botón con onclick) ya no está.
    assert "abrirAgregar()" not in html


def _opciones_del_selector(html, campo="tipo"):
    """Los value= de ese <select>, en orden."""
    import re

    trozo = html.split(f'name="{campo}"', 1)[1].split("</select>", 1)[0]
    return re.findall(r'value="([^"]*)"', trozo)


def test_elegir_el_tipo_es_un_selector_que_arranca_vacio(
        cliente, con_inventario, odoo):
    """Pedido del dueño (30/09/2026): «si Python, pon elegir o maceta, insumo
    o planta» — el mismo gesto que el selector de categoría de la planta."""
    html = cliente.get("/productos/crear").text
    # Un form GET de HTML puro, al mismo sitio que los enlaces directos.
    assert 'method="get" action="/productos/crear"' in html
    # El placeholder vacío va PRIMERO y después los tres tipos, con planta
    # arriba: es lo que se crea todos los días.
    assert _opciones_del_selector(html) == ["", "planta", "maceta", "insumo"]
    assert "— elegir —" in html
    # Nada de navegar desde el navegador: el botón manda el form.
    assert "onchange" not in html
    assert 'type="submit"' in html


def test_un_tipo_sin_su_categoria_sale_deshabilitado_con_su_motivo(
        cliente, con_inventario, odoo):
    odoo.categorias = {"Insumos": 10}
    altas.reiniciar_cache()
    html = cliente.get("/productos/crear").text
    # Sigue en la lista (para que se vea que existe) pero no se puede elegir.
    assert _opciones_del_selector(html) == ["", "planta", "maceta", "insumo"]
    assert 'value="maceta" disabled' in html
    assert "Falta la categoría «Macetas» en Odoo" in html


def test_el_tipo_planta_manda_al_formulario_de_siempre(cliente, con_inventario, odoo):
    """El selector y un enlace directo llegan por la MISMA puerta: un form
    HTML no puede tener dos destinos sin JS, así que la redirección al modal
    de la planta vive en la ruta."""
    r = cliente.get("/productos/crear?tipo=planta", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/?tab=stock&crear=planta"


def test_el_enlace_directo_al_formulario_sigue_valiendo(
        cliente, con_inventario, odoo):
    """Quien llega con el tipo ya puesto ve su formulario, sin pasar por el
    selector."""
    for tipo, campo in (("maceta", 'name="material"'),
                        ("insumo", 'name="unidad"')):
        html = cliente.get(f"/productos/crear?tipo={tipo}").text
        assert campo in html
        assert f'<input type="hidden" name="tipo" value="{tipo}">' in html
        # Y el selector del paso 1 no se pinta.
        assert "— elegir —" not in html


def test_mandar_el_selector_sin_elegir_vuelve_a_preguntar(
        cliente, con_inventario, odoo):
    html = cliente.get("/productos/crear?tipo=").text
    assert "Elige qué vas a crear." in html
    assert "— elegir —" in html
    # Llegar sin ?tipo= (la primera vez) no reta a nadie.
    assert "Elige qué vas a crear." not in cliente.get("/productos/crear").text


def test_pedir_un_tipo_que_no_se_puede_crear_dice_que_falta(
        cliente, con_inventario, odoo):
    """?tipo=maceta sin la categoría en Odoo: el motivo concreto, no un
    genérico."""
    odoo.categorias = {"Insumos": 10}
    altas.reiniciar_cache()
    html = cliente.get("/productos/crear?tipo=maceta").text
    assert "Falta la categoría «Macetas» en Odoo" in html
    assert 'name="material"' not in html


def test_con_crear_planta_el_formulario_llega_abierto(cliente, con_inventario):
    html = cliente.get("/?tab=stock&crear=planta").text
    assert 'class="panel centrado abierto" id="modal-agregar"' in html
    # Sin el parámetro, cerrado.
    assert 'class="panel centrado" id="modal-agregar"' in \
        cliente.get("/?tab=stock").text


def test_el_formulario_de_maceta_trae_sus_campos(cliente, con_inventario, odoo):
    html = cliente.get("/productos/crear?tipo=maceta").text
    for campo in ('name="material"', 'name="diametro"', 'name="alto"',
                  'name="color"', 'name="precio"', 'name="costo"',
                  'name="foto"'):
        assert campo in html
    assert "Cobra ITBMS (7%)" in html
    # La casilla nace APAGADA.
    assert 'name="itbms" value="1" >' in html or 'name="itbms" value="1">' in html
    assert "checked" not in html.split('name="itbms"', 1)[1].split(">", 1)[0]


def test_el_formulario_de_insumo_trae_su_unidad_y_nada_de_maceta(
        cliente, con_inventario, odoo):
    html = cliente.get("/productos/crear?tipo=insumo").text
    assert 'name="unidad"' in html
    assert 'name="material"' not in html
    assert 'name="foto"' not in html


def test_el_selector_de_unidad_ofrece_litro_y_unidad_y_nada_mas(
        cliente, con_inventario, odoo):
    """En la pantalla, exactamente las dos que Odoo tiene, con el placeholder
    vacío primero. «Saco» ya no se ofrece (dueño, 01/10/2026)."""
    html = cliente.get("/productos/crear?tipo=insumo").text
    assert _opciones_del_selector(html, "unidad") == ["", "litro", "unidad"]
    assert ">Litro<" in html and ">Unidad<" in html
    assert "Saco" not in html


def test_el_post_crea_y_redirige_con_la_referencia(cliente, con_inventario, odoo):
    r = cliente.post("/productos/crear",
                     data={"tipo": "maceta", **MACETA},
                     follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == \
        "/productos/crear?tipo=maceta&creado=MC-MACETA-BARRO-30"
    assert len(odoo.creados) == 1
    # Y la pantalla de vuelta lo canta.
    html = cliente.get(r.headers["location"]).text
    assert "MC-MACETA-BARRO-30" in html


def test_el_post_con_avisos_los_pasa_en_la_url(cliente, con_inventario, odoo):
    # Un Odoo sin ninguna unidad que calce: el insumo se crea igual y el
    # aviso viaja como código en la URL del redirect.
    odoo.unidades = [{"id": 21, "name": "kg"}]
    altas.reiniciar_cache()
    r = cliente.post("/productos/crear",
                     data={"tipo": "insumo", **INSUMO},
                     follow_redirects=False)
    assert "aviso=sin_unidad" in r.headers["location"]
    html = cliente.get(r.headers["location"]).text
    assert "unidad por defecto" in html


def test_un_error_no_pierde_lo_escrito(cliente, con_inventario, odoo):
    r = cliente.post("/productos/crear",
                     data={"tipo": "maceta", **MACETA, "material": ""})
    assert r.status_code == 400
    assert odoo.creados == []
    assert "Maceta barro 30" in r.text  # lo escrito sigue en pantalla


def test_odoo_caido_lo_dice_y_no_redirige(cliente, con_inventario, odoo,
                                          monkeypatch):
    def revienta(*a, **k):
        raise RuntimeError("Odoo dijo que no")

    monkeypatch.setattr(ventas, "_ejecutar",
                        lambda modelo, metodo, args, kw=None:
                        revienta() if metodo == "create"
                        else odoo.ejecutar(modelo, metodo, args, kw))
    r = cliente.post("/productos/crear", data={"tipo": "maceta", **MACETA})
    assert r.status_code == 502
    assert "Odoo no aceptó el producto" in r.text


def test_sin_sesion_no_se_crea_nada(db_limpia, odoo):
    from fastapi.testclient import TestClient

    from app.main import app

    r = TestClient(app).post("/productos/crear", data={"tipo": "maceta", **MACETA},
                             follow_redirects=False)
    assert r.status_code in (302, 303, 401, 403)
    assert odoo.creados == []


def test_la_foto_viaja_en_base64(cliente, con_inventario, odoo):
    import base64

    contenido = b"\xff\xd8una foto"
    r = cliente.post("/productos/crear", data={"tipo": "maceta", **MACETA},
                     files={"foto": ("m.jpg", contenido, "image/jpeg")},
                     follow_redirects=False)
    assert r.status_code == 303
    assert odoo.creados[0]["image_1920"] == base64.b64encode(contenido).decode()


def test_sin_foto_no_se_manda_el_campo(cliente, con_inventario, odoo):
    cliente.post("/productos/crear", data={"tipo": "maceta", **MACETA},
                 follow_redirects=False)
    assert "image_1920" not in odoo.creados[0]
