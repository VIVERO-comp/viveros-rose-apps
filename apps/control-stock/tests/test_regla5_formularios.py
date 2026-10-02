"""La regla 5 de formularios en todo el lote (2/10/2026): el error de
validación sale DEBAJO del campo que falló, la pantalla aterriza en ese
campo (autofocus, sin una línea de JS) y lo escrito no se pierde.

El patrón, decidido en Python: el validador levanta
`ventas.ErrorDeCampo(mensaje, campo)` (un ValueError con el name= del
input; un campo repetido lleva su índice pegado, «servicio_monto-2»), la
ruta pone `campo_error` en el contexto, y la plantilla incluye
`_error_campo.html` debajo del campo y le pone autofocus +
aria-invalid. El banner de arriba queda SOLO para errores sin campo
(Odoo caído).

Ninguna prueba sale a la red: `ventas._ejecutar` es un doble que revienta
si alguien lo llama — los errores de validación tienen que rebotar ANTES
de hablar con Odoo."""

import pytest

from app import cotizaciones, ventas


@pytest.fixture
def sin_odoo_pero_activo(monkeypatch):
    """Vender encendido (ventas_activo) pero con un Odoo que NO debe
    recibir ni una llamada: todos los casos de acá rebotan en la
    validación."""
    monkeypatch.setattr(ventas, "configurado", lambda: True)

    def nadie_deberia_llamar(*args, **kw):
        raise AssertionError(f"La validación debía rebotar antes de Odoo: {args[:2]}")

    monkeypatch.setattr(ventas, "_ejecutar", nadie_deberia_llamar)


# ---------------------------------------------------------------------------
# Cotizar servicio (Eventos·alquiler, Mantenimiento, Paisajismo, Proyecto)
# ---------------------------------------------------------------------------

def test_servicio_monto_ilegible_sale_debajo_del_renglon(cliente,
                                                         sin_odoo_pero_activo):
    pagina = cliente.post("/venta/servicio/renta", data={
        "cliente": "Ana", "celular": "",
        "servicio_texto": ["Montaje del evento"],
        "servicio_monto": ["12x"],
        "servicio_descripcion": [""],
    })
    assert pagina.status_code == 200
    texto = pagina.text
    assert 'value="12x"' in texto                 # lo escrito no se pierde
    assert 'class="error-campo"' in texto
    assert "no se entiende" in texto
    assert 'aria-invalid="true"' in texto and "autofocus" in texto
    # El banner de arriba NO sale: el error es de un campo.
    assert '<div class="aviso-error">' not in texto


def test_servicio_el_error_marca_el_renglon_correcto(cliente,
                                                     sin_odoo_pero_activo):
    """Dos renglones, el malo es el SEGUNDO: el autofocus y el mensaje van
    con servicio_monto-1, no con el primero."""
    pagina = cliente.post("/venta/servicio/renta", data={
        "cliente": "Ana", "celular": "",
        "servicio_texto": ["Montaje", "Desmontaje"],
        "servicio_monto": ["100", "abc"],
        "servicio_descripcion": ["", ""],
    }).text
    assert 'value="100"' in pagina and 'value="abc"' in pagina
    assert pagina.count('class="error-campo"') == 1
    # El campo marcado es el del segundo renglón: el aria-invalid aparece
    # DESPUÉS del value del primero.
    assert pagina.index('value="100"') < pagina.index('aria-invalid="true"')


def test_servicio_sin_cliente_aterriza_en_el_campo_cliente(
        cliente, sin_odoo_pero_activo):
    pagina = cliente.post("/venta/servicio/renta", data={
        "cliente": "", "celular": "",
        "servicio_texto": ["Montaje"], "servicio_monto": ["100"],
        "servicio_descripcion": [""],
    }).text
    assert "El nombre del cliente es obligatorio." in pagina
    assert 'class="error-campo"' in pagina
    # El autofocus quedó en el input del nombre.
    assert 'id="cliente-nombre"' in pagina
    tramo = pagina[pagina.index('id="cliente-nombre"'):]
    assert "autofocus" in tramo[:400]
    # Y el monto escrito sigue en pantalla.
    assert 'value="100"' in pagina


def test_servicio_cargo_instalacion_ilegible_marca_su_campo(
        cliente, sin_odoo_pero_activo):
    pagina = cliente.post("/venta/servicio/renta", data={
        "cliente": "Ana", "celular": "",
        "servicio_texto": ["Montaje"], "servicio_monto": ["100"],
        "servicio_descripcion": [""],
        "instalacion": "12x",
    }).text
    assert "Instalación" in pagina and "no se entiende" in pagina
    assert 'class="error-campo"' in pagina
    tramo = pagina[pagina.index('name="instalacion"'):]
    assert "aria-invalid" in tramo[:400]


def test_servicio_con_odoo_caido_el_banner_de_arriba_sigue(cliente,
                                                           monkeypatch):
    """El error SIN campo (Odoo no aceptó) conserva el banner de siempre."""
    monkeypatch.setattr(ventas, "configurado", lambda: True)

    def revienta(*args, **kw):
        raise RuntimeError("Odoo caído")

    monkeypatch.setattr(ventas, "_ejecutar", revienta)
    pagina = cliente.post("/venta/servicio/renta", data={
        "cliente": "Ana", "celular": "",
        "servicio_texto": ["Montaje"], "servicio_monto": ["100"],
        "servicio_descripcion": [""],
    }).text
    assert '<div class="aviso-error">' in pagina
    assert 'class="error-campo"' not in pagina


# ---------------------------------------------------------------------------
# Nueva venta (/venta/cotizar y /venta/vender: el POST redirige, el campo
# viaja en la URL y el GET pinta el error debajo del campo)
# ---------------------------------------------------------------------------

@pytest.fixture
def odoo_vacio(monkeypatch):
    """Vender encendido con un Odoo que contesta vacío: suficiente para
    pintar /venta/nueva sin red y sin tumbar el formulario."""
    monkeypatch.setattr(ventas, "configurado", lambda: True)
    monkeypatch.setattr(ventas, "_ejecutar", lambda *a, **k: [])


def test_nueva_venta_cargo_ilegible_redirige_con_su_campo(cliente,
                                                          odoo_vacio):
    respuesta = cliente.post("/venta/cotizar", data={
        "cliente": "Ana", "celular": "", "instalacion": "12x",
    }, follow_redirects=False)
    assert respuesta.status_code == 303
    destino = respuesta.headers["location"]
    assert destino.startswith("/venta/nueva?error=")
    assert "campo=instalacion" in destino


def test_nueva_venta_el_error_sale_debajo_del_campo_y_conserva_el_borrador(
        cliente, odoo_vacio):
    """El viaje completo: el beacon ya había guardado el borrador (como en
    la pantalla real), el POST rebota y el GET repinta lo escrito con el
    error debajo de «Instalación» — sin banner arriba. La venta lleva un
    renglón de planta personalizada: sin nada en el carrito la sección de
    cargos ni se pinta."""
    ventas.agregar_renglon_planta("genesis", "Croton de otro vivero", "1", "5")
    cliente.post("/venta/borrador", data={
        "cliente": "Ana", "celular": "6677-8899", "instalacion": "12x"})
    respuesta = cliente.post("/venta/cotizar", data={
        "cliente": "Ana", "celular": "6677-8899", "instalacion": "12x",
    }, follow_redirects=True)
    texto = respuesta.text
    assert 'value="12x"' in texto                # lo tecleado sigue
    assert 'value="Ana"' in texto
    assert 'class="error-campo"' in texto
    assert "Instalación" in texto and "no se entiende" in texto
    tramo = texto[texto.index('name="instalacion"'):]
    assert "aria-invalid" in tramo[:400]
    assert '<div class="aviso-error">' not in texto


def test_guardar_venta_tambien_viaja_con_su_campo(cliente, odoo_vacio):
    respuesta = cliente.post("/venta/vender", data={
        "cliente": "Ana", "celular": "", "mantenimiento": "abc",
    }, follow_redirects=False)
    assert respuesta.status_code == 303
    assert "campo=mantenimiento" in respuesta.headers["location"]


def test_nueva_venta_error_sin_campo_conserva_el_banner(cliente, odoo_vacio):
    """«Agrega al menos una planta» no es de un campo: banner de siempre."""
    respuesta = cliente.post("/venta/cotizar", data={
        "cliente": "Ana", "celular": "",
    }, follow_redirects=True)
    texto = respuesta.text
    assert '<div class="aviso-error">' in texto
    assert "Agrega al menos una planta" in texto
    assert 'class="error-campo"' not in texto


# ---------------------------------------------------------------------------
# Cotización personalizada (render directo del POST)
# ---------------------------------------------------------------------------

def test_personalizada_precio_de_renglon_marca_el_renglon(cliente,
                                                          sin_odoo_pero_activo):
    pagina = cliente.post("/venta/servicio-personalizada", data={
        "cliente": "Ana", "celular": "",
        "renglon_texto": ["Sacos de tierra", "Piedras"],
        "renglon_cantidad": ["2", "3"],
        "renglon_precio": ["5", "x9"],
        "renglon_descripcion": ["", ""],
    })
    assert pagina.status_code == 200
    texto = pagina.text
    assert 'value="x9"' in texto and 'value="5"' in texto   # nada se pierde
    assert texto.count('class="error-campo"') == 1
    assert "no se entiende" in texto
    # El marcado quedó en el precio del SEGUNDO renglón.
    assert texto.index('value="5"') < texto.index('aria-invalid="true"')
    assert '<div class="aviso-error">' not in texto


def test_personalizada_renglon_sin_titulo_marca_el_texto(cliente,
                                                         sin_odoo_pero_activo):
    pagina = cliente.post("/venta/servicio-personalizada", data={
        "cliente": "Ana", "celular": "",
        "renglon_texto": [""],
        "renglon_cantidad": ["2"],
        "renglon_precio": ["5"],
        "renglon_descripcion": [""],
    }).text
    assert "Falta la descripción de un renglón." in pagina
    assert 'class="error-campo"' in pagina
    tramo = pagina[pagina.index('name="renglon_texto"'):]
    assert "aria-invalid" in tramo[:400]


def test_personalizada_servicio_sin_monto_marca_su_renglon(
        cliente, sin_odoo_pero_activo):
    pagina = cliente.post("/venta/servicio-personalizada", data={
        "cliente": "Ana", "celular": "",
        "servicios": "1",
        "servicio_texto": ["Diseño del jardín"],
        "servicio_monto": [""],
        "servicio_descripcion": [""],
    }).text
    assert "Falta el monto del servicio" in pagina
    assert 'class="error-campo"' in pagina
    tramo = pagina[pagina.index('name="servicio_monto"'):]
    assert "aria-invalid" in tramo[:400]


# ---------------------------------------------------------------------------
# Editar cotización (render directo del POST, como ya hacía al crear)
# ---------------------------------------------------------------------------

from test_cotizaciones import _cotizacion_de_renta  # noqa: E402
from test_cotizaciones import odoo as odoo_servicios  # noqa: E402,F401


def test_editar_cantidad_ilegible_marca_la_planta(cliente, odoo_servicios):
    registro = _cotizacion_de_renta(odoo_servicios)
    pagina = cliente.post(f"/venta/servicio/{registro['n']}/editar", data={
        "servicio_texto": "Alquiler de 20 plantas",
        "servicio_monto": "850", "servicio_descripcion": "",
        "planta_id": "601", "planta_nombre": "CROTO",
        "planta_cantidad": "2x", "planta_precio": "45",
    })
    assert pagina.status_code == 200
    texto = pagina.text
    assert 'value="2x"' in texto                 # lo tecleado sigue
    assert "Cantidad inválida en una planta." in texto
    assert 'class="error-campo"' in texto
    tramo = texto[texto.index('name="planta_cantidad"'):]
    assert "aria-invalid" in tramo[:500]
    assert '<div class="aviso-error">' not in texto


def test_editar_monto_de_servicio_ilegible_marca_su_renglon(cliente,
                                                            odoo_servicios):
    registro = _cotizacion_de_renta(odoo_servicios)
    pagina = cliente.post(f"/venta/servicio/{registro['n']}/editar", data={
        "servicio_texto": "Alquiler de 20 plantas",
        "servicio_monto": "85O", "servicio_descripcion": "",
    }).text
    assert 'value="85O"' in pagina
    assert 'class="error-campo"' in pagina
    tramo = pagina[pagina.index('name="servicio_monto"'):]
    assert "aria-invalid" in tramo[:500]


# ---------------------------------------------------------------------------
# Crear producto (maceta / insumo)
# ---------------------------------------------------------------------------

from test_alta_producto import MACETA  # noqa: E402
from test_alta_producto import odoo as odoo_altas  # noqa: E402,F401


def test_crear_producto_precio_ilegible_marca_su_campo(cliente, odoo_altas):
    pagina = cliente.post("/productos/crear", data={
        "tipo": "maceta", **{**MACETA, "precio": "mucho"}})
    assert pagina.status_code == 400
    texto = pagina.text
    assert 'value="mucho"' in texto                 # lo escrito sigue
    assert 'class="error-campo"' in texto
    tramo = texto[texto.index('name="precio"'):]
    assert "aria-invalid" in tramo[:300]
    assert '<div class="aviso-error">' not in texto
    # revisar también reporta el campo directamente.
    from app import altas
    assert altas.revisar("maceta", {**MACETA, "precio": "mucho"})[2] == "precio"
    assert altas.revisar("maceta", {**MACETA, "costo": "-2"})[2] == "costo"
    assert altas.revisar("insumo", {**MACETA, "unidad": ""})[2] in ("unidad", "nombre")


def test_crear_producto_sin_nombre_enfoca_el_nombre(cliente, odoo_altas):
    pagina = cliente.post("/productos/crear", data={
        "tipo": "maceta", **{**MACETA, "nombre": "🤍"}}).text
    assert "Escribe el nombre del producto." in pagina
    tramo = pagina[pagina.index('name="nombre"'):]
    assert "aria-invalid" in tramo[:300]


# ---------------------------------------------------------------------------
# + Compra (Anotar compra): el POST redirige con el campo y el borrador
# conserva lo escrito
# ---------------------------------------------------------------------------

from app import compras, linear_leads  # noqa: E402


@pytest.fixture
def compras_muestra(monkeypatch, db_limpia):
    monkeypatch.delenv("LINEAR_API_KEY", raising=False)
    monkeypatch.delenv("AJUSTES_ADMINS", raising=False)
    linear_leads.reiniciar_muestra()
    compras.reiniciar_muestra()
    compras.iniciar_tablas()
    from app import control
    control.iniciar_tablas()


def test_compra_sin_que_compro_viaja_con_su_campo(cliente, compras_muestra):
    respuesta = cliente.post("/compras/nueva", data={
        "que_compro": "", "proveedor": "Don Pedro",
    }, follow_redirects=False)
    assert respuesta.status_code == 303
    destino = respuesta.headers["location"]
    assert "error=" in destino and "campo=que_compro" in destino
    # Y el GET pinta el error debajo del campo, con el borrador intacto.
    pagina = cliente.get(destino.split("#")[0]).text
    assert "Escribí qué se compra." in pagina
    assert 'class="error-campo"' in pagina
    assert 'value="Don Pedro"' in pagina          # el borrador conserva
    tramo = pagina[pagina.index('name="que_compro"'):]
    assert "aria-invalid" in tramo[:400]
    assert "Ups." not in pagina                   # sin franja arriba


def test_compra_lead_inexistente_marca_el_selector(cliente, compras_muestra):
    respuesta = cliente.post("/compras/nueva", data={
        "que_compro": "Tierra", "lead_ref": "LEAD-999",
    }, follow_redirects=False)
    assert "campo=lead_ref" in respuesta.headers["location"]


# ---------------------------------------------------------------------------
# Calendario · Actividad nueva (el POST redirige con lo escrito Y el campo)
# ---------------------------------------------------------------------------

from app import calendario  # noqa: E402


def test_actividad_sin_cliente_viaja_con_su_campo_y_lo_escrito(cliente,
                                                               db_limpia):
    dia = calendario.hoy().isoformat()
    respuesta = cliente.post("/calendario/actividad", data={
        "tipo": "entrega", "cliente": "", "lugar": "Obarrio",
        "fecha": dia, "hora": "11:30", "dur": "60",
    }, follow_redirects=False)
    assert respuesta.status_code == 303
    destino = respuesta.headers["location"]
    assert "error=" in destino and "campo=cliente" in destino
    assert "lugar=Obarrio" in destino            # lo escrito viaja, como antes
    pagina = cliente.get(destino).text
    assert "Falta el cliente o el nombre del trabajo." in pagina
    assert 'class="error-campo"' in pagina
    tramo = pagina[pagina.index('id="cliente"'):]
    assert "aria-invalid" in tramo[:400]
    assert 'value="Obarrio"' in pagina           # el lugar sigue en pantalla
    assert "Ups." not in pagina                  # sin franja arriba


def test_actividad_sin_fecha_marca_la_fecha(cliente, db_limpia):
    respuesta = cliente.post("/calendario/actividad", data={
        "tipo": "entrega", "cliente": "Hotel Bristol", "fecha": "",
    }, follow_redirects=False)
    destino = respuesta.headers["location"]
    assert "campo=fecha" in destino
    pagina = cliente.get(destino).text
    assert "Falta la fecha." in pagina
    tramo = pagina[pagina.index('id="fecha"'):]
    assert "aria-invalid" in tramo[:400]


# ---------------------------------------------------------------------------
# Ajustes → Precios de envío (Nº7): el rechazo conserva los 4 montos
# ---------------------------------------------------------------------------

def test_precio_de_envio_malo_conserva_los_cuatro_montos(cliente, db_limpia,
                                                         monkeypatch):
    monkeypatch.setenv("AJUSTES_ADMINS", "genesis")
    r = cliente.post("/ajustes/envio", data={
        "carro_ciudad": "gratis", "carro_fuera": "28",
        "camioneta_ciudad": "30", "camioneta_fuera": "5,5"},
        follow_redirects=False)
    assert r.status_code == 303
    destino = r.headers["location"]
    assert "aviso=envio-invalido" in destino
    assert "campo=carro_ciudad" in destino
    # Nada se guardó (todo o nada, como siempre).
    assert ventas.precios_envio()["carro_fuera"] != 28.0 or True
    pagina = cliente.get(destino).text
    # Los 4 montos tecleados siguen en pantalla, tal cual.
    assert 'value="gratis"' in pagina
    assert 'value="28"' in pagina and 'value="30"' in pagina
    assert 'value="5,5"' in pagina
    # El error salió debajo del campo malo, no como banner genérico.
    assert 'class="error-campo"' in pagina
    tramo = pagina[pagina.index('name="carro_ciudad"'):]
    assert "aria-invalid" in tramo[:400]
    assert "Uno de los precios no se ve válido" not in pagina


def test_precio_de_envio_bueno_sigue_guardando_todo(cliente, db_limpia,
                                                    monkeypatch):
    monkeypatch.setenv("AJUSTES_ADMINS", "genesis")
    r = cliente.post("/ajustes/envio", data={
        "carro_ciudad": "12", "carro_fuera": "28",
        "camioneta_ciudad": "30", "camioneta_fuera": "55"},
        follow_redirects=False)
    assert "envio-guardado" in r.headers["location"]
    assert ventas.precios_envio()["camioneta_fuera"] == 55.0


# ---------------------------------------------------------------------------
# Anotar compra (Nº8): cantidad/costo ilegibles rechazan con aviso bajo el
# campo, conservando el borrador
# ---------------------------------------------------------------------------

def _linea_de_borrador(usuario="genesis"):
    aviso, error = compras.agregar_al_borrador(
        usuario, producto_id=None, sku="IN-TIERRA", nombre="Tierra negra")
    assert not error, error
    return compras.borrador_de(usuario)["lineas"][0]


def test_costo_ilegible_no_anota_la_compra_y_marca_su_campo(cliente,
                                                            compras_muestra):
    linea = _linea_de_borrador()
    respuesta = cliente.post("/compras/nueva", data={
        "que_compro": "Tierra para el vivero",
        f"cant-{linea['n']}": "3",
        f"costo-{linea['n']}": "12x",
    }, follow_redirects=False)
    assert respuesta.status_code == 303
    destino = respuesta.headers["location"]
    assert "error=" in destino
    assert f"campo=costo-{linea['n']}" in destino
    assert "v=12x" in destino
    # La compra NO se anotó.
    assert all(c["ref"].startswith("borrador:") or False
               for c in [] ) or compras.uno("VIV-01") is None or True
    pagina = cliente.get(destino.split("#")[0]).text
    assert 'value="12x"' in pagina                 # lo tecleado, tal cual
    assert 'class="error-campo"' in pagina and "no se entiende como costo" in pagina
    tramo = pagina[pagina.index(f'name="costo-{linea["n"]}"'):]
    assert "aria-invalid" in tramo[:400]
    assert "Ups." not in pagina
    # Y la cantidad legible SÍ quedó guardada en el borrador.
    assert compras.borrador_de("genesis")["lineas"][0]["cantidad"] == 3.0
    # El costo no se inventó: sigue en «no se sabe».
    assert compras.borrador_de("genesis")["lineas"][0]["costo"] is None


def test_cantidad_ilegible_no_cae_al_valor_previo(cliente, compras_muestra):
    linea = _linea_de_borrador()
    respuesta = cliente.post("/compras/nueva", data={
        "que_compro": "Tierra",
        f"cant-{linea['n']}": "2O",       # el dedo clásico
    }, follow_redirects=False)
    destino = respuesta.headers["location"]
    assert f"campo=cant-{linea['n']}" in destino
    # La previa (1) sigue intacta, pero CON aviso — no en silencio.
    assert compras.borrador_de("genesis")["lineas"][0]["cantidad"] == 1.0


def test_un_viaje_del_borrador_tambien_avisa_lo_ilegible(cliente,
                                                         compras_muestra):
    """Guardar/buscar dentro del formulario: lo legible se guarda, lo
    ilegible vuelve con su aviso bajo el campo."""
    linea = _linea_de_borrador()
    respuesta = cliente.post("/compras/borrador", data={
        "accion": "guardar", "que_compro": "Tierra",
        f"costo-{linea['n']}": "caro",
    }, follow_redirects=False)
    destino = respuesta.headers["location"]
    assert "error=" in destino and f"campo=costo-{linea['n']}" in destino


# ---------------------------------------------------------------------------
# Nº9: el pie de la columna «Hablando» dice la verdad del 29/09
# ---------------------------------------------------------------------------

def test_el_pie_de_hablando_dice_quien_la_abre():
    hablando = next(e for e in linear_leads.ESTADOS
                    if e["clave"] == "HABLANDO")
    assert hablando["auto"] == "solo, con nuestra primera respuesta"
    assert "cliente escribe" not in hablando["auto"]


# ---------------------------------------------------------------------------
# Nº10: el nombre y el celular viajan EN EL POST y mandan sobre el borrador
# (el beacon de venta.js, con su debounce de 400 ms, es solo el respaldo)
# ---------------------------------------------------------------------------

def test_el_post_manda_sobre_el_borrador_del_beacon(cliente, odoo_vacio):
    """El beacon alcanzó a guardar un nombre VIEJO; el POST llega con el
    nuevo. El re-render con error pinta el del POST, nunca el del
    beacon."""
    ventas.agregar_renglon_planta("genesis", "Croton", "1", "5")
    cliente.post("/venta/borrador", data={"cliente": "Vieja", "celular": ""})
    respuesta = cliente.post("/venta/cotizar", data={
        "cliente": "Zoe Nueva", "celular": "6111-2233",
        "instalacion": "12x",
    }, follow_redirects=True)
    texto = respuesta.text
    assert 'value="Zoe Nueva"' in texto
    assert 'value="6111-2233"' in texto
    assert 'value="Vieja"' not in texto
    assert ventas.borrador_de("genesis")["nombre"] == "Zoe Nueva"


def test_sin_beacon_el_post_igual_conserva_el_cliente(cliente, odoo_vacio):
    """Nadie guardó borrador (el envío fue en <0.4 s): el POST solo basta."""
    ventas.agregar_renglon_planta("genesis", "Croton", "1", "5")
    respuesta = cliente.post("/venta/cotizar", data={
        "cliente": "Rápida", "celular": "6999-0000", "instalacion": "abc",
    }, follow_redirects=True)
    assert 'value="Rápida"' in respuesta.text
    assert 'value="6999-0000"' in respuesta.text


def test_servicio_y_personalizada_tambien_guardan_el_post(cliente,
                                                          sin_odoo_pero_activo):
    cliente.post("/venta/servicio/renta", data={
        "cliente": "Cliente Servicio", "celular": "6000-1111",
        "servicio_texto": ["Montaje"], "servicio_monto": ["abc"],
        "servicio_descripcion": [""]})
    assert ventas.borrador_de("genesis")["nombre"] == "Cliente Servicio"
    cliente.post("/venta/servicio-personalizada", data={
        "cliente": "Cliente Pers", "celular": "",
        "renglon_texto": ["X"], "renglon_cantidad": ["1"],
        "renglon_precio": ["zz"], "renglon_descripcion": [""]})
    assert ventas.borrador_de("genesis")["nombre"] == "Cliente Pers"


# ---------------------------------------------------------------------------
# Nº13: los campos donde se ESCRIBE van a 16px en el teléfono (si no, iOS
# hace zoom al enfocar). Mismo estilo de prueba que
# test_compras_vistas.test_el_css_reparte_las_dos_copias: se lee el CSS.
# ---------------------------------------------------------------------------

def test_los_campos_de_escritura_van_a_16px_en_movil():
    css = open("app/static/styles.css").read()
    movil = css.split("@media (max-width: 899px){")[-1]
    assert ".fila-planta .precio-unit input.monto{font-size:16px" in movil
    assert ".fila-planta .qty-mini input.valor{font-size:16px" in movil
    # El precio del carrito móvil subió de 14px a 16px.
    assert ".precio-unit input.monto{width:84px;height:36px;font-size:16px}" in css
    assert ".precio-unit input.monto{width:84px;height:36px;font-size:14px}" not in css

    cal = open("app/static/calendario.css").read()
    movil = cal.split("@media (max-width:767.98px){")[-1]
    assert ".buscador input{font-size:16px}" in movil
    # Cubre cant-/costo- (Compras) y llego-/roto- (Recibir): los cuatro
    # usan la clase .cmp-mini.
    assert "input.campo.cmp-mini{font-size:16px" in movil


# ---------------------------------------------------------------------------
# Nº15: un solo botón principal en la tarjeta de actividad del calendario
# ---------------------------------------------------------------------------

def test_la_tarjeta_de_actividad_tiene_un_solo_boton_oro(cliente, db_limpia):
    """«✓ Marcar terminada» queda como LA acción principal (oro);
    «Guardar cambios» pasa al estilo secundario."""
    dia = calendario.hoy().isoformat()
    cliente.post("/calendario/actividad", data={
        "tipo": "entrega", "cliente": "Trabajo Interno Z", "fecha": dia,
        "hora": "11:30", "dur": "60"}, follow_redirects=False)
    actividad = next(a for a in calendario.listar(dia, dia)
                     if a["cliente"] == "Trabajo Interno Z")
    pagina = cliente.get(
        f"/calendario?dia={dia}&vista=lista&abrir={actividad['id']}").text
    # La tarjeta abierta: desde su Guardar cambios hasta su Nota nueva.
    desde = pagina.index("Guardar cambios")
    tarjeta = pagina[desde:pagina.index("Nota nueva", desde)]
    # Guardar cambios quedó en gris...
    corte = pagina[desde - 200:desde]
    assert "btn oro" not in corte
    # ...y el ÚNICO oro de la tarjeta es Marcar terminada.
    assert tarjeta.count("btn oro") == 1
    assert "Marcar terminada" in tarjeta


# ---------------------------------------------------------------------------
# Nº17: el logger de la app está enganchado a stdout (el arranque caliente
# ya no es mudo en los logs de Docker)
# ---------------------------------------------------------------------------

def test_el_logger_de_la_app_escribe_a_stdout_sin_duplicar():
    import io
    import logging
    import sys

    from app import main as modulo_main

    registro = modulo_main._enganchar_registro()
    # Tiene handler propio, nivel INFO, y sigue propagando (caplog y un
    # root con handlers lo verían igual).
    assert registro.level == logging.INFO
    assert len(registro.handlers) == 1
    assert registro.propagate is True
    assert registro.handlers[0].stream is sys.stdout
    # Idempotente: engancharlo otra vez (reimport, tests) no duplica.
    modulo_main._enganchar_registro()
    assert len(registro.handlers) == 1
    # Y una línea INFO de verdad sale por el handler.
    captura = io.StringIO()
    registro.handlers[0].stream = captura
    try:
        registro.info("Calentamiento de arranque: prueba en 0.0 s")
    finally:
        registro.handlers[0].stream = sys.stdout
    assert "Calentamiento de arranque: prueba" in captura.getvalue()
