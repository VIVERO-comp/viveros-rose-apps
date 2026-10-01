"""Las tres mejoras al formulario de compra (dueño, 01/10/2026).

1. **Ver cuánto hay de cada producto al elegirlo.** El buscador encontraba
   la planta y no decía cuánto había, que es justo el dato que hace falta
   cuando se está decidiendo qué comprar. Sale de donde ya lo lee Stock y
   lo lee Vender (`datos.obtener_inventario`), y **un stock que no se sabe
   dice que no se sabe, nunca 0**. Más el atajo «lo que está bajo», para
   elegir sin escribir el nombre.
2. **«¿Cómo llega?»** — camión · mula · la recogemos nosotros ·
   encomienda. Las dos primeras son palabras del dueño («ahora yo traigo
   por camión o mula»). **Moto, carro y pickup NO van acá**: esos son los
   vehículos con los que el negocio ENTREGA a un cliente, y esta pregunta
   es sobre la mercadería que ENTRA.
3. **El proveedor deja de ser texto suelto.** Lo escrito ya no se pierde:
   si no calza con ningún contacto de Odoo marcado proveedor, la pantalla
   ofrece CREARLO — explícito, nunca solo, porque un dedo resbalado no
   puede dejar dos «Agroservicios» distintos y partir el historial en dos.

Ninguna sale a la red: modo muestra sin `LINEAR_API_KEY`, el inventario
sin stock-proxy configurado (que devuelve los datos de prueba de
`datos.INVENTARIO_DE_PRUEBA`) y Odoo doblado con
`monkeypatch.setattr(ventas, "_ejecutar", ...)`, que es la única puerta
XML-RPC del módulo.
"""

import pytest

from app import compras, datos, ventas


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
    assert 'id="cp-que"' in texto, "el formulario no está abierto"
    return texto.split('class="modal"', 1)[1]


def _caja(texto, ancla):
    """El trozo del formulario que arranca en ese ancla y termina donde
    empieza la caja siguiente."""
    return texto.split(ancla, 1)[1].split("campo-caja", 1)[0]


def _odoo(monkeypatch, responde, anota=None):
    """Odoo conectado, con `_ejecutar` doblado. `anota` recoge las
    llamadas para poder mirar QUÉ se le mandó, sin adivinarlo."""
    monkeypatch.setattr(ventas, "configurado", lambda: True)

    def falso(modelo, metodo, args, kw=None):
        if anota is not None:
            anota.append((modelo, metodo, args, kw))
        return responde(modelo, metodo, args, kw)

    monkeypatch.setattr(ventas, "_ejecutar", falso)
    return falso


# ---------------------------------------------------------------------------
# 1. Cuánto hay de cada producto
# ---------------------------------------------------------------------------

def test_el_buscador_dice_cuanto_hay_de_cada_producto():
    """El inventario de prueba trae la Palma Areca con 41: el resultado
    sale con su número y con el texto ya armado por Python."""
    productos = compras.buscar_productos("palma")["productos"]
    palma = next(p for p in productos if p["sku"] == "PL-PALMA-ARECA")
    assert palma["disponible"] == 41
    assert palma["stock_texto"] == "41 en stock"
    assert palma["stock_cero"] is False


def test_un_stock_que_no_se_sabe_no_se_pinta_como_cero():
    """LA REGLA DE LA TANDA. La maceta de muestra no está en el inventario
    (el stock-proxy sirve los prefijos de su `CATALOGO_FILTRO`, que es una
    variable del droplet): eso es «no se sabe», y un 0 ahí mandaría a no
    comprar algo que puede estar agotado."""
    maceta = next(p for p in compras.buscar_productos("maceta")["productos"]
                  if p["sku"] == "MC-MACETA-BARRO-30")
    assert maceta["disponible"] is None
    assert maceta["stock_texto"] == "stock: no se sabe"
    # Y no es el cero: el cero tiene su propio texto y su propio resalte.
    assert maceta["stock_cero"] is False
    assert "0" not in maceta["stock_texto"]


def test_si_el_inventario_no_contesta_ningun_producto_estrena_un_cero(
        monkeypatch):
    def revienta(refrescar=False):
        raise RuntimeError("el stock-proxy no contesta")

    monkeypatch.setattr(datos, "obtener_inventario", revienta)
    productos = compras.buscar_productos("palma")["productos"]
    assert productos, "la búsqueda sí encontró el producto"
    for p in productos:
        assert p["disponible"] is None
        assert p["stock_texto"] == "stock: no se sabe"
        assert p["stock_cero"] is False


def test_un_cero_de_verdad_se_dice_y_se_resalta():
    """La Albahaca está en 0 en el inventario de prueba: ESE cero es real
    —medido— y por eso se resalta. Es lo contrario del que no se sabe."""
    bajos = compras.bajos()
    albahaca = next(p for p in bajos["productos"] if p["sku"] == "PL-ALBAHACA")
    assert albahaca["disponible"] == 0
    assert albahaca["stock_texto"] == "sin stock"
    assert albahaca["stock_cero"] is True


def test_el_stock_sale_en_la_pantalla_junto_al_producto(cliente, de_dueno):
    buscador = _caja(cliente.get("/compras?nueva=1&q=palma").text, 'id="cp-buscar"')
    assert "PL-PALMA-ARECA" in buscador
    assert "41 en stock" in buscador
    buscador = _caja(cliente.get("/compras?nueva=1&q=maceta").text, 'id="cp-buscar"')
    assert "MC-MACETA-BARRO-30" in buscador
    assert "stock: no se sabe" in buscador


def test_el_stock_no_le_cuesta_una_consulta_a_odoo_por_producto(monkeypatch):
    """El inventario se lee UNA vez por búsqueda, no una por resultado: es
    la misma lectura cacheada de Stock."""
    cuantas = {"n": 0}

    def contar(refrescar=False):
        cuantas["n"] += 1
        return list(datos.INVENTARIO_DE_PRUEBA), 1756800000.0

    monkeypatch.setattr(datos, "obtener_inventario", contar)
    productos = compras.buscar_productos("a")["productos"]
    assert len(productos) >= 2, "la búsqueda trajo varios resultados"
    assert cuantas["n"] == 1


def test_el_inventario_no_se_lee_si_no_hay_resultados(monkeypatch):
    def revienta(refrescar=False):
        raise AssertionError("no se le pregunta al inventario por nada")

    monkeypatch.setattr(datos, "obtener_inventario", revienta)
    assert compras.buscar_productos("no-existe-esto")["productos"] == []
    assert compras.buscar_productos("")["productos"] == []


# ---------------------------------------------------------------------------
# 1b. «Lo que está bajo»: elegir sin escribir
# ---------------------------------------------------------------------------

def test_lo_que_esta_bajo_usa_el_umbral_de_stock_y_no_uno_propio():
    """Qué es «bajo» no lo inventa esta pantalla: es el umbral global de
    Stock con la regla de `calculos.estado` (menos de dos veces el
    umbral). El día que el dueño lo cambie en Ajustes, la lista cambia."""
    bajos = compras.bajos()
    assert bajos["ok"] is True
    assert bajos["umbral"] == datos.umbral() == 3
    assert bajos["productos"], "el inventario de prueba tiene varios bajos"
    for p in bajos["productos"]:
        assert p["disponible"] < bajos["umbral"] * 2

    # Y sigue al umbral: subiéndolo entran más productos.
    datos.fijar_umbral(10)
    assert compras.bajos()["cuantos"] > bajos["cuantos"]


def test_lo_mas_vacio_va_arriba():
    """Es lo que hay que comprar primero."""
    disponibles = [p["disponible"] for p in compras.bajos()["productos"]]
    assert disponibles == sorted(disponibles)
    assert disponibles[0] == 0          # la Albahaca, agotada


def test_si_el_inventario_no_contesta_la_lista_lo_dice_y_no_revienta(
        monkeypatch):
    def revienta(refrescar=False):
        raise RuntimeError("el stock-proxy no contesta")

    monkeypatch.setattr(datos, "obtener_inventario", revienta)
    bajos = compras.bajos()
    assert bajos["ok"] is False
    assert "no contesta" in bajos["error"]
    assert bajos["productos"] == [] and bajos["cuantos"] == 0
    # El umbral tampoco se inventa cuando no se pudo leer nada.
    assert bajos["umbral"] is None


def test_la_lista_nace_cerrada_y_se_abre_con_su_boton(cliente, de_dueno):
    caja = _caja(cliente.get("/compras?nueva=1").text, 'id="cp-bajos"')
    assert "Ver lo que está bajo" in caja
    assert 'name="agregar"' not in caja          # cerrada: no hay renglones

    caja = _caja(cliente.get("/compras?nueva=1&bajos=1").text, 'id="cp-bajos"')
    assert "PL-ALBAHACA" in caja and "sin stock" in caja
    assert 'name="agregar" value="PL-ALBAHACA"' in caja
    assert "Ocultar" in caja


def test_abrir_y_cerrar_la_lista_guarda_lo_escrito(cliente, de_dueno):
    """Los dos botones pasan por `/compras/borrador`, así que el viaje no
    pierde el formulario — la regla del borrador vale también para esto."""
    base = {"que_compro": "Pedido de octubre", "proveedor": "Don Pepe",
            "resp": "Mary", "lead_ref": "", "como_llega": "camion"}
    r = cliente.post("/compras/borrador", data={**base, "accion": "bajos"},
                     follow_redirects=False)
    assert r.status_code == 303
    assert "bajos=1" in r.headers["location"]
    assert r.headers["location"].endswith(compras.ANCLA_BAJOS)
    assert compras.borrador_de(USUARIO)["que_compro"] == "Pedido de octubre"

    r = cliente.post("/compras/borrador",
                     data={**base, "bajos": "1", "accion": "ocultar_bajos"},
                     follow_redirects=False)
    assert "bajos=1" not in r.headers["location"]
    assert compras.borrador_de(USUARIO)["proveedor"] == "Don Pepe"


def test_agregar_desde_la_lista_no_la_cierra(cliente, de_dueno):
    """Se agregan varios seguidos: cerrarla en cada clic haría volver a
    abrirla cada vez. Lo logra el marcador escondido del formulario."""
    r = cliente.post("/compras/borrador", data={
        "que_compro": "Pedido", "bajos": "1", "agregar": "PL-ALBAHACA",
        "nom-PL-ALBAHACA": "Albahaca"}, follow_redirects=False)
    destino = r.headers["location"]
    assert "bajos=1" in destino and destino.endswith(compras.ANCLA_LINEAS)
    assert [l["sku"] for l in compras.borrador_de(USUARIO)["lineas"]] == \
        ["PL-ALBAHACA"]


def test_agregar_desde_la_lista_resuelve_el_id_del_producto(monkeypatch,
                                                            cliente, de_dueno):
    """La lista sale del inventario, que no trae el id de
    `product.product`: el servidor se lo pregunta a Odoo por ESE producto.
    Así la línea queda completa igual que si viniera del buscador."""
    _odoo(monkeypatch, lambda modelo, metodo, args, kw: (
        [{"id": 777, "default_code": "PL-ALBAHACA", "name": "Albahaca",
          "list_price": 3.5}]
        if modelo == "product.product" else []))
    cliente.post("/compras/borrador", data={
        "que_compro": "Pedido", "bajos": "1", "agregar": "PL-ALBAHACA",
        "nom-PL-ALBAHACA": "Albahaca"}, follow_redirects=False)
    linea = compras.borrador_de(USUARIO)["lineas"][0]
    assert linea["producto_id"] == 777


def test_si_odoo_no_contesta_la_linea_igual_se_guarda_con_su_sku(monkeypatch,
                                                                 cliente,
                                                                 de_dueno):
    """El SKU es lo durable: perder la línea por no haber podido resolver
    un id sería perder el trabajo del empleado."""
    def revienta(*_a, **_k):
        raise RuntimeError("Odoo no contesta")

    monkeypatch.setattr(ventas, "configurado", lambda: True)
    monkeypatch.setattr(ventas, "_ejecutar", revienta)
    cliente.post("/compras/borrador", data={
        "que_compro": "Pedido", "bajos": "1", "agregar": "PL-ALBAHACA",
        "nom-PL-ALBAHACA": "Albahaca"}, follow_redirects=False)
    linea = compras.borrador_de(USUARIO)["lineas"][0]
    assert linea["sku"] == "PL-ALBAHACA" and linea["nombre"] == "Albahaca"
    assert linea["producto_id"] is None


def test_el_buscador_no_paga_una_consulta_extra_por_el_id(monkeypatch, cliente,
                                                          de_dueno):
    """Lo que viene del buscador ya trae su id escondido: agregar no le
    cuesta ni una consulta más a Odoo, y funciona aunque Odoo se haya
    caído entre la búsqueda y el clic (la garantía que ya existía)."""
    monkeypatch.setattr(ventas, "configurado", lambda: True)

    def revienta(*_a, **_k):
        raise AssertionError("no se le pregunta nada a Odoo al agregar")

    monkeypatch.setattr(ventas, "_ejecutar", revienta)
    cliente.post("/compras/borrador", data={
        "que_compro": "Pedido", "agregar": "PL-PALMA-ARECA",
        "pid-PL-PALMA-ARECA": "101", "nom-PL-PALMA-ARECA": "Palma Areca"},
        follow_redirects=False)
    assert compras.borrador_de(USUARIO)["lineas"][0]["producto_id"] == 101


# ---------------------------------------------------------------------------
# 2. «¿Cómo llega?»
# ---------------------------------------------------------------------------

def test_las_cuatro_formas_y_sus_palabras():
    """Camión y mula son palabras del dueño. Moto, carro y pickup NO están:
    esos son los vehículos con los que se ENTREGA a un cliente."""
    claves = [f["clave"] for f in compras.FORMAS_LLEGADA]
    assert claves == ["camion", "mula", "nosotros", "encomienda"]
    titulos = " ".join(f["titulo"] for f in compras.FORMAS_LLEGADA).lower()
    assert "camión" in titulos and "mula" in titulos
    for ajeno in ("moto", "carro", "pickup"):
        assert ajeno not in titulos, ajeno


def test_la_forma_de_llegada_se_guarda_y_se_muestra():
    nueva = compras.crear("50 sacos de tierra", como_llega="mula",
                          autor="Génesis")
    compra = compras.uno(nueva["ref"])
    assert compra["como_llega"] == "mula"
    assert compra["llegada"] == "En mula"
    # Y en la tabla local, que es donde la tarjeta la lee en modo real.
    from app.datos import _db
    with _db() as con:
        fila = con.execute("SELECT * FROM compra WHERE ref = ?",
                           (nueva["ref"],)).fetchone()
    assert fila["como_llega"] == "mula"


def test_una_forma_inventada_cae_en_todavia_no_se_dijo():
    """Llega de un POST y de acá sale a la tarjeta: nada que no esté en el
    vocabulario se queda guardado."""
    nueva = compras.crear("Sacos", como_llega="helicoptero", autor="G")
    assert compras.uno(nueva["ref"])["como_llega"] == ""
    assert compras.uno(nueva["ref"])["llegada"] == ""
    # Y los vehículos de ENTREGA tampoco son formas de llegada.
    for ajeno in ("moto", "carro", "pickup"):
        assert compras._llegada(ajeno) == "", ajeno


def test_sin_decir_como_llega_la_compra_se_anota_igual():
    """Es un dato de apoyo, no un requisito: lo que hay que comprar se
    anota aunque todavía no se sepa cómo va a llegar."""
    nueva = compras.crear("Mangueras", autor="G")
    assert compras.uno(nueva["ref"])["como_llega"] == ""
    assert compras.uno(nueva["ref"])["llegada"] == ""


def test_la_tarjeta_y_el_panel_dicen_como_llega(cliente, de_dueno):
    texto = cliente.get("/compras").text
    tarjeta = texto.split('id="c-VIV-201"')[1].split("</div>")[0]
    assert "En camión" in tarjeta               # VIV-201 de la muestra
    # La tarjeta OMITE lo que no se dijo (el espacio manda)…
    sin_decir = texto.split('id="c-VIV-206"')[1].split("</div>")[0]
    assert "cmp-llega" not in sin_decir
    # …y el panel lo ESCRIBE (ahí la pregunta importa: ¿hay que ir?).
    panel = cliente.get("/compras?abrir=VIV-206").text
    assert "Cómo llega" in panel and "Todavía no se dijo" in panel
    panel = cliente.get("/compras?abrir=VIV-203").text
    assert "En mula" in panel


def test_el_formulario_pinta_las_cuatro_opciones_y_la_vacia(cliente, de_dueno):
    modal = _modal(cliente.get("/compras?nueva=1").text)
    assert 'name="como_llega"' in modal
    for f in compras.FORMAS_LLEGADA:
        assert f'value="{f["clave"]}"' in modal, f["clave"]
        assert f["titulo"] in modal, f["titulo"]
    # El «todavía no se sabe» es un radio de verdad: sin él, un grupo
    # marcado por error no se podría desmarcar.
    assert 'name="como_llega" value=""' in modal
    # Y el formulario NO pregunta por moto/carro/pickup.
    assert 'name="vehiculo"' not in modal


def test_la_forma_de_llegada_viaja_en_el_borrador(cliente, de_dueno):
    """Como todo lo escrito: el viaje a crear un producto no la pierde."""
    cliente.post("/compras/borrador", data={
        "que_compro": "Pedido", "como_llega": "nosotros",
        "accion": "guardar"}, follow_redirects=False)
    assert compras.borrador_de(USUARIO)["como_llega"] == "nosotros"
    modal = _modal(cliente.get("/compras?nueva=1").text)
    marcado = modal.split('value="nosotros"')[1][:40]
    assert "checked" in marcado


def test_una_forma_inventada_no_se_guarda_ni_en_el_borrador(cliente, de_dueno):
    cliente.post("/compras/borrador", data={
        "que_compro": "Pedido", "como_llega": "<script>",
        "accion": "guardar"}, follow_redirects=False)
    assert compras.borrador_de(USUARIO)["como_llega"] == ""


def test_anotar_la_compra_desde_la_pantalla_guarda_como_llega(cliente,
                                                              de_dueno):
    cliente.post("/compras/nueva", data={
        "que_compro": "Tierra de hoja", "proveedor": "", "resp": "",
        "lead_ref": "", "como_llega": "encomienda"}, follow_redirects=False)
    nueva = next(c for c in compras.listar()
                 if c["que_compro"] == "Tierra de hoja")
    assert nueva["como_llega"] == "encomienda"
    assert nueva["llegada"] == "Por encomienda"


def test_el_issue_de_linear_tambien_lo_cuenta(monkeypatch):
    """Quien abra el issue en Linear entiende la compra sin la pantalla."""
    descripcion = compras._descripcion("Don Pepe", "", "Génesis", "En camión")
    assert "Cómo llega" in descripcion and "En camión" in descripcion
    # Sin dato, el renglón no sale: no se escribe un «no se sabe» en Linear.
    assert "Cómo llega" not in compras._descripcion("Don Pepe", "", "G", "")


# ---------------------------------------------------------------------------
# 3. El proveedor deja de ser texto suelto
# ---------------------------------------------------------------------------

PROVEEDOR = {"id": 9, "name": "Agroservicios del Istmo",
             "phone": "6000-0000", "email": "", "city": "Panamá"}


def _con_proveedores(monkeypatch, filas=(PROVEEDOR,), anota=None):
    return _odoo(monkeypatch, lambda modelo, metodo, args, kw: (
        [dict(f) for f in filas] if metodo == "search_read" else []),
        anota=anota)


def test_un_proveedor_no_se_crea_solo_al_escribir(monkeypatch, cliente,
                                                  de_dueno):
    """LA REGLA DE LA TANDA: escribir el nombre NO crea nada en Odoo, ni al
    guardar el borrador ni al anotar la compra. Un dedo resbalado no puede
    dejar dos «Agroservicios» distintos."""
    llamadas = []
    _con_proveedores(monkeypatch, anota=llamadas)
    cliente.post("/compras/borrador", data={
        "que_compro": "Pedido", "proveedor": "Don Pepe Nuevo",
        "accion": "guardar"}, follow_redirects=False)
    cliente.get("/compras?nueva=1")
    cliente.post("/compras/nueva", data={
        "que_compro": "Pedido", "proveedor": "Don Pepe Nuevo", "resp": "",
        "lead_ref": ""}, follow_redirects=False)
    creaciones = [l for l in llamadas if l[1] == "create"]
    assert creaciones == [], creaciones
    # La compra sí se anotó, con el nombre libre y sin id.
    nueva = next(c for c in compras.listar() if c["proveedor"] == "Don Pepe Nuevo")
    assert nueva["proveedor_id"] is None


def test_la_pantalla_ofrece_crearlo_cuando_no_calza(monkeypatch, cliente,
                                                    de_dueno):
    _con_proveedores(monkeypatch)
    cliente.post("/compras/borrador", data={
        "que_compro": "Pedido", "proveedor": "Don Pepe Nuevo",
        "accion": "guardar"}, follow_redirects=False)
    modal = _modal(cliente.get("/compras?nueva=1").text)
    assert 'value="crear_proveedor"' in modal
    assert 'name="proveedor_tel"' in modal       # y le pide el teléfono
    assert "no está entre los proveedores de Odoo" in modal


def test_un_proveedor_que_ya_existe_no_se_ofrece_crear(monkeypatch, cliente,
                                                       de_dueno):
    _con_proveedores(monkeypatch)
    cliente.post("/compras/borrador", data={
        "que_compro": "Pedido", "proveedor": "agroservicios DEL istmo",
        "accion": "guardar"}, follow_redirects=False)
    modal = _modal(cliente.get("/compras?nueva=1").text)
    assert 'value="crear_proveedor"' not in modal
    assert "ya está en Odoo" in modal
    assert "6000-0000" in modal


def test_el_casamiento_no_distingue_mayusculas_ni_tildes_ni_espacios():
    lista = [{"id": 9, "nombre": "Agroservicios del Istmo"}]
    for escrito in ("Agroservicios del Istmo", "agroservicios del istmo",
                    "  AGROSERVICIOS DEL ISTMO  ", "Agroservicios del Ístmo"):
        assert compras.proveedor_que_calza(escrito, lista) is not None, escrito
    assert compras.proveedor_que_calza("Agroservicios", lista) is None
    assert compras.proveedor_que_calza("", lista) is None


def test_crearlo_explicitamente_lo_deja_marcado_proveedor(monkeypatch,
                                                          cliente, de_dueno):
    """Nace como `res.partner` con `supplier_rank` y su teléfono, y nada
    más: no se le inventan campos al contacto."""
    llamadas = []
    _odoo(monkeypatch, lambda modelo, metodo, args, kw: (
        [] if metodo == "search_read" else 77), anota=llamadas)
    respuesta = cliente.post("/compras/borrador", data={
        "que_compro": "Pedido", "proveedor": "Don Pepe",
        "proveedor_tel": "6111-2222", "accion": "crear_proveedor"},
        follow_redirects=False)
    assert respuesta.status_code == 303
    assert "creado%20como%20proveedor" in respuesta.headers["location"]

    creaciones = [l for l in llamadas if l[1] == "create"]
    assert len(creaciones) == 1
    modelo, _metodo, args, _kw = creaciones[0]
    assert modelo == "res.partner"
    assert args[0] == {"name": "Don Pepe", "supplier_rank": 1,
                       "phone": "6111-2222"}


def test_el_telefono_es_phone_y_nunca_mobile(monkeypatch, cliente, de_dueno):
    """OJO con Odoo 19: `res.partner` ya NO tiene `mobile`, y pedirlo o
    escribirlo revienta la llamada entera."""
    llamadas = []
    _odoo(monkeypatch, lambda modelo, metodo, args, kw: (
        [] if metodo == "search_read" else 78), anota=llamadas)
    cliente.post("/compras/borrador", data={
        "que_compro": "Pedido", "proveedor": "Don Pepe",
        "proveedor_tel": "6111-2222", "accion": "crear_proveedor"},
        follow_redirects=False)
    for _modelo, metodo, args, kw in llamadas:
        escrito = args[0] if metodo == "create" else {}
        assert "mobile" not in escrito
        assert "mobile" not in ((kw or {}).get("fields") or [])
    # Y no hay ningún `"mobile"` como dato en el módulo. Se busca con sus
    # comillas a propósito: el módulo SÍ nombra el campo en un comentario,
    # justamente para avisar de esta trampa, y un barrido sin comillas
    # cazaría el aviso en vez del bug.
    assert '"mobile"' not in open(compras.__file__).read()
    assert "'mobile'" not in open(compras.__file__).read()


def test_sin_telefono_el_campo_no_se_manda(monkeypatch, cliente, de_dueno):
    """Un teléfono vacío no viaja: escribir "" en Odoo es distinto de no
    escribirlo, y acá no hay nada que decir."""
    llamadas = []
    _odoo(monkeypatch, lambda modelo, metodo, args, kw: (
        [] if metodo == "search_read" else 79), anota=llamadas)
    cliente.post("/compras/borrador", data={
        "que_compro": "Pedido", "proveedor": "Don Pepe",
        "proveedor_tel": "", "accion": "crear_proveedor"},
        follow_redirects=False)
    creado = next(l for l in llamadas if l[1] == "create")[2][0]
    assert creado == {"name": "Don Pepe", "supplier_rank": 1}


def test_crearlo_no_le_pone_impuestos(monkeypatch, cliente, de_dueno):
    """El impuesto no lo decide el código."""
    llamadas = []
    _odoo(monkeypatch, lambda modelo, metodo, args, kw: (
        [] if metodo == "search_read" else 80), anota=llamadas)
    cliente.post("/compras/borrador", data={
        "que_compro": "Pedido", "proveedor": "Don Pepe",
        "accion": "crear_proveedor"}, follow_redirects=False)
    creado = next(l for l in llamadas if l[1] == "create")[2][0]
    for prohibido in ("taxes_id", "supplier_taxes_id", "property_account_"):
        assert not any(k.startswith(prohibido) for k in creado), prohibido


def test_crear_el_mismo_proveedor_dos_veces_no_lo_duplica(monkeypatch):
    """Entre que la pantalla se pintó y el clic, pudo haberlo creado otra
    persona: se vuelve a mirar con la lista fresca justo antes de crear."""
    llamadas = []
    _con_proveedores(monkeypatch, anota=llamadas)
    resultado = compras.crear_proveedor("agroservicios del istmo", "6000-0000")
    assert resultado["ok"] is True
    assert resultado["ya_estaba"] is True
    assert resultado["proveedor"]["id"] == 9
    assert [l for l in llamadas if l[1] == "create"] == []


def test_si_no_se_puede_leer_la_lista_no_se_crea_a_ciegas(monkeypatch):
    def revienta(*_a, **_k):
        raise RuntimeError("Odoo no contesta")

    monkeypatch.setattr(ventas, "configurado", lambda: True)
    monkeypatch.setattr(ventas, "_ejecutar", revienta)
    resultado = compras.crear_proveedor("Don Pepe")
    assert resultado["ok"] is False
    assert resultado["proveedor"] is None
    assert "No se pudo leer los proveedores" in resultado["error"]


def test_sin_odoo_no_se_ofrece_un_boton_muerto(cliente, de_dueno):
    """Sin Odoo conectado el botón no podría crear nada, y un botón muerto
    es peor que no tenerlo."""
    compras.guardar_borrador(USUARIO, datos={"que_compro": "Pedido",
                                             "proveedor": "Don Pepe"})
    modal = _modal(cliente.get("/compras?nueva=1").text)
    assert 'value="crear_proveedor"' not in modal
    assert compras.crear_proveedor("Don Pepe")["ok"] is False


def test_un_nombre_vacio_no_crea_nada(monkeypatch):
    llamadas = []
    _con_proveedores(monkeypatch, anota=llamadas)
    resultado = compras.crear_proveedor("   ")
    assert resultado["ok"] is False and "nombre" in resultado["error"]
    assert llamadas == []


def test_la_compra_guarda_el_id_del_proveedor(monkeypatch, cliente, de_dueno):
    """Lo que la pantalla dio por existente se guarda CON su id, no solo
    con su nombre: sin el id no se puede saber cuánto se le compró."""
    _con_proveedores(monkeypatch)
    cliente.post("/compras/nueva", data={
        "que_compro": "Tierra de hoja",
        "proveedor": "Agroservicios del Istmo", "resp": "", "lead_ref": ""},
        follow_redirects=False)
    nueva = next(c for c in compras.listar()
                 if c["que_compro"] == "Tierra de hoja")
    assert nueva["proveedor_id"] == 9
    from app.datos import _db
    with _db() as con:
        fila = con.execute("SELECT * FROM compra WHERE ref = ?",
                           (nueva["ref"],)).fetchone()
    assert fila["proveedor_id"] == 9
    assert fila["proveedor_nombre"] == "Agroservicios del Istmo"


def test_irse_a_crear_un_proveedor_no_pierde_el_borrador(monkeypatch, cliente,
                                                         de_dueno):
    """El viaje de crear el proveedor respeta el mecanismo del borrador,
    igual que el de crear un producto: al volver está TODO lo escrito y
    sus productos."""
    _odoo(monkeypatch, lambda modelo, metodo, args, kw: (
        [] if metodo == "search_read" else 81))
    compras.agregar_al_borrador(USUARIO, sku="IN-TIERRA-NEGRA",
                                nombre="Tierra negra", cantidad=50)
    respuesta = cliente.post("/compras/borrador", data={
        "que_compro": "Pedido de octubre", "proveedor": "Don Pepe",
        "proveedor_tel": "6111-2222", "resp": "Mary", "lead_ref": "",
        "como_llega": "mula", "q": "tierra", "bajos": "1",
        "accion": "crear_proveedor"}, follow_redirects=False)
    destino = respuesta.headers["location"]
    assert destino.endswith(compras.ANCLA_PROVEEDOR)
    # El estado de la pantalla también vuelve: lo buscado y la lista abierta.
    assert "q=tierra" in destino and "bajos=1" in destino

    borrador = compras.borrador_de(USUARIO)
    assert borrador["que_compro"] == "Pedido de octubre"
    assert borrador["resp"] == "Mary"
    assert borrador["como_llega"] == "mula"
    assert [(l["sku"], l["cantidad"]) for l in borrador["lineas"]] == \
        [("IN-TIERRA-NEGRA", 50.0)]
    # Y el nombre quedó con el de Odoo, así que la próxima pintada CALZA.
    assert borrador["proveedor"] == "Don Pepe"


def test_si_el_proveedor_no_se_crea_el_borrador_sigue_en_pie(monkeypatch,
                                                             cliente,
                                                             de_dueno):
    def revienta(*_a, **_k):
        raise RuntimeError("Odoo no contesta")

    monkeypatch.setattr(ventas, "configurado", lambda: True)
    monkeypatch.setattr(ventas, "_ejecutar", revienta)
    respuesta = cliente.post("/compras/borrador", data={
        "que_compro": "Pedido de octubre", "proveedor": "Don Pepe",
        "accion": "crear_proveedor"}, follow_redirects=False)
    assert "error=" in respuesta.headers["location"]
    assert "El%20proveedor%20no%20se%20cre" in respuesta.headers["location"]
    assert compras.borrador_de(USUARIO)["que_compro"] == "Pedido de octubre"


def test_en_solo_lectura_no_se_crea_ningun_proveedor(monkeypatch, cliente):
    """El candado del servidor: un POST se puede mandar a mano."""
    llamadas = []
    _con_proveedores(monkeypatch, anota=llamadas)
    monkeypatch.setenv("LINEAR_API_KEY", "clave-de-prueba")
    monkeypatch.setenv("CALENDARIO_ESCRITURA", "0")
    respuesta = cliente.post("/compras/borrador", data={
        "que_compro": "Pedido", "proveedor": "Don Pepe",
        "accion": "crear_proveedor"}, follow_redirects=False)
    assert "no%20escribe" in respuesta.headers["location"]
    assert [l for l in llamadas if l[1] == "create"] == []


# ---------------------------------------------------------------------------
# Y lo que no se tocó: ningún redirect nuevo se va sin su ancla
# ---------------------------------------------------------------------------

def test_los_redirect_nuevos_tambien_llevan_su_ancla(monkeypatch, cliente,
                                                     de_dueno):
    """El barrido de siempre, extendido a las acciones de esta tanda."""
    _odoo(monkeypatch, lambda modelo, metodo, args, kw: (
        [] if metodo == "search_read" else 82))
    base = {"que_compro": "Pedido", "proveedor": "Don Pepe", "resp": "",
            "lead_ref": "", "como_llega": "camion"}
    for datos_form in ({**base, "accion": "bajos"},
                       {**base, "accion": "ocultar_bajos", "bajos": "1"},
                       {**base, "accion": "crear_proveedor"},
                       {**base, "bajos": "1", "agregar": "PL-ALBAHACA",
                        "nom-PL-ALBAHACA": "Albahaca"}):
        respuesta = cliente.post("/compras/borrador", data=datos_form,
                                 follow_redirects=False)
        assert respuesta.status_code == 303, datos_form
        assert "#" in respuesta.headers["location"], datos_form


def test_el_formulario_sigue_siendo_un_solo_form_sin_javascript_nuevo(cliente,
                                                                      de_dueno):
    """Todo lo nuevo son submits del MISMO formulario con `formaction`: ni
    un archivo JS nuevo, ni un `onchange` que navegue."""
    texto = cliente.get("/compras?nueva=1&bajos=1&q=palma").text
    assert texto.count('class="modal"') == 1
    for guion in ("control.js", "menu.js", "calendario.js"):
        assert guion in texto
    assert texto.count("<script") == 3
    assert "onchange" not in texto and "oninput" not in texto
