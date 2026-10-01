"""Los PRODUCTOS de una compra, y que Compras no tire al empleado para arriba.

Tres cosas que el dueño reportó usando la pestaña (30/09/2026):

1. «Cuando pongo a hacer compra no me deja agregar las plantas que
   compramos»: la compra no tenía el concepto de LÍNEA. Ahora sí
   (`compra_linea`), y el buscador encuentra los tres tipos que el vivero
   compra — plantas (`PL-`), macetas (`MC-`) e insumos (`IN-`).
2. «Si escribe una planta que no hay, pon crear planta y que lo lleve al
   formulario SIN SACARLO DE LA MISMA PESTAÑA»: la compra a medio llenar se
   guarda como borrador antes del viaje, y al volver está todo lo escrito
   más el producto nuevo ya agregado.
3. «Asegúrate que todas las pestañas de compras no me lleven para arriba
   otra vez»: cada redirect lleva su ANCLA.

Nada de esto sale a la red: modo muestra (sin LINEAR_API_KEY, sin Odoo
configurado), igual que `tests/test_compras.py`.
"""

import pytest

from app import altas, compras, datos, ventas


@pytest.fixture(autouse=True)
def muestra_limpia(monkeypatch, db_limpia):
    monkeypatch.delenv("LINEAR_API_KEY", raising=False)
    monkeypatch.delenv("CALENDARIO_ESCRITURA", raising=False)
    monkeypatch.delenv("AJUSTES_ADMINS", raising=False)
    from app import control, linear_leads
    linear_leads.reiniciar_muestra()
    compras.reiniciar_muestra()
    compras.iniciar_tablas()
    control.iniciar_tablas()


@pytest.fixture
def de_dueno(monkeypatch):
    """La sesión de las pruebas es el dueño: el formulario se le abre."""
    monkeypatch.setenv("AJUSTES_ADMINS", "genesis")


USUARIO = "genesis"      # el id de la empleada con la que entra el TestClient


def _modal(texto):
    """El trozo del HTML que es el formulario de compra nueva."""
    assert 'id="cp-que"' in texto, "el formulario no está abierto"
    return texto.split('class="modal"', 1)[1]


# ---------------------------------------------------------------------------
# 1. La compra lleva líneas de producto
# ---------------------------------------------------------------------------

def test_la_tabla_de_lineas_guarda_producto_cantidad_y_costo():
    from app.datos import _db
    with _db() as con:
        columnas = {f["name"] for f in con.execute(
            "PRAGMA table_info(compra_linea)")}
    assert columnas == {"n", "ref", "producto_id", "sku", "nombre",
                        "cantidad", "costo"}
    # Igual que la tabla `compra`: ni estado (vive en Linear) ni totales
    # (viven en Odoo).
    assert "estado" not in columnas and "total" not in columnas


def test_una_compra_guarda_y_muestra_sus_lineas(cliente, de_dueno):
    aviso, error = compras.agregar_al_borrador(
        USUARIO, producto_id=104, sku="IN-TIERRA-NEGRA", nombre="Tierra negra",
        cantidad=50)
    assert error == "" and "Tierra negra" in aviso
    compras.agregar_al_borrador(USUARIO, producto_id=101,
                                sku="PL-PALMA-ARECA", nombre="Palma Areca")
    nueva = compras.crear("Pedido de octubre", autor="Génesis",
                          usuario_borrador=USUARIO)

    lineas = compras.lineas_de(nueva["ref"])
    assert [(l["sku"], l["cantidad"]) for l in lineas] == [
        ("IN-TIERRA-NEGRA", 50.0), ("PL-PALMA-ARECA", 1.0)]
    # Y el borrador quedó vacío: las líneas se MUDARON, no se copiaron.
    assert compras.borrador_de(USUARIO)["lineas"] == []
    assert compras.borrador_con_algo(USUARIO) is False

    # La tarjeta del tablero lo dice, y enlaza al panel con la lista.
    texto = cliente.get("/compras").text
    tarjeta = texto.split(f'id="c-{nueva["ref"]}"')[1].split("</div>")[0]
    assert "2 productos · Tierra negra, Palma Areca" in tarjeta
    assert f"/compras?abrir={nueva['ref']}" in tarjeta

    # Y el panel los muestra uno por uno.
    panel = cliente.get(f"/compras?abrir={nueva['ref']}").text
    assert "panel-der" in panel
    assert "Tierra negra" in panel and "IN-TIERRA-NEGRA" in panel
    assert "Palma Areca" in panel


def test_el_texto_libre_de_que_se_compra_se_queda(cliente, de_dueno):
    """No todo lo que se compra es un producto del catálogo (un flete, una
    herramienta): el título del issue sigue siendo el texto libre y las
    líneas son ADICIONALES, no su reemplazo."""
    nueva = compras.crear("Flete del camión y 2 palas", autor="Génesis")
    assert compras.uno(nueva["ref"])["que_compro"] == "Flete del camión y 2 palas"
    assert compras.lineas_de(nueva["ref"]) == []
    # Una compra sin productos no revienta el panel ni la tarjeta.
    texto = cliente.get(f"/compras?abrir={nueva['ref']}").text
    assert "se anotó sin productos" in texto


def test_el_mismo_producto_dos_veces_suma_en_vez_de_duplicar():
    """Agregar dos veces el mismo saco queriendo decir «dos sacos» es lo
    natural; dos renglones iguales en la orden de compra de Odoo serían un
    error para alguien después."""
    compras.agregar_al_borrador(USUARIO, sku="IN-TIERRA-NEGRA",
                                nombre="Tierra negra", cantidad=10)
    aviso, error = compras.agregar_al_borrador(
        USUARIO, sku="IN-TIERRA-NEGRA", nombre="Tierra negra", cantidad=5)
    assert error == "" and "15 en total" in aviso
    lineas = compras.borrador_de(USUARIO)["lineas"]
    assert len(lineas) == 1 and lineas[0]["cantidad"] == 15.0


def test_el_costo_vacio_es_no_se_sabe_y_nunca_un_cero():
    """Una compra se anota antes de saber el precio. Pintar $0.00 ahí sería
    inventar un número — la trampa de siempre."""
    compras.agregar_al_borrador(USUARIO, sku="IN-ABONO", nombre="Abono")
    linea = compras.borrador_de(USUARIO)["lineas"][0]
    assert linea["costo"] is None
    assert linea["importe"] is None          # no hay subtotal que pintar
    assert linea["costo_texto"] == ""        # el campo del formulario, vacío

    compras.guardar_cantidades(compras._clave_borrador(USUARIO),
                               costos={linea["n"]: "7.00"})
    linea = compras.borrador_de(USUARIO)["lineas"][0]
    assert linea["costo"] == 7.0 and linea["importe"] == 7.0


def test_el_total_dice_al_menos_cuando_falta_algun_costo():
    compras.agregar_al_borrador(USUARIO, sku="IN-A", nombre="Tierra",
                                cantidad=10, costo="4.00")
    compras.agregar_al_borrador(USUARIO, sku="IN-B", nombre="Abono")
    total = compras.borrador_de(USUARIO)["total"]
    assert total == {"total": 40.0, "sin_costo": 1}


def test_una_cantidad_ilegible_no_rompe_la_linea():
    compras.agregar_al_borrador(USUARIO, sku="IN-A", nombre="Tierra",
                                cantidad=10)
    n = compras.borrador_de(USUARIO)["lineas"][0]["n"]
    clave = compras._clave_borrador(USUARIO)
    for basura in ("", "abc", "-3", "0"):
        compras.guardar_cantidades(clave, cantidades={n: basura})
        assert compras.lineas_de(clave)[0]["cantidad"] == 10.0, basura


def test_la_cantidad_se_pinta_sin_el_punto_cero():
    compras.agregar_al_borrador(USUARIO, sku="IN-A", nombre="Tierra",
                                cantidad=3)
    assert compras.borrador_de(USUARIO)["lineas"][0]["cantidad_texto"] == "3"
    compras.guardar_cantidades(compras._clave_borrador(USUARIO),
                               cantidades={1: "2.5"})
    assert compras.borrador_de(USUARIO)["lineas"][0]["cantidad_texto"] == "2.5"


def test_un_n_de_otra_compra_no_se_puede_tocar_por_post():
    """`guardar_cantidades` solo toca los renglones DE esa compra: el
    formulario manda lo que tiene en pantalla, y un POST a mano no puede
    cambiarle la cantidad a la compra de otro."""
    compras.agregar_al_borrador("mary", sku="IN-A", nombre="Tierra",
                                cantidad=10)
    ajena = compras.borrador_de("mary")["lineas"][0]
    compras.guardar_cantidades(compras._clave_borrador(USUARIO),
                               cantidades={ajena["n"]: "999"})
    assert compras.borrador_de("mary")["lineas"][0]["cantidad"] == 10.0


def test_las_lineas_del_tablero_salen_en_una_sola_consulta():
    """El tablero trae varias compras y no se le hace una consulta por
    tarjeta (la misma regla que `_filas_locales`)."""
    primera = compras.crear("Una", autor="G")
    compras.agregar_linea(primera["ref"], sku="IN-A", nombre="Tierra")
    segunda = compras.crear("Otra", autor="G")
    compras.agregar_linea(segunda["ref"], sku="MC-B", nombre="Maceta")
    todas = compras.lineas_de_varias([primera["ref"], segunda["ref"], "VIV-201"])
    assert set(todas) == {primera["ref"], segunda["ref"]}   # la sin líneas no está
    assert todas[primera["ref"]][0]["sku"] == "IN-A"


def test_el_tope_de_lineas_lo_dice_en_vez_de_crecer_sin_fin():
    for i in range(compras.MAX_LINEAS):
        compras.agregar_al_borrador(USUARIO, sku=f"IN-{i}", nombre=f"Cosa {i}")
    aviso, error = compras.agregar_al_borrador(USUARIO, sku="IN-X",
                                               nombre="Una más")
    assert aviso == "" and str(compras.MAX_LINEAS) in error
    assert len(compras.borrador_de(USUARIO)["lineas"]) == compras.MAX_LINEAS


def test_quitar_una_linea_que_ya_no_esta_lo_dice():
    aviso, error = compras.quitar_del_borrador(USUARIO, 999)
    assert aviso == "" and "ya no está" in error
    aviso, error = compras.quitar_del_borrador(USUARIO, "no-es-un-numero")
    assert aviso == "" and "cuál producto quitar" in error


# ---------------------------------------------------------------------------
# El buscador: los TRES tipos, por el camino de Vender
# ---------------------------------------------------------------------------

def test_el_buscador_encuentra_los_tres_tipos_de_producto():
    """Plantas, macetas e insumos: las tres cosas que el vivero le compra a
    un proveedor."""
    assert compras.PREFIJOS_COMPRA == ("PL-", "MC-", "IN-")
    for texto, esperado in (("palma", "PL-PALMA-ARECA"),
                            ("maceta", "MC-MACETA-BARRO-30"),
                            ("tierra", "IN-TIERRA-NEGRA")):
        encontrados = compras.buscar_productos(texto)
        assert encontrados["ok"] is True
        assert esperado in [p["sku"] for p in encontrados["productos"]], texto


def test_el_buscador_usa_el_camino_de_vender_con_los_tres_prefijos(monkeypatch):
    """Se reusa `ventas.buscar_productos` —el mismo XML-RPC contra
    `product.product` que usa Vender— con otros dos argumentos, no un
    segundo buscador."""
    monkeypatch.setattr(ventas, "configurado", lambda: True)
    pedidos = []

    def falso(modelo, metodo, args, kw=None):
        pedidos.append((modelo, metodo, args, kw))
        return [{"id": 7, "default_code": "IN-TIERRA", "name": "Tierra",
                 "list_price": 4.0}]

    monkeypatch.setattr(ventas, "_ejecutar", falso)
    assert compras.buscar_productos("tierra")["productos"][0]["sku"] == "IN-TIERRA"
    modelo, metodo, args, _kw = pedidos[0]
    assert (modelo, metodo) == ("product.product", "search_read")
    dominio = args[0]
    # Los tres prefijos, en un OR (notación prefija de Odoo: N-1 "|").
    assert dominio[:2] == ["|", "|"]
    assert [d for d in dominio if d[0] == "default_code" and d[1] == "like"] == [
        ["default_code", "like", "PL-"], ["default_code", "like", "MC-"],
        ["default_code", "like", "IN-"]]
    # `sale_ok` NO se le pide: un insumo que se compra no tiene por qué
    # estar a la venta, y filtrarlo lo esconderia de esta pantalla.
    assert not any(d[0] == "sale_ok" for d in dominio if isinstance(d, list))


def test_el_buscador_de_vender_no_cambio():
    """Generalizar `buscar_productos` no le movió nada a Vender: con UN
    prefijo el dominio sale IDÉNTICO al de siempre."""
    assert ventas.dominio_de_busqueda("romero") == [
        ["default_code", "like", "PL-"], ["sale_ok", "=", True],
        "|", ["name", "ilike", "romero"], ["default_code", "ilike", "romero"]]


def test_odoo_caido_no_se_lee_como_no_existe(monkeypatch, cliente, de_dueno):
    """La diferencia entre «no hay» y «no sé» tiene que llegar a la
    pantalla: con Odoo caído NO se ofrece «Crear producto», porque el
    producto puede existir perfectamente."""
    monkeypatch.setattr(ventas, "configurado", lambda: True)

    def revienta(*_a, **_k):
        raise RuntimeError("Odoo no contesta")

    monkeypatch.setattr(ventas, "_ejecutar", revienta)
    resultado = compras.buscar_productos("tierra")
    assert resultado["ok"] is False and resultado["productos"] == []

    texto = cliente.get("/compras?nueva=1&q=tierra").text
    assert "No se pudo buscar en Odoo" in texto
    assert "Crear producto" not in _modal(texto)


def test_buscar_sin_resultados_ofrece_crear_el_producto(cliente, de_dueno):
    texto = cliente.get("/compras?nueva=1&q=corteza+de+pino").text
    modal = _modal(texto)
    assert "No hay ningún producto que diga" in modal
    assert "Crear producto «corteza de pino»" in modal
    assert 'value="crear_producto"' in modal


def test_el_buscador_vacio_no_dice_que_no_hay_nada(cliente, de_dueno):
    modal = _modal(cliente.get("/compras?nueva=1").text)
    assert "No hay ningún producto" not in modal
    assert 'value="crear_producto"' not in modal


# ---------------------------------------------------------------------------
# 2. Crear un producto que no existe, sin perder lo escrito
# ---------------------------------------------------------------------------

def test_el_formulario_a_medio_llenar_sobrevive_al_viaje(cliente, de_dueno):
    """El caso del dueño, de punta a punta: escribe la compra, busca algo
    que no hay, se va a crearlo, y al volver está TODO más el producto
    nuevo."""
    # 1. Escribe la compra y busca algo que no existe.
    respuesta = cliente.post("/compras/borrador", data={
        "accion": "buscar", "que_compro": "Pedido de octubre",
        "proveedor": "Don Pepe", "resp": "Mary", "lead_ref": "LEAD-88",
        "q": "corteza de pino"}, follow_redirects=False)
    assert respuesta.status_code == 303

    # 2. Se va a crear el producto: el borrador YA quedó guardado, y lo
    #    escrito en el buscador viaja como nombre sugerido.
    respuesta = cliente.post("/compras/borrador", data={
        "accion": "crear_producto", "que_compro": "Pedido de octubre",
        "proveedor": "Don Pepe", "resp": "Mary", "lead_ref": "LEAD-88",
        "q": "corteza de pino"}, follow_redirects=False)
    destino = respuesta.headers["location"]
    assert destino.startswith("/productos/crear?volver=compra")
    assert "nombre=corteza" in destino

    # 3. La pantalla de alta lo lleva escondido en sus DOS pasos, así que
    #    elegir el tipo no lo pierde, y el nombre viene sugerido.
    paso1 = cliente.get(destino).text
    assert '<input type="hidden" name="volver" value="compra">' in paso1
    assert '<input type="hidden" name="nombre" value="corteza de pino">' in paso1
    paso2 = cliente.get("/productos/crear?tipo=insumo&volver=compra"
                        "&nombre=corteza+de+pino").text
    assert '<input type="hidden" name="volver" value="compra">' in paso2
    assert 'value="corteza de pino"' in paso2
    assert "Volver a la compra" in paso2

    # 4. Y mientras no haya vuelto, lo que escribió sigue guardado.
    borrador = compras.borrador_de(USUARIO)
    assert borrador["que_compro"] == "Pedido de octubre"
    assert borrador["proveedor"] == "Don Pepe"
    assert borrador["resp"] == "Mary"
    assert borrador["lead_ref"] == "LEAD-88"


def test_al_crear_el_producto_vuelve_a_la_compra_con_la_linea_puesta(
        monkeypatch, cliente, de_dueno):
    """La otra mitad de «sin sacarlo de la misma pestaña»: al volver, el
    producto recién creado ya es una línea de la compra."""
    compras.guardar_borrador(USUARIO, datos={
        "que_compro": "Pedido de octubre", "proveedor": "Don Pepe",
        "resp": "", "lead_ref": ""})
    monkeypatch.setattr(ventas, "configurado", lambda: True)
    monkeypatch.setattr(altas, "categorias_de_producto",
                        lambda: {"Macetas": 13, "Insumos": 10})
    monkeypatch.setattr(altas, "metadatos", lambda: {})

    def falso(modelo, metodo, args, kw=None):
        if (modelo, metodo) == ("product.template", "create"):
            return 777
        if (modelo, metodo) == ("product.product", "search_read"):
            return [{"id": 888, "default_code": "IN-CORTEZA-DE-PINO",
                     "name": "Corteza de pino", "list_price": 0.0}]
        return []

    monkeypatch.setattr(ventas, "_ejecutar", falso)

    respuesta = cliente.post("/productos/crear", data={
        "tipo": "insumo", "volver": "compra", "nombre": "Corteza de pino",
        "unidad": "saco", "precio": "0", "costo": "4.50"},
        follow_redirects=False)
    assert respuesta.status_code == 303
    destino = respuesta.headers["location"]
    assert destino.startswith("/compras?nueva=1")
    assert destino.endswith(compras.ANCLA_LINEAS)   # a la lista, no al tope

    borrador = compras.borrador_de(USUARIO)
    assert borrador["que_compro"] == "Pedido de octubre"   # nada se perdió
    assert borrador["proveedor"] == "Don Pepe"
    assert [(l["sku"], l["nombre"], l["producto_id"])
            for l in borrador["lineas"]] == [
        ("IN-CORTEZA-DE-PINO", "Corteza de pino", 888)]


def test_si_odoo_no_dice_el_id_del_producto_la_linea_no_se_pierde(
        monkeypatch, cliente, de_dueno):
    """El alta devuelve el id del `product.template` y las líneas guardan el
    de `product.product`, que no es el mismo. Si esa segunda consulta falla,
    la línea se agrega IGUAL con su SKU y su nombre, que es lo durable."""
    monkeypatch.setattr(ventas, "configurado", lambda: True)
    monkeypatch.setattr(altas, "categorias_de_producto",
                        lambda: {"Macetas": 13, "Insumos": 10})
    monkeypatch.setattr(altas, "metadatos", lambda: {})

    def falso(modelo, metodo, args, kw=None):
        if (modelo, metodo) == ("product.template", "create"):
            return 777
        raise RuntimeError("Odoo se cayó justo ahora")

    monkeypatch.setattr(ventas, "_ejecutar", falso)
    cliente.post("/productos/crear", data={
        "tipo": "insumo", "volver": "compra", "nombre": "Corteza de pino",
        "unidad": "saco"}, follow_redirects=False)
    lineas = compras.borrador_de(USUARIO)["lineas"]
    assert len(lineas) == 1
    assert lineas[0]["sku"] == "IN-CORTEZA-DE-PINO"
    assert lineas[0]["producto_id"] is None       # no se sabe, y no se inventa


# ---------------------------------------------------------------------------
# La PLANTA, que es el caso que el dueño describió con esas palabras
#
# Su alta es la única que no vive en `/productos/crear`: sigue en el modal de
# Stock, que postea a `/productos/nuevo` por el order-api. La primera
# entrega dejaba el borrador a salvo pero la planta había que buscarla otra
# vez — media solución, justo para el caso principal.
#
# Lo que lo cerró: el destino de después de crear LO CALCULA PYTHON y el JS
# solo lo lee (antes era una URL escrita a mano en app.js, que es justo lo
# que la regla del proyecto no permite), y la línea la agrega el SERVIDOR al
# recibir el alta.
# ---------------------------------------------------------------------------

def test_crear_planta_desde_una_compra_lleva_el_volver_hasta_stock(cliente,
                                                                   de_dueno):
    respuesta = cliente.get("/productos/crear?tipo=planta&volver=compra",
                            follow_redirects=False)
    assert respuesta.status_code == 303
    assert respuesta.headers["location"] == ("/?tab=stock&crear=planta"
                                             "&volver=compra")


def test_sin_volver_la_planta_sigue_yendo_a_stock_como_siempre(cliente):
    respuesta = cliente.get("/productos/crear?tipo=planta",
                            follow_redirects=False)
    assert respuesta.headers["location"] == "/?tab=stock&crear=planta"


def test_el_destino_despues_de_crear_la_planta_lo_decide_python(
        cliente, con_inventario, de_dueno):
    """La pantalla de Stock renderiza el destino; el JS no elige nada. Sin
    nadie esperando la planta es la recarga de siempre."""
    from app import main
    assert main._destino_tras_crear_planta("") == main.DESTINO_TRAS_CREAR_PLANTA
    assert main._destino_tras_crear_planta("compra") == (
        "/compras?nueva=1" + compras.ANCLA_LINEAS)
    # Un `volver` inventado no manda a ninguna parte: cae en el de siempre.
    assert main._destino_tras_crear_planta("https://otro-sitio.com") == (
        main.DESTINO_TRAS_CREAR_PLANTA)

    normal = cliente.get("/?tab=stock").text
    assert '"destinoTrasCrear": "/?refrescar=1&tab=stock&vista=global"' in normal
    assert '"volverTrasCrear": ""' in normal

    desde_compra = cliente.get("/?tab=stock&crear=planta&volver=compra").text
    assert '"destinoTrasCrear": "/compras?nueva=1#cp-lineas"' in desde_compra
    assert '"volverTrasCrear": "compra"' in desde_compra


def test_el_js_no_elige_el_destino_ni_lo_lleva_escrito():
    """El candado, mirado en el código: la URL de después de crear una
    planta ya no vive en app.js, y la línea que navega solo LEE el valor que
    mandó Python. Si alguien la vuelve a escribir a mano, esto lo caza."""
    fuente = open("app/static/app.js").read()
    assert "location.assign(DATOS.destinoTrasCrear)" in fuente
    # La URL de Stock aparece UNA sola vez, y es el respaldo de `DATOS`
    # cuando la página no lo trae — no la decisión de a dónde ir.
    assert fuente.count("/?refrescar=1&tab=stock&vista=global") == 1
    assert "volver: DATOS.volverTrasCrear" in fuente


def test_la_planta_recien_creada_entra_sola_a_la_compra(monkeypatch, cliente,
                                                        de_dueno):
    """El caso del dueño, cerrado: vuelve y la planta está puesta, igual que
    una maceta o un insumo."""
    compras.guardar_borrador(USUARIO, datos={
        "que_compro": "Pedido de octubre", "proveedor": "Don Pepe",
        "resp": "", "lead_ref": ""})
    monkeypatch.setattr(datos, "crear_planta_en_odoo",
                        lambda *a, **k: {"ok": True, "id": 555})
    monkeypatch.setattr(ventas, "configurado", lambda: True)
    monkeypatch.setattr(ventas, "_ejecutar", lambda *a, **k: [
        {"id": 666, "default_code": "PL-CORTEZA-DE-PINO",
         "name": "Corteza de Pino", "list_price": 0.0}])

    respuesta = cliente.post("/productos/nuevo", json={
        "nombre": "Corteza de Pino", "sku": "PL-CORTEZA-DE-PINO",
        "categoria": "Exterior", "precioCentavos": 0, "costoCentavos": 450,
        "cantidad": 0, "alturaMin": 0, "alturaMax": 0, "sinMoto": False,
        "volver": "compra"})
    assert respuesta.status_code == 200
    assert respuesta.json()["agregadaA"] == "compra"

    borrador = compras.borrador_de(USUARIO)
    assert borrador["que_compro"] == "Pedido de octubre"   # nada se perdió
    assert [(l["sku"], l["nombre"], l["producto_id"])
            for l in borrador["lineas"]] == [
        ("PL-CORTEZA-DE-PINO", "Corteza de Pino", 666)]


def test_una_planta_creada_desde_stock_no_se_mete_en_ninguna_compra(
        monkeypatch, cliente, de_dueno):
    """Sin `volver` no se toca nada: quien entró a Stock por su cuenta crea
    su planta y se queda en Stock, aunque tenga una compra a medio llenar."""
    compras.guardar_borrador(USUARIO, datos={
        "que_compro": "Pedido de octubre", "proveedor": "", "resp": "",
        "lead_ref": ""})
    monkeypatch.setattr(datos, "crear_planta_en_odoo",
                        lambda *a, **k: {"ok": True, "id": 555})
    respuesta = cliente.post("/productos/nuevo", json={
        "nombre": "Palma Areca", "sku": "PL-PALMA-ARECA",
        "categoria": "Exterior", "precioCentavos": 1500, "costoCentavos": 0,
        "cantidad": 0, "alturaMin": 0, "alturaMax": 0, "sinMoto": False})
    assert respuesta.status_code == 200
    assert respuesta.json()["agregadaA"] == ""
    assert compras.borrador_de(USUARIO)["lineas"] == []


def test_un_volver_inventado_en_el_alta_de_planta_no_hace_nada(monkeypatch,
                                                               cliente):
    monkeypatch.setattr(datos, "crear_planta_en_odoo",
                        lambda *a, **k: {"ok": True, "id": 555})
    respuesta = cliente.post("/productos/nuevo", json={
        "nombre": "Palma Areca", "sku": "PL-PALMA-ARECA",
        "categoria": "Exterior", "precioCentavos": 0, "costoCentavos": 0,
        "cantidad": 0, "alturaMin": 0, "alturaMax": 0, "sinMoto": False,
        "volver": "https://otro-sitio.com"})
    assert respuesta.json()["agregadaA"] == ""
    assert compras.borrador_con_algo(USUARIO) is False


def test_si_la_linea_no_se_puede_agregar_la_planta_igual_queda_creada(
        monkeypatch, cliente, de_dueno):
    """La planta YA está en Odoo: cantar un error ahí dejaría al empleado
    creyendo que no se creó, y la crearía dos veces."""
    monkeypatch.setattr(datos, "crear_planta_en_odoo",
                        lambda *a, **k: {"ok": True, "id": 555})

    def revienta(*_a, **_k):
        raise RuntimeError("la base local se cayó")

    monkeypatch.setattr(compras, "agregar_al_borrador", revienta)
    respuesta = cliente.post("/productos/nuevo", json={
        "nombre": "Corteza de Pino", "sku": "PL-CORTEZA-DE-PINO",
        "categoria": "Exterior", "precioCentavos": 0, "costoCentavos": 0,
        "cantidad": 0, "alturaMin": 0, "alturaMax": 0, "sinMoto": False,
        "volver": "compra"})
    assert respuesta.status_code == 200
    assert respuesta.json()["ok"] is True       # la planta se creó
    assert respuesta.json()["agregadaA"] == ""  # y se dice que no se agregó


def test_el_formulario_ya_no_pide_disculpas_por_la_planta(cliente, de_dueno):
    modal = _modal(cliente.get("/compras?nueva=1&q=corteza").text)
    assert "Vale para los tres" in modal
    assert "planta, maceta e insumo" in modal
    assert "formulario vive en Stock" not in modal


def test_altas_sigue_siendo_el_unico_camino_de_creacion():
    """No hay un segundo camino de alta para Compras: el producto se crea
    con `altas.crear`, que es el que tiene las reglas (los dos impuestos
    explícitamente vacíos, la casilla de ITBMS apagada, la maceta sin
    publicar). Mirado en el código, que es donde se cuela una copia.

    El candado concreto: TODO lo que `compras.py` le pide a Odoo es de
    lectura. Si algún día alguien mete acá un `create` de producto, esta
    prueba lo caza.
    """
    import re
    fuente = open(compras.__file__).read()
    metodos = re.findall(r'_ejecutar\(\s*"[^"]+",\s*"(\w+)"', fuente)
    assert len(metodos) >= 4, "el barrido no encontró las consultas a Odoo"
    assert set(metodos) <= {"search_read", "read", "search", "fields_get"}, \
        set(metodos)
    for prohibida in ("taxes_id", "categ_id", "supplier_taxes_id"):
        assert prohibida not in fuente, prohibida
    # Y el alta de verdad sigue siendo la de siempre.
    assert "def crear(" in open(altas.__file__).read()


def test_un_volver_inventado_no_manda_a_ninguna_parte(cliente, de_dueno):
    """`volver` llega en el query: lo único que puede hacer es elegir uno de
    los destinos de la lista de permitidos, nunca una URL suelta."""
    from app import main
    assert main._vuelta_del_alta("https://otro-sitio.com") is None
    assert main._vuelta_del_alta("") is None
    assert main._vuelta_del_alta("compra")["url"] == "/compras"
    texto = cliente.get("/productos/crear?volver=https://otro-sitio.com").text
    assert "otro-sitio.com" not in texto
    assert "Volver a Stock" in texto     # el destino de siempre


def test_el_borrador_abre_el_formulario_solo(cliente, de_dueno):
    """Quien volvió de crear un producto —o de cualquier otra pestaña—
    encuentra su trabajo, no el tablero."""
    assert 'id="cp-que"' not in cliente.get("/compras").text
    compras.guardar_borrador(USUARIO, datos={
        "que_compro": "Pedido de octubre", "proveedor": "", "resp": "",
        "lead_ref": ""})
    texto = cliente.get("/compras").text
    assert 'id="cp-que"' in texto
    assert 'value="Pedido de octubre"' in texto


def test_solo_las_lineas_tambien_abren_el_formulario(cliente, de_dueno):
    compras.agregar_al_borrador(USUARIO, sku="IN-A", nombre="Tierra negra")
    texto = cliente.get("/compras").text
    assert 'id="cp-que"' in texto
    assert "Tierra negra" in _modal(texto)


def test_mejor_no_descarta_el_borrador_entero(cliente, de_dueno):
    compras.guardar_borrador(USUARIO, datos={
        "que_compro": "Pedido de octubre", "proveedor": "Don Pepe",
        "resp": "", "lead_ref": ""})
    compras.agregar_al_borrador(USUARIO, sku="IN-A", nombre="Tierra")
    respuesta = cliente.post("/compras/borrador", data={"accion": "descartar"},
                             follow_redirects=False)
    assert respuesta.status_code == 303
    assert compras.borrador_con_algo(USUARIO) is False
    assert compras.borrador_de(USUARIO)["lineas"] == []
    # Y la pantalla vuelve al tablero, sin el formulario.
    assert 'id="cp-que"' not in cliente.get("/compras").text


def test_el_telon_tambien_cierra_el_formulario(cliente, de_dueno):
    """El telón es un `<a>` y no puede hacer POST: su `?cerrar=1` descarta
    el borrador. Sin eso no cerraría nada, porque mientras haya borrador la
    pantalla abre el formulario sola."""
    compras.agregar_al_borrador(USUARIO, sku="IN-A", nombre="Tierra")
    texto = cliente.get("/compras").text
    assert 'href="/compras?cerrar=1"' in texto
    respuesta = cliente.get("/compras?cerrar=1", follow_redirects=False)
    assert respuesta.status_code == 303
    assert compras.borrador_con_algo(USUARIO) is False


def test_un_error_al_anotar_no_pierde_lo_escrito(cliente, de_dueno):
    """El lead no existe: el formulario vuelve con todo, incluidos los
    productos, en vez de hacerle escribir de nuevo."""
    compras.agregar_al_borrador(USUARIO, sku="IN-A", nombre="Tierra negra")
    respuesta = cliente.post("/compras/nueva", data={
        "que_compro": "Pedido de octubre", "proveedor": "Don Pepe",
        "resp": "", "lead_ref": "LEAD-999"}, follow_redirects=False)
    destino = respuesta.headers["location"]
    assert "nueva=1" in destino and "LEAD-999" in destino
    borrador = compras.borrador_de(USUARIO)
    assert borrador["que_compro"] == "Pedido de octubre"
    assert borrador["proveedor"] == "Don Pepe"
    assert [l["sku"] for l in borrador["lineas"]] == ["IN-A"]


def test_agregar_desde_la_pantalla_guarda_lo_escrito_de_paso(cliente, de_dueno):
    """Cada viaje del formulario guarda primero: es lo que hace que nada se
    pierda. El id y el nombre del producto viajan escondidos con el SKU en
    su nombre, así que agregar no le cuesta ni una consulta más a Odoo."""
    respuesta = cliente.post("/compras/borrador", data={
        "que_compro": "Pedido de octubre", "proveedor": "Don Pepe",
        "resp": "", "lead_ref": "", "q": "tierra",
        "agregar": "IN-TIERRA-NEGRA",
        "pid-IN-TIERRA-NEGRA": "104",
        "nom-IN-TIERRA-NEGRA": "Tierra negra"}, follow_redirects=False)
    assert respuesta.status_code == 303
    borrador = compras.borrador_de(USUARIO)
    assert borrador["que_compro"] == "Pedido de octubre"
    assert [(l["sku"], l["producto_id"]) for l in borrador["lineas"]] == [
        ("IN-TIERRA-NEGRA", 104)]


def test_quitar_desde_la_pantalla(cliente, de_dueno):
    compras.agregar_al_borrador(USUARIO, sku="IN-A", nombre="Tierra")
    n = compras.borrador_de(USUARIO)["lineas"][0]["n"]
    respuesta = cliente.post("/compras/borrador", data={
        "que_compro": "Pedido", "proveedor": "", "resp": "", "lead_ref": "",
        "quitar": str(n)}, follow_redirects=False)
    assert respuesta.status_code == 303
    assert compras.borrador_de(USUARIO)["lineas"] == []


def test_las_cantidades_escritas_viajan_con_el_numero_de_renglon(cliente,
                                                                 de_dueno):
    """Un formulario sin JavaScript no puede mandar una lista de objetos: el
    número del renglón va en el NOMBRE del campo (`cant-7`, `costo-7`)."""
    compras.agregar_al_borrador(USUARIO, sku="IN-A", nombre="Tierra")
    n = compras.borrador_de(USUARIO)["lineas"][0]["n"]
    modal = _modal(cliente.get("/compras").text)
    assert f'name="cant-{n}"' in modal and f'name="costo-{n}"' in modal
    cliente.post("/compras/borrador", data={
        "accion": "guardar", "que_compro": "Pedido", "proveedor": "",
        "resp": "", "lead_ref": "", f"cant-{n}": "50", f"costo-{n}": "4.25"},
        follow_redirects=False)
    linea = compras.borrador_de(USUARIO)["lineas"][0]
    assert (linea["cantidad"], linea["costo"], linea["importe"]) == (
        50.0, 4.25, 212.5)


def test_anotar_la_compra_se_lleva_las_lineas_y_lo_dice(cliente, de_dueno):
    compras.agregar_al_borrador(USUARIO, sku="IN-A", nombre="Tierra")
    compras.agregar_al_borrador(USUARIO, sku="MC-B", nombre="Maceta")
    respuesta = cliente.post("/compras/nueva", data={
        "que_compro": "Pedido de octubre", "proveedor": "", "resp": "",
        "lead_ref": ""}, follow_redirects=False)
    destino = respuesta.headers["location"]
    assert "2%20productos" in destino
    nueva = [c for c in compras.listar()
             if c["que_compro"] == "Pedido de octubre"][0]
    assert len(compras.lineas_de(nueva["ref"])) == 2
    assert compras.borrador_con_algo(USUARIO) is False


def test_si_la_compra_no_nace_el_borrador_no_se_pierde(monkeypatch):
    """Las líneas se mudan AL FINAL y solo si Linear aceptó: una compra que
    no nació no puede quedarse con el trabajo del empleado."""
    compras.agregar_al_borrador(USUARIO, sku="IN-A", nombre="Tierra")
    with pytest.raises(compras.ErrorCompras):
        compras.crear("   ", autor="G", usuario_borrador=USUARIO)
    assert len(compras.borrador_de(USUARIO)["lineas"]) == 1


def test_los_botones_del_formulario_no_pasan_por_la_validacion_del_titulo(
        cliente, de_dueno):
    """El `required` de «¿Qué se compra?» no puede impedir buscar un
    producto antes de escribir el título: los botones que no son «Anotar la
    compra» llevan `formnovalidate`. La validación de verdad vive en Python
    (`compras.crear`), que es la que cuenta."""
    compras.agregar_al_borrador(USUARIO, sku="IN-A", nombre="Tierra")
    modal = _modal(cliente.get("/compras").text)
    # El único submit SIN formnovalidate es el que crea la compra.
    botones = [b for b in modal.split("<button")[1:] if 'type="submit"' in b]
    sin_candado = [b for b in botones if "formnovalidate" not in b]
    assert len(sin_candado) == 1
    assert "Anotar la compra" in sin_candado[0]
    # Y el formulario sigue pidiendo el título a quien sí va a crearla.
    assert "required" in modal
    respuesta = cliente.post("/compras/nueva", data={
        "que_compro": "  ", "proveedor": "", "resp": "", "lead_ref": ""},
        follow_redirects=False)
    assert "qu%C3%A9%20se%20compra" in respuesta.headers["location"]


# ---------------------------------------------------------------------------
# 3. Que ninguna pantalla de Compras lo tire para arriba
# ---------------------------------------------------------------------------

def test_mover_una_compra_vuelve_a_su_tarjeta(cliente, de_dueno):
    respuesta = cliente.post("/compras/estado",
                             data={"ref": "VIV-204", "estado": "EN_CAMINO"},
                             follow_redirects=False)
    assert respuesta.headers["location"].endswith("#c-VIV-204")


def test_un_error_al_mover_tambien_vuelve_a_la_tarjeta(cliente):
    """La sesión sin etiqueta `Resp:` no mueve nada ajeno — y el error no la
    manda al tope."""
    respuesta = cliente.post("/compras/estado",
                             data={"ref": "VIV-204", "estado": "EN_CAMINO"},
                             follow_redirects=False)
    destino = respuesta.headers["location"]
    assert "error=" in destino and destino.endswith("#c-VIV-204")


def test_un_ref_raro_no_se_cuela_en_el_ancla(cliente, de_dueno):
    """El ref llega de un POST y va PEGADO a la URL del redirect: lo que no
    sea un ref sano cae en el ancla del tablero."""
    assert compras.ancla_de_compra("VIV-204") == "#c-VIV-204"
    assert compras.ancla_de_compra("") == compras.ANCLA_TABLERO
    assert compras.ancla_de_compra("x y") == compras.ANCLA_TABLERO
    assert compras.ancla_de_compra("a#b?c") == compras.ANCLA_TABLERO
    respuesta = cliente.post("/compras/estado",
                             data={"ref": "no sano", "estado": "PEDIDO"},
                             follow_redirects=False)
    destino = respuesta.headers["location"]
    assert destino.endswith(compras.ANCLA_TABLERO)
    assert "#c-no" not in destino


def test_anotar_una_compra_vuelve_a_la_tarjeta_nueva(cliente, de_dueno):
    respuesta = cliente.post("/compras/nueva", data={
        "que_compro": "Tierra de hoja", "proveedor": "", "resp": "",
        "lead_ref": ""}, follow_redirects=False)
    nueva = [c for c in compras.listar()
             if c["que_compro"] == "Tierra de hoja"][0]
    assert respuesta.headers["location"].endswith(f"#c-{nueva['ref']}")


def test_cada_accion_del_formulario_vuelve_a_su_sitio(cliente, de_dueno):
    compras.agregar_al_borrador(USUARIO, sku="IN-A", nombre="Tierra")
    n = compras.borrador_de(USUARIO)["lineas"][0]["n"]
    base = {"que_compro": "Pedido", "proveedor": "", "resp": "", "lead_ref": ""}
    casos = [
        # Buscar vuelve al BUSCADOR, que es donde están los resultados…
        ({**base, "accion": "buscar", "q": "tierra"}, compras.ANCLA_BUSCADOR),
        # …y agregar o quitar, a la LISTA, que es donde cambió algo.
        ({**base, "agregar": "IN-TIERRA-NEGRA",
          "nom-IN-TIERRA-NEGRA": "Tierra negra"}, compras.ANCLA_LINEAS),
        ({**base, "quitar": str(n)}, compras.ANCLA_LINEAS),
        ({**base, "accion": "guardar"}, compras.ANCLA_LINEAS),
        ({"accion": "descartar"}, compras.ANCLA_TABLERO),
    ]
    for datos_form, ancla in casos:
        respuesta = cliente.post("/compras/borrador", data=datos_form,
                                 follow_redirects=False)
        destino = respuesta.headers["location"]
        assert destino.endswith(ancla), (datos_form, destino)


def test_ningun_redirect_de_compras_se_va_sin_ancla(cliente, de_dueno):
    """El barrido: TODO redirect de Compras tiene que llevar su ancla — es
    la regla de siempre del proyecto («volver tiene que devolverte donde
    estabas»), y era justo lo que estas pantallas no respetaban."""
    compras.agregar_al_borrador(USUARIO, sku="IN-A", nombre="Tierra")
    base = {"que_compro": "Pedido", "proveedor": "", "resp": "", "lead_ref": ""}
    posts = [
        ("/compras/estado", {"ref": "VIV-201", "estado": "COTIZANDO"}),
        ("/compras/estado", {"ref": "", "estado": "COTIZANDO"}),
        ("/compras/nueva", dict(base)),
        ("/compras/nueva", {**base, "lead_ref": "LEAD-999"}),
        ("/compras/borrador", {**base, "accion": "buscar", "q": "x"}),
        ("/compras/borrador", {**base, "accion": "guardar"}),
        ("/compras/borrador", {**base, "quitar": "1"}),
        ("/compras/borrador", {"accion": "descartar"}),
    ]
    for ruta, datos_form in posts:
        respuesta = cliente.post(ruta, data=datos_form, follow_redirects=False)
        assert respuesta.status_code == 303, ruta
        assert "#" in respuesta.headers["location"], (ruta, datos_form)


def test_el_tablero_la_columna_y_la_tarjeta_tienen_su_ancla(cliente):
    texto = cliente.get("/compras").text
    assert 'id="cp-tablero"' in texto
    for estado in compras.ESTADOS:
        assert f'id="col-{estado["clave"]}"' in texto
    assert 'id="c-VIV-201"' in texto


def test_la_x_del_banner_no_vuelve_al_tope(cliente, de_dueno):
    """Cerrar un aviso tampoco puede tirarlo para arriba: con el tablero
    vuelve al tablero y con el formulario abierto, al formulario."""
    texto = cliente.get("/compras?aviso=Hecho").text
    assert 'href="/compras#cp-tablero"' in texto
    compras.agregar_al_borrador(USUARIO, sku="IN-A", nombre="Tierra")
    texto = cliente.get("/compras?aviso=Hecho").text
    assert 'href="/compras?nueva=1#cp-lineas"' in texto


def test_cerrar_el_panel_de_productos_vuelve_a_su_tarjeta(cliente, de_dueno):
    nueva = compras.crear("Pedido de octubre", autor="G")
    compras.agregar_linea(nueva["ref"], sku="IN-A", nombre="Tierra")
    texto = cliente.get(f"/compras?abrir={nueva['ref']}").text
    assert f'href="/compras#c-{nueva["ref"]}"' in texto


def test_el_formulario_y_el_panel_no_se_pintan_a_la_vez(cliente, de_dueno):
    """Serían dos telones y dos hojas encima del tablero."""
    compras.agregar_al_borrador(USUARIO, sku="IN-A", nombre="Tierra")
    texto = cliente.get("/compras?abrir=VIV-201&nueva=1").text
    assert 'id="cp-que"' in texto          # el formulario sí
    assert "panel-der" not in texto        # el panel no


def test_ver_los_productos_de_una_tarjeta_le_gana_al_borrador(cliente, de_dueno):
    """Quien tocó «ver los productos» pidió ESO, aunque tenga una compra a
    medio llenar — que sigue guardada para cuando vuelva."""
    nueva = compras.crear("Pedido de octubre", autor="G")
    compras.agregar_linea(nueva["ref"], sku="IN-A", nombre="Tierra negra")
    compras.agregar_al_borrador(USUARIO, sku="MC-B", nombre="Maceta barro")
    texto = cliente.get(f"/compras?abrir={nueva['ref']}").text
    assert "panel-der" in texto
    assert 'id="cp-que"' not in texto
    # Y el borrador no se tocó.
    assert [l["sku"] for l in compras.borrador_de(USUARIO)["lineas"]] == ["MC-B"]


def test_en_solo_lectura_el_borrador_tampoco_se_llena_por_post(monkeypatch,
                                                               cliente):
    """El candado está en el SERVIDOR: que la instancia no muestre el
    formulario no basta, porque un POST se puede mandar a mano."""
    monkeypatch.setenv("LINEAR_API_KEY", "clave-de-prueba")
    monkeypatch.setenv("CALENDARIO_ESCRITURA", "0")
    respuesta = cliente.post("/compras/borrador", data={
        "que_compro": "Pedido", "proveedor": "", "resp": "", "lead_ref": "",
        "agregar": "IN-A", "nom-IN-A": "Tierra"}, follow_redirects=False)
    assert respuesta.status_code == 303
    assert "no%20escribe%20en%20Linear" in respuesta.headers["location"]
    assert compras.borrador_con_algo(USUARIO) is False


# ---------------------------------------------------------------------------
# Que nada de esto le pase por encima a lo que ya andaba
# ---------------------------------------------------------------------------

def test_sin_javascript_nuevo():
    """Todo en Python, pantallas en Jinja2: el arrastre sigue siendo el
    control.js de Control y no hay un cuarto script."""
    plantilla = open("app/plantillas/compras.html").read()
    scripts = [s.split('"')[0] for s in plantilla.split('<script src="')[1:]]
    assert [s.split("?")[0] for s in scripts] == [
        "/static/control.js", "/static/menu.js", "/static/calendario.js"]
    assert "onchange" not in plantilla and "onclick" not in plantilla


def test_el_tablero_sigue_andando_sin_lineas(cliente):
    """Ninguna de las 8 compras de muestra tiene líneas: el tablero de
    siempre no cambió en nada."""
    texto = cliente.get("/compras").text
    assert "50 sacos de tierra negra" in texto
    assert "producto ·" not in texto and "productos ·" not in texto


def test_las_lineas_no_guardan_ni_estado_ni_plata_de_la_compra():
    """El estado vive en Linear y el dinero en Odoo. El costo de la línea es
    lo que se le va a PEDIR al proveedor, no lo que se le pagó: eso sigue
    saliendo de la orden de compra (`compras.plata_de`)."""
    nueva = compras.crear("Pedido", autor="G")
    compras.agregar_linea(nueva["ref"], sku="IN-A", nombre="Tierra",
                          cantidad=10, costo="4.00")
    assert compras.plata_de(nueva["ref"])["hay"] is False
    assert compras.total_de_lineas(
        compras.lineas_de(nueva["ref"]))["total"] == 40.0


def test_el_inventario_de_prueba_no_hace_falta_para_buscar():
    """El catálogo de muestra del buscador es propio y trae los TRES tipos:
    el inventario del stock-proxy solo tiene plantas."""
    assert all(p["sku"].startswith("PL-") for p in datos.INVENTARIO_DE_PRUEBA)
    prefijos = {p["sku"].split("-")[0] + "-" for p in compras._CATALOGO_MUESTRA}
    assert prefijos == set(compras.PREFIJOS_COMPRA)
